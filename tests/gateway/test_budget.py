"""Spend guard and paid-provider policy.

These tests exist because of a specific failure mode: Gemini reports free-tier
exhaustion as HTTP 429, which providers.py correctly classifies as transient. Without
a paid marker the ladder would retry, fall through to OpenAI, and start billing --
with no signal that anything had changed. Every test below is about making that
impossible.
"""

from __future__ import annotations

import pytest

from vericlaim.config import ModelRouting, ModelSpec, Settings
from vericlaim.gateway.core import Gateway, reset_session_spend
from vericlaim.gateway.types import (
    BudgetExceededError,
    PaidFallbackBlockedError,
    PermanentProviderError,
    TransientProviderError,
    UsageLedger,
)

FREE_PRIMARY = ModelSpec(
    provider="alpha",
    model="alpha-free",
    usd_per_1m_input=0.0,
    usd_per_1m_output=0.0,
    paid=False,
)
FREE_BACKUP = ModelSpec(
    provider="alpha",
    model="alpha-free-lite",
    usd_per_1m_input=0.0,
    usd_per_1m_output=0.0,
    paid=False,
)
PAID_LAST = ModelSpec(
    provider="beta",
    model="beta-billed",
    usd_per_1m_input=2.0,
    usd_per_1m_output=10.0,
    paid=True,
)


@pytest.fixture(autouse=True)
def clean_session():
    reset_session_spend()
    yield
    reset_session_spend()


@pytest.fixture
def free_first_routing() -> ModelRouting:
    """Free primary, free backup, paid last rung -- the shipped shape."""
    return ModelRouting(
        tiers={"strong": FREE_PRIMARY},
        tasks={"synthesize": "strong"},
        fallbacks={"strong": (FREE_BACKUP, PAID_LAST)},
        transient_retries=0,
        transient_backoff_s=0.0,
    )


@pytest.fixture
def paid_only_routing() -> ModelRouting:
    """Every rung is billed. Nothing may run with paid fallback off."""
    return ModelRouting(
        tiers={"strong": PAID_LAST},
        tasks={"synthesize": "strong"},
        fallbacks={},
        transient_retries=0,
        transient_backoff_s=0.0,
    )


def _rate_limited() -> TransientProviderError:
    """What Gemini raises when the free tier is spent."""
    return TransientProviderError(
        "429 RESOURCE_EXHAUSTED", provider="alpha", model="alpha-free"
    )


class TestPaidFallbackBlocked:
    def test_free_ladder_is_used_and_paid_rung_never_reached(
        self, free_first_routing, alpha, beta
    ):
        alpha.script = {"alpha-free": [_rate_limited()], "alpha-free-lite": ["ok"]}
        gateway = Gateway(
            routing=free_first_routing, settings=Settings(allow_paid_fallback=False)
        )

        result = gateway.complete("synthesize", "q")

        assert result.model == "alpha-free-lite"
        assert result.cost_usd == 0.0
        assert beta.calls == []  # the billed provider was never contacted

    def test_free_tier_exhaustion_refuses_rather_than_spending(
        self, free_first_routing, alpha, beta
    ):
        # The headline case: every free model is rate limited and the only thing left
        # is billed. Correct behaviour is to stop, not to pay.
        alpha.script = {
            "alpha-free": [_rate_limited()],
            "alpha-free-lite": [_rate_limited()],
        }
        gateway = Gateway(
            routing=free_first_routing, settings=Settings(allow_paid_fallback=False)
        )

        with pytest.raises(PaidFallbackBlockedError) as info:
            gateway.complete("synthesize", "q")

        assert beta.calls == []
        assert gateway.ledger.total_cost_usd == 0.0
        assert "beta/beta-billed" in info.value.blocked

    def test_error_names_the_flag_that_would_permit_it(
        self, free_first_routing, alpha, beta
    ):
        alpha.script = {
            "alpha-free": [_rate_limited()],
            "alpha-free-lite": [_rate_limited()],
        }
        gateway = Gateway(
            routing=free_first_routing, settings=Settings(allow_paid_fallback=False)
        )
        with pytest.raises(PaidFallbackBlockedError, match="VC_ALLOW_PAID_FALLBACK"):
            gateway.complete("synthesize", "q")

    def test_all_paid_ladder_refuses_without_calling_anything(
        self, paid_only_routing, beta
    ):
        gateway = Gateway(
            routing=paid_only_routing, settings=Settings(allow_paid_fallback=False)
        )
        with pytest.raises(PaidFallbackBlockedError):
            gateway.complete("synthesize", "q")
        assert beta.calls == []

    def test_enabling_the_flag_takes_the_paid_hop(
        self, free_first_routing, alpha, beta
    ):
        # Fallback stays real and demonstrable; it is gated, not removed.
        alpha.script = {
            "alpha-free": [_rate_limited()],
            "alpha-free-lite": [_rate_limited()],
        }
        beta.script = {"beta-billed": ["paid answer"]}
        gateway = Gateway(
            routing=free_first_routing, settings=Settings(allow_paid_fallback=True)
        )

        result = gateway.complete("synthesize", "q")

        assert result.provider == "beta"
        assert result.text == "paid answer"
        assert result.cost_usd > 0
        assert len(result.fallbacks) == 2

    def test_unmarked_config_entry_is_treated_as_paid(self):
        # A config entry that forgets `paid:` must fail closed. The cost of guessing
        # wrong in the safe direction is a refused call; guessing wrong the other way
        # is an unexpected bill.
        assert ModelSpec(provider="x", model="y").paid is True

    def test_blocked_rungs_are_reported_even_when_free_ones_errored(
        self, free_first_routing, alpha, beta
    ):
        # A hard failure on the free rungs plus a withheld paid rung must not be
        # reported as "all providers failed" -- a working option was declined on
        # policy, which is a different problem with a different fix.
        alpha.script = {
            "alpha-free": [PermanentProviderError("bad", provider="alpha", model="alpha-free")],
            "alpha-free-lite": [
                PermanentProviderError("bad", provider="alpha", model="alpha-free-lite")
            ],
        }
        gateway = Gateway(
            routing=free_first_routing, settings=Settings(allow_paid_fallback=False)
        )
        with pytest.raises(PaidFallbackBlockedError):
            gateway.complete("synthesize", "q")


