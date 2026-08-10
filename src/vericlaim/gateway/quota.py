"""Client-side free-tier throttling.

The provider will happily tell us we have exceeded its free tier -- with HTTP 429,
which is indistinguishable from an ordinary transient rate limit. The gateway then
retries, exhausts the rung, and walks the fallback ladder toward a billed provider.
Self-throttling *before* the request is what breaks that chain: if we never exceed the
published limits, we never generate the 429s that would push us toward spending.

Two limits with deliberately different behaviour:

* **Requests per minute** is a pacing problem. Overrunning it means waiting a few
  seconds, so we wait (bounded by ``max_rate_limit_wait_s``).
* **Requests per day** is not. The quota resets at midnight US/Pacific and no retry
  budget outlasts that, so we raise :class:`QuotaExhaustedError` immediately and let
  the ladder try a model with allowance left.

The daily counter is persisted, because a process restart must not hand us a fresh
allowance the provider will not honour. It is keyed by model *and* Pacific date, since
that is the boundary Google actually resets on -- keying by local date would reset at
the wrong moment for anyone outside that timezone.
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from vericlaim.config import ModelSpec, Settings, get_settings
from vericlaim.gateway.types import QuotaExhaustedError

# Google's free-tier quotas reset at midnight in this zone.
QUOTA_TIMEZONE = ZoneInfo("America/Los_Angeles")

_WINDOW_SECONDS = 60.0


def pacific_date(now: datetime | None = None) -> str:
    """Return today's date in the provider's quota timezone as ``YYYY-MM-DD``."""
    moment = now or datetime.now(QUOTA_TIMEZONE)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=QUOTA_TIMEZONE)
    return moment.astimezone(QUOTA_TIMEZONE).strftime("%Y-%m-%d")


def _load_state(path: Path) -> dict[str, dict[str, Any]]:
    """Read persisted daily counters, discarding anything malformed.

    A corrupt counter file must not stop the system: the worst case of starting from
    zero is that we trust the provider's own 429 as the backstop, which is exactly
    where we were before this module existed.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    clean: dict[str, dict[str, Any]] = {}
    for model, entry in raw.items():
        if (
            isinstance(model, str)
            and isinstance(entry, dict)
            and isinstance(entry.get("date"), str)
            and isinstance(entry.get("count"), int)
        ):
            clean[model] = {"date": entry["date"], "count": entry["count"]}
    return clean


def _save_state(path: Path, state: dict[str, dict[str, Any]]) -> None:
    """Write counters atomically so a crash mid-write cannot corrupt them."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, delete=False, encoding="utf-8"
    ) as handle:
        json.dump(state, handle, separators=(",", ":"), sort_keys=True)
        temporary = Path(handle.name)
    temporary.replace(path)


class RateLimiter:
    """Enforces per-model RPM and RPD before a request leaves the process."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        state_path: Path | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        today: Callable[[], str] = pacific_date,
    ) -> None:
        self._settings = settings or get_settings()
        self._state_path = state_path or self._settings.quota_state_path
        self._monotonic = monotonic
        self._sleep = sleep
        self._today = today
        self._recent: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._daily = _load_state(self._state_path)

    # ------------------------------------------------------------------ inspection

    def used_today(self, spec: ModelSpec) -> int:
        """Return how many requests this model has already spent today."""
        entry = self._daily.get(spec.label)
        if entry is None or entry["date"] != self._today():
            return 0
        return int(entry["count"])

    def remaining_today(self, spec: ModelSpec) -> int | None:
        """Return the model's remaining daily allowance, or None if uncapped."""
        if spec.rpd is None:
            return None
        return max(0, spec.rpd - self.used_today(spec))

    # -------------------------------------------------------------------- acquire

    def acquire(self, spec: ModelSpec) -> None:
        """Block until this model may be called, or raise if it may not today."""
        if not self._settings.enforce_rate_limits:
            return
        with self._lock:
            self._check_daily(spec)
            self._await_minute_slot(spec)
            self._record(spec)

    def _check_daily(self, spec: ModelSpec) -> None:
        if spec.rpd is None:
            return
        today = self._today()
        entry = self._daily.get(spec.label)
        if entry is None or entry["date"] != today:
            # First call of a new Pacific day: the provider has reset, so we do too.
            self._daily[spec.label] = {"date": today, "count": 0}
            return
        if int(entry["count"]) >= spec.rpd:
            raise QuotaExhaustedError(
                f"{spec.label} has used its free-tier daily allowance "
                f"({entry['count']}/{spec.rpd} requests on {today} US/Pacific). "
                "It resets at midnight US/Pacific.",
                provider=spec.provider,
                model=spec.model,
                resets_at=f"{today} 24:00 US/Pacific",
            )

    def _await_minute_slot(self, spec: ModelSpec) -> None:
        if spec.rpm is None:
            return
        deadline = self._monotonic() + self._settings.max_rate_limit_wait_s
        while True:
            window = self._recent[spec.label]
            cutoff = self._monotonic() - _WINDOW_SECONDS
            while window and window[0] <= cutoff:
                window.popleft()
            if len(window) < spec.rpm:
                return
            # Wait only until the oldest request ages out of the window.
            wait = max(0.0, window[0] + _WINDOW_SECONDS - self._monotonic())
            if self._monotonic() + wait > deadline:
                raise QuotaExhaustedError(
                    f"{spec.label} is at its per-minute limit ({spec.rpm} RPM) and a "
                    f"slot would not free within {self._settings.max_rate_limit_wait_s:g}s.",
                    provider=spec.provider,
                    model=spec.model,
                )
            self._sleep(max(wait, 0.01))

    def _record(self, spec: ModelSpec) -> None:
        if spec.rpm is not None:
            self._recent[spec.label].append(self._monotonic())
        if spec.rpd is None:
            return
        today = self._today()
        entry = self._daily.setdefault(spec.label, {"date": today, "count": 0})
        if entry["date"] != today:
            entry.update({"date": today, "count": 0})
        entry["count"] = int(entry["count"]) + 1
        _save_state(self._state_path, self._daily)

    # ----------------------------------------------------------------- maintenance

    def reset(self) -> None:
        """Clear all counters, in memory and on disk. For tests."""
        self._recent.clear()
        self._daily = {}
        _save_state(self._state_path, self._daily)


_DEFAULT: RateLimiter | None = None


def default_limiter() -> RateLimiter:
    """Return the process-wide limiter, created on first use."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = RateLimiter()
    return _DEFAULT


def reset_default_limiter() -> None:
    """Drop the process-wide limiter so the next call rebuilds it."""
    global _DEFAULT
    _DEFAULT = None


__all__ = [
    "QUOTA_TIMEZONE",
    "RateLimiter",
    "default_limiter",
    "pacific_date",
    "reset_default_limiter",
]
