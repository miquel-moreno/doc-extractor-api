"""Response schemas shared by the API and the worker (webhook payloads)."""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, field_validator

from doc_extractor_api.adapters.db import DocumentRecord, JobRecord


class DocumentOut(BaseModel):
    id: str
    status: str
    filename: str | None
    media_type: str
    data: dict[str, Any] | None
    issues: list[dict[str, Any]]
    model: str
    attempts: int
    input_tokens: int
    output_tokens: int
    latency_ms: float
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _as_utc(cls, value: datetime) -> datetime:
        # Stored in UTC; SQLite (tests) drops the timezone, PostgreSQL keeps it.
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    @classmethod
    def from_record(cls, record: DocumentRecord) -> "DocumentOut":
        return cls.model_validate(record, from_attributes=True)


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
