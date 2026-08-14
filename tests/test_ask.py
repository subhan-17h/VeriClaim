"""The CLI: what it prints, and when it refuses to call a run successful.

An unresolvable citation is a hard failure everywhere else in the system, and the demo
is the wrong place to soften it -- an answer that cites evidence which does not exist is
a fabrication with a footnote. These tests pin that to an exit code.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from vericlaim.evidence import Evidence, EvidenceSet, PolicyLocator, Provenance, SqlLocator
from vericlaim.orchestrator.state import GraphState

SCRIPT = Path(__file__).parents[1] / "scripts" / "ask.py"


def _load_script():
    """Import the script by path; scripts/ is deliberately not a package."""
    spec = importlib.util.spec_from_file_location("ask", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script():
    return _load_script()


class FakeLedger:
    total_cost_usd = 0.0
    calls: list[object] = []

    def by_task(self) -> dict[str, dict[str, float | int]]:
        return {
            "synthesize": {
                "calls": 1,
                "input_tokens": 10,
                "output_tokens": 20,
                "cost_usd": 0.0,
            }
        }


class FakeGateway:
    ledger = FakeLedger()


def _evidence() -> EvidenceSet:
    return EvidenceSet(
        [
            Evidence(
                source_type="policy",
                source_id="HomeSecure_Plus_2026.pdf",
                content="Sudden and accidental escape of water is covered.",
                locator=PolicyLocator(
                    document="HomeSecure_Plus_2026.pdf",
                    page=3,
                    section="4.2",
                    chunk_id="HomeSecure_Plus_2026.pdf:18",
                ),
                provenance=Provenance(tool="search_policy"),
                id="E1",
            ),
            Evidence(
                source_type="sql",
                source_id="ops.claims",
                content="398",
                locator=SqlLocator(
                    tables=("ops.claims",),
                    executed_sql="SELECT count(*) FROM ops.claims WHERE peril = 'water_damage'",
                    row_count=1,
                ),
                provenance=Provenance(tool="query_claims_db"),
                id="E2",
            ),
        ]
    )


def _state(answer: str, **citations: object) -> GraphState:
    return GraphState(
        question="Are burst pipes covered?",
        answer=answer,
        evidence=_evidence(),
        citations={"verified": True, "degraded": False, **citations},
    )


class TestWhatItPrints:
    def test_it_prints_every_resolved_citation_with_its_locator(
        self, script, capsys
    ) -> None:
        script._report(_state("Covered [E1]. There were 398 [E2]."), FakeGateway())
        printed = capsys.readouterr().out

        assert "[E1]" in printed and "[E2]" in printed
        assert "HomeSecure_Plus_2026.pdf" in printed
        assert "SELECT count(*) FROM ops.claims" in printed

    def test_it_names_a_source_that_was_never_consulted(self, script, capsys) -> None:
        """Silence from a source and absence of that source look identical otherwise."""
        script._report(_state("Covered [E1]."), FakeGateway())
        printed = capsys.readouterr().out

        assert "not consulted" in printed

    def test_it_reports_cost_from_the_gateway_not_the_state(self, script, capsys) -> None:
        """Tool-internal model calls are recorded on no graph stage, so the state's
        total under-reports a multi-source question by most of what it spent."""
        script._report(_state("Covered [E1]."), FakeGateway())
        printed = capsys.readouterr().out

        assert "model calls" in printed
        assert "synthesize" in printed

    def test_it_names_a_broken_citation_rather_than_dropping_it(
        self, script, capsys
    ) -> None:
        script._report(_state("Covered [E1] and also [E9]."), FakeGateway())
        printed = capsys.readouterr().out

        assert "Broken citations" in printed
        assert "E9" in printed


class TestTheExitCode:
    def test_a_fully_cited_answer_succeeds(self, script) -> None:
        assert script._exit_code(_state("Covered [E1]. There were 398 [E2].")) == 0

    def test_an_honest_refusal_succeeds(self, script) -> None:
        """Refusing is a correct outcome, not a failed run."""
        state = GraphState(
            question="What is the capital of France?",
            answer="That is outside what these sources cover.",
            citations={"verified": True, "degraded": False},
        )

        assert script._exit_code(state) == 0

    def test_an_unresolvable_citation_fails(self, script) -> None:
        assert script._exit_code(_state("Covered [E1] and [E9].")) == 1

    def test_a_malformed_marker_fails(self, script) -> None:
        assert script._exit_code(_state("Covered [E].")) == 1

    def test_a_degraded_verification_fails(self, script) -> None:
        assert script._exit_code(_state("Covered [E1].", degraded=True)) == 1

    def test_a_source_that_could_not_be_consulted_fails_the_run(self, script) -> None:
        """A well-cited answer over three sources when four were asked is incomplete,
        not successful. This gate is what phase acceptance is measured by, so it has to
        distinguish "the records say nothing" from "we never reached the records".
        """
        state = GraphState(
            question="Are burst pipes covered?",
            answer="Covered [E1]. There were 398 [E2].",
            evidence=_evidence(),
            citations={"verified": True, "degraded": False},
            collection={"failed_sources": ["spreadsheet"]},
        )
        assert script._exit_code(state) == 1

    def test_a_source_consulted_that_held_nothing_still_succeeds(self, script) -> None:
        # Bounds the check above: negative information is a real answer, and a corpus
        # that genuinely lacks something must not be reported as a broken run.
        state = GraphState(
            question="Are burst pipes covered?",
            answer="Covered [E1]. There were 398 [E2].",
            evidence=_evidence(),
            citations={"verified": True, "degraded": False},
            collection={"silent_sources": ["spreadsheet"]},
        )
        assert script._exit_code(state) == 0


class TestJsonOutput:
    def test_the_run_serialises(self, script) -> None:
        payload = json.loads(
            json.dumps(_state("Covered [E1].").to_dict(), default=str)
        )

        assert payload["answer"] == "Covered [E1]."
        assert payload["question"] == "Are burst pipes covered?"
