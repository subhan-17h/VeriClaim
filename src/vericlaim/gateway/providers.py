"""Provider adapters behind one protocol.

Each adapter does three things and nothing else: translate our neutral
:class:`~vericlaim.gateway.types.Message` list into the provider's request shape,
call it, and translate the response and any exception back into our vocabulary.

Exception translation is the load-bearing part. Because every adapter maps its SDK's
errors onto :class:`TransientProviderError` / :class:`PermanentProviderError`, the
fallback ladder in ``fallback.py`` needs no provider-specific knowledge at all.

Clients are cached per API key rather than constructed per call. unibot built a fresh
``OpenAI()`` on every single completion, which discards connection reuse.
"""

from __future__ import annotations

import base64
import os
from functools import lru_cache
from typing import Any, Protocol

from vericlaim.config import ModelSpec
from vericlaim.gateway.types import (
    ImagePart,
    Message,
    PermanentProviderError,
    ProviderUnavailableError,
    RawCompletion,
    TransientProviderError,
    Usage,
)

# HTTP statuses that are worth retrying against the same model.
_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
_GROQ_BASE_URL = "https://api.groq.com/openai/v1"


class Provider(Protocol):
    """What the gateway requires of any model backend."""

    name: str

    def complete(
        self,
        spec: ModelSpec,
        messages: list[Message],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> RawCompletion:
        """Run one completion, returning its text and token usage."""
        ...


def strictify_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``schema`` satisfying OpenAI strict structured-output rules.

    Strict mode requires every object to set ``additionalProperties: false`` and to
    list every declared property in ``required``. Hand-written schemas routinely omit
    one or the other, and the resulting error names neither the offending object nor
    the missing key. Normalising here means callers write ordinary JSON Schema.
    """
    if not isinstance(schema, dict):
        return schema

    result: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "properties" and isinstance(value, dict):
            result[key] = {k: strictify_schema(v) for k, v in value.items()}
        elif key in {"items", "additionalItems"}:
            result[key] = strictify_schema(value)
        elif key in {"anyOf", "oneOf", "allOf"} and isinstance(value, list):
            result[key] = [strictify_schema(entry) for entry in value]
        elif key == "$defs" and isinstance(value, dict):
            result[key] = {k: strictify_schema(v) for k, v in value.items()}
        else:
            result[key] = value

    if result.get("type") == "object":
        result["additionalProperties"] = False
        result["required"] = list(result.get("properties", {}))
    return result


def _split_system(messages: list[Message]) -> tuple[str, list[Message]]:
    """Split leading system content from the rest, for providers that separate them."""
    system = "\n\n".join(m.content for m in messages if m.role == "system")
    rest = [m for m in messages if m.role != "system"]
    return system, rest


# --------------------------------------------------------------------------- OpenAI


@lru_cache(maxsize=4)
def _openai_client(api_key: str):
    from openai import OpenAI

    return OpenAI(api_key=api_key, max_retries=0)


@lru_cache(maxsize=4)
def _groq_client(api_key: str):
    from openai import OpenAI

    return OpenAI(api_key=api_key, base_url=_GROQ_BASE_URL, max_retries=0)


class OpenAIProvider:
    """OpenAI chat completions, with strict JSON-schema structured output."""

    name = "openai"

    def _client(self, spec: ModelSpec):
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise ProviderUnavailableError(
                "OPENAI_API_KEY is not set", provider=self.name, model=spec.model
            )
        return _openai_client(api_key)

    def _extra_payload(self, spec: ModelSpec) -> dict[str, Any]:
        """Provider-specific request parameters. Empty for OpenAI itself."""
        return {}

    def _content(self, message: Message) -> Any:
        if not message.images:
            return message.content
        parts: list[dict[str, Any]] = [{"type": "text", "text": message.content}]
        for image in message.images:
            encoded = base64.b64encode(image.data).decode("ascii")
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{image.mime_type};base64,{encoded}"},
                }
            )
        return parts

    def complete(
        self,
        spec: ModelSpec,
        messages: list[Message],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> RawCompletion:
        client = self._client(spec)
        payload: dict[str, Any] = {
            "model": spec.model,
            "messages": [
                {"role": m.role, "content": self._content(m)} for m in messages
            ],
            "temperature": temperature,
            "max_completion_tokens": spec.max_output_tokens,
            "timeout": spec.timeout_s,
        }
        payload.update(self._extra_payload(spec))
        if json_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "vericlaim_response",
                    "strict": True,
                    "schema": strictify_schema(json_schema),
                },
            }

        try:
            response = client.chat.completions.create(**payload)
        except Exception as exc:  # noqa: BLE001 - translated below
            raise self._translate(exc, spec) from exc

        choice = response.choices[0]
        usage = response.usage
        return RawCompletion(
            text=choice.message.content or "",
            usage=Usage(
                input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            ),
        )

    def _translate(self, exc: Exception, spec: ModelSpec) -> Exception:
        import openai

        transient = (
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.RateLimitError,
            openai.InternalServerError,
        )
        if isinstance(exc, transient):
            return TransientProviderError(
                str(exc), provider=self.name, model=spec.model
            )
        if isinstance(exc, openai.APIStatusError):
            cls = (
                TransientProviderError
                if exc.status_code in _RETRYABLE_STATUS
                else PermanentProviderError
            )
            return cls(str(exc), provider=self.name, model=spec.model)
        return PermanentProviderError(str(exc), provider=self.name, model=spec.model)


# Groq speaks the OpenAI protocol, so inheritance keeps request handling identical.
class GroqProvider(OpenAIProvider):
    """Groq through its OpenAI-compatible endpoint."""

    name = "groq"

    def _extra_payload(self, spec: ModelSpec) -> dict[str, Any]:
        return (
            {"reasoning_effort": spec.reasoning_effort}
            if spec.reasoning_effort
            else {}
        )

    def _client(self, spec: ModelSpec):
        api_key = os.environ.get("GROQ_API_KEY", "")
        if not api_key:
            raise ProviderUnavailableError(
                "GROQ_API_KEY is not set", provider=self.name, model=spec.model
            )
        return _groq_client(api_key)


# --------------------------------------------------------------------------- Gemini


@lru_cache(maxsize=4)
def _gemini_client(api_key: str):
    from google import genai

    return genai.Client(api_key=api_key)


class GeminiProvider:
    """Google Gemini via google-genai.

    Two shape differences from OpenAI are handled here: system content is a separate
    ``system_instruction`` rather than a message, and the assistant role is ``model``.
    """

    name = "gemini"

    def _client(self, spec: ModelSpec):
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get(
            "GOOGLE_API_KEY", ""
        )
        if not api_key:
            raise ProviderUnavailableError(
                "GEMINI_API_KEY is not set", provider=self.name, model=spec.model
            )
        return _gemini_client(api_key)

    def _parts(self, message: Message, types_mod: Any) -> list[Any]:
        parts = [types_mod.Part.from_text(text=message.content)]
        parts.extend(
            types_mod.Part.from_bytes(data=image.data, mime_type=image.mime_type)
            for image in message.images
        )
        return parts

    def complete(
        self,
        spec: ModelSpec,
        messages: list[Message],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> RawCompletion:
        from google.genai import types as genai_types

        client = self._client(spec)
        system, turns = _split_system(messages)

        contents = [
            genai_types.Content(
                role="model" if turn.role == "assistant" else "user",
                parts=self._parts(turn, genai_types),
            )
            for turn in turns
        ]

        config: dict[str, Any] = {
            "temperature": temperature,
            "max_output_tokens": spec.max_output_tokens,
        }
        if system:
            config["system_instruction"] = system
        if json_schema is not None:
            config["response_mime_type"] = "application/json"
            config["response_json_schema"] = json_schema

        try:
            response = client.models.generate_content(
                model=spec.model,
                contents=contents,
                config=genai_types.GenerateContentConfig(**config),
            )
        except Exception as exc:  # noqa: BLE001 - translated below
            raise self._translate(exc, spec) from exc

        meta = getattr(response, "usage_metadata", None)
        return RawCompletion(
            text=response.text or "",
            usage=Usage(
                input_tokens=getattr(meta, "prompt_token_count", 0) or 0,
                output_tokens=getattr(meta, "candidates_token_count", 0) or 0,
            ),
        )

    def _translate(self, exc: Exception, spec: ModelSpec) -> Exception:
        from google.genai import errors as genai_errors

        if isinstance(exc, genai_errors.ServerError):
            return TransientProviderError(
                str(exc), provider=self.name, model=spec.model
            )
        if isinstance(exc, genai_errors.ClientError):
            # 429 arrives as a ClientError but is a rate limit, so it is retryable.
            code = getattr(exc, "code", None)
            cls = (
                TransientProviderError
                if code in _RETRYABLE_STATUS
                else PermanentProviderError
            )
            return cls(str(exc), provider=self.name, model=spec.model)
        if isinstance(exc, (TimeoutError, ConnectionError)):
            return TransientProviderError(
                str(exc), provider=self.name, model=spec.model
            )
        return PermanentProviderError(str(exc), provider=self.name, model=spec.model)


# ------------------------------------------------------------------------- registry

_REGISTRY: dict[str, Provider] = {
    OpenAIProvider.name: OpenAIProvider(),
    GroqProvider.name: GroqProvider(),
    GeminiProvider.name: GeminiProvider(),
}


def get_provider(name: str) -> Provider:
    """Return the adapter registered under ``name``."""
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        known = ", ".join(sorted(_REGISTRY))
        raise ProviderUnavailableError(
            f"Unknown provider {name!r}. Known providers: {known}",
            provider=name,
            model="",
        ) from exc


def register_provider(provider: Provider) -> None:
    """Register an adapter. Used by tests to install fakes."""
    _REGISTRY[provider.name] = provider


def available_providers() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


__all__ = [
    "GeminiProvider",
    "GroqProvider",
    "ImagePart",
    "OpenAIProvider",
    "Provider",
    "available_providers",
    "get_provider",
    "register_provider",
    "strictify_schema",
]
