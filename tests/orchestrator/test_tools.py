"""The registry: four real tools over one store, one embedder and one pool.

Everything here runs offline. ``build_tools`` opens nothing -- the pool connects lazily
and the values catalogue profiles lazily -- so the wiring can be proved without
Postgres, Chroma content or Ollama. What cannot be proved offline is model behaviour:
that the router picks one source for a policy-only question, that the planner writes
usable sub-goals, that the synthesizer cites. Those belong to the live run.
"""

from __future__ import annotations

import inspect

import pytest

from vericlaim.config import Settings
from vericlaim.evidence import SOURCE_TYPES
from vericlaim.orchestrator.graph import SourceRequest
from vericlaim.orchestrator.sources import load_capabilities
from vericlaim.orchestrator.tools import (
    SourceTools,
    build_tools,
    claim_reference,
    open_tools,
)
from vericlaim.policy.store import ChunkStore


class StubDatabase:
    """A pool that records whether it was closed and never connects."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings().model_copy(update={"chroma_dir": tmp_path / "chroma"})


@pytest.fixture
def store(settings: Settings) -> ChunkStore:
    return ChunkStore(path=settings.chroma_dir, collection_name="test")


@pytest.fixture
def tools(settings: Settings, store: ChunkStore, embedder) -> SourceTools:
    return build_tools(
        settings=settings, store=store, embedder=embedder, database=StubDatabase()
    )


class TestTheRegistry:
    def test_its_keys_are_the_evidence_source_types(self, tools: SourceTools) -> None:
        assert set(tools.registry()) == set(SOURCE_TYPES)

    def test_its_keys_match_the_described_sources(self, tools: SourceTools) -> None:
        """A source described but not wired fails mid-run, after the models are paid."""
        assert set(tools.registry()) == set(load_capabilities())

    def test_every_tool_takes_one_source_request(self, tools: SourceTools) -> None:
        for name, tool in tools.registry().items():
            parameters = list(inspect.signature(tool, eval_str=True).parameters.values())
            assert len(parameters) == 1, name
            assert parameters[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD, name
            assert parameters[0].annotation is SourceRequest, name

    def test_two_registries_share_no_tool_objects(
        self, settings: Settings, store: ChunkStore, embedder
    ) -> None:
        first = build_tools(
            settings=settings, store=store, embedder=embedder, database=StubDatabase()
        )
        second = build_tools(
            settings=settings, store=store, embedder=embedder, database=StubDatabase()
        )

        assert first.policy is not second.policy
        assert first.claims is not second.claims


class TestSharedDependencies:
    """The whole point of the module: one of each, not four."""

    def test_both_searchers_share_one_store_and_one_embedder(
        self, tools: SourceTools
    ) -> None:
        assert tools.policy._store is tools.scanned._store is tools.store
        assert tools.policy._embedder is tools.scanned._embedder is tools.embedder

    def test_both_queriers_execute_against_one_database(self, tools: SourceTools) -> None:
        assert tools.claims.execute.database is tools.database
        assert tools.spreadsheets.execute.database is tools.database

    def test_the_two_queriers_keep_separate_contexts(self, tools: SourceTools) -> None:
        """One catalogue over both would let a claims question match a workbook value."""
        assert set(tools.claims.contexts) != set(tools.spreadsheets.contexts)
        assert tools.claims.catalog is not tools.spreadsheets.catalog

    def test_building_opens_no_connection(self, tools: SourceTools) -> None:
        assert not tools.database.closed


class TestLifecycle:
    """Close what you opened, and only that."""

    @pytest.fixture
    def opened_pool(self, monkeypatch) -> StubDatabase:
        """Make build_tools open its own pool, by giving it one to open."""
        stub = StubDatabase()
        monkeypatch.setattr(
            "vericlaim.orchestrator.tools.default_database",
            lambda **_kwargs: stub,
        )
        return stub

    def test_it_closes_the_pool_it_opened(
        self, settings, store, embedder, opened_pool
    ) -> None:
        tools = build_tools(settings=settings, store=store, embedder=embedder)
        assert tools.owns_database

        tools.close()

        assert opened_pool.closed

    def test_it_leaves_an_injected_pool_open(self, tools: SourceTools) -> None:
        """default_database hands out a process-wide pool; closing it from one
        caller's teardown would shut it under every other."""
        assert not tools.owns_database

        tools.close()

        assert not tools.database.closed

    def test_open_tools_releases_even_when_the_block_raises(
        self, settings, store, embedder, opened_pool
    ) -> None:
        with pytest.raises(RuntimeError):
            with open_tools(settings=settings, store=store, embedder=embedder):
                raise RuntimeError("the question failed")

        assert opened_pool.closed


class TestClaimScoping:
    """The scanned source is claim-keyed, so a sub-goal naming one matter scopes to it."""

    def test_a_sub_goal_naming_a_claim_is_scoped_to_it(self) -> None:
        assert claim_reference("What did the inspector record on CLM-1088?") == "CLM-1088"

    def test_the_reference_is_folded_to_the_form_documents_are_filed_under(self) -> None:
        assert claim_reference("the report for clm-1088") == "CLM-1088"

    def test_a_sub_goal_naming_no_claim_scopes_to_nothing(self) -> None:
        assert claim_reference("Which regions saw the most water damage?") is None

    def test_a_fold_that_no_longer_matches_the_pattern_is_discarded(self) -> None:
        """A pattern case-sensitive by design must not be satisfied by what it rejects."""
        settings = Settings().model_copy(update={"claim_id_pattern": r"clm-\d{3,}"})

        assert claim_reference("see CLM-1088", settings) is None

    def test_the_scanned_adapter_passes_the_claim_through(
        self, tools: SourceTools, monkeypatch
    ) -> None:
        seen: dict[str, object] = {}

        def record(query: str, **kwargs: object) -> list[object]:
            seen.update(kwargs, query=query)
            return []

        monkeypatch.setattr(tools.scanned, "search", record)
        tools.search_scanned(SourceRequest(goal="the inspection report on CLM-1207"))

        assert seen["claim_id"] == "CLM-1207"
        assert seen["query"] == "the inspection report on CLM-1207"


def test_the_sql_adapter_carries_understanding_to_entity_resolution(
    tools: SourceTools, monkeypatch
) -> None:
    goal = "Count the matching records."
    understanding = {"entities": ["water damage"]}
    seen: dict[str, object] = {}

    def record(candidate: object, catalog: object, *, scope: str | None = None) -> None:
        seen.update(candidate=candidate, catalog=catalog, scope=scope)
        raise RuntimeError("entity resolution reached")

    monkeypatch.setattr("vericlaim.sql.tool.resolve_entities", record)

    with pytest.raises(RuntimeError, match="entity resolution reached"):
        tools.query_claims(
            SourceRequest(goal=goal, understanding=understanding)
        )

    assert seen == {
        "candidate": understanding,
        "catalog": tools.claims.catalog,
        "scope": goal,
    }


def test_the_module_holds_no_mutable_state() -> None:
    """C-8.7's "no module-level globals", as a check rather than a claim."""
    import vericlaim.orchestrator.tools as module

    suspect = {
        name: value
        for name, value in vars(module).items()
        if not name.startswith("__")
        and not name.isupper()
        and isinstance(value, (dict, list, set))
    }

    assert suspect == {}
