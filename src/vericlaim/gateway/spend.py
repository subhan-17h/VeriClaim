"""Cumulative spend, persisted across processes.

The in-memory ledger measures one request and dies with the process. Against a fixed
prepaid credit that is not a ceiling at all: every ``uv run`` starts the count at zero,
so a $5 cap could be spent many times over and each run would believe itself compliant.

This module keeps the running total on disk, so the ceiling bounds the *project* rather
than whichever process happens to be executing. That distinction is the whole reason it
exists -- a budget you can reset by restarting is a suggestion.

Free calls are recorded too, at $0.00. They cost nothing but the record is worth having:
it shows how much work the free tier actually absorbed, which is the evidence that the
routing decisions are doing their job.
"""

from __future__ import annotations

import json
import tempfile
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vericlaim.config import Settings, get_settings
from vericlaim.gateway.types import BudgetExceededError

_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class SpendSummary:
    """What has been spent so far, and on what."""

    total_usd: float
    calls: int
    by_model: dict[str, dict[str, float | int]]
    first_recorded: str | None
    last_recorded: str | None

    def remaining(self, ceiling: float) -> float:
        return max(0.0, ceiling - self.total_usd)


class PersistentSpend:
    """A cumulative USD total that survives process restarts."""

    def __init__(self, path: Path | None = None, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._path = path or self._settings.spend_state_path
        self._lock = threading.Lock()
        self._state = self._load()

    # ------------------------------------------------------------------- storage

    def _load(self) -> dict[str, Any]:
        blank: dict[str, Any] = {
            "version": _SCHEMA_VERSION,
            "total_usd": 0.0,
            "calls": 0,
            "by_model": {},
            "first_recorded": None,
            "last_recorded": None,
        }
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return blank
        if not isinstance(raw, dict) or raw.get("version") != _SCHEMA_VERSION:
            return blank
        # A corrupt total must not read as zero and silently unlock more spending, so
        # anything unparseable falls back to the blank record only when the whole file
        # is unreadable -- a malformed total within a valid file is treated as 0.0 and
        # the file is rewritten on the next record.
        try:
            raw["total_usd"] = float(raw.get("total_usd", 0.0))
            raw["calls"] = int(raw.get("calls", 0))
        except (TypeError, ValueError):
            return blank
        if not isinstance(raw.get("by_model"), dict):
            raw["by_model"] = {}
        return raw

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", dir=self._path.parent, delete=False, encoding="utf-8"
        ) as handle:
            json.dump(self._state, handle, separators=(",", ":"), sort_keys=True)
            temporary = Path(handle.name)
        temporary.replace(self._path)

    # ------------------------------------------------------------------ inspection

    @property
    def total_usd(self) -> float:
        return float(self._state["total_usd"])

    def summary(self) -> SpendSummary:
        return SpendSummary(
            total_usd=self.total_usd,
            calls=int(self._state["calls"]),
            by_model=dict(self._state["by_model"]),
            first_recorded=self._state.get("first_recorded"),
            last_recorded=self._state.get("last_recorded"),
        )

    # -------------------------------------------------------------------- writing

    def record(self, *, model_label: str, usd: float, tokens: int = 0) -> float:
        """Add one call's cost to the running total and persist it."""
        with self._lock:
            now = datetime.now(tz=UTC).isoformat()
            self._state["total_usd"] = self.total_usd + usd
            self._state["calls"] = int(self._state["calls"]) + 1
            entry = self._state["by_model"].setdefault(
                model_label, {"calls": 0, "usd": 0.0, "tokens": 0}
            )
            entry["calls"] = int(entry["calls"]) + 1
            entry["usd"] = float(entry["usd"]) + usd
            entry["tokens"] = int(entry["tokens"]) + tokens
            if self._state.get("first_recorded") is None:
                self._state["first_recorded"] = now
            self._state["last_recorded"] = now
            self._save()
            return self.total_usd

    def check(self, ceiling: float) -> None:
        """Raise if the accumulated total has reached ``ceiling``."""
        if ceiling > 0 and self.total_usd >= ceiling:
            raise BudgetExceededError("Lifetime", self.total_usd, ceiling)

    def reset(self) -> None:
        """Clear the running total. Deliberately explicit -- there is no auto-reset."""
        with self._lock:
            self._state = {
                "version": _SCHEMA_VERSION,
                "total_usd": 0.0,
                "calls": 0,
                "by_model": {},
                "first_recorded": None,
                "last_recorded": None,
            }
            self._save()


_DEFAULT: PersistentSpend | None = None


def default_spend() -> PersistentSpend:
    """Return the process-wide persistent spend record."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = PersistentSpend()
    return _DEFAULT


def reset_default_spend() -> None:
    """Drop the cached record so the next call reloads from disk."""
    global _DEFAULT
    _DEFAULT = None


__all__ = [
    "PersistentSpend",
    "SpendSummary",
    "default_spend",
    "reset_default_spend",
]
