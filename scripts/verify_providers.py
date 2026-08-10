"""Verify provider credentials with the smallest possible live calls.

    uv run python scripts/verify_providers.py              # Gemini only (free)
    uv run python scripts/verify_providers.py --paid       # also test OpenAI

Every prompt here is a handful of tokens and every reply is capped at a few more, so
the paid check costs a small fraction of a cent. The point is to prove the credentials,
the request shapes, the usage accounting, and the trace wiring all work -- before a real
question spends real money discovering otherwise.

Run `uv run python scripts/spend.py` afterwards to see exactly what it cost.
"""

from __future__ import annotations

import argparse
import os
import sys

from vericlaim.config import ModelSpec, get_model_routing, get_settings
from vericlaim.gateway.core import Gateway
from vericlaim.gateway.spend import default_spend
from vericlaim.gateway.types import GatewayError, Message
from vericlaim.tracing import is_tracing_enabled, traced

PROMPT = "Reply with exactly one word: ok"
# Generous on purpose. Gemini 3.x models reason before answering and those thoughts are
# billed against max_output_tokens -- a 16-token budget produced 128 thought tokens and
# an empty reply. The prompt keeps the actual answer to one word regardless, so this
# ceiling costs nothing in practice; it only stops thinking from starving the response.
MAX_TOKENS = 512

OK = "✓"
NO = "✗"


def _mask(value: str) -> str:
    return f"{value[:6]}...{value[-4:]} (len {len(value)})" if value else "not set"


def check_credentials() -> dict[str, bool]:
    print("=" * 66)
    print("CREDENTIALS")
    print("=" * 66)
    present = {}
    for name in ("GEMINI_API_KEY", "OPENAI_API_KEY", "LANGSMITH_API_KEY"):
        value = os.environ.get(name, "")
        present[name] = bool(value)
        mark = OK if value else NO
        print(f"  {mark} {name:<20} {_mask(value)}")
    print(f"  {OK if is_tracing_enabled() else NO} LangSmith tracing    "
          f"{'enabled' if is_tracing_enabled() else 'disabled'}")
    return present


@traced("provider_smoke_test", run_type="chain")
def call(gateway: Gateway, spec: ModelSpec, label: str) -> bool:
    """Make one minimal call and report what it cost."""
    try:
        completion = gateway.call_model(
            spec, [Message("user", PROMPT)], task="verify", temperature=0.0
        )
    except GatewayError as exc:
        print(f"  {NO} {label:<28} {type(exc).__name__}: {exc}")
        return False
    except Exception as exc:  # noqa: BLE001 - surfacing any surprise is the point
        print(f"  {NO} {label:<28} unexpected {type(exc).__name__}: {exc}")
        return False

    gateway._finish(completion)  # record to ledgers and annotate the trace
    reply = completion.text.strip().replace("\n", " ")[:24]
    print(
        f"  {OK} {label:<28} {completion.usage.input_tokens:>4} in / "
        f"{completion.usage.output_tokens:>4} out  "
        f"${completion.cost_usd:.8f}  {completion.latency_ms:>6.0f}ms  reply={reply!r}"
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--paid",
        action="store_true",
        help="also make one billed OpenAI call (a fraction of a cent)",
    )
    args = parser.parse_args()

    settings = get_settings()
    routing = get_model_routing()
    present = check_credentials()

    spend = default_spend()
    before = spend.total_usd
    print(f"\n  spend before: ${before:.6f} of ${settings.max_cost_usd_lifetime:.2f}")

    gateway = Gateway()
    results: dict[str, bool] = {}

    print()
    print("=" * 66)
    print("FREE PROVIDER (Gemini)")
    print("=" * 66)
    if not present["GEMINI_API_KEY"]:
        print(f"  {NO} skipped - GEMINI_API_KEY not set")
    else:
        for tier in ("cheap", "mid"):
            spec = routing.tiers[tier]
            small = ModelSpec(
                provider=spec.provider,
                model=spec.model,
                usd_per_1m_input=spec.usd_per_1m_input,
                usd_per_1m_output=spec.usd_per_1m_output,
                paid=spec.paid,
                rpm=spec.rpm,
                rpd=spec.rpd,
                timeout_s=30.0,
                max_output_tokens=MAX_TOKENS,
            )
            results[f"gemini:{tier}"] = call(gateway, small, f"{tier}  {spec.model}")

    print()
    print("=" * 66)
    print("PAID PROVIDER (OpenAI)")
    print("=" * 66)
    if not args.paid:
        print("  - skipped. Pass --paid to make one billed call.")
    elif not present["OPENAI_API_KEY"]:
        print(f"  {NO} skipped - OPENAI_API_KEY not set")
    else:
        paid_spec = ModelSpec(
            provider="openai",
            model="gpt-4o-mini",
            usd_per_1m_input=0.15,
            usd_per_1m_output=0.60,
            paid=True,
            timeout_s=30.0,
            max_output_tokens=MAX_TOKENS,
        )
        results["openai"] = call(gateway, paid_spec, "paid  gpt-4o-mini")

    print()
    print("=" * 66)
    print("SPEND")
    print("=" * 66)
    after = default_spend().total_usd
    print(f"  before : ${before:.8f}")
    print(f"  after  : ${after:.8f}")
    print(f"  this run cost ${after - before:.8f}")
    print(f"  remaining: ${max(0.0, settings.max_cost_usd_lifetime - after):.6f} "
          f"of ${settings.max_cost_usd_lifetime:.2f}")

    if is_tracing_enabled():
        print(f"\n  Traces sent to LangSmith project "
              f"{os.environ.get('LANGSMITH_PROJECT', 'default')!r}.")
        print("  View at https://smith.langchain.com")

    failed = [name for name, ok in results.items() if not ok]
    print()
    if not results:
        print("Nothing was tested.")
        return 1
    if failed:
        print(f"FAILED: {', '.join(failed)}")
        return 1
    print(f"All {len(results)} provider check(s) passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
