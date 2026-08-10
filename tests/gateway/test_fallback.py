"""Fallback ladder tests.

The routing fixture puts alpha-main (provider alpha) on the strong tier with a
two-hop ladder: beta-backup (provider beta), then alpha-small (provider alpha).
That shape lets us assert both that a hop happens and that it genuinely crosses
providers rather than merely retrying the same one under a different model name.
"""

from __future__ import annotations

import pytest

from vericlaim.gateway.core import Gateway
from vericlaim.gateway.fallback import build_ladder, walk_ladder
from vericlaim.gateway.types import (
    AllProvidersFailedError,
    Message,
    PermanentProviderError,
    ProviderUnavailableError,
    TransientProviderError,
)


def _permanent(model: str = "alpha-main", provider: str = "alpha"):
    return PermanentProviderError("upstream rejected", provider=provider, model=model)


def _transient(model: str = "alpha-main", provider: str = "alpha"):
    return TransientProviderError("timed out", provider=provider, model=model)


class TestLadderConstruction:
    def test_primary_comes_first_then_fallbacks(self, routing, alpha):
        ladder = build_ladder(Gateway(routing=routing), "synthesize")
        assert [spec.model for spec in ladder] == [
            "alpha-main",
            "beta-backup",
            "alpha-small",
        ]

    def test_tier_without_fallbacks_has_a_single_rung(self, routing, alpha):
        ladder = build_ladder(Gateway(routing=routing), "route")
        assert [spec.model for spec in ladder] == ["alpha-small"]


class TestHappyPath:
    def test_no_fallback_events_when_the_primary_succeeds(self, routing, alpha, beta):
        result = Gateway(routing=routing).complete("synthesize", "q")
        assert result.model == "alpha-main"
        assert result.fallbacks == ()
        assert result.used_fallback is False
        assert beta.calls == []  # the backup provider was never touched


class TestFallback:
    def test_permanent_failure_falls_back_to_the_next_provider(
        self, routing, alpha, beta
    ):
        alpha.script = {"alpha-main": [_permanent()]}
        beta.script = {"beta-backup": ["answer from backup"]}

        result = Gateway(routing=routing).complete("synthesize", "q")

        assert result.text == "answer from backup"
        assert result.provider == "beta"
        assert result.model == "beta-backup"

    def test_the_hop_crosses_providers(self, routing, alpha, beta):
        # The point of the ladder: a provider outage must not take the request down.
        alpha.script = {"alpha-main": [_permanent()]}
        beta.script = {"beta-backup": ["ok"]}

        result = Gateway(routing=routing).complete("synthesize", "q")

        event = result.fallbacks[0]
        assert event.from_provider == "alpha"
        assert event.to_provider == "beta"
        assert event.from_provider != event.to_provider

    def test_fallback_event_records_the_reason(self, routing, alpha, beta):
        alpha.script = {"alpha-main": [_permanent()]}
        beta.script = {"beta-backup": ["ok"]}

        result = Gateway(routing=routing).complete("synthesize", "q")

        assert "PermanentProviderError" in result.fallbacks[0].reason
        assert "upstream rejected" in result.fallbacks[0].reason

    def test_exhausted_transient_retries_also_trigger_fallback(
        self, routing, alpha, beta
    ):
        # call_model retries transients; once it gives up, the ladder takes over.
        alpha.script = {"alpha-main": [_transient()]}
        beta.script = {"beta-backup": ["ok"]}

        result = Gateway(routing=routing).complete("synthesize", "q")

        assert result.provider == "beta"
        # 1 initial + 2 retries against the primary before moving on
        assert len(alpha.calls) == 3

    def test_ladder_walks_multiple_hops(self, routing, alpha, beta):
        alpha.script = {
            "alpha-main": [_permanent()],
            "alpha-small": ["third time lucky"],
        }
        beta.script = {"beta-backup": [_permanent("beta-backup", "beta")]}

        result = Gateway(routing=routing).complete("synthesize", "q")

        assert result.text == "third time lucky"
        assert result.model == "alpha-small"
        assert len(result.fallbacks) == 2
        assert [e.to_model for e in result.fallbacks] == [
            "beta-backup",
            "alpha-small",
        ]

    def test_missing_api_key_is_treated_as_a_failure_worth_falling_back_from(
        self, routing, alpha, beta
    ):
        # An unconfigured provider must not take the request down when another works.
        alpha.script = {
            "alpha-main": [
                ProviderUnavailableError(
                    "ALPHA_API_KEY is not set", provider="alpha", model="alpha-main"
                )
            ]
        }
        beta.script = {"beta-backup": ["ok"]}

        result = Gateway(routing=routing).complete("synthesize", "q")
        assert result.provider == "beta"

    def test_cost_is_charged_at_the_model_that_actually_answered(
        self, routing, alpha, beta
    ):
        alpha.script = {"alpha-main": [_permanent()]}
        beta.script = {"beta-backup": ["ok"]}

        result = Gateway(routing=routing).complete("synthesize", "q")

        # beta-backup is $0.50/$1.00 per 1M, not alpha-main's $2.00/$10.00.
        expected = (1000 * 0.5 + 500 * 1.0) / 1_000_000
        assert result.cost_usd == pytest.approx(expected)

    def test_fallback_events_reach_the_ledger(self, routing, alpha, beta):
        alpha.script = {"alpha-main": [_permanent()]}
        beta.script = {"beta-backup": ["ok"]}

        gateway = Gateway(routing=routing)
        gateway.complete("synthesize", "q")

        assert len(gateway.ledger.fallback_events) == 1

    def test_structured_output_survives_a_fallback(self, routing, alpha, beta):
        alpha.script = {"alpha-main": [_permanent()]}
        beta.script = {"beta-backup": ['{"verdict": "ok"}']}

        result = Gateway(routing=routing).complete_json(
            "synthesize", "q", {"type": "object", "properties": {}}
        )

        assert result.parsed == {"verdict": "ok"}
        assert result.used_fallback is True


