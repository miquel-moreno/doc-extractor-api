"""LLM access. Business logic depends on the LLMClient Protocol, never on a vendor SDK.

OpenAI and Ollama both speak the OpenAI Chat Completions API, so one HTTP client
serves both: only the base URL, key and model change (LLM_PROVIDER in .env).
Tests use FakeLLMClient, so CI never calls a real LLM.
"""

import time
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel

from doc_extractor_api.core.config import Settings

Role = Literal["system", "user", "assistant"]
PLACEHOLDER_KEY = "change-me"


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0


class LLMError(Exception):
    """The provider failed, is misconfigured or returned something unusable."""


class LLMClient(Protocol):
    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        json_schema: dict[str, Any] | None = None,
        schema_name: str = "response",
    ) -> LLMResponse: ...


class OpenAICompatibleClient:
    """Chat Completions over plain HTTP, with optional structured output (JSON schema)."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._transport = transport

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        json_schema: dict[str, Any] | None = None,
        schema_name: str = "response",
    ) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": 0,
        }
        if json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": json_schema, "strict": True},
            }

        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    json=body,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
        except httpx.HTTPError as exc:
            raise LLMError(f"request to {self._base_url} failed: {exc!r}") from exc
        latency_ms = (time.perf_counter() - start) * 1000

        if response.status_code != httpx.codes.OK:
            raise LLMError(
                f"HTTP {response.status_code} from {self._base_url}: {response.text[:300]}"
            )
        try:
            data = response.json()
            text = data["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("unexpected response shape from the LLM provider") from exc

        usage = data.get("usage") or {}
        return LLMResponse(
            text=text,
            model=str(data.get("model", self._model)),
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            latency_ms=latency_ms,
        )


def build_llm_client(settings: Settings) -> LLMClient:
    """Pick the client from LLM_PROVIDER."""
    if settings.llm_provider == "openai":
        key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
        if key in ("", PLACEHOLDER_KEY):
            raise LLMError("OPENAI_API_KEY is not set in .env")
        return OpenAICompatibleClient(
            base_url=settings.openai_base_url,
            api_key=key,
            model=settings.llm_model,
            timeout=settings.llm_timeout_seconds,
        )
    if settings.llm_provider == "ollama":
        return OpenAICompatibleClient(
            base_url=f"{settings.ollama_base_url.rstrip('/')}/v1",
            api_key="ollama",  # Ollama ignores it, but the header must be present
            model=settings.llm_model,
            timeout=settings.llm_timeout_seconds,
        )
    raise LLMError(f"LLM provider {settings.llm_provider!r} is not implemented yet")


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON schema accepted by OpenAI strict structured outputs.

    Strict mode needs every property listed in `required` (optional ones are
    nullable instead), `additionalProperties: false` and no `default` values.
    """
    schema = model.model_json_schema()

    def fix(node: Any) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("title"), str):
                del node["title"]
            node.pop("default", None)
            if node.get("type") == "object" and isinstance(node.get("properties"), dict):
                node["required"] = list(node["properties"])
                node["additionalProperties"] = False
            for value in node.values():
                fix(value)
        elif isinstance(node, list):
            for item in node:
                fix(item)

    fix(schema)
    return schema


class FakeLLMClient:
    """Returns canned answers in order and records every call. For tests only."""

    def __init__(self, responses: list[str], *, model: str = "fake-model") -> None:
        self._responses = list(responses)
        self._model = model
        self.calls: list[list[ChatMessage]] = []

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        json_schema: dict[str, Any] | None = None,
        schema_name: str = "response",
    ) -> LLMResponse:
        self.calls.append(list(messages))
        if not self._responses:
            raise LLMError("FakeLLMClient ran out of responses")
        return LLMResponse(
            text=self._responses.pop(0),
            model=self._model,
            input_tokens=100,
            output_tokens=50,
            latency_ms=1.0,
        )
