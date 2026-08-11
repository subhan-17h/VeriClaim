#!/usr/bin/env python3
"""Fetch and verify every model weight the system needs, before a demo needs them.

Three model families have to be on disk for VeriClaim to run offline: Docling's
layout and table models, its OCR checkpoints, and the FlashRank reranker. The
embedding model is Ollama's and is reported rather than pulled by default, because
those downloads are gigabytes.

**OCR weights are part of the required set here, and that is the whole point of this
script.** The reference implementation this is adapted from downloads layout and
tableformer and skips OCR deliberately -- reasonably, since it never reads a scan.
Carrying that exclusion across would mean an image-only claim file meeting a pipeline
with nothing to read it, discovered at the moment it is being demonstrated.

Which OCR checkpoints are required is resolved through Docling for the *configured*
engine and language rather than listed here by hand. RapidOCR serves English from a
multilingual PP-OCRv6 checkpoint today; a version bump would leave a hand-written list
naming files nobody downloads any more, and this script would then cheerfully verify
weights the parser does not use.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from vericlaim.config import Settings, get_settings

# Docling's non-OCR parser weights, named by their layout in the artifacts directory.
DOCLING_CORE_FILES = (
    Path("docling-project--docling-layout-heron/model.safetensors"),
    Path(
        "docling-project--docling-models/model_artifacts/tableformer/accurate/"
        "tableformer_accurate.safetensors"
    ),
)

# The subdirectory Docling keeps RapidOCR checkpoints in, relative to the artifacts
# directory. Read from Docling itself in :func:`required_ocr_files`; named here only
# for the download command's benefit.
RAPIDOCR_DIRECTORY = "RapidOcr"


class UnsupportedOcrEngineError(RuntimeError):
    """Raised for an OCR engine whose weights this script cannot enumerate.

    Returning an empty list instead would report success for weights nobody checked,
    which is the failure mode this script exists to remove rather than relocate.
    """


def required_ocr_files(settings: Settings) -> tuple[Path, ...]:
    """Return the OCR checkpoints the configured engine needs, relative to artifacts.

    Resolved by asking Docling which files it will look for, given the engine and
    language this project is configured with, so the answer stays correct across a
    checkpoint version bump instead of drifting silently.
    """
    if settings.ocr_engine != "rapidocr":
        raise UnsupportedOcrEngineError(
            f"This script cannot enumerate weights for the {settings.ocr_engine!r} "
            "engine. Verify them by hand, or switch VC_OCR_ENGINE back to rapidocr."
        )

    from docling.models.stages.ocr.rapid_ocr_model import (
        _backend_to_engine_type,
        _rapidocr_artifacts,
        _resolve_rapidocr,
    )

    backend = _rapidocr_backend()
    language = settings.ocr_lang[0] if settings.ocr_lang else "english"
    resolved = _resolve_rapidocr(language, backend)
    root = Path(RAPIDOCR_DIRECTORY)
    artifacts = _rapidocr_artifacts(
        root,
        _backend_to_engine_type(backend),
        resolved.ppocr_version,
        resolved.rapidocr_lang_token,
    )
    return tuple(
        destination
        for artifact in artifacts.values()
        for destination in artifact.files
    )


def _rapidocr_backend() -> str:
    """Return the backend Docling will use, taken from its own default options."""
    from docling.datamodel.pipeline_options import RapidOcrOptions

    return RapidOcrOptions().backend


def required_docling_files(settings: Settings) -> tuple[Path, ...]:
    """Return every Docling weight file this project's pipelines read."""
    return (*DOCLING_CORE_FILES, *required_ocr_files(settings))


def missing_files(path: Path, required: tuple[Path, ...]) -> tuple[Path, ...]:
    """Return the required files absent from ``path``, in the order given."""
    return tuple(relative for relative in required if not (path / relative).is_file())


