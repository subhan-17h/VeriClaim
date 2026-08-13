#!/usr/bin/env python3
"""Ask one question several times and record how the answers differed.

    uv run python scripts/replay.py "Is gradual water leakage covered?" -n 5

The same question has returned a verified answer on one run and a degraded one on the
next, and nothing was known about why. This script exists to replace that impression
with a measurement: it puts one question through the graph N times and writes one
NDJSON record per run holding what each run routed to, what evidence came back from
each source, what the verifier decided and what it objected to, which model wrote each
stage, and what the run cost.

A script rather than a test, deliberately. Every run spends real quota against a
per-day allowance, so this cannot live anywhere ``pytest`` might reach it.

Nothing here interprets the results. Four causes could produce the same symptom -- a
source returning nothing on one run, a filter on a value the database does not hold, a
fallback putting a different model on the answer, or ordinary sampling variance -- and
they are distinguished by different fields of the same record. Reading the records is
the analysis; guessing from a summary line is what this replaces.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from vericlaim.citations import resolve_citations
from vericlaim.config import get_settings
from vericlaim.gateway.core import Gateway
from vericlaim.gateway.types import (
    BudgetExceededError,
    Completion,
    PaidFallbackBlockedError,
    QuotaExhaustedError,
)
from vericlaim.orchestrator.graph import build_graph, run_question
from vericlaim.orchestrator.sources import load_capabilities
from vericlaim.orchestrator.state import GraphState
from vericlaim.orchestrator.tools import open_tools
from vericlaim.sql.contexts import ContextError
from vericlaim.sql.db import DatabaseUnavailableError

# The three terminal gateway errors. A run that hits one of these has not produced a
# variant answer, it has run out of allowance, and continuing the loop would only burn
# the remainder of the day's budget discovering the same thing four more times.
TERMINAL_ERRORS = (BudgetExceededError, PaidFallbackBlockedError, QuotaExhaustedError)


def _record(
    run: int,
    state: GraphState,
    calls: Sequence[Completion],
    wall_ms: float,
) -> dict[str, Any]:
    """One run, in the fields that would distinguish one outcome from another.

    ``calls`` is this run's slice of the shared ledger, not the whole of it. Source
    tools make their own model calls -- the SQL planner, generator, refiner and
    arbiter among them -- and none of those are recorded on a graph stage, so the
    state's own cost total omits most of what a multi-source question spends.
    """
    report = resolve_citations(state.answer, state.evidence)
    by_source = state.evidence.by_source()
    citations = state.citations

    return {
        "run": run,
        "wall_ms": round(wall_ms, 1),
        # -- what the question was taken to be, and where it was sent
        "routed": list(state.routing.sources) if state.routing else [],
        "out_of_scope": bool(state.routing.out_of_scope) if state.routing else False,
        "answerable": bool(state.plans.get("answerable")),
        "sub_goals": {
            source: (plan or {}).get("goal", "")
            for source, plan in (state.plans.get("sub_goals") or {}).items()
        },
        # -- what came back. A source routed but returning nothing is hypothesis (a),
        #    and it is only visible as a zero beside a non-empty sub-goal.
        "evidence_by_source": {
            source: len(items) for source, items in sorted(by_source.items())
        },
        "evidence_total": len(state.evidence.items),
        # -- what the verifier decided, and what it objected to
        "verified": bool(citations.get("verified")),
        "degraded": bool(citations.get("degraded")),
        "regenerated": bool(citations.get("regenerated")),
        "problems": list(citations.get("problems") or []),
        "resolved": len(report.resolved),
        "unresolved": list(report.unresolved),
        "malformed": list(report.malformed),
        "uncited": list(report.uncited),
        "replans": state.replans,
        # -- which model wrote each stage, and whether the ladder was walked.
        #    Hypothesis (c) shows up here and nowhere else.
        "stage_models": {stage.name: stage.model for stage in state.stages if stage.model},
        "failures": list(state.failures),
        "fallbacks": [
            f"{event.from_model} -> {event.to_model} ({event.reason})"
            for call in calls
            for event in call.fallbacks
        ],
        "calls": len(calls),
        "cost_usd": round(sum(call.cost_usd for call in calls), 6),
        "answer": state.answer,
    }


def _summarise(records: Sequence[dict[str, Any]]) -> None:
    """Print what varied across the runs, and nothing that did not.

    A field identical on every run is not evidence about the variance and printing it
    would bury the two or three fields that are.
    """
    print(f"\n{len(records)} runs")
    for record in records:
        outcome = (
            "degraded" if record["degraded"]
            else "verified" if record["verified"]
            else "unverified"
        )
        counts = " ".join(
            f"{source}={count}" for source, count in record["evidence_by_source"].items()
        )
        print(
            f"  run {record['run']}: {outcome:<11} "
            f"{record['resolved']} cited / {len(record['uncited'])} uncited   "
            f"{counts or 'no evidence'}   "
            f"{record['calls']} calls  ${record['cost_usd']:.6f}"
        )
        for problem in record["problems"]:
            print(f"      ! {problem}")
        for failure in record["failures"]:
            print(f"      x {failure}")

    varying = [
        field
        for field in ("routed", "evidence_by_source", "stage_models", "verified", "degraded")
        if len({json.dumps(r[field], sort_keys=True) for r in records}) > 1
    ]
    print(f"\n  varied across runs: {', '.join(varying) if varying else 'nothing'}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one question N times and record how the outcomes differed."
    )
    parser.add_argument("question")
    parser.add_argument("-n", "--runs", type=int, default=5)
    parser.add_argument(
        "-o",
        "--out",
        type=Path,
        default=None,
        help="NDJSON output path (default: data/replay/<epoch>.ndjson)",
    )
    parser.add_argument("--recursion-limit", type=int, default=40)
    args = parser.parse_args()

    if args.runs < 1:
        print("FAILED: --runs must be at least 1")
        return 1

    settings = get_settings()
    out = args.out or settings.data_dir / "replay" / f"{int(time.time())}.ndjson"
    out.parent.mkdir(parents=True, exist_ok=True)

    # One gateway across every run, so its ledger is a single sequence that each run
    # takes a slice of. A fresh gateway per run would be tidier to read and would also
    # rebuild the tools, and the tools hold the database pool.
    gateway = Gateway(settings=settings)
    records: list[dict[str, Any]] = []

    try:
        with open_tools(settings=settings, gateway=gateway) as tools:
            if tools.store.count() == 0:
                print("FAILED: the document index is empty. Run scripts/load_corpus.py first.")
                return 2
            registry = tools.registry()
            capabilities = load_capabilities()
            if set(registry) != set(capabilities):
                print(
                    f"FAILED: the registered tools {sorted(registry)} do not match the "
                    f"described sources {sorted(capabilities)}"
                )
                return 1

            print(f"Question\n  {args.question}\n\nWriting to {out}")
            graph = build_graph(
                tools=registry, capabilities=capabilities, gateway=gateway
            )

            with out.open("w", encoding="utf-8") as handle:
                for run in range(1, args.runs + 1):
                    mark = len(gateway.ledger.calls)
                    started = time.perf_counter()
                    print(f"  run {run}/{args.runs} ...", flush=True)
                    try:
                        state = run_question(
                            graph,
                            args.question,
                            config={"recursion_limit": args.recursion_limit},
                        )
                    except TERMINAL_ERRORS as exc:
                        # Out of allowance, not a variant outcome. Keep what has been
                        # measured so far rather than losing it to the exception.
                        print(f"  stopped after {run - 1} runs: {exc}")
                        break
                    except Exception as exc:  # noqa: BLE001 - a crash IS an outcome
                        record = {
                            "run": run,
                            "crashed": f"{type(exc).__name__}: {exc}",
                            "traceback": traceback.format_exc(),
                            "calls": len(gateway.ledger.calls) - mark,
                        }
                        handle.write(json.dumps(record) + "\n")
                        handle.flush()
                        records.append(record)
                        print(f"      x crashed: {type(exc).__name__}: {exc}")
                        continue

                    record = _record(
                        run,
                        state,
                        gateway.ledger.calls[mark:],
                        (time.perf_counter() - started) * 1000,
                    )
                    handle.write(json.dumps(record) + "\n")
                    handle.flush()
                    records.append(record)
    except DatabaseUnavailableError as exc:
        print(f"FAILED: {exc}")
        print("Is Postgres up? docker compose up -d")
        return 2
    except ContextError as exc:
        print(f"FAILED: {exc}")
        return 1

    complete = [record for record in records if "crashed" not in record]
    if complete:
        _summarise(complete)
    print(f"\n  total: ${gateway.ledger.total_cost_usd:.6f} over "
          f"{len(gateway.ledger.calls)} model calls")
    return 0 if records else 2


if __name__ == "__main__":
    sys.exit(main())
