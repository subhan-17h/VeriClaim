"""Free-tier rate limiter.

The limiter exists to stop us generating 429s. A 429 is transient, so the gateway
retries it and then walks the fallback ladder -- toward a paid provider. Every test
here is ultimately about that chain never starting.

Time is injected throughout, so nothing sleeps for real and the day-rollover case is
directly testable rather than inferred.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vericlaim.config import ModelSpec, Settings
from vericlaim.gateway.quota import RateLimiter, pacific_date
from vericlaim.gateway.types import QuotaExhaustedError

FLASH = ModelSpec(provider="gemini", model="gemini-2.5-flash", paid=False, rpm=10, rpd=250)
# The two limits are deliberately isolated into separate fixtures. A model capped on
# both would trip the minute limit while a daily-limit test was still setting up,
# making failures ambiguous about which rule fired.
DAILY = ModelSpec(provider="gemini", model="daily-capped", paid=False, rpd=3)
MINUTE = ModelSpec(provider="gemini", model="minute-capped", paid=False, rpm=2)
UNCAPPED = ModelSpec(provider="local", model="ollama", paid=False)


class FakeClock:
    """A monotonic clock that only advances when a test says so."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept += seconds
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _limiter(tmp_path: Path, clock: FakeClock, *, day: str = "2026-08-11", **kw):
    settings = Settings(**kw)
    return RateLimiter(
        settings,
        state_path=tmp_path / "quota.json",
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        today=lambda: day,
    )


class TestDailyLimit:
    def test_allows_up_to_the_daily_cap(self, tmp_path):
        limiter = _limiter(tmp_path, FakeClock())
        for _ in range(DAILY.rpd):
            limiter.acquire(DAILY)
        assert limiter.used_today(DAILY) == 3

    def test_refuses_beyond_the_cap(self, tmp_path):
        limiter = _limiter(tmp_path, FakeClock())
        for _ in range(DAILY.rpd):
            limiter.acquire(DAILY)
        with pytest.raises(QuotaExhaustedError, match="daily allowance"):
            limiter.acquire(DAILY)

    def test_daily_refusal_does_not_sleep(self, tmp_path):
        # The quota resets at midnight Pacific; no retry budget outlasts that, so
        # waiting would be pure latency for a guaranteed failure.
        clock = FakeClock()
        limiter = _limiter(tmp_path, clock)
        for _ in range(DAILY.rpd):
            limiter.acquire(DAILY)
        with pytest.raises(QuotaExhaustedError):
            limiter.acquire(DAILY)
        assert clock.slept == 0.0

    def test_error_names_the_reset_boundary(self, tmp_path):
        limiter = _limiter(tmp_path, FakeClock())
        for _ in range(DAILY.rpd):
            limiter.acquire(DAILY)
        with pytest.raises(QuotaExhaustedError) as info:
            limiter.acquire(DAILY)
        assert "US/Pacific" in str(info.value)
        assert info.value.resets_at

    def test_remaining_is_reported(self, tmp_path):
        limiter = _limiter(tmp_path, FakeClock())
        assert limiter.remaining_today(DAILY) == 3
        limiter.acquire(DAILY)
        assert limiter.remaining_today(DAILY) == 2

    def test_models_are_counted_independently(self, tmp_path):
        limiter = _limiter(tmp_path, FakeClock())
        for _ in range(DAILY.rpd):
            limiter.acquire(DAILY)
        limiter.acquire(FLASH)  # a different model still has allowance
        assert limiter.used_today(FLASH) == 1

    def test_uncapped_models_are_never_refused(self, tmp_path):
        limiter = _limiter(tmp_path, FakeClock())
        for _ in range(50):
            limiter.acquire(UNCAPPED)
        assert limiter.remaining_today(UNCAPPED) is None


class TestPersistence:
    def test_counters_survive_a_restart(self, tmp_path):
        # A restart must not hand us an allowance the provider will not honour.
        first = _limiter(tmp_path, FakeClock())
        first.acquire(DAILY)
        first.acquire(DAILY)

        second = _limiter(tmp_path, FakeClock())
        assert second.used_today(DAILY) == 2

        second.acquire(DAILY)
        with pytest.raises(QuotaExhaustedError):
            second.acquire(DAILY)

    def test_state_is_written_atomically_and_readably(self, tmp_path):
        limiter = _limiter(tmp_path, FakeClock())
        limiter.acquire(FLASH)
        payload = json.loads((tmp_path / "quota.json").read_text())
        assert payload["gemini/gemini-2.5-flash"] == {
            "date": "2026-08-11",
            "count": 1,
        }

    def test_corrupt_state_file_is_discarded_not_fatal(self, tmp_path):
        (tmp_path / "quota.json").write_text("{ not json")
        limiter = _limiter(tmp_path, FakeClock())
        limiter.acquire(DAILY)  # must not raise
        assert limiter.used_today(DAILY) == 1

    def test_malformed_entries_are_dropped(self, tmp_path):
        # A wrong-typed entry under a real model label must be discarded rather than
        # trusted, and must not poison the well-formed entry beside it.
        (tmp_path / "quota.json").write_text(
            json.dumps(
                {
                    DAILY.label: {"date": 20260811, "count": "many"},
                    FLASH.label: {"date": "2026-08-11", "count": 7},
                }
            )
        )
        limiter = _limiter(tmp_path, FakeClock())

        assert limiter.used_today(DAILY) == 0  # malformed -> dropped
        assert limiter.used_today(FLASH) == 7  # well-formed -> kept


