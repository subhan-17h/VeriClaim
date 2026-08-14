"""The four real source tools, over one store, one embedder and one pool.

``build_graph`` takes its tools injected and, until this module, only fakes had ever
been passed. What it needs is narrow -- one per-call source request in and a sequence of
evidence out, keyed by source name -- and these adapters give all four entry points that
shape.

The work is in sharing dependencies. Each source also has a settings-driven module
function (``search_policy``, ``query_claims_db``, and so on) that builds its own chunk
store and its own database connection. Registering those four would open four of
everything per question. So the classes are constructed here instead, over one set of
dependencies, and handed out as bound methods.

Nothing in this module is a module-level global. The tools live on an instance whose
lifetime the caller controls, which is the pattern C-5.5 already put in place once.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from vericlaim.config import Settings, get_settings
from vericlaim.evidence import SOURCE_TYPES, Evidence
from vericlaim.orchestrator.graph import SourceRequest, SourceTool
from vericlaim.policy.embeddings import Embedder, OllamaEmbedder
from vericlaim.policy.store import ChunkStore
from vericlaim.policy.tool import PolicySearcher
from vericlaim.scanned.tool import ScannedSearcher
from vericlaim.sheets.tool import SpreadsheetQuerier
from vericlaim.sql.contexts import ContextError, load_contexts
from vericlaim.sql.db import Database, default_database
from vericlaim.sql.executor import ExecutionResult
from vericlaim.sql.executor import execute as execute_sql
from vericlaim.sql.tool import ClaimsQuerier
from vericlaim.sql.values_catalog import database_catalog

__all__ = ("SourceTools", "build_tools", "claim_reference", "open_tools")


@dataclass(frozen=True, slots=True)
class _DatabaseExecutor:
    """Runs already-validated SQL against one pool.

    A class rather than a closure so a test can assert that both SQL-shaped sources
    execute against the same database, which is most of what this module is for.
    """

    database: Database

    def __call__(self, sql: str) -> ExecutionResult:
        return execute_sql(self.database, sql)


@dataclass(frozen=True, slots=True)
class SourceTools:
    """The four constructed tools and the dependencies they share."""

    policy: PolicySearcher
    scanned: ScannedSearcher
    claims: ClaimsQuerier
    spreadsheets: SpreadsheetQuerier
    settings: Settings
    store: ChunkStore
    embedder: Embedder
    database: Database
    #: Whether this object opened the pool, and may therefore close it.
    owns_database: bool = False

    def registry(self) -> dict[str, SourceTool]:
        """The mapping ``build_graph`` takes, keyed by evidence source type."""
        registry: dict[str, SourceTool] = {
            "policy": self.search_policy,
            "sql": self.query_claims,
            "spreadsheet": self.query_spreadsheets,
            "scanned_pdf": self.search_scanned,
        }
        missing = [name for name in SOURCE_TYPES if name not in registry]
        if missing:
            raise ValueError(
                f"No tool is registered for source type(s): {', '.join(missing)}. "
                "A routed source with no tool fails mid-run, after the planning and "
                "routing calls have already been paid for."
            )
        return registry

    # -- the adapters -----------------------------------------------------
    # ``tables`` stays unset deliberately: that selects every documented table, exactly
    # the set each catalogue was built over.

    def search_policy(self, request: SourceRequest) -> Sequence[Evidence]:
        return self.policy.search(request.goal, trace_id=request.trace_id)

    def search_scanned(self, request: SourceRequest) -> Sequence[Evidence]:
        return self.scanned.search(
            request.goal,
            claim_id=claim_reference(request.goal, self.settings),
            trace_id=request.trace_id,
        )

    def query_claims(self, request: SourceRequest) -> Sequence[Evidence]:
        return self.claims.query(
            request.goal,
            understanding=request.understanding,
            trace_id=request.trace_id,
        )

    def query_spreadsheets(self, request: SourceRequest) -> Sequence[Evidence]:
        # The shared grounding machinery now runs against the workbook catalogue too. A
        # mention that is clear in claims data may be ambiguous there; refusing that source
        # as an evidence gap is the resolver working as designed.
        return self.spreadsheets.query(
            request.goal,
            understanding=request.understanding,
            trace_id=request.trace_id,
        )

    # -- lifecycle --------------------------------------------------------

    def close(self) -> None:
        """Release what this object opened. Idempotent.

        The pool is the only thing with a release. ``ChunkStore`` wraps a Chroma
        persistent client, which exposes no shutdown; claiming to close it here would
        be a lie inside a ``finally``. An injected pool is left alone, because
        ``default_database`` hands out a process-wide one and closing it from a single
        caller's teardown would shut it under everyone else.
        """
        if self.owns_database:
            self.database.close()


def build_tools(
    *,
    settings: Settings | None = None,
    store: ChunkStore | None = None,
    embedder: Embedder | None = None,
    database: Database | None = None,
    gateway: Any | None = None,
) -> SourceTools:
    """Construct all four tools over one set of dependencies.

    Opens nothing: the pool connects lazily and the values catalogue profiles lazily,
    so this is callable with no database running, which is what lets the wiring be
    tested offline.
    """
    resolved = settings if settings is not None else get_settings()
    chunks = store if store is not None else ChunkStore(
        path=resolved.chroma_dir, collection_name=resolved.collection_name
    )
    # OllamaEmbedder directly, never get_embedder(): that is lru_cached, which is
    # process-wide state surviving between callers and tests -- the thing this module
    # exists to avoid. The embedder is a client object; the sharing that matters is
    # one instance across both searchers, which the registry provides.
    vectors = embedder if embedder is not None else OllamaEmbedder()
    owns_database = database is None
    pool = database if database is not None else default_database(
        readonly=True, settings=resolved
    )
    execute = _DatabaseExecutor(pool)

    return SourceTools(
        policy=PolicySearcher(chunks, vectors, settings=resolved),
        scanned=ScannedSearcher(chunks, vectors, settings=resolved),
        claims=_querier(ClaimsQuerier, resolved.sql_context_dir, pool, execute, resolved, gateway),
        spreadsheets=_querier(
            SpreadsheetQuerier, resolved.sheets_context_dir, pool, execute, resolved, gateway
        ),
        settings=resolved,
        store=chunks,
        embedder=vectors,
        database=pool,
        owns_database=owns_database,
    )


@contextmanager
def open_tools(**kwargs: Any) -> Iterator[SourceTools]:
    """Build the tools and release them afterwards, however the block exits."""
    tools = build_tools(**kwargs)
    try:
        yield tools
    finally:
        tools.close()


def claim_reference(text: str, settings: Settings | None = None) -> str | None:
    """Return the claim a sub-goal names, in the form documents are filed under.

    The scanned source is claim-keyed, so a sub-goal naming one matter should search
    that matter rather than the whole corpus. The planner is instructed to write each
    sub-goal self-contained, carrying the restrictions its source needs, so a
    claim-scoped question names the claim in the goal.

    Note this deliberately does not reuse the indexer's ``extract_claim_id``, whose
    contract is that a claim reference comes from the filed path and never from
    recognised text -- because a misread character there would attach evidence to the
    wrong matter. That argument is about OCR output. A sub-goal derives from the user's
    own question, so the risk is different: the worst case here is a scope not applied
    and a search that is merely wider, never one narrowed onto the wrong claim.
    """
    resolved = settings if settings is not None else get_settings()
    match = re.search(resolved.claim_id_pattern, text, re.IGNORECASE)
    if match is None:
        return None
    # Fold to the filed form, then require that the fold still matches. A pattern that
    # is case-sensitive by design must not be satisfied by a string it would reject.
    filed = match.group(0).upper()
    return filed if re.fullmatch(resolved.claim_id_pattern, filed) else None


def _querier(
    kind: type[ClaimsQuerier],
    context_dir: Any,
    pool: Database,
    execute: _DatabaseExecutor,
    settings: Settings,
    gateway: Any,
) -> Any:
    """Build one SQL-shaped querier over its own contexts and its own catalogue.

    Two catalogues, one database, deliberately. Merging the SQL and spreadsheet
    contexts into a single catalogue would let the entity resolver match a mention in a
    claims question against a value that only appears in a workbook column.
    """
    try:
        contexts: Mapping[str, Any] = load_contexts(context_dir)
    except ContextError as exc:
        raise ContextError(f"{context_dir} does not describe any source: {exc}") from exc
    return kind(
        contexts=contexts,
        catalog=database_catalog(pool, contexts).select(sorted(contexts)),
        execute=execute,
        settings=settings,
        gateway=gateway,
    )
