"""Walk the generated corpus into every index, over one set of connections.

No indexing logic lives here. The three ingest paths already exist and are already
tested; what was missing was anything that calls them over a real corpus, sharing one
embedder, one chunk store and one database rather than opening its own of each.

The hazard this module exists to close is the shared manifest. Policy and scanned
documents land in the same Chroma collection, separated only by ``source_type``, but
``index_corpus`` removes every document its manifest names and its directory does not
hold. Point both passes at one manifest and the second silently deletes the first's
chunks -- the collection ends up holding one source, the run reports success, and the
missing half only surfaces as an answer that cites nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from vericlaim.config import Settings, get_settings
from vericlaim.policy.embeddings import Embedder, OllamaEmbedder
from vericlaim.policy.indexer import IndexResult, index_corpus
from vericlaim.policy.store import ChunkStore
from vericlaim.scanned.indexer import OcrParser, index_scanned_corpus
from vericlaim.sheets.ingest import IngestedTable, ingest_workbook
from vericlaim.sql.db import Database, default_database

__all__ = ("LoadReport", "load_corpus")

ProgressCallback = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class LoadReport:
    """What one load did, per source."""

    policy: IndexResult
    scanned: IndexResult
    tables: tuple[IngestedTable, ...] = ()
    workbooks: int = 0

    @property
    def chunks(self) -> int:
        return self.policy.chunks_created + self.scanned.chunks_created

    @property
    def changed(self) -> bool:
        """Whether anything differs from before the run. Workbooks always reload."""
        return self.policy.changed or self.scanned.changed or bool(self.tables)


def load_corpus(
    *,
    settings: Settings | None = None,
    store: ChunkStore | None = None,
    embedder: Embedder | None = None,
    database: Database | None = None,
    parser: OcrParser | None = None,
    gateway: object | None = None,
    force: bool = False,
    on_progress: ProgressCallback | None = None,
) -> LoadReport:
    """Index the policy and scanned corpora and ingest every workbook.

    Every dependency is injectable so a test can drive this without Ollama, Chroma,
    Postgres or OCR weights; supplying none builds the real ones from ``settings``.
    ``parser`` and ``gateway`` reach the scanned pass, which is the only one that reads
    pixels and the only one that can spend provider quota.
    """
    resolved = settings if settings is not None else get_settings()
    report = on_progress if on_progress is not None else lambda _message: None

    _refuse_a_shared_manifest(resolved)

    chunks = store if store is not None else ChunkStore(
        path=resolved.chroma_dir, collection_name=resolved.collection_name
    )
    vectors = embedder if embedder is not None else OllamaEmbedder()
    pool = database if database is not None else default_database(
        readonly=False, settings=resolved
    )

    report(f"policy documents from {resolved.policy_dir}")
    policy = index_corpus(
        resolved.policy_dir,
        chunks,
        vectors,
        manifest_path=resolved.policy_manifest_path,
        chunk_size=resolved.chunk_size,
        chunk_overlap=resolved.chunk_overlap,
        source_type="policy",
        force=force,
        on_progress=report,
    )

    report(f"scanned documents from {resolved.scanned_dir}")
    scanned = index_scanned_corpus(
        resolved.scanned_dir,
        chunks,
        vectors,
        manifest_path=resolved.scanned_manifest_path,
        settings=resolved,
        parser=parser,
        gateway=gateway,
        force=force,
        on_progress=report,
    )

    report(f"workbooks from {resolved.spreadsheet_dir}")
    tables: list[IngestedTable] = []
    workbooks = sorted(resolved.spreadsheet_dir.glob("*.xlsx"))
    for path in workbooks:
        ingested = ingest_workbook(pool, path)
        tables.extend(ingested)
        report(f"  {path.name}: {len(ingested)} tables")

    return LoadReport(
        policy=policy, scanned=scanned, tables=tuple(tables), workbooks=len(workbooks)
    )


def _refuse_a_shared_manifest(settings: Settings) -> None:
    """Two corpora, one collection, two manifests -- checked, not merely defaulted.

    The defaults differ, which is why this has never fired. That is exactly the reason
    to assert it: an environment override of one path to the other's value is a single
    line of configuration away, and the damage it does is silent.
    """
    policy = settings.policy_manifest_path.resolve()
    scanned = settings.scanned_manifest_path.resolve()
    if policy == scanned:
        raise ValueError(
            "policy_manifest_path and scanned_manifest_path are both "
            f"{policy}. They share a Chroma collection, so one manifest means the "
            "scanned pass deletes every policy chunk and reports success."
        )
