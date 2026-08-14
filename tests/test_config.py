"""Configuration and model-routing tests.

These are deliberately strict about failure modes. A routing table that silently
accepts a task pointing at a nonexistent tier would surface the bug only when that
task first ran, which for a rarely-taken path could be in front of a grader.
"""

from pathlib import Path

import pytest
import yaml

from vericlaim.config import (
    DEFAULT_ROUTING_PATH,
    ModelSpec,
    Settings,
    UnknownTaskError,
    load_model_routing,
)


def _write_routing(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "routing.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


MINIMAL = {
    "tiers": {
        "cheap": {
            "provider": "gemini",
            "model": "flash",
            "usd_per_1m_input": 0.1,
            "usd_per_1m_output": 0.4,
            "timeout_s": 30,
            "max_output_tokens": 1024,
        },
        "strong": {
            "provider": "openai",
            "model": "big",
            "usd_per_1m_input": 2.5,
            "usd_per_1m_output": 10.0,
        },
    },
    "tasks": {"route": "cheap", "synthesize": "strong"},
    "fallback": {"strong": [{"provider": "gemini", "model": "pro"}]},
    "limits": {
        "transient_retries": 3,
        "last_rung_transient_retries": 6,
        "transient_backoff_s": 0.5,
    },
}


class TestSettings:
    def test_defaults_are_coherent(self):
        settings = Settings()
        assert settings.embed_model == "nomic-embed-text"
        assert settings.embed_dim == 768
        assert settings.chunk_overlap < settings.chunk_size
        assert settings.pg_port == 5435

    def test_ocr_language_is_explicit_not_docling_default(self):
        # RapidOcrOptions.lang defaults to ["chinese"]; relying on that default is a
        # live bug, so the setting must name the language explicitly.
        assert Settings().ocr_lang == ("english",)

    def test_overlap_must_be_smaller_than_chunk_size(self):
        with pytest.raises(ValueError, match="chunk_overlap"):
            Settings(chunk_size=100, chunk_overlap=100)

    def test_confidence_floor_must_be_a_probability(self):
        with pytest.raises(ValueError, match="ocr_confidence_floor"):
            Settings(ocr_confidence_floor=1.5)

    def test_dsn_selects_the_role_and_reads_password_from_env(self, monkeypatch):
        monkeypatch.setenv("READONLY_PASSWORD", "ro-secret")
        monkeypatch.setenv("POSTGRES_PASSWORD", "admin-secret")
        settings = Settings()

        readonly = settings.dsn(readonly=True)
        assert f"user={settings.pg_readonly_user}" in readonly
        assert "password=ro-secret" in readonly

        admin = settings.dsn(readonly=False)
        assert f"user={settings.pg_admin_user}" in admin
        assert "password=admin-secret" in admin

    def test_dsn_password_is_empty_when_env_is_unset(self, monkeypatch):
        # Empty rather than absent, so the connection fails loudly at connect time
        # instead of falling back to an ambient superuser.
        monkeypatch.delenv("READONLY_PASSWORD", raising=False)
        assert "password=" in Settings().dsn(readonly=True)


class TestModelSpecCost:
    def test_cost_is_computed_from_per_million_rates(self):
        spec = ModelSpec(
            provider="openai",
            model="m",
            usd_per_1m_input=2.50,
            usd_per_1m_output=10.00,
        )
        # 1M input + 0.5M output = 2.50 + 5.00
        assert spec.cost_usd(1_000_000, 500_000) == pytest.approx(7.50)

    def test_zero_rated_models_cost_nothing(self):
        spec = ModelSpec(provider="ollama", model="local")
        assert spec.cost_usd(10_000, 10_000) == 0.0


class TestModelRouting:
    def test_resolves_task_to_its_tier_model(self, tmp_path):
        routing = load_model_routing(_write_routing(tmp_path, MINIMAL))
        assert routing.resolve("route").model == "flash"
        assert routing.resolve("synthesize").provider == "openai"

    def test_unknown_task_raises_rather_than_defaulting(self, tmp_path):
        routing = load_model_routing(_write_routing(tmp_path, MINIMAL))
        with pytest.raises(UnknownTaskError, match="no_such_task"):
            routing.resolve("no_such_task")

    def test_fallback_chain_crosses_providers(self, tmp_path):
        routing = load_model_routing(_write_routing(tmp_path, MINIMAL))
        chain = routing.fallback_chain("synthesize")
        assert [spec.provider for spec in chain] == ["gemini"]
        # A tier with no declared fallback yields an empty chain, not an error.
        assert routing.fallback_chain("route") == ()

    def test_fallback_inherits_timing_from_its_tier(self, tmp_path):
        routing = load_model_routing(_write_routing(tmp_path, MINIMAL))
        assert routing.fallback_chain("synthesize")[0].timeout_s == 60.0

    def test_limits_are_read(self, tmp_path):
        routing = load_model_routing(_write_routing(tmp_path, MINIMAL))
        assert routing.transient_retries == 3
        assert routing.last_rung_transient_retries == 6
        assert routing.transient_backoff_s == 0.5

    def test_task_pointing_at_missing_tier_is_rejected(self, tmp_path):
        broken = {**MINIMAL, "tasks": {"route": "nonexistent"}}
        with pytest.raises(ValueError, match="unknown tier"):
            load_model_routing(_write_routing(tmp_path, broken))

    def test_fallback_for_missing_tier_is_rejected(self, tmp_path):
        broken = {**MINIMAL, "fallback": {"ghost": [{"provider": "p", "model": "m"}]}}
        with pytest.raises(ValueError, match="unknown tier"):
            load_model_routing(_write_routing(tmp_path, broken))

    def test_model_entry_missing_required_keys_is_rejected(self, tmp_path):
        broken = {**MINIMAL, "tiers": {"cheap": {"provider": "openai"}}}
        with pytest.raises(ValueError, match="requires 'provider' and 'model'"):
            load_model_routing(_write_routing(tmp_path, broken))

    def test_empty_tiers_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="no tiers"):
            load_model_routing(_write_routing(tmp_path, {"tasks": {"a": "b"}}))