class TestExhaustion:
    def test_all_models_failing_raises_with_every_failure(self, routing, alpha, beta):
        alpha.script = {
            "alpha-main": [_permanent()],
            "alpha-small": [_permanent("alpha-small")],
        }
        beta.script = {"beta-backup": [_permanent("beta-backup", "beta")]}

        with pytest.raises(AllProvidersFailedError) as info:
            Gateway(routing=routing).complete("synthesize", "q")

        error = info.value
        assert error.task == "synthesize"
        # Every rung is reported, not just the last -- that is what makes the
        # message diagnosable.
        assert len(error.failures) == 3
        assert {model for _, model, _ in error.failures} == {
            "alpha-main",
            "beta-backup",
            "alpha-small",
        }

    def test_exhaustion_message_names_the_task_and_the_models(
        self, routing, alpha, beta
    ):
        alpha.script = {"alpha-main": [_permanent()], "alpha-small": [_permanent()]}
        beta.script = {"beta-backup": [_permanent("beta-backup", "beta")]}

        with pytest.raises(AllProvidersFailedError, match="synthesize"):
            Gateway(routing=routing).complete("synthesize", "q")

    def test_nothing_is_recorded_when_everything_fails(self, routing, alpha, beta):
        alpha.script = {"alpha-main": [_permanent()], "alpha-small": [_permanent()]}
        beta.script = {"beta-backup": [_permanent("beta-backup", "beta")]}

        gateway = Gateway(routing=routing)
        with pytest.raises(AllProvidersFailedError):
            gateway.complete("synthesize", "q")

        assert gateway.ledger.calls == []
        assert gateway.ledger.total_cost_usd == 0.0


class TestDirectLadderCall:
    def test_walk_ladder_is_usable_directly(self, routing, alpha):
        result = walk_ladder(
            Gateway(routing=routing), "route", [Message("user", "q")]
        )
        assert result.model == "alpha-small"
