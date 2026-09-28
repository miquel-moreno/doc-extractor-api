from collections.abc import AsyncIterator
from typing import Any

import pytest
from arq import Retry
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from doc_extractor_api import worker
from doc_extractor_api.adapters.db import Base, DocumentRecord, JobRecord, make_session_factory
from doc_extractor_api.adapters.llm import FakeLLMClient, LLMError
from doc_extractor_api.core.config import Settings
from doc_extractor_api.services.jobs import JobStatus, RetryLater, create_job, run_job
from doc_extractor_api.services.processing import fingerprint
from tests.factories import llm_answer


class DownLLM:
    async def chat(self, *args: object, **kwargs: object) -> None:
        raise LLMError("connection refused")


async def new_job(session: AsyncSession, content: bytes = b"doc") -> JobRecord:
    return await create_job(
        session,
        sha256=fingerprint(content),
        text="FACTURA ...",
        media_type="text/plain",
        filename=None,
    )


async def test_created_job_is_queued(session: AsyncSession) -> None:
    job = await new_job(session)

    assert (job.status, job.attempts, job.document_id) == ("queued", 0, None)


async def test_successful_job_links_the_document(session: AsyncSession) -> None:
    job = await new_job(session)

    status = await run_job(job.id, session=session, llm=FakeLLMClient([llm_answer()]))

    await session.refresh(job)
    assert status == JobStatus.DONE
    assert (job.status, job.attempts, job.error) == ("done", 1, None)
    document = await session.get(DocumentRecord, job.document_id)
    assert document is not None and document.status == "valid"


async def test_job_already_done_is_not_processed_again(session: AsyncSession) -> None:
    job = await new_job(session)
    await run_job(job.id, session=session, llm=FakeLLMClient([llm_answer()]))

    status = await run_job(job.id, session=session, llm=FakeLLMClient([]))  # no LLM call

    assert status == JobStatus.DONE


async def test_same_document_in_two_jobs_reuses_the_stored_result(session: AsyncSession) -> None:
    first, second = await new_job(session), await new_job(session)
    llm = FakeLLMClient([llm_answer()])

    await run_job(first.id, session=session, llm=llm)
    await run_job(second.id, session=session, llm=llm)

    assert len(llm.calls) == 1
    assert first.document_id == second.document_id


async def test_llm_down_requeues_while_attempts_are_left(session: AsyncSession) -> None:
    job = await new_job(session)

    with pytest.raises(RetryLater) as raised:
        await run_job(job.id, session=session, llm=DownLLM(), max_attempts=3)

    await session.refresh(job)
    assert raised.value.attempt == 1
    assert (job.status, job.attempts) == ("queued", 1)
    assert job.error is not None and "connection refused" in job.error


async def test_llm_down_on_the_last_attempt_fails_the_job(session: AsyncSession) -> None:
    job = await new_job(session)
    for _ in range(2):
        with pytest.raises(RetryLater):
            await run_job(job.id, session=session, llm=DownLLM(), max_attempts=3)

    status = await run_job(job.id, session=session, llm=DownLLM(), max_attempts=3)

    await session.refresh(job)
    assert status == JobStatus.FAILED
    assert (job.status, job.attempts) == ("failed", 3)
    assert job.document_id is None


async def test_unknown_job_returns_none(session: AsyncSession) -> None:
    assert await run_job("nope", session=session, llm=FakeLLMClient([])) is None


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield make_session_factory(engine)
    await engine.dispose()


async def test_worker_function_processes_the_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        job_id = (await new_job(session)).id
    ctx: dict[str, Any] = {
        "session_factory": session_factory,
        "llm": FakeLLMClient([llm_answer()]),
        "settings": Settings(_env_file=None),  # type: ignore[call-arg]
    }

    assert await worker.process_job(ctx, job_id) == "done"


async def test_worker_turns_retry_later_into_an_arq_retry_with_backoff(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        job_id = (await new_job(session)).id
    ctx: dict[str, Any] = {
        "session_factory": session_factory,
        "llm": DownLLM(),
        "settings": Settings(_env_file=None),  # type: ignore[call-arg]
    }

    with pytest.raises(Retry) as raised:
        await worker.process_job(ctx, job_id)

    assert raised.value.defer_score == worker.RETRY_DELAY_SECONDS * 1000


def test_worker_settings_point_to_the_job_function() -> None:
    assert [f.__name__ for f in worker.WorkerSettings.functions] == [
        "process_job",
        "send_webhook",
    ]
    assert worker.WorkerSettings.max_tries == 3
