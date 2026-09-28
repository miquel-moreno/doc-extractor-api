"""Process one document end to end: fingerprint, deduplicate, extract, validate, store.

Idempotent: the same content always returns the same stored record, and the LLM
is only paid for the first time. Nothing is lost: if extraction fails, the
document is stored as needs_review with the reason.
"""

import hashlib
from dataclasses import dataclass
from datetime import date

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from doc_extractor_api.adapters.db import DocumentRecord, get_by_sha256
from doc_extractor_api.adapters.llm import LLMClient
from doc_extractor_api.services.extraction import extract_document
from doc_extractor_api.services.validation import (
    IssueCode,
    ReviewStatus,
    ValidationIssue,
    validate_document,
)


@dataclass(frozen=True)
class ProcessedDocument:
    record: DocumentRecord
    created: bool  # False when the same content had already been processed


def fingerprint(content: bytes) -> str:
    """SHA-256 of the raw content: same file, same fingerprint."""
    return hashlib.sha256(content).hexdigest()


async def process_document(
    *,
    content: bytes,
    text: str,
    media_type: str,
    filename: str | None,
    session: AsyncSession,
    llm: LLMClient,
    today: date | None = None,
) -> ProcessedDocument:
    sha256 = fingerprint(content)
    if existing := await get_by_sha256(session, sha256):
        return ProcessedDocument(existing, created=False)

    # LLM provider errors (LLMError) propagate: nothing is stored, the caller can retry.
    result = await extract_document(text, llm)
    if result.document is not None:
        report = validate_document(result.document, today=today)
        status, issues = report.status, report.issues
        data = result.document.model_dump(mode="json")
    else:
        status = ReviewStatus.NEEDS_REVIEW
        issues = [
            ValidationIssue(
                code=IssueCode.EXTRACTION_FAILED,
                field="document",
                message=f"the model did not return valid data after "
                f"{result.attempts} attempts: {result.error}",
            )
        ]
        data = None

    record = DocumentRecord(
        sha256=sha256,
        filename=filename,
        media_type=media_type,
        status=status.value,
        data=data,
        issues=[issue.model_dump(mode="json") for issue in issues],
        model=result.model,
        attempts=result.attempts,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        latency_ms=result.latency_ms,
    )
    session.add(record)
    try:
        await session.commit()
    except IntegrityError:
        # The same document arrived twice at the same time and the other request
        # stored it first: the UNIQUE constraint on sha256 stops the duplicate.
        await session.rollback()
        existing = await get_by_sha256(session, sha256)
        if existing is None:  # pragma: no cover - the constraint that failed was not sha256
            raise
        return ProcessedDocument(existing, created=False)
    return ProcessedDocument(record, created=True)
