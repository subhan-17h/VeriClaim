"""Persistent spend ledger.

The property under test is survival. An in-memory ceiling resets every time a process
starts, so against a fixed prepaid credit it bounds nothing -- each run believes itself
compliant while the card drains. These tests pin that the total persists and that the
ceiling is enforced against the persisted figure.
"""

from __future__ import annotations

import json

import pytest

from vericlaim.config import ModelRouting, ModelSpec, Settings
from vericlaim.gateway.core import Gateway, reset_session_spend
from vericlaim.gateway.spend import PersistentSpend
from vericlaim.gateway.types import BudgetExceededError

PRICED = ModelSpec(
    provider="alpha",
    model="alpha-main",
    usd_per_1m_input=1.0,
    usd_per_1m_output=1.0,
    paid=False,
)


@pytest.fixture(autouse=True)
def clean_session():
    reset_session_spend()
    yield
    reset_session_spend()


@pytest.fixture
def routing_priced() -> ModelRouting:
    return ModelRouting(
        tiers={"strong": PRICED},
        tasks={"synthesize": "strong"},
        fallbacks={},
        transient_retries=0,
        transient_backoff_s=0.0,
    )


def _spend(tmp_path) -> PersistentSpend:
    return PersistentSpend(path=tmp_path / "spend.json", settings=Settings())


class TestRecording:
    def test_starts_at_zero(self, tmp_path):
        assert _spend(tmp_path).total_usd == 0.0

    def test_accumulates(self, tmp_path):
        spend = _spend(tmp_path)
        spend.record(model_label="openai/gpt-4o-mini", usd=0.01, tokens=100)
        spend.record(model_label="openai/gpt-4o-mini", usd=0.02, tokens=200)
        assert spend.total_usd == pytest.approx(0.03)

    def test_breaks_down_by_model(self, tmp_path):
        spend = _spend(tmp_path)
        spend.record(model_label="openai/gpt-4o-mini", usd=0.01, tokens=100)
        spend.record(model_label="gemini/flash", usd=0.0, tokens=500)

        summary = spend.summary()
        assert summary.calls == 2
        assert summary.by_model["openai/gpt-4o-mini"]["usd"] == pytest.approx(0.01)
        # Free calls are recorded at $0.00 -- the record shows how much work the free
        # tier absorbed, which is the evidence the routing is doing its job.
        assert summary.by_model["gemini/flash"]["tokens"] == 500
        assert summary.by_model["gemini/flash"]["usd"] == 0.0

    def test_timestamps_are_recorded(self, tmp_path):
        spend = _spend(tmp_path)
        assert spend.summary().first_recorded is None
        spend.record(model_label="m", usd=0.0)
        assert spend.summary().first_recorded is not None
        assert spend.summary().last_recorded is not None


class TestPersistence:
    def test_total_survives_a_restart(self, tmp_path):
        # The whole point: a fresh process must not start the count at zero.
        first = _spend(tmp_path)
        first.record(model_label="openai/gpt-4o-mini", usd=0.30, tokens=1000)

        second = _spend(tmp_path)
        assert second.total_usd == pytest.approx(0.30)

    def test_written_atomically_and_readably(self, tmp_path):
        spend = _spend(tmp_path)
        spend.record(model_label="openai/gpt-4o-mini", usd=0.25, tokens=10)
        payload = json.loads((tmp_path / "spend.json").read_text())
        assert payload["total_usd"] == pytest.approx(0.25)
        assert payload["calls"] == 1

    def test_corrupt_file_does_not_crash(self, tmp_path):
        (tmp_path / "spend.json").write_text("{ not json")
        spend = _spend(tmp_path)
        spend.record(model_label="m", usd=0.01)
        assert spend.total_usd == pytest.approx(0.01)

    def test_unknown_schema_version_is_discarded(self, tmp_path):
        (tmp_path / "spend.json").write_text(json.dumps({"version": 99, "total_usd": 5.0}))
        assert _spend(tmp_path).total_usd == 0.0

    def test_reset_clears_the_total(self, tmp_path):
        spend = _spend(tmp_path)
        spend.record(model_label="m", usd=0.4)
        spend.reset()
        assert spend.total_usd == 0.0
        assert _spend(tmp_path).total_usd == 0.0  # persisted too


class TestCeiling:
    def test_under_the_ceiling_passes(self, tmp_path):
        spend = _spend(tmp_path)
        spend.record(model_label="m", usd=0.1)
        spend.check(0.5)  # must not raise

    def test_at_the_ceiling_raises(self, tmp_path):
        spend = _spend(tmp_path)
        spend.record(model_label="m", usd=0.5)
        with pytest.raises(BudgetExceededError, match="Lifetime"):
            spend.check(0.5)

    def test_zero_ceiling_disables_the_check(self, tmp_path):
        spend = _spend(tmp_path)
        spend.record(model_label="m", usd=99.0)
        spend.check(0.0)  # must not raise

    def test_remaining_is_reported(self, tmp_path):
        spend = _spend(tmp_path)
        spend.record(model_label="m", usd=0.2)
        assert spend.summary().remaining(0.5) == pytest.approx(0.3)

    def test_remaining_never_goes_negative(self, tmp_path):
        spend = _spend(tmp_path)
        spend.record(model_label="m", usd=0.9)
        assert spend.summary().remaining(0.5) == 0.0


class TestGatewayIntegration:
    def test_gateway_records_to_the_persistent_ledger(self, routing_priced, alpha, tmp_path):
        spend = _spend(tmp_path)
        gateway = Gateway(routing=routing_priced, spend=spend)

        gateway.complete("synthesize", "q")

        assert spend.total_usd == pytest.approx(0.0015)
        assert spend.summary().calls == 1

    def test_ceiling_survives_a_new_gateway(self, routing_priced, alpha, tmp_path):
        # A new Gateway is a new request; the lifetime cap must still hold.
        settings = Settings(
            max_cost_usd_lifetime=0.004,
            max_cost_usd_total=1000.0,
            max_cost_usd_per_request=1000.0,
        )
        made = 0
        with pytest.raises(BudgetExceededError, match="Lifetime"):
            for _ in range(20):
                Gateway(
                    routing=routing_priced,
                    settings=settings,
                    spend=_spend(tmp_path),
                ).complete("synthesize", "q")
                made += 1
                reset_session_spend()

        assert made == 3  # 3 x $0.0015 = $0.0045 > $0.004

    def test_ceiling_is_checked_before_the_call(self, routing_priced, alpha, tmp_path):
        settings = Settings(
            max_cost_usd_lifetime=0.001,
            max_cost_usd_total=1000.0,
            max_cost_usd_per_request=1000.0,
        )
        spend = _spend(tmp_path)
        spend.record(model_label="prior", usd=0.001)

        with pytest.raises(BudgetExceededError):
            Gateway(routing=routing_priced, settings=settings, spend=spend).complete(
                "synthesize", "q"
            )
        assert alpha.calls == []  # nothing reached the provider


class TestShippedCeilingsFitTheCredit:
    """The committed defaults must be safe against a $1.00 prepaid credit."""

    def test_lifetime_ceiling_is_well_under_a_dollar(self):
        assert Settings().max_cost_usd_lifetime <= 0.50

    def test_session_ceiling_is_below_the_lifetime_ceiling(self):
        # A single runaway process must not be able to consume the whole allowance.
        settings = Settings()
        assert settings.max_cost_usd_total < settings.max_cost_usd_lifetime

    def test_per_request_ceiling_is_below_the_session_ceiling(self):
        settings = Settings()
        assert settings.max_cost_usd_per_request < settings.max_cost_usd_total