class TestSpendCeiling:
    def _priced_routing(self) -> ModelRouting:
        # $1.00 per 1M tokens against the fixture's 1000-in / 500-out usage means
        # exactly $0.0015 per call, so the ceilings below trip on a known call number.
        spec = ModelSpec(
            provider="alpha",
            model="alpha-main",
            usd_per_1m_input=1.0,
            usd_per_1m_output=1.0,
            paid=False,
        )
        return ModelRouting(
            tiers={"strong": spec},
            tasks={"synthesize": "strong"},
            fallbacks={},
            transient_retries=0,
            transient_backoff_s=0.0,
        )

    def test_per_request_ceiling_stops_a_runaway_loop(self, alpha):
        # $0.0015 per call, so a $0.004 ceiling admits exactly 3 and refuses the 4th.
        settings = Settings(
            max_cost_usd_per_request=0.004, max_cost_usd_total=1000.0
        )
        gateway = Gateway(routing=self._priced_routing(), settings=settings)

        for _ in range(3):
            gateway.complete("synthesize", "q")

        with pytest.raises(BudgetExceededError, match="Per-request"):
            gateway.complete("synthesize", "q")

    def test_spend_never_exceeds_the_ceiling(self, alpha):
        settings = Settings(
            max_cost_usd_per_request=0.004, max_cost_usd_total=1000.0
        )
        gateway = Gateway(routing=self._priced_routing(), settings=settings)

        with pytest.raises(BudgetExceededError):
            for _ in range(50):
                gateway.complete("synthesize", "q")

        # Checked before the call, so the recorded spend is bounded by the ceiling
        # plus at most the one call that crossed it.
        assert gateway.ledger.total_cost_usd < 0.004 + 0.0015 + 1e-9

    def test_session_ceiling_spans_separate_requests(self, alpha):
        # Each Gateway is one request; the session cap has to bound the project.
        settings = Settings(
            max_cost_usd_per_request=1000.0, max_cost_usd_total=0.004
        )
        routing = self._priced_routing()

        made = 0
        with pytest.raises(BudgetExceededError, match="Session"):
            for _ in range(20):
                Gateway(routing=routing, settings=settings).complete("synthesize", "q")
                made += 1

        assert made == 3  # 3 x $0.0015 = $0.0045 > $0.004

    def test_ceilings_can_be_disabled_with_zero(self, alpha):
        settings = Settings(max_cost_usd_per_request=0.0, max_cost_usd_total=0.0)
        gateway = Gateway(routing=self._priced_routing(), settings=settings)
        for _ in range(10):
            gateway.complete("synthesize", "q")
        assert len(gateway.ledger.calls) == 10

    def test_an_injected_ledger_still_respects_the_session_cap(self, alpha):
        settings = Settings(
            max_cost_usd_per_request=1000.0, max_cost_usd_total=0.004
        )
        routing = self._priced_routing()
        shared = UsageLedger()

        with pytest.raises(BudgetExceededError, match="Session"):
            for _ in range(20):
                Gateway(
                    routing=routing, settings=settings, ledger=UsageLedger()
                ).complete("synthesize", "q")
        assert shared.calls == []  # unrelated ledger untouched


class TestShippedConfigIsFree:
    """The committed routing table must cost nothing under normal operation."""

    def test_every_tier_is_a_free_provider(self):
        from vericlaim.config import DEFAULT_ROUTING_PATH, load_model_routing

        routing = load_model_routing(DEFAULT_ROUTING_PATH)
        for name, spec in routing.tiers.items():
            assert spec.paid is False, f"tier {name} routes to a billed model"
            assert spec.provider == "gemini", f"tier {name} is not on the free provider"

    def test_paid_rungs_exist_but_only_as_last_resort(self):
        from vericlaim.config import DEFAULT_ROUTING_PATH, load_model_routing

        routing = load_model_routing(DEFAULT_ROUTING_PATH)
        for tier, chain in routing.fallbacks.items():
            paid_positions = [i for i, s in enumerate(chain) if s.paid]
            free_positions = [i for i, s in enumerate(chain) if not s.paid]
            for paid_at in paid_positions:
                assert all(free_at < paid_at for free_at in free_positions), (
                    f"tier {tier}: a billed rung precedes a free one"
                )

    def test_free_tiers_declare_their_rate_limits(self):
        # The limiter cannot self-throttle a model whose limits it does not know.
        from vericlaim.config import DEFAULT_ROUTING_PATH, load_model_routing

        routing = load_model_routing(DEFAULT_ROUTING_PATH)
        for name, spec in routing.tiers.items():
            assert spec.rpm and spec.rpd, f"tier {name} declares no rpm/rpd"

    def test_default_settings_block_paid_use(self):
        assert Settings().allow_paid_fallback is False
        assert Settings().max_cost_usd_total == 5.00
        assert Settings().max_cost_usd_per_request == 0.25
