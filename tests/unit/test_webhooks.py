import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from arq import Retry
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from doc_extractor_api import worker
from doc_extractor_api.adapters.db import Base, make_session_factory
from doc_extractor_api.adapters.llm import FakeLLMClient
from doc_extractor_api.adapters.webhook import WebhookError, deliver
from doc_extractor_api.core.config import Settings
from doc_extractor_api.schemas import JobOut
from doc_extractor_api.services.jobs import create_job
from doc_extractor_api.services.processing import fingerprint
from doc_extractor_api.services.webhooks import build_payload, encode, sign, verify
from tests.factories import llm_answer

SECRET = "not-a-real-secret"


def settings(**values: Any) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


# --- signature -----------------------------------------------------------------


def test_signature_can_be_verified_and_detects_any_change() -> None:
    body = b'{"event":"job.finished"}'
    signature = sign(body, SECRET)

    assert signature.startswith("sha256=") and len(signature) == 7 + 64
    assert verify(body, SECRET, signature)
    assert not verify(b'{"event":"job.finished "}', SECRET, signature)  # body changed
    assert not verify(body, "another-secret", signature)  # wrong secret


# --- delivery ------------------------------------------------------------------


async def test_deliver_posts_the_exact_body_with_headers() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        seen["headers"] = dict(request.headers)
        return httpx.Response(204)

    await deliver(
        "https://hooks.example/n8n",
        b'{"a":1}',
        {"X-Webhook-Signature": "sha256=abc"},
        transport=httpx.MockTransport(handler),
    )

    assert seen["body"] == b'{"a":1}'
    assert seen["headers"]["content-type"] == "application/json"
    assert seen["headers"]["x-webhook-signature"] == "sha256=abc"


@pytest.mark.parametrize("answer", [500, 404, "network"])
async def test_deliver_raises_when_the_receiver_does_not_accept(answer: object) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if answer == "network":
            raise httpx.ConnectError("refused")
        return httpx.Response(int(str(answer)))

    with pytest.raises(WebhookError):
        await deliver(
            "https://hooks.example/n8n", b"{}", {}, transport=httpx.MockTransport(handler)
        )


# --- worker --------------------------------------------------------------------


class FakeRedis:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, str, str]] = []

    async def enqueue_job(self, name: str, job_id: str, *, _job_id: str) -> None:
        self.enqueued.append((name, job_id, _job_id))


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield make_session_factory(engine)
    await engine.dispose()


async def queued_job(factory: async_sessionmaker[AsyncSession]) -> str:
    async with factory() as session:
        job = await create_job(
            session,
            sha256=fingerprint(b"doc"),
            text="FACTURA",
            media_type="text/plain",
            filename=None,
        )
        return job.id


def context(factory: async_sessionmaker[AsyncSession], **values: Any) -> dict[str, Any]:
    return {
        "session_factory": factory,
        "llm": FakeLLMClient([llm_answer()]),
        "redis": FakeRedis(),
        "settings": settings(**values),
    }


async def test_finished_job_queues_a_webhook_when_configured(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    job_id = await queued_job(session_factory)
    ctx = context(session_factory, webhook_url="https://hooks.example/n8n")

    assert await worker.process_job(ctx, job_id) == "done"
    assert ctx["redis"].enqueued == [("send_webhook", job_id, f"webhook-{job_id}")]


async def test_no_webhook_when_not_configured(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    job_id = await queued_job(session_factory)
    ctx = context(session_factory)

    await worker.process_job(ctx, job_id)

    assert ctx["redis"].enqueued == []


async def test_send_webhook_posts_the_signed_job_result(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = await queued_job(session_factory)
    ctx = context(
        session_factory,
        webhook_url="https://hooks.example/n8n",
        webhook_secret=SecretStr(SECRET),
    )
    await worker.process_job(ctx, job_id)
    sent: dict[str, Any] = {}

    async def fake_deliver(url: str, body: bytes, headers: dict[str, str]) -> None:
        sent.update(url=url, body=body, headers=headers)

    monkeypatch.setattr(worker, "deliver", fake_deliver)

    assert await worker.send_webhook(ctx, job_id) == "sent"

    assert sent["url"] == "https://hooks.example/n8n"
    assert verify(sent["body"], SECRET, sent["headers"]["X-Webhook-Signature"])
    assert sent["headers"]["X-Webhook-Id"] == job_id
    payload = json.loads(sent["body"])
    assert payload["event"] == "job.finished"
    assert payload["job"]["status"] == "done"
    assert payload["job"]["document"]["data"]["total"] == "44.17"


async def test_failed_delivery_is_retried_with_backoff(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = await queued_job(session_factory)
    ctx = context(
        session_factory, webhook_url="https://hooks.example/n8n", webhook_secret=SecretStr(SECRET)
    )
    ctx["job_try"] = 2

    async def failing_deliver(url: str, body: bytes, headers: dict[str, str]) -> None:
        raise WebhookError("HTTP 500")

    monkeypatch.setattr(worker, "deliver", failing_deliver)

    with pytest.raises(Retry) as raised:
        await worker.send_webhook(ctx, job_id)

    assert raised.value.defer_score == worker.WEBHOOK_RETRY_DELAY_SECONDS * 2 * 1000


async def test_send_webhook_ignores_unknown_jobs_and_missing_config(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    configured = context(
        session_factory, webhook_url="https://hooks.example/n8n", webhook_secret=SecretStr(SECRET)
    )

    assert await worker.send_webhook(configured, "nope") is None
    assert await worker.send_webhook(context(session_factory), "nope") is None


@pytest.mark.parametrize("secret", [None, "", "change-me"])
def test_worker_refuses_to_start_with_an_unsigned_webhook(secret: str | None) -> None:
    values: dict[str, Any] = {"webhook_url": "https://hooks.example/n8n"}
    if secret is not None:
        values["webhook_secret"] = SecretStr(secret)

    with pytest.raises(ValueError, match="WEBHOOK_SECRET"):
        worker.check_webhook_settings(settings(**values))


def test_webhook_settings_are_optional() -> None:
    worker.check_webhook_settings(settings())  # no URL: nothing to check


def test_payload_is_the_job_as_returned_by_the_api() -> None:
    now = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
    job = JobOut(
        id="j1",
        status="failed",
        filename=None,
        media_type="text/plain",
        attempts=3,
        error="LLM down",
        document=None,
        created_at=now,
        updated_at=now,
    )

    payload = build_payload(job, sent_at=now)

    assert payload == {
        "event": "job.finished",
        "sent_at": "2026-09-28T12:00:00+00:00",
        "job": job.model_dump(mode="json"),
    }
    assert encode(payload) == encode(json.loads(encode(payload)))  # stable bytes
