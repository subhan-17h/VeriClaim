"""Model warming: prove the weights are present before a demo needs them.

The failure this guards against is the reference implementation's: it downloads
layout and table weights and skips OCR deliberately, so an image-only PDF meets a
pipeline with nothing to read it and fails at the moment it is being demonstrated.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from vericlaim.config import get_settings

SCRIPT = Path(__file__).parents[1] / "scripts" / "warm_models.py"


def _load_script():
    """Import the script by path; scripts/ is deliberately not a package."""
    spec = importlib.util.spec_from_file_location("warm_models", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def warm():
    return _load_script()


@pytest.fixture
def settings():
    return get_settings()


# ------------------------------------------------------------- what is required


def test_the_ocr_checkpoints_are_required(warm, settings) -> None:
    """The C-4.7 point: OCR weights are no longer excluded from the warm set."""
    required = warm.required_docling_files(settings)

    assert any("RapidOcr" in part for path in required for part in path.parts)


def test_the_layout_and_table_weights_are_still_required(warm, settings) -> None:
    required = {path.as_posix() for path in warm.required_docling_files(settings)}

    assert any("docling-layout" in path for path in required)
    assert any("tableformer" in path for path in required)


def test_the_ocr_checkpoints_follow_the_configured_engine(warm, settings) -> None:
    """Resolved through Docling, so a checkpoint bump cannot leave this list stale."""
    required = warm.required_ocr_files(settings)

    assert required
    assert all(path.parts[0] == "RapidOcr" for path in required)
    assert all(path.suffix == ".onnx" for path in required)


def test_an_engine_whose_weights_we_cannot_enumerate_is_reported(warm, settings) -> None:
    """Returning an empty list would vouch for weights nobody checked."""
    with pytest.raises(warm.UnsupportedOcrEngineError, match="tesseract"):
        warm.required_ocr_files(settings.model_copy(update={"ocr_engine": "tesseract"}))


# ------------------------------------------------------------- readiness checks


def test_a_complete_artifacts_directory_is_ready(warm, tmp_path: Path, settings) -> None:
    for relative in warm.required_docling_files(settings):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"weights")

    assert warm.missing_files(tmp_path, warm.required_docling_files(settings)) == ()


def test_a_missing_ocr_checkpoint_is_not_ready(warm, tmp_path: Path, settings) -> None:
    """Layout and table weights present is exactly the state that hid this before."""
    required = warm.required_docling_files(settings)
    ocr_files = set(warm.required_ocr_files(settings))
    for relative in required:
        if relative in ocr_files:
            continue
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"weights")

    missing = warm.missing_files(tmp_path, required)

    assert missing
    assert set(missing) == ocr_files


def test_an_empty_directory_is_missing_everything(warm, tmp_path: Path, settings) -> None:
    required = warm.required_docling_files(settings)

    assert set(warm.missing_files(tmp_path, required)) == set(required)


# ---------------------------------------------------- against the real cache


@pytest.mark.ocr
def test_the_configured_weights_are_actually_on_this_machine(warm, settings) -> None:
    """The tests marked ocr run OCR for real; this says why they can."""
    path = settings.docling_artifacts_path.expanduser()

    assert warm.missing_files(path, warm.required_docling_files(settings)) == ()
