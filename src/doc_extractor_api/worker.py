"""arq worker: takes job ids from Redis, processes them and sends the webhook.

arq doc_extractor_api.worker.WorkerSettings
"""

import logging
from typing import Any, ClassVar

from arq import Retry

from doc_extractor_api.adapters.db import (
    DocumentRecord,
    JobRecord,
    make_engine,
    make_session_factory,
)
from doc_extractor_api.adapters.llm import PLACEHOLDER_KEY, build_llm_client
from doc_extractor_api.adapters.queue import redis_settings
from doc_extractor_api.adapters.webhook import WebhookError, deliver
from doc_extractor_api.core.config import Settings, get_settings
from doc_extractor_api.core.logging import configure_logging
from doc_extractor_api.schemas import JobOut
from doc_extractor_api.services.jobs import MAX_JOB_ATTEMPTS, RetryLater, run_job
from doc_extractor_api.services.webhooks import (
    EVENT_JOB_FINISHED,
    SIGNATURE_HEADER,
    build_payload,
    encode,
    sign,
)

logger = logging.getLogger(__name__)

RETRY_DELAY_SECONDS = 10
WEBHOOK_RETRY_DELAY_SECONDS = 5
SEND_WEBHOOK = "send_webhook"


async def process_job(ctx: dict[str, Any], job_id: str) -> str | None:
    async with ctx["session_factory"]() as session:
        try:
            status = await run_job(job_id, session=session, llm=ctx["llm"])
        except RetryLater as exc:
            # Back off: 10 s, then 20 s, ...
            raise Retry(defer=RETRY_DELAY_SECONDS * exc.attempt) from exc
    if status is None:
        return None
    if ctx["settings"].webhook_url:
        # A separate task: if the receiver is down, only the webhook is retried,
        # the document is not processed (nor paid) again.
        await ctx["redis"].enqueue_job(SEND_WEBHOOK, job_id, _job_id=f"webhook-{job_id}")
    return status.value


async def send_webhook(ctx: dict[str, Any], job_id: str) -> str | None:
    settings: Settings = ctx["settings"]
    if not settings.webhook_url or settings.webhook_secret is None:
        return None
    async with ctx["session_factory"]() as session:
        job = await session.get(JobRecord, job_id)
        if job is None:
            return None
        document = await session.get(DocumentRecord, job.document_id) if job.document_id else None
        body = encode(build_payload(JobOut.from_records(job, document)))
    headers = {
        SIGNATURE_HEADER: sign(body, settings.webhook_secret.get_secret_value()),
        "X-Webhook-Event": EVENT_JOB_FINISHED,
        "X-Webhook-Id": job_id,  # lets the receiver ignore a repeated delivery
    }
    try:
        await deliver(settings.webhook_url, body, headers)
    except WebhookError as exc:
        attempt = ctx.get("job_try", 1)
        logger.warning("webhook for job %s failed (attempt %s): %s", job_id, attempt, exc)
        raise Retry(defer=WEBHOOK_RETRY_DELAY_SECONDS * attempt) from exc
    return "sent"


def check_webhook_settings(settings: Settings) -> None:
    if not settings.webhook_url:
        return
    secret = settings.webhook_secret.get_secret_value() if settings.webhook_secret else ""
    if secret in ("", PLACEHOLDER_KEY):
        raise ValueError(
            "WEBHOOK_URL is set but WEBHOOK_SECRET is missing: webhooks must be signed"
        )


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    # arq installs its own plain-text handler: keep only our JSON logs.
    logging.getLogger("arq").handlers.clear()
    # Fail fast: a misconfigured worker should not start.
    check_webhook_settings(settings)
    ctx["settings"] = settings
    ctx["llm"] = build_llm_client(settings)
    ctx["engine"] = make_engine(settings.database_url)
    ctx["session_factory"] = make_session_factory(ctx["engine"])


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["engine"].dispose()


class WorkerSettings:
    functions: ClassVar[list[Any]] = [process_job, send_webhook]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = redis_settings(get_settings().redis_url)
    max_tries = MAX_JOB_ATTEMPTS
