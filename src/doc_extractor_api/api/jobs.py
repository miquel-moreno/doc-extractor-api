"""Queued processing: submit a document and get a job id back at once (202)."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile, status
from redis.exceptions import RedisError

from doc_extractor_api.adapters.db import DocumentRecord, JobRecord
from doc_extractor_api.adapters.queue import JobQueue
from doc_extractor_api.api.dependencies import get_queue
from doc_extractor_api.api.documents import SessionDep, read_incoming
from doc_extractor_api.core.errors import NotFoundError, ServiceUnavailableError
from doc_extractor_api.schemas import JobOut
from doc_extractor_api.services.jobs import JobStatus, create_job

router = APIRouter(tags=["jobs"])

QueueDep = Annotated[JobQueue, Depends(get_queue)]


@router.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
async def submit_job(
    response: Response,
    session: SessionDep,
    queue: QueueDep,
    file: Annotated[UploadFile | None, File(description="PDF or UTF-8 text file")] = None,
    text: Annotated[str | None, Form(description="Plain text, e.g. an email body")] = None,
) -> JobOut:
    """Queue a document for processing. Poll GET /jobs/{id} for the result."""
    incoming = await read_incoming(file, text)
    job = await create_job(
        session,
        sha256=incoming.sha256,
        text=incoming.text,
        media_type=incoming.media_type,
        filename=incoming.filename,
    )
    try:
        await queue.enqueue(job.id)
    except (RedisError, OSError) as exc:
        # Never leave a "queued" job that no worker will ever see.
        job.status = JobStatus.FAILED.value
        job.error = f"could not be queued: {exc}"
        await session.commit()
        raise ServiceUnavailableError(f"the job queue is not available: {exc}") from exc
    response.headers["Location"] = f"/jobs/{job.id}"
    return JobOut.from_records(job, None)


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, session: SessionDep) -> JobOut:
    job = await session.get(JobRecord, job_id)
    if job is None:
        raise NotFoundError(f"job {job_id} not found")
    document = await session.get(DocumentRecord, job.document_id) if job.document_id else None
    return JobOut.from_records(job, document)