def _directory_size(path: Path) -> int:
    """Return the total size of regular files below ``path``."""
    if not path.is_dir():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def warm_docling(settings: Settings) -> bool:
    """Download Docling's layout, table, and OCR weights when any are absent."""
    path = settings.docling_artifacts_path.expanduser()
    print("Docling models:")
    print(f"  path: {path}")

    try:
        required = required_docling_files(settings)
    except UnsupportedOcrEngineError as exc:
        print(f"  FAILED: {exc}\n")
        return False
    except ImportError as exc:
        print(f"  FAILED: Docling is unavailable or has moved: {exc}\n")
        return False

    language = settings.ocr_lang[0] if settings.ocr_lang else "english"
    print(f"  OCR: {settings.ocr_engine} ({language}), {len(required)} required files")

    if not missing_files(path, required):
        print(f"  {_directory_size(path):,} bytes  [skipped (already present)]\n")
        return True

    executable = shutil.which("docling-tools")
    if executable is None:
        print("  FAILED: `docling-tools` is unavailable; run `uv sync`.\n")
        return False

    print("  downloading layout, tableformer, and OCR weights...")
    result = subprocess.run(  # noqa: S603
        [
            executable,
            "models",
            "download",
            "--output-dir",
            str(path),
            "--rapidocr-backend-lang",
            f"{_rapidocr_backend()}:{language}",
            "layout",
            "tableformer",
            "rapidocr",
        ],
        check=False,
    )
    size = _directory_size(path)
    if result.returncode != 0:
        print(f"  {size:,} bytes  [FAILED: downloader exited {result.returncode}]\n")
        return False

    still_missing = missing_files(path, required)
    if still_missing:
        listed = ", ".join(str(relative) for relative in still_missing)
        print(f"  {size:,} bytes  [FAILED: still missing {listed}]\n")
        return False

    print(f"  {size:,} bytes  [downloaded]\n")
    return True


def warm_flashrank(settings: Settings) -> bool:
    """Download and verify the configured FlashRank reranker weights."""
    path = settings.flashrank_cache_dir.expanduser()
    model_path = path / settings.flashrank_model
    print("FlashRank model:")
    print(f"  path: {model_path}")

    if not settings.rerank_enabled:
        print("  reranking is disabled; weights are not required.\n")
        return True

    try:
        from flashrank import Ranker
        from flashrank.Config import model_file_map
    except ImportError:
        print("  FAILED: FlashRank is unavailable; run `uv sync`.\n")
        return False

    model_file = model_file_map.get(settings.flashrank_model)
    if model_file is None:
        print(f"  FAILED: unsupported model {settings.flashrank_model!r}.\n")
        return False

    weights_path = model_path / model_file
    already_present = weights_path.is_file()
    try:
        Ranker(
            model_name=settings.flashrank_model,
            cache_dir=str(path),
            log_level="WARNING",
        )
    except Exception as exc:  # noqa: BLE001 - any failure here means "not ready"
        print(f"  FAILED: could not prepare the model: {exc}\n")
        return False

    size = _directory_size(model_path)
    if not weights_path.is_file():
        print(f"  {size:,} bytes  [FAILED: required weights are still missing]\n")
        return False

    status = "skipped (already present)" if already_present else "downloaded"
    print(f"  {size:,} bytes  [{status}]\n")
    return True


def warm_ollama(settings: Settings, pull: bool) -> bool:
    """Report the embedding model's presence, and optionally pull it.

    Only embeddings run locally. Every generative call goes through the gateway to a
    hosted provider, so there is no list of chat models to warm here.
    """
    required = settings.embed_model
    print("Ollama models:")
    print(f"  host: {settings.ollama_host}")

    try:
        import ollama
    except ImportError:
        print("  FAILED: the Ollama Python client is unavailable; run `uv sync`.\n")
        return False

    client = ollama.Client(host=settings.ollama_host)
    try:
        response = client.list()
    except OSError as exc:
        print(f"  FAILED: Ollama is unreachable at {settings.ollama_host}: {exc}")
        print("  Start it with `ollama serve`, then run this script again.\n")
        return False
    except (ollama.RequestError, ollama.ResponseError) as exc:
        print(f"  FAILED: Ollama could not list models: {exc}\n")
        return False

    installed = {
        model.model.split(":")[0]
        for model in response.models
        if model.model is not None
    }
    present = required.split(":")[0] in installed
    print(f"  {required}  [{'present' if present else 'missing'}]")

    if present:
        print()
        return True
    if not pull:
        print(f"\n  Not pulled automatically. Run:\n    ollama pull {required}\n")
        return False

    print(f"  Pulling {required}...")
    try:
        client.pull(required)
    except (OSError, ollama.RequestError, ollama.ResponseError) as exc:
        print(f"    FAILED: {exc}\n")
        return False
    print("    pulled\n")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pull-ollama",
        action="store_true",
        help="pull the embedding model if absent (the download is hundreds of MB)",
    )
    args = parser.parse_args()
    settings = get_settings()

    print("Preparing model weights for offline use\n")
    docling_ok = warm_docling(settings)
    flashrank_ok = warm_flashrank(settings)
    ollama_ok = warm_ollama(settings, args.pull_ollama)

    print("Summary:")
    print(f"  Docling (layout, table, OCR): {'ready' if docling_ok else 'not ready'}")
    print(f"  FlashRank: {'ready' if flashrank_ok else 'not ready'}")
    print(f"  Ollama embeddings: {'ready' if ollama_ok else 'not ready'}")

    if not (docling_ok and flashrank_ok and ollama_ok):
        print("\nRequired model weights are missing or could not be verified.")
        return 1
    print("\nAll required model weights are present.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
