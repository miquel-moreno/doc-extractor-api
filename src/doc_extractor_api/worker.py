"""arq worker: takes job ids from Redis and processes them.

arq doc_extractor_api.worker.WorkerSettings
"""

import logging
from typing import Any, ClassVar

from arq import Retry

from doc_extractor_api.adapters.db import make_engine, make_session_factory
from doc_extractor_api.adapters.llm import build_llm_client
from doc_extractor_api.adapters.queue import redis_settings
from doc_extractor_api.core.config import get_settings
from doc_extractor_api.core.logging import configure_logging
from doc_extractor_api.services.jobs import MAX_JOB_ATTEMPTS, RetryLater, run_job

RETRY_DELAY_SECONDS = 10


async def process_job(ctx: dict[str, Any], job_id: str) -> str | None:
    async with ctx["session_factory"]() as session:
        try:
            status = await run_job(job_id, session=session, llm=ctx["llm"])
        except RetryLater as exc:
            # Back off: 10 s, then 20 s, ...
            raise Retry(defer=RETRY_DELAY_SECONDS * exc.attempt) from exc
    return None if status is None else status.value


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    # arq installs its own plain-text handler: keep only our JSON logs.
    logging.getLogger("arq").handlers.clear()
    ctx["engine"] = make_engine(settings.database_url)
    ctx["session_factory"] = make_session_factory(ctx["engine"])
    # Fail fast: a worker without a usable LLM configuration should not start.
    ctx["llm"] = build_llm_client(settings)


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["engine"].dispose()


class WorkerSettings:
    functions: ClassVar[list[Any]] = [process_job]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = redis_settings(get_settings().redis_url)
    max_tries = MAX_JOB_ATTEMPTS