class TestShippedRoutingTable:
    """The committed config.yaml must itself be valid and complete."""

    def test_shipped_config_loads(self):
        routing = load_model_routing(DEFAULT_ROUTING_PATH)
        assert routing.tiers
        assert routing.tasks

    def test_every_task_the_system_calls_is_routed(self):
        routing = load_model_routing(DEFAULT_ROUTING_PATH)
        # Named here so adding a gateway caller without routing it fails a test
        # rather than erroring at runtime on a rarely-taken path.
        required = {
            "understand",
            "route",
            "plan",
            "sufficiency",
            "synthesize",
            "verify",
            "sql_table_select",
            "sql_planner",
            "sql_generator",
            "sql_refiner",
            "ocr_vision",
            "eval_judge",
        }
        assert required <= set(routing.tasks)

    def test_two_distinct_providers_are_configured(self):
        # A single-provider setup cannot demonstrate genuine fallback.
        routing = load_model_routing(DEFAULT_ROUTING_PATH)
        providers = {spec.provider for spec in routing.tiers.values()}
        for chain in routing.fallbacks.values():
            providers.update(spec.provider for spec in chain)
        assert len(providers) >= 2

    def test_billed_models_declare_their_rates(self):
        # A paid model with no rate would silently under-report real spend. Free-tier
        # models are legitimately priced at zero: charging them notional paid rates
        # would consume the ceiling that protects an actual prepaid credit.
        routing = load_model_routing(DEFAULT_ROUTING_PATH)
        billed = [
            spec
            for spec in (*routing.tiers.values(), *sum(routing.fallbacks.values(), ()))
            if spec.paid
        ]
        assert billed, "no billed model configured, so fallback cannot be demonstrated"
        for spec in billed:
            assert spec.usd_per_1m_input > 0, f"{spec.label} has no input rate"
            assert spec.usd_per_1m_output > 0, f"{spec.label} has no output rate"

    def test_free_tiers_are_priced_at_zero(self):
        routing = load_model_routing(DEFAULT_ROUTING_PATH)
        for name, spec in routing.tiers.items():
            assert not spec.paid, f"tier {name} routes to a billed model"
            assert spec.usd_per_1m_input == 0.0, f"tier {name} charges for a free model"

    def test_output_budgets_leave_room_for_thinking_tokens(self):
        # Gemini 3.x reasons before answering and bills those thoughts against
        # max_output_tokens. A small budget yields an empty reply, not a short one.
        routing = load_model_routing(DEFAULT_ROUTING_PATH)
        for name, spec in routing.tiers.items():
            assert spec.max_output_tokens >= 2048, f"tier {name} may starve on thinking"

    def test_vision_task_routes_to_a_vision_capable_tier(self):
        routing = load_model_routing(DEFAULT_ROUTING_PATH)
        assert routing.tier_for("ocr_vision") == "vision"
