import json
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from doc_extractor_api.adapters.llm import (
    ChatMessage,
    FakeLLMClient,
    LLMError,
    OpenAICompatibleClient,
    build_llm_client,
)
from doc_extractor_api.core.config import Settings

MESSAGES = [ChatMessage("system", "be precise"), ChatMessage("user", "hello")]


def ok_response(content: str = '{"a": 1}') -> dict[str, Any]:
    return {
        "model": "gpt-test-2026",
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30},
    }


def client_with(handler: Any, **kwargs: Any) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        base_url="https://llm.example/v1/",
        api_key="test-key",
        model="gpt-test",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


async def test_sends_chat_completion_with_strict_schema() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=ok_response())

    schema = {"type": "object", "properties": {}}
    response = await client_with(handler).chat(MESSAGES, json_schema=schema, schema_name="doc")

    assert seen["url"] == "https://llm.example/v1/chat/completions"
    assert seen["auth"] == "Bearer test-key"
    body = seen["body"]
    assert body["model"] == "gpt-test"
    assert body["temperature"] == 0
    assert body["messages"] == [
        {"role": "system", "content": "be precise"},
        {"role": "user", "content": "hello"},
    ]
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "doc", "schema": schema, "strict": True},
    }
    assert response.text == '{"a": 1}'
    assert response.model == "gpt-test-2026"
    assert (response.input_tokens, response.output_tokens) == (120, 30)
    assert response.latency_ms >= 0


async def test_without_schema_no_response_format_is_sent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "response_format" not in json.loads(request.content)
        return httpx.Response(200, json=ok_response("hi"))

    assert (await client_with(handler).chat(MESSAGES)).text == "hi"


async def test_http_error_status_raises_llm_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    with pytest.raises(LLMError, match="HTTP 429"):
        await client_with(handler).chat(MESSAGES)


async def test_network_error_raises_llm_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(LLMError, match="failed"):
        await client_with(handler).chat(MESSAGES)


async def test_unexpected_body_raises_llm_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"nothing": "here"})

    with pytest.raises(LLMError, match="unexpected response"):
        await client_with(handler).chat(MESSAGES)


def settings(**values: Any) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_openai_provider_requires_a_real_key() -> None:
    with pytest.raises(LLMError, match="OPENAI_API_KEY"):
        build_llm_client(settings(llm_provider="openai", openai_api_key=SecretStr("change-me")))

    client = build_llm_client(
        settings(llm_provider="openai", openai_api_key=SecretStr("sk-test"), llm_model="m")
    )
    assert isinstance(client, OpenAICompatibleClient)


def test_ollama_provider_uses_the_openai_compatible_endpoint() -> None:
    client = build_llm_client(
        settings(llm_provider="ollama", ollama_base_url="http://localhost:11434/")
    )

    assert isinstance(client, OpenAICompatibleClient)
    assert client._base_url == "http://localhost:11434/v1"


def test_anthropic_is_not_implemented_yet() -> None:
    with pytest.raises(LLMError, match="not implemented"):
        build_llm_client(settings(llm_provider="anthropic"))


async def test_fake_client_returns_answers_in_order_and_records_calls() -> None:
    llm = FakeLLMClient(["first", "second"])

    assert (await llm.chat(MESSAGES)).text == "first"
    assert (await llm.chat(MESSAGES)).text == "second"
    assert llm.calls == [MESSAGES, MESSAGES]
    with pytest.raises(LLMError, match="ran out"):
        await llm.chat(MESSAGES)
