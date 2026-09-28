"""Queued processing: the API creates a job, the worker runs `run_job`.

The job row in PostgreSQL is the source of truth for its status; the queue
(Redis) only carries the job id. Running the same job twice is harmless.
"""

from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from doc_extractor_api.adapters.db import JobRecord
from doc_extractor_api.adapters.llm import LLMClient, LLMError
from doc_extractor_api.services.processing import process_document

MAX_JOB_ATTEMPTS = 3


class JobStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class RetryLater(Exception):  # noqa: N818 - it is a signal, not an error
    """The LLM provider failed but the job has attempts left: requeue it."""

    def __init__(self, attempt: int) -> None:
        super().__init__(f"attempt {attempt} failed, retrying later")
        self.attempt = attempt


async def create_job(
    session: AsyncSession, *, sha256: str, text: str, media_type: str, filename: str | None
) -> JobRecord:
    job = JobRecord(
        status=JobStatus.QUEUED.value,
        sha256=sha256,
        text=text,
        media_type=media_type,
        filename=filename,
        attempts=0,
    )
    session.add(job)
    await session.commit()
    return job


async def run_job(
    job_id: str,
    *,
    session: AsyncSession,
    llm: LLMClient,
    max_attempts: int = MAX_JOB_ATTEMPTS,
) -> JobStatus | None:
    """Process one job. Returns its final status, or None if it does not exist.

    Raises RetryLater when the LLM provider fails and attempts are left.
    """
    job = await session.get(JobRecord, job_id)
    if job is None:
        return None
    if job.status in (JobStatus.DONE, JobStatus.FAILED):
        return JobStatus(job.status)  # delivered twice: nothing to do

    job.status = JobStatus.PROCESSING.value
    job.attempts += 1
    await session.commit()

    try:
        result = await process_document(
            sha256=job.sha256,
            text=job.text,
            media_type=job.media_type,
            filename=job.filename,
            session=session,
            llm=llm,
        )
    except LLMError as exc:
        job.error = str(exc)
        if job.attempts < max_attempts:
            job.status = JobStatus.QUEUED.value
            await session.commit()
            raise RetryLater(job.attempts) from exc
        job.status = JobStatus.FAILED.value
        await session.commit()
        return JobStatus.FAILED

    job.status = JobStatus.DONE.value
    job.document_id = result.record.id
    job.error = None
    await session.commit()
    return JobStatus.DONE
