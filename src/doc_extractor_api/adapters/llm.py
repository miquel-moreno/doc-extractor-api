"""LLM interface. Business logic depends on this Protocol, never on a vendor SDK.

Each project adds concrete clients (OpenAI, Anthropic, Ollama) behind it and picks
one from settings.llm_provider. Tests use FakeLLMClient, so CI never calls a real LLM.
"""

from typing import Protocol


class LLMClient(Protocol):
    async def complete(self, prompt: str, *, system: str | None = None) -> str: ...


class FakeLLMClient:
    """Returns canned answers in order. For tests only."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    async def complete(self, prompt: str, *, system: str | None = None) -> str:
        self.prompts.append(prompt)
        if not self._responses:
            raise RuntimeError("FakeLLMClient ran out of responses")
        return self._responses.pop(0)
