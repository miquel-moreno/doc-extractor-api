"""Queued processing: submit a document and get a job id back at once (202)."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile, status
from pydantic import BaseModel, field_validator
from redis.exceptions import RedisError

from doc_extractor_api.adapters.db import DocumentRecord, JobRecord
from doc_extractor_api.adapters.queue import JobQueue
from doc_extractor_api.api.dependencies import get_queue
from doc_extractor_api.api.documents import DocumentOut, SessionDep, read_incoming
from doc_extractor_api.core.errors import NotFoundError, ServiceUnavailableError
from doc_extractor_api.services.jobs import JobStatus, create_job

router = APIRouter(tags=["jobs"])

QueueDep = Annotated[JobQueue, Depends(get_queue)]


class JobOut(BaseModel):
    id: str
    status: str
    filename: str | None
    media_type: str
    attempts: int
    error: str | None
    document: DocumentOut | None
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def _as_utc(cls, value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    @classmethod
    def from_records(cls, job: JobRecord, document: DocumentRecord | None) -> "JobOut":
        return cls(
            id=job.id,
            status=job.status,
            filename=job.filename,
            media_type=job.media_type,
            attempts=job.attempts,
            error=job.error,
            document=DocumentOut.from_record(document) if document else None,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )


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
