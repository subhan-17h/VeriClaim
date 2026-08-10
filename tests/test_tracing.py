"""Tracing tests.

The property that matters most: with tracing off, @traced must be transparent. If a
traced run could behave differently from an untraced one, every test in this repo
would be testing a different system than the one that ships.
"""

from __future__ import annotations

import functools

import pytest

from vericlaim.tracing import (
    add_run_metadata,
    is_tracing_enabled,
    trace_completion,
    traced,
)


@pytest.fixture
def tracing_off(monkeypatch):
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("VC_LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)


class TestEnablement:
    def test_disabled_without_any_configuration(self, tracing_off):
        assert is_tracing_enabled() is False

    def test_flag_without_a_key_is_not_enough(self, monkeypatch, tracing_off):
        # A flag with no key would make every span attempt a call that cannot succeed.
        monkeypatch.setenv("LANGSMITH_TRACING", "true")
        assert is_tracing_enabled() is False

    def test_key_without_the_flag_is_not_enough(self, monkeypatch, tracing_off):
        monkeypatch.setenv("LANGSMITH_API_KEY", "k")
        assert is_tracing_enabled() is False

    def test_enabled_with_both(self, monkeypatch, tracing_off):
        monkeypatch.setenv("LANGSMITH_TRACING", "true")
        monkeypatch.setenv("LANGSMITH_API_KEY", "k")
        assert is_tracing_enabled() is True

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
    def test_truthy_spellings(self, monkeypatch, tracing_off, value):
        monkeypatch.setenv("LANGSMITH_TRACING", value)
        monkeypatch.setenv("LANGSMITH_API_KEY", "k")
        assert is_tracing_enabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
    def test_falsy_spellings(self, monkeypatch, tracing_off, value):
        monkeypatch.setenv("LANGSMITH_TRACING", value)
        monkeypatch.setenv("LANGSMITH_API_KEY", "k")
        assert is_tracing_enabled() is False

    def test_decision_is_made_per_call_not_at_import(self, monkeypatch, tracing_off):
        # Import order must not determine whether tracing works.
        assert is_tracing_enabled() is False
        monkeypatch.setenv("LANGSMITH_TRACING", "true")
        monkeypatch.setenv("LANGSMITH_API_KEY", "k")
        assert is_tracing_enabled() is True


class TestTracedIsTransparent:
    def test_return_value_is_unchanged(self, tracing_off):
        @traced("adder")
        def add(a, b):
            return a + b

        assert add(2, 3) == 5

    def test_arguments_pass_through_including_keywords(self, tracing_off):
        @traced()
        def fn(a, b=1, *args, **kwargs):
            return (a, b, args, kwargs)

        assert fn(1, 2, 3, x=4) == (1, 2, (3,), {"x": 4})

    def test_exceptions_propagate_unchanged(self, tracing_off):
        @traced("boom")
        def fail():
            raise ValueError("original message")

        with pytest.raises(ValueError, match="original message"):
            fail()

    def test_metadata_is_preserved(self, tracing_off):
        @traced("named-span")
        def documented():
            """A docstring."""

        assert documented.__name__ == "documented"
        assert documented.__doc__ == "A docstring."

    def test_underlying_function_is_reachable(self, tracing_off):
        def original():
            return "raw"

        assert traced()(original).__wrapped__ is original

    def test_langsmith_is_not_imported_when_disabled(self, tracing_off, monkeypatch):
        # Proves the disabled path really is the original function: if it built a
        # traceable wrapper, this import hook would fire.
        import builtins

        real_import = builtins.__import__
        seen: list[str] = []

        def spy(name, *args, **kwargs):
            seen.append(name)
            return real_import(name, *args, **kwargs)

        @traced("x")
        def fn():
            return 1

        monkeypatch.setattr(builtins, "__import__", spy)
        assert fn() == 1
        assert not any(name.startswith("langsmith") for name in seen)


class TestTracedWhenEnabled:
    """The enabled path, exercised against a stub langsmith so no network is used."""

    @pytest.fixture
    def fake_langsmith(self, monkeypatch, tracing_off):
        import sys

        monkeypatch.setenv("LANGSMITH_TRACING", "true")
        monkeypatch.setenv("LANGSMITH_API_KEY", "k")

        recorded: dict[str, object] = {}

        def traceable(**kwargs):
            recorded.update(kwargs)

            def decorate(fn):
                @functools.wraps(fn)
                def inner(*args, **kw):
                    recorded["called"] = True
                    return fn(*args, **kw)

                return inner

            return decorate

        monkeypatch.setitem(
            sys.modules,
            "langsmith",
            type("M", (), {"traceable": staticmethod(traceable)}),
        )
        return recorded

    def test_enabled_path_wraps_and_still_returns_the_value(self, fake_langsmith):
        @traced("route", run_type="chain", tags=["orchestrator"])
        def route(x):
            return x * 2

        assert route(21) == 42
        assert fake_langsmith["called"] is True

    def test_span_name_run_type_and_tags_are_forwarded(self, fake_langsmith):
        @traced("policy_rag", run_type="tool", tags=["source"])
        def tool():
            return None

        tool()
        assert fake_langsmith["name"] == "policy_rag"
        assert fake_langsmith["run_type"] == "tool"
        assert fake_langsmith["tags"] == ["source"]

    def test_span_name_defaults_to_the_function_name(self, fake_langsmith):
        @traced()
        def synthesize():
            return None

        synthesize()
        assert fake_langsmith["name"] == "synthesize"

    def test_exceptions_still_propagate_when_traced(self, fake_langsmith):
        @traced("failing")
        def fail():
            raise ValueError("still mine")

        with pytest.raises(ValueError, match="still mine"):
            fail()


class TestRunMetadata:
    def test_returns_false_when_tracing_is_off(self, tracing_off):
        assert add_run_metadata(anything="value") is False

    def test_returns_false_when_no_span_is_active(self, monkeypatch, tracing_off):
        monkeypatch.setenv("LANGSMITH_TRACING", "true")
        monkeypatch.setenv("LANGSMITH_API_KEY", "k")
        # Enabled, but called outside any traced function.
        assert add_run_metadata(x=1) is False

    def test_never_raises_when_langsmith_misbehaves(self, monkeypatch, tracing_off):
        # Annotating a trace must not be able to break the traced operation.
        import sys

        monkeypatch.setenv("LANGSMITH_TRACING", "true")
        monkeypatch.setenv("LANGSMITH_API_KEY", "k")

        def explode():
            raise RuntimeError("langsmith is unhappy")

        stub = type("M", (), {"get_current_run_tree": staticmethod(explode)})
        monkeypatch.setitem(sys.modules, "langsmith.run_helpers", stub)

        assert add_run_metadata(x=1) is False


class TestTraceCompletion:
    def test_is_a_noop_when_tracing_is_off(self, tracing_off, routing, alpha):
        from vericlaim.gateway.core import Gateway

        completion = Gateway(routing=routing).complete("synthesize", "q")
        assert trace_completion(completion) is False

    def test_gateway_calls_still_succeed_untraced(self, tracing_off, routing, alpha):
        from vericlaim.gateway.core import Gateway

        gateway = Gateway(routing=routing)
        result = gateway.complete("synthesize", "q")
        # The gateway annotates the trace on every call; with tracing off that must
        # be invisible rather than fatal.
        assert result.text == "ok"
        assert len(gateway.ledger.calls) == 1
