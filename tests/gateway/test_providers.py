"""Provider adapter tests.

No network. Provider SDK clients are replaced with recording fakes so we can assert
on the exact request shape each adapter builds, and real SDK exception instances are
constructed to verify the transient/permanent translation that the fallback ladder
depends on.
"""

from __future__ import annotations

import httpx
import pytest

from vericlaim.config import ModelSpec
from vericlaim.gateway import providers as providers_mod
from vericlaim.gateway.providers import (
    GeminiProvider,
    OpenAIProvider,
    get_provider,
    register_provider,
    strictify_schema,
)
from vericlaim.gateway.types import (
    ImagePart,
    Message,
    PermanentProviderError,
    ProviderUnavailableError,
    TransientProviderError,
)

OPENAI_SPEC = ModelSpec(provider="openai", model="gpt-4o-mini", timeout_s=12.0)
GEMINI_SPEC = ModelSpec(provider="gemini", model="gemini-2.0-flash", timeout_s=12.0)

SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "sources": {"type": "array", "items": {"type": "string"}},
    },
}


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://example.invalid/v1")


# --------------------------------------------------------------------------- schema


class TestStrictifySchema:
    def test_objects_gain_additional_properties_false_and_full_required(self):
        result = strictify_schema(SCHEMA)
        assert result["additionalProperties"] is False
        assert sorted(result["required"]) == ["answer", "sources"]

    def test_nested_objects_are_normalised_too(self):
        nested = {
            "type": "object",
            "properties": {
                "locator": {
                    "type": "object",
                    "properties": {"page": {"type": "integer"}},
                }
            },
        }
        locator = strictify_schema(nested)["properties"]["locator"]
        assert locator["additionalProperties"] is False
        assert locator["required"] == ["page"]

    def test_objects_inside_array_items_are_normalised(self):
        schema = {
            "type": "object",
            "properties": {
                "rows": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                    },
                }
            },
        }
        items = strictify_schema(schema)["properties"]["rows"]["items"]
        assert items["additionalProperties"] is False
        assert items["required"] == ["id"]

    def test_input_is_not_mutated(self):
        original = {"type": "object", "properties": {"a": {"type": "string"}}}
        strictify_schema(original)
        assert "additionalProperties" not in original

    def test_non_object_schemas_pass_through(self):
        assert strictify_schema({"type": "string"}) == {"type": "string"}


# --------------------------------------------------------------------------- OpenAI


class _FakeOpenAIClient:
    """Records the payload and returns a canned completion."""

    def __init__(self, *, text: str = "ok", raiser: Exception | None = None):
        self.payload: dict | None = None
        self._text = text
        self._raiser = raiser
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.payload = kwargs
                if outer._raiser is not None:
                    raise outer._raiser
                return outer._response()

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()

    def _response(self):
        message = type("M", (), {"content": self._text})()
        choice = type("C", (), {"message": message})()
        usage = type("U", (), {"prompt_tokens": 120, "completion_tokens": 45})()
        return type("R", (), {"choices": [choice], "usage": usage})()


@pytest.fixture
def openai_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")


