"""Report or reset the persisted API spend.

    uv run python scripts/spend.py            # show what has been spent
    uv run python scripts/spend.py --reset    # clear the running total

The total survives process restarts, so it is the figure that actually bounds a fixed
prepaid credit. Run this before and after anything that makes live calls.
"""

from __future__ import annotations

import argparse

from vericlaim.config import get_settings
from vericlaim.gateway.spend import default_spend


def main() -> int:
    parser = argparse.ArgumentParser(description="Show or reset persisted API spend.")
    parser.add_argument(
        "--reset", action="store_true", help="clear the running total and exit"
    )
    args = parser.parse_args()

    settings = get_settings()
    spend = default_spend()

    if args.reset:
        previous = spend.total_usd
        spend.reset()
        print(f"Spend record cleared (was ${previous:.6f}).")
        return 0

    summary = spend.summary()
    ceiling = settings.max_cost_usd_lifetime

    print("VeriClaim API spend")
    print("=" * 58)
    print(f"  lifetime total : ${summary.total_usd:.6f}")
    print(f"  ceiling        : ${ceiling:.2f}")
    print(f"  remaining      : ${summary.remaining(ceiling):.6f}")
    print(f"  calls recorded : {summary.calls}")
    if summary.first_recorded:
        print(f"  first call     : {summary.first_recorded}")
        print(f"  last call      : {summary.last_recorded}")

    if summary.by_model:
        print("\n  by model")
        width = max(len(name) for name in summary.by_model)
        for name, row in sorted(
            summary.by_model.items(), key=lambda kv: -float(kv[1]["usd"])
        ):
            print(
                f"    {name:<{width}}  {int(row['calls']):>4} calls  "
                f"{int(row['tokens']):>8} tok  ${float(row['usd']):.6f}"
            )
    else:
        print("\n  nothing recorded yet")

    print(f"\n  state file: {settings.spend_state_path}")
    if summary.total_usd >= ceiling:
        print("\n  CEILING REACHED - further paid calls will raise BudgetExceededError.")
        print("  Raise VC_MAX_COST_USD_LIFETIME or run with --reset to continue.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