class TestDayRollover:
    def test_a_new_pacific_day_restores_the_allowance(self, tmp_path):
        clock = FakeClock()
        day_one = _limiter(tmp_path, clock, day="2026-08-11")
        for _ in range(DAILY.rpd):
            day_one.acquire(DAILY)
        with pytest.raises(QuotaExhaustedError):
            day_one.acquire(DAILY)

        day_two = _limiter(tmp_path, clock, day="2026-08-12")
        assert day_two.used_today(DAILY) == 0
        day_two.acquire(DAILY)  # must not raise

    def test_pacific_date_is_used_not_local_date(self):
        # Keying on local date would reset at the wrong moment outside US/Pacific.
        from datetime import datetime
        from zoneinfo import ZoneInfo

        # 09:00 in Karachi on the 12th is still the 11th in Los Angeles.
        karachi_morning = datetime(2026, 8, 12, 9, 0, tzinfo=ZoneInfo("Asia/Karachi"))
        assert pacific_date(karachi_morning) == "2026-08-11"


class TestMinuteLimit:
    def test_allows_up_to_the_per_minute_cap_without_waiting(self, tmp_path):
        clock = FakeClock()
        limiter = _limiter(tmp_path, clock)
        for _ in range(MINUTE.rpm):
            limiter.acquire(MINUTE)
        assert clock.slept == 0.0

    def test_waits_for_a_slot_rather_than_failing(self, tmp_path):
        # Per-minute overrun is a pacing problem, so waiting is the right response.
        clock = FakeClock()
        limiter = _limiter(tmp_path, clock, max_rate_limit_wait_s=120.0)
        limiter.acquire(MINUTE)
        limiter.acquire(MINUTE)

        limiter.acquire(MINUTE)  # third call in the same minute must wait

        assert clock.slept > 0
        assert clock.slept <= 60.0

    def test_window_slides_so_old_requests_stop_counting(self, tmp_path):
        clock = FakeClock()
        limiter = _limiter(tmp_path, clock)
        limiter.acquire(MINUTE)
        limiter.acquire(MINUTE)

        clock.advance(61.0)  # the first two age out of the 60s window
        limiter.acquire(MINUTE)

        assert clock.slept == 0.0

    def test_gives_up_when_a_slot_will_not_free_in_time(self, tmp_path):
        clock = FakeClock()
        limiter = _limiter(tmp_path, clock, max_rate_limit_wait_s=1.0)
        limiter.acquire(MINUTE)
        limiter.acquire(MINUTE)

        with pytest.raises(QuotaExhaustedError, match="per-minute limit"):
            limiter.acquire(MINUTE)

    def test_uncapped_models_never_wait(self, tmp_path):
        clock = FakeClock()
        limiter = _limiter(tmp_path, clock)
        for _ in range(100):
            limiter.acquire(UNCAPPED)
        assert clock.slept == 0.0


class TestEnforcementToggle:
    def test_disabling_enforcement_bypasses_both_limits(self, tmp_path):
        clock = FakeClock()
        limiter = _limiter(tmp_path, clock, enforce_rate_limits=False)
        for _ in range(100):
            limiter.acquire(DAILY)
        assert clock.slept == 0.0
        assert limiter.used_today(DAILY) == 0


class TestGatewayIntegration:
    def test_gateway_throttles_before_calling_the_provider(self, routing, alpha, tmp_path):
        from vericlaim.gateway.core import Gateway

        clock = FakeClock()
        limiter = _limiter(tmp_path, clock)
        capped = ModelSpec(provider="alpha", model="alpha-main", paid=False, rpd=2)
        routing.tiers["strong"] = capped

        gateway = Gateway(routing=routing, limiter=limiter)
        gateway.complete("synthesize", "q")
        gateway.complete("synthesize", "q")

        assert limiter.used_today(capped) == 2

    def test_exhausted_model_falls_back_rather_than_failing(
        self, routing, alpha, beta, tmp_path
    ):
        # QuotaExhaustedError is a ProviderError, so the ladder treats the model as
        # done and moves on -- which is the whole point of throttling early.
        from vericlaim.gateway.core import Gateway

        limiter = _limiter(tmp_path, FakeClock())
        routing.tiers["strong"] = ModelSpec(
            provider="alpha", model="alpha-main", paid=False, rpd=1
        )
        gateway = Gateway(routing=routing, limiter=limiter)

        gateway.complete("synthesize", "q")  # consumes the single daily request
        result = gateway.complete("synthesize", "q")

        assert result.model == "beta-backup"
        assert result.used_fallback is True

    def test_retries_count_against_quota(self, routing, alpha, tmp_path):
        # Every attempt is a real request against the provider's allowance.
        from vericlaim.gateway.core import Gateway
        from vericlaim.gateway.types import TransientProviderError

        limiter = _limiter(tmp_path, FakeClock())
        capped = ModelSpec(provider="alpha", model="alpha-main", paid=False, rpd=100)
        routing.tiers["strong"] = capped
        alpha.script = {
            "alpha-main": [
                TransientProviderError("429", provider="alpha", model="alpha-main"),
                "recovered",
            ]
        }

        Gateway(routing=routing, limiter=limiter).complete("synthesize", "q")

        assert limiter.used_today(capped) == 2
