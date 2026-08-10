"""Gateway core tests: routing, retries, cost accounting, structured output."""

from __future__ import annotations

import pytest

from vericlaim.gateway.core import Gateway
from vericlaim.gateway.types import (
    ImagePart,
    Message,
    PermanentProviderError,
    StructuredOutputError,
    TransientProviderError,
    Usage,
    UsageLedger,
)

SCHEMA = {"type": "object", "properties": {"verdict": {"type": "string"}}}


def _transient() -> TransientProviderError:
    return TransientProviderError("rate limited", provider="alpha", model="alpha-main")


def _permanent() -> PermanentProviderError:
    return PermanentProviderError("bad request", provider="alpha", model="alpha-main")


class TestRouting:
    def test_task_is_routed_to_its_tier_model(self, routing, alpha):
        gateway = Gateway(routing=routing)
        result = gateway.complete("synthesize", "question")
        assert result.model == "alpha-main"
        assert result.provider == "alpha"

    def test_different_tasks_reach_different_models(self, routing, alpha):
        gateway = Gateway(routing=routing)
        gateway.complete("synthesize", "q")
        gateway.complete("route", "q")
        assert alpha.models_called == ["alpha-main", "alpha-small"]

    def test_unknown_task_raises(self, routing, alpha):
        from vericlaim.config import UnknownTaskError

        with pytest.raises(UnknownTaskError):
            Gateway(routing=routing).complete("not_a_task", "q")

    def test_bare_string_prompt_becomes_a_user_message(self, routing, alpha):
        Gateway(routing=routing).complete("synthesize", "hello")
        assert alpha.calls  # reached the provider without a shape error

    def test_dict_messages_are_accepted(self, routing, alpha):
        gateway = Gateway(routing=routing)
        result = gateway.complete(
            "synthesize",
            [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        )
        assert result.text == "ok"

    def test_message_objects_are_accepted(self, routing, alpha):
        result = Gateway(routing=routing).complete(
            "synthesize", [Message("user", "u")]
        )
        assert result.text == "ok"


class TestCostAccounting:
    def test_cost_is_computed_from_the_model_rates(self, routing, alpha):
        # Fake usage is 1000 in / 500 out; alpha-main is $2.00/$10.00 per 1M.
        result = Gateway(routing=routing).complete("synthesize", "q")
        expected = (1000 * 2.0 + 500 * 10.0) / 1_000_000
        assert result.cost_usd == pytest.approx(expected)

    def test_usage_tokens_are_recorded(self, routing, alpha):
        result = Gateway(routing=routing).complete("synthesize", "q")
        assert result.usage.input_tokens == 1000
        assert result.usage.output_tokens == 500
        assert result.usage.total_tokens == 1500

    def test_latency_is_measured(self, routing, alpha):
        result = Gateway(routing=routing).complete("synthesize", "q")
        assert result.latency_ms >= 0.0

    def test_ledger_accumulates_across_calls(self, routing, alpha):
        gateway = Gateway(routing=routing)
        gateway.complete("synthesize", "q")
        gateway.complete("route", "q")

        assert len(gateway.ledger.calls) == 2
        assert gateway.ledger.total_input_tokens == 2000
        assert gateway.ledger.total_output_tokens == 1000
        assert gateway.ledger.total_cost_usd == pytest.approx(
            (1000 * 2.0 + 500 * 10.0) / 1_000_000
            + (1000 * 0.1 + 500 * 0.2) / 1_000_000
        )

    def test_ledger_breaks_down_by_task(self, routing, alpha):
        gateway = Gateway(routing=routing)
        gateway.complete("synthesize", "q")
        gateway.complete("synthesize", "q")
        gateway.complete("route", "q")

        summary = gateway.ledger.by_task()
        assert summary["synthesize"]["calls"] == 2
        assert summary["route"]["calls"] == 1
        assert summary["synthesize"]["input_tokens"] == 2000

    def test_an_injected_ledger_is_used(self, routing, alpha):
        ledger = UsageLedger()
        Gateway(routing=routing, ledger=ledger).complete("synthesize", "q")
        assert len(ledger.calls) == 1

    def test_zero_rated_model_costs_nothing(self, routing, alpha):
        # A local or free model is priced at 0 and must contribute no spend, while
        # still contributing its token counts.
        from vericlaim.config import ModelSpec

        routing.tiers["strong"] = ModelSpec(provider="alpha", model="alpha-main")
        alpha.default_usage = Usage(10, 10)

        gateway = Gateway(routing=routing)
        result = gateway.complete("synthesize", "q")

        assert result.cost_usd == 0.0
        assert result.usage.total_tokens == 20
        assert gateway.ledger.total_cost_usd == 0.0


class TestTransientRetry:
    def test_transient_failure_is_retried_against_the_same_model(self, routing, alpha):
        alpha.script = {"alpha-main": [_transient(), "recovered"]}
        result = Gateway(routing=routing).complete("synthesize", "q")

        assert result.text == "recovered"
        assert result.attempts == 2
        assert alpha.models_called == ["alpha-main", "alpha-main"]

    def test_retries_are_bounded(self, routing, alpha):
        alpha.script = {"alpha-main": [_transient()]}  # always fails
        with pytest.raises(TransientProviderError):
            Gateway(routing=routing).complete("synthesize", "q")
        # 1 initial attempt + transient_retries (2)
        assert len(alpha.calls) == 3

    def test_permanent_failure_is_not_retried(self, routing, alpha):
        # Retrying a malformed request or a bad key wastes the budget a genuine
        # rate limit needs, so permanents propagate immediately.
        alpha.script = {"alpha-main": [_permanent(), "would-have-worked"]}
        with pytest.raises(PermanentProviderError):
            Gateway(routing=routing).complete("synthesize", "q")
        assert len(alpha.calls) == 1

    def test_failed_calls_are_not_recorded_in_the_ledger(self, routing, alpha):
        alpha.script = {"alpha-main": [_permanent()]}
        gateway = Gateway(routing=routing)
        with pytest.raises(PermanentProviderError):
            gateway.complete("synthesize", "q")
        assert gateway.ledger.calls == []


class TestStructuredOutput:
    def test_valid_json_is_parsed(self, routing, alpha):
        alpha.script = {"alpha-main": ['{"verdict": "covered"}']}
        result = Gateway(routing=routing).complete_json("synthesize", "q", SCHEMA)
        assert result.parsed == {"verdict": "covered"}
        assert result.text == '{"verdict": "covered"}'

    def test_fenced_json_is_tolerated(self, routing, alpha):
        alpha.script = {"alpha-main": ['```json\n{"verdict": "ok"}\n```']}
        result = Gateway(routing=routing).complete_json("synthesize", "q", SCHEMA)
        assert result.parsed == {"verdict": "ok"}

    def test_unparseable_output_raises_rather_than_returning_none(
        self, routing, alpha
    ):
        alpha.script = {"alpha-main": ["I'm afraid I can't do that"]}
        with pytest.raises(StructuredOutputError, match="did not return valid JSON"):
            Gateway(routing=routing).complete_json("synthesize", "q", SCHEMA)

    def test_parsed_completion_is_what_the_ledger_records(self, routing, alpha):
        alpha.script = {"alpha-main": ['{"verdict": "ok"}']}
        gateway = Gateway(routing=routing)
        result = gateway.complete_json("synthesize", "q", SCHEMA)
        # The recorded call and the returned object must agree, or the cost panel
        # and the trace would show different things.
        assert gateway.ledger.calls[-1] is result
        assert gateway.ledger.calls[-1].parsed == {"verdict": "ok"}


class TestVision:
    def test_images_are_passed_through_as_one_user_message(self, routing, alpha):
        gateway = Gateway(routing=routing)
        result = gateway.complete_vision(
            "synthesize", "read this page", [ImagePart(b"\x89PNG")]
        )
        assert result.text == "ok"
        assert len(gateway.ledger.calls) == 1

    def test_vision_with_schema_is_parsed(self, routing, alpha):
        alpha.script = {"alpha-main": ['{"verdict": "legible"}']}
        result = Gateway(routing=routing).complete_vision(
            "synthesize", "read", [ImagePart(b"x")], schema=SCHEMA
        )
        assert result.parsed == {"verdict": "legible"}
