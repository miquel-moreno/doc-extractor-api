from collections.abc import AsyncIterator

import httpx
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from doc_extractor_api.adapters.db import JobRecord
from doc_extractor_api.adapters.llm import FakeLLMClient
from doc_extractor_api.api.dependencies import get_queue, get_session
from doc_extractor_api.main import create_app
from doc_extractor_api.services.jobs import run_job
from tests.factories import llm_answer


class FakeQueue:
    def __init__(self, *, down: bool = False) -> None:
        self.ids: list[str] = []
        self.down = down

    async def enqueue(self, job_id: str) -> None:
        if self.down:
            raise RedisConnectionError("redis is down")
        self.ids.append(job_id)


@pytest.fixture
async def queue() -> FakeQueue:
    return FakeQueue()


@pytest.fixture
async def client(session: AsyncSession, queue: FakeQueue) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_queue] = lambda: queue
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def test_submit_returns_202_and_queues_the_job(
    client: httpx.AsyncClient, queue: FakeQueue
) -> None:
    response = await client.post("/jobs", data={"text": "Hola, os paso el pedido..."})

    assert response.status_code == 202
    job = response.json()
    assert job["status"] == "queued" and job["document"] is None
    assert response.headers["Location"] == f"/jobs/{job['id']}"
    assert queue.ids == [job["id"]]


async def test_job_result_is_available_after_the_worker_runs(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    job_id = (await client.post("/jobs", data={"text": "factura"})).json()["id"]

    await run_job(job_id, session=session, llm=FakeLLMClient([llm_answer()]))
    response = await client.get(f"/jobs/{job_id}")

    assert response.status_code == 200
    job = response.json()
    assert (job["status"], job["attempts"]) == ("done", 1)
    assert job["document"]["status"] == "valid"
    assert job["document"]["data"]["total"] == "44.17"


async def test_invalid_input_is_rejected_and_nothing_is_queued(
    client: httpx.AsyncClient, queue: FakeQueue
) -> None:
    response = await client.post("/jobs", data={"text": "   "})

    assert response.status_code == 422
    assert queue.ids == []


async def test_queue_down_returns_503_and_marks_the_job_failed(
    client: httpx.AsyncClient, queue: FakeQueue, session: AsyncSession
) -> None:
    queue.down = True

    response = await client.post("/jobs", data={"text": "factura"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"
    jobs = (await session.execute(select(JobRecord))).scalars().all()
    assert [(j.status, (j.error or "")[:17]) for j in jobs] == [("failed", "could not be queu")]


async def test_unknown_job_is_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/jobs/does-not-exist")

    assert response.status_code == 404
