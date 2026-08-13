#!/usr/bin/env python3
"""Ask VeriClaim one question and print the answer with its citations.

    docker compose up -d
    uv run python scripts/generate_corpus.py --seed 42
    uv run python scripts/load_corpus.py
    uv run python scripts/ask.py "Are burst pipes covered under HomeSecure?"

Every material claim in the answer carries an ``[En]`` marker, and every marker is
resolved here against the evidence the run actually collected -- through the same
function the verify node uses, so the CLI cannot disagree with the run about which
citations hold. A marker that resolves to nothing exits non-zero: an unresolvable
citation is a fabrication with a footnote, not a formatting problem.

The cost line reads the gateway's own ledger rather than the state's total. Source
tools make their own model calls -- the SQL planner, generator and refiner among them --
and those are not recorded on any graph stage, so the state under-reports a
multi-source question by most of what it spent.
"""

from __future__ import annotations

import argparse
import json
import sys

from vericlaim.citations import resolve_citations
from vericlaim.config import get_settings
from vericlaim.evidence import SOURCE_LABELS
from vericlaim.gateway.core import Gateway
from vericlaim.gateway.spend import default_spend
from vericlaim.gateway.types import (
    BudgetExceededError,
    PaidFallbackBlockedError,
    QuotaExhaustedError,
)
from vericlaim.orchestrator.graph import build_graph, run_question
from vericlaim.orchestrator.sources import load_capabilities
from vericlaim.orchestrator.state import GraphState
from vericlaim.orchestrator.tools import open_tools
from vericlaim.sql.contexts import ContextError
from vericlaim.sql.db import DatabaseUnavailableError


def _report(state: GraphState, gateway: Gateway) -> None:
    settings = get_settings()
    report = resolve_citations(state.answer, state.evidence)

    print("\nAnswer")
    print(f"  {state.answer or '(no answer was produced)'}")

    if report.resolved:
        print("\nCitations")
        for evidence_id in report.resolved:
            item = state.evidence.get(evidence_id)
            if item is None:  # pragma: no cover - resolved means it is present
                continue
            print(f"  [{item.id}] {item.label} — {item.cite()}")
            for line in _locator_detail(item, settings):
                print(f"       {line}")

    if report.unresolved or report.malformed:
        print("\nBroken citations")
        for evidence_id in report.unresolved:
            print(f"  [{evidence_id}] cites evidence that does not exist")
        for marker in report.malformed:
            print(f"  {marker} is not a well-formed citation")

    print("\nSources consulted")
    by_source = state.evidence.by_source()
    cited: set[str] = set()
    for evidence_id in report.resolved:
        item = state.evidence.get(evidence_id)
        if item is not None:
            cited.add(item.source_type)
    for source, label in SOURCE_LABELS.items():
        items = by_source.get(source, ())
        if not items:
            mark = "not consulted" if source not in state.sources_used else "no evidence found"
        else:
            mark = "cited" if source in cited else "retrieved but not cited"
        print(f"  {label:<24} {len(items):>3} retrieved, {mark}")
    if state.failures:
        print("\nFailures")
        for failure in state.failures:
            print(f"  {failure}")

    spend = default_spend().summary()
    print("\nRun")
    print(f"  verified   : {'yes' if state.citations.get('verified') else 'no'}", end="")
    print(f"  (degraded: {'yes' if state.citations.get('degraded') else 'no'})")
    print(
        f"  citations  : {len(report.resolved)} resolved, {len(report.unresolved)} "
        f"unresolved, {len(report.malformed)} malformed, {len(report.uncited)} uncited"
    )
    print(f"  replans    : {state.replans}   stages: {len(state.stages)}")
    print(f"  latency    : {state.total_latency_ms / 1000:.1f} s")
    print(f"  cost       : ${gateway.ledger.total_cost_usd:.6f} over "
          f"{len(gateway.ledger.calls)} model calls")
    for task, entry in sorted(gateway.ledger.by_task().items()):
        print(f"               {task}: {entry['calls']} calls, {entry['output_tokens']} out")
    print(f"  lifetime   : ${spend.total_usd:.6f} of ${settings.max_cost_usd_lifetime:.2f}")


def _locator_detail(item: object, settings: object) -> list[str]:
    """The part of a locator that ``cite()`` does not carry, per source."""
    locator = item.locator.to_dict()  # type: ignore[attr-defined]
    lines: list[str] = []
    if sql := locator.get("executed_sql"):
        lines.append(" ".join(sql.split())[:200])
    if chunk := locator.get("chunk_id"):
        lines.append(f"chunk {chunk}")
    if a1 := locator.get("a1_range"):
        lines.append(f"cells {a1}")
    confidence = locator.get("ocr_confidence")
    if confidence is not None:
        floor = settings.ocr_confidence_floor  # type: ignore[attr-defined]
        note = "below the confidence floor — qualified, not asserted" if confidence < floor else ""
        escalated = " (re-read by the vision tier)" if locator.get("escalated") else ""
        lines.append(f"OCR {confidence:.2f}{escalated} {note}".rstrip())
    return lines


def _exit_code(state: GraphState) -> int:
    """Non-zero when the answer cannot stand on its evidence."""
    report = resolve_citations(state.answer, state.evidence)
    if not report.ok:
        return 1
    if state.citations.get("degraded"):
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask VeriClaim one question.")
    parser.add_argument("question")
    parser.add_argument("--json", action="store_true", help="print the whole run as JSON")
    parser.add_argument("--recursion-limit", type=int, default=40)
    args = parser.parse_args()

    settings = get_settings()
    gateway = Gateway(settings=settings)

    try:
        with open_tools(settings=settings, gateway=gateway) as tools:
            if tools.store.count() == 0:
                print("FAILED: the document index is empty. Run scripts/load_corpus.py first.")
                print("An empty index is not evidence that the policies are silent.")
                return 2
            registry = tools.registry()
            capabilities = load_capabilities()
            if set(registry) != set(capabilities):
                print(
                    f"FAILED: the registered tools {sorted(registry)} do not match the "
                    f"described sources {sorted(capabilities)}"
                )
                return 1
            print(f"Question\n  {args.question}")
            state = run_question(
                build_graph(tools=registry, capabilities=capabilities, gateway=gateway),
                args.question,
                config={"recursion_limit": args.recursion_limit},
            )
    except DatabaseUnavailableError as exc:
        print(f"FAILED: {exc}")
        print("Is Postgres up? docker compose up -d")
        return 2
    except (BudgetExceededError, PaidFallbackBlockedError, QuotaExhaustedError) as exc:
        # Infrastructure, not a bad question. The free tiers throttle per minute as well
        # as per day, so asking several questions back to back is enough to hit this.
        print(f"FAILED: {exc}")
        return 2
    except ContextError as exc:
        print(f"FAILED: {exc}")
        return 1

    if args.json:
        print(json.dumps(state.to_dict(), indent=2, default=str))
    else:
        _report(state, gateway)
    return _exit_code(state)


if __name__ == "__main__":
    sys.exit(main())
