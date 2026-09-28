"""Document endpoints: submit a document, read one, list the review queue."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from doc_extractor_api.adapters.db import DocumentRecord, get_by_id, list_by_status
from doc_extractor_api.adapters.llm import LLMClient, LLMError
from doc_extractor_api.adapters.pdf import PdfError, is_pdf, pdf_to_text
from doc_extractor_api.api.dependencies import get_llm, get_session
from doc_extractor_api.core.errors import (
    NotFoundError,
    PayloadTooLargeError,
    ServiceUnavailableError,
    UnprocessableDocumentError,
    UnsupportedMediaTypeError,
)
from doc_extractor_api.services.processing import fingerprint, process_document
from doc_extractor_api.services.validation import ReviewStatus

router = APIRouter(tags=["documents"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
# Below this, a PDF has no real text layer (a scanned image): it would need OCR.
MIN_TEXT_CHARS = 20

SessionDep = Annotated[AsyncSession, Depends(get_session)]
LLMDep = Annotated[LLMClient, Depends(get_llm)]


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


@dataclass(frozen=True)
class IncomingDocument:
    sha256: str
    text: str
    media_type: str
    filename: str | None


async def read_incoming(file: UploadFile | None, text: str | None) -> IncomingDocument:
    """Validate the request and turn it into text. Shared by /extract and /jobs.

    Every input error is raised here, before any LLM call or queueing.
    """
    if (file is None) == (text is None):
        raise UnprocessableDocumentError("send exactly one of: a file or a text field")
    if file is not None:
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        filename = file.filename
    else:
        content = (text or "").encode("utf-8")
        filename = None
    if len(content) > MAX_UPLOAD_BYTES:
        raise PayloadTooLargeError(f"the maximum size is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    document_text, media_type = _read_input(content, filename)
    return IncomingDocument(fingerprint(content), document_text, media_type, filename)


def _read_input(content: bytes, filename: str | None) -> tuple[str, str]:
    """Return (text, media_type) for a PDF or a UTF-8 text document."""
    if is_pdf(content):
        try:
            text = pdf_to_text(content)
        except PdfError as exc:
            raise UnprocessableDocumentError(f"{filename or 'file'} is not a readable PDF") from exc
        if len(text.replace(" ", "").replace("\n", "")) < MIN_TEXT_CHARS:
            raise UnprocessableDocumentError(
                "the PDF has no text layer (scanned image?); OCR is not supported yet"
            )
        return text, "application/pdf"
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnsupportedMediaTypeError("send a PDF or a UTF-8 text file") from exc
    if not text.strip():
        raise UnprocessableDocumentError("the document is empty")
    return text, "text/plain"


@router.post(
    "/extract",
    status_code=status.HTTP_201_CREATED,
    responses={200: {"description": "Already processed: the stored result is returned"}},
)
async def extract(
    response: Response,
    session: SessionDep,
    llm: LLMDep,
    file: Annotated[UploadFile | None, File(description="PDF or UTF-8 text file")] = None,
    text: Annotated[str | None, Form(description="Plain text, e.g. an email body")] = None,
) -> DocumentOut:
    """Extract and validate a document. 201 if new, 200 if the same content was already sent."""
    incoming = await read_incoming(file, text)
    try:
        result = await process_document(
            sha256=incoming.sha256,
            text=incoming.text,
            media_type=incoming.media_type,
            filename=incoming.filename,
            session=session,
            llm=llm,
        )
    except LLMError as exc:
        raise ServiceUnavailableError(f"the LLM provider is not available: {exc}") from exc

    if not result.created:
        response.status_code = status.HTTP_200_OK
    return DocumentOut.from_record(result.record)


@router.get("/documents/{document_id}")
async def get_document(document_id: str, session: SessionDep) -> DocumentOut:
    record = await get_by_id(session, document_id)
    if record is None:
        raise NotFoundError(f"document {document_id} not found")
    return DocumentOut.from_record(record)


@router.get("/reviews")
async def list_reviews(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[DocumentOut]:
    """Documents waiting for a person to check them, newest first."""
    records = await list_by_status(
        session, ReviewStatus.NEEDS_REVIEW.value, limit=limit, offset=offset
    )
    return [DocumentOut.from_record(r) for r in records]