class TestOpenAIProvider:
    def test_missing_key_is_provider_unavailable(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(ProviderUnavailableError, match="OPENAI_API_KEY"):
            OpenAIProvider().complete(OPENAI_SPEC, [Message("user", "hi")])

    def test_builds_request_and_reports_usage(self, monkeypatch, openai_env):
        fake = _FakeOpenAIClient(text="hello")
        monkeypatch.setattr(providers_mod, "_openai_client", lambda key: fake)

        result = OpenAIProvider().complete(
            OPENAI_SPEC, [Message("system", "sys"), Message("user", "q")]
        )

        assert result.text == "hello"
        assert result.usage.input_tokens == 120
        assert result.usage.output_tokens == 45
        assert result.usage.total_tokens == 165
        assert fake.payload["model"] == "gpt-4o-mini"
        assert fake.payload["timeout"] == 12.0
        assert [m["role"] for m in fake.payload["messages"]] == ["system", "user"]

    def test_json_schema_requests_strict_structured_output(
        self, monkeypatch, openai_env
    ):
        fake = _FakeOpenAIClient(text="{}")
        monkeypatch.setattr(providers_mod, "_openai_client", lambda key: fake)

        OpenAIProvider().complete(
            OPENAI_SPEC, [Message("user", "q")], json_schema=SCHEMA
        )

        fmt = fake.payload["response_format"]
        assert fmt["type"] == "json_schema"
        assert fmt["json_schema"]["strict"] is True
        # The caller passed an ordinary schema; the adapter made it strict-compatible.
        assert fmt["json_schema"]["schema"]["additionalProperties"] is False

    def test_images_become_inline_data_urls(self, monkeypatch, openai_env):
        fake = _FakeOpenAIClient()
        monkeypatch.setattr(providers_mod, "_openai_client", lambda key: fake)

        OpenAIProvider().complete(
            OPENAI_SPEC,
            [Message("user", "read this", images=(ImagePart(b"\x89PNG", "image/png"),))],
        )

        parts = fake.payload["messages"][0]["content"]
        assert parts[0]["type"] == "text"
        assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")

    @pytest.mark.parametrize(
        "exc",
        [
            pytest.param(
                lambda: __import__("openai").APIConnectionError(request=_request()),
                id="connection",
            ),
            pytest.param(
                lambda: __import__("openai").RateLimitError(
                    "slow down",
                    response=httpx.Response(429, request=_request()),
                    body=None,
                ),
                id="rate-limit",
            ),
            pytest.param(
                lambda: __import__("openai").InternalServerError(
                    "boom",
                    response=httpx.Response(500, request=_request()),
                    body=None,
                ),
                id="server-error",
            ),
            pytest.param(
                lambda: __import__("openai").APIStatusError(
                    "unavailable",
                    response=httpx.Response(503, request=_request()),
                    body=None,
                ),
                id="503",
            ),
        ],
    )
    def test_retryable_failures_are_transient(self, monkeypatch, openai_env, exc):
        fake = _FakeOpenAIClient(raiser=exc())
        monkeypatch.setattr(providers_mod, "_openai_client", lambda key: fake)
        with pytest.raises(TransientProviderError):
            OpenAIProvider().complete(OPENAI_SPEC, [Message("user", "q")])

    def test_auth_failure_is_permanent(self, monkeypatch, openai_env):
        import openai

        fake = _FakeOpenAIClient(
            raiser=openai.AuthenticationError(
                "bad key", response=httpx.Response(401, request=_request()), body=None
            )
        )
        monkeypatch.setattr(providers_mod, "_openai_client", lambda key: fake)
        # Permanent, so the ladder walks on rather than burning retries on a bad key.
        with pytest.raises(PermanentProviderError):
            OpenAIProvider().complete(OPENAI_SPEC, [Message("user", "q")])

    def test_translated_error_carries_provider_and_model(self, monkeypatch, openai_env):
        fake = _FakeOpenAIClient(raiser=ValueError("weird"))
        monkeypatch.setattr(providers_mod, "_openai_client", lambda key: fake)
        with pytest.raises(PermanentProviderError) as info:
            OpenAIProvider().complete(OPENAI_SPEC, [Message("user", "q")])
        assert info.value.provider == "openai"
        assert info.value.model == "gpt-4o-mini"


# --------------------------------------------------------------------------- Gemini


class _FakeGeminiClient:
    def __init__(self, *, text: str = "ok", raiser: Exception | None = None):
        self.kwargs: dict | None = None
        outer = self

        class _Models:
            def generate_content(self, **kwargs):
                outer.kwargs = kwargs
                if raiser is not None:
                    raise raiser
                meta = type(
                    "U", (), {"prompt_token_count": 30, "candidates_token_count": 7}
                )()
                return type("R", (), {"text": text, "usage_metadata": meta})()

        self.models = _Models()


@pytest.fixture
def gemini_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")


class TestGeminiProvider:
    def test_missing_key_is_provider_unavailable(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        with pytest.raises(ProviderUnavailableError, match="GEMINI_API_KEY"):
            GeminiProvider().complete(GEMINI_SPEC, [Message("user", "hi")])

    def test_system_message_becomes_system_instruction(self, monkeypatch, gemini_env):
        fake = _FakeGeminiClient()
        monkeypatch.setattr(providers_mod, "_gemini_client", lambda key: fake)

        GeminiProvider().complete(
            GEMINI_SPEC, [Message("system", "be terse"), Message("user", "q")]
        )

        config = fake.kwargs["config"]
        assert config.system_instruction == "be terse"
        # System content is lifted out; only the real turns remain as contents.
        assert len(fake.kwargs["contents"]) == 1

    def test_assistant_role_is_renamed_to_model(self, monkeypatch, gemini_env):
        fake = _FakeGeminiClient()
        monkeypatch.setattr(providers_mod, "_gemini_client", lambda key: fake)

        GeminiProvider().complete(
            GEMINI_SPEC,
            [Message("user", "a"), Message("assistant", "b"), Message("user", "c")],
        )

        assert [c.role for c in fake.kwargs["contents"]] == ["user", "model", "user"]

    def test_reports_usage(self, monkeypatch, gemini_env):
        fake = _FakeGeminiClient(text="hi")
        monkeypatch.setattr(providers_mod, "_gemini_client", lambda key: fake)

        result = GeminiProvider().complete(GEMINI_SPEC, [Message("user", "q")])

        assert result.text == "hi"
        assert result.usage.input_tokens == 30
        assert result.usage.output_tokens == 7

    def test_json_schema_sets_mime_type(self, monkeypatch, gemini_env):
        fake = _FakeGeminiClient(text="{}")
        monkeypatch.setattr(providers_mod, "_gemini_client", lambda key: fake)

        GeminiProvider().complete(
            GEMINI_SPEC, [Message("user", "q")], json_schema=SCHEMA
        )

        assert fake.kwargs["config"].response_mime_type == "application/json"

    def test_server_error_is_transient(self, monkeypatch, gemini_env):
        from google.genai import errors as genai_errors

        fake = _FakeGeminiClient(
            raiser=genai_errors.ServerError(503, {"error": {"message": "down"}})
        )
        monkeypatch.setattr(providers_mod, "_gemini_client", lambda key: fake)
        with pytest.raises(TransientProviderError):
            GeminiProvider().complete(GEMINI_SPEC, [Message("user", "q")])

    def test_rate_limit_client_error_is_transient(self, monkeypatch, gemini_env):
        from google.genai import errors as genai_errors

        # 429 arrives as a ClientError, but retrying is the right response.
        fake = _FakeGeminiClient(
            raiser=genai_errors.ClientError(429, {"error": {"message": "quota"}})
        )
        monkeypatch.setattr(providers_mod, "_gemini_client", lambda key: fake)
        with pytest.raises(TransientProviderError):
            GeminiProvider().complete(GEMINI_SPEC, [Message("user", "q")])

    def test_bad_request_client_error_is_permanent(self, monkeypatch, gemini_env):
        from google.genai import errors as genai_errors

        fake = _FakeGeminiClient(
            raiser=genai_errors.ClientError(400, {"error": {"message": "bad"}})
        )
        monkeypatch.setattr(providers_mod, "_gemini_client", lambda key: fake)
        with pytest.raises(PermanentProviderError):
            GeminiProvider().complete(GEMINI_SPEC, [Message("user", "q")])


# ------------------------------------------------------------------------- registry


class TestRegistry:
    def test_both_providers_are_registered(self):
        assert get_provider("openai").name == "openai"
        assert get_provider("gemini").name == "gemini"

    def test_unknown_provider_raises_and_lists_known(self):
        with pytest.raises(ProviderUnavailableError, match="Known providers"):
            get_provider("nope")

    def test_fakes_can_be_registered(self):
        class _Fake:
            name = "fake-test-provider"

            def complete(self, spec, messages, *, json_schema=None, temperature=0.0):
                raise NotImplementedError

        register_provider(_Fake())
        assert get_provider("fake-test-provider").name == "fake-test-provider"
