"""FastAPI dependencies. Tests override them to use SQLite and a fake LLM."""

from collections.abc import AsyncIterator

from fastapi import Request
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from doc_extractor_api.adapters.llm import LLMClient, LLMError, build_llm_client
from doc_extractor_api.adapters.queue import JobQueue, connect_queue
from doc_extractor_api.core.config import get_settings
from doc_extractor_api.core.errors import ServiceUnavailableError


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


def get_llm() -> LLMClient:
    # Built per request (it is cheap) so a missing key is a clear 503, not a crash at startup.
    try:
        return build_llm_client(get_settings())
    except LLMError as exc:
        raise ServiceUnavailableError(str(exc)) from exc


async def get_queue(request: Request) -> JobQueue:
    # Connected on first use, so the API starts (and /extract works) without Redis.
    queue: JobQueue | None = getattr(request.app.state, "queue", None)
    if queue is None:
        try:
            queue = await connect_queue(get_settings().redis_url)
        except (RedisError, OSError) as exc:
            raise ServiceUnavailableError(f"the job queue is not available: {exc}") from exc
        request.app.state.queue = queue
    return queue
