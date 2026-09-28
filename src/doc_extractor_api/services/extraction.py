"""Ask the LLM to fill the document schema; if the answer is invalid, retry once with the errors.

The LLM fills a simple, LLM-friendly schema (LLMExtraction: plain numbers, ISO date
as text, fixed confidence fields). Our code converts it into the domain model
(ExtractedDocument, with Decimal money). Business rules are checked later by
services.validation: the LLM proposes, the rules decide.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from doc_extractor_api.adapters.llm import ChatMessage, LLMClient, strict_json_schema
from doc_extractor_api.services.document import DocumentType, ExtractedDocument

DEFAULT_MAX_ATTEMPTS = 2
MAX_ERRORS_IN_RETRY = 10

SYSTEM_PROMPT = """\
You extract data from Spanish business documents: invoices, delivery notes and \
purchase orders received by email. Answer with JSON that matches the schema.

Rules:
- Only use data that appears in the document. If a field is not there, use null. \
Never invent, guess or calculate missing values.
- The issuer is who issues or sends the document. Never confuse the issuer with \
the customer or the recipient.
- In an order email, issuer_name is the company (or self-employed person) placing \
the order, usually in the signature, not the first name of whoever writes the email.
- issuer_tax_id: the issuer's Spanish NIF, NIE or CIF, without spaces, dots, dashes \
or the "ES" prefix.
- issue_date: format YYYY-MM-DD.
- Amounts: plain numbers with a dot as decimal separator and no currency symbol \
("1.234,56 €" -> 1234.56, "EUR 1234.56" -> 1234.56).
- lines: one item per product line, in the same order. quantity is the number of \
units. unit_price and amount are null when the document shows no prices.
- subtotal is the tax base before VAT, vat_amount is the VAT amount (not the rate) \
and total includes VAT.
- confidence: for each field, how sure you are that the value is correct, from 0 \
to 1. Use a low value when the field is missing, ambiguous or hard to read.
"""

RETRY_PROMPT = """\
Your previous answer was not valid:
{errors}
Return the corrected JSON for the same document."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LLMLineItem(_Strict):
    description: str
    quantity: float
    unit_price: float | None
    amount: float | None


class LLMConfidence(_Strict):
    document_number: float
    issuer_name: float
    issuer_tax_id: float
    issue_date: float
    subtotal: float
    vat_amount: float
    total: float


class LLMExtraction(_Strict):
    """What the LLM must return. Kept simple so small models can fill it."""

    document_type: DocumentType
    document_number: str | None
    issuer_name: str | None
    issuer_tax_id: str | None
    issue_date: str | None
    lines: list[LLMLineItem]
    subtotal: float | None
    vat_amount: float | None
    total: float | None
    confidence: LLMConfidence

    def to_document(self) -> ExtractedDocument:
        """Convert to the domain model. Raises ValidationError if a value is unusable."""
        return ExtractedDocument.model_validate(
            {
                "document_type": self.document_type,
                "document_number": self.document_number,
                "issuer_name": self.issuer_name,
                "issuer_tax_id": self.issuer_tax_id,
                "issue_date": self.issue_date,
                "lines": [
                    {
                        "description": line.description,
                        "quantity": _decimal(line.quantity),
                        "unit_price": _decimal(line.unit_price),
                        "amount": _decimal(line.amount),
                    }
                    for line in self.lines
                ],
                "subtotal": _decimal(self.subtotal),
                "vat_amount": _decimal(self.vat_amount),
                "total": _decimal(self.total),
                "confidence": self.confidence.model_dump(),
            }
        )


EXTRACTION_SCHEMA: dict[str, Any] = strict_json_schema(LLMExtraction)


def _decimal(value: float | None) -> Decimal | None:
    # str() first: Decimal(0.1) would keep the binary float error, Decimal("0.1") does not.
    return None if value is None else Decimal(str(value))


@dataclass(frozen=True)
class ExtractionResult:
    document: ExtractedDocument | None
    attempts: int
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.document is not None


def build_messages(text: str) -> list[ChatMessage]:
    return [
        ChatMessage("system", SYSTEM_PROMPT),
        ChatMessage("user", f"Document:\n<<<\n{text.strip()}\n>>>"),
    ]


def summarize_errors(exc: ValidationError) -> str:
    lines = []
    for error in exc.errors()[:MAX_ERRORS_IN_RETRY]:
        location = ".".join(str(part) for part in error["loc"]) or "(root)"
        lines.append(f"- {location}: {error['msg']}")
    return "\n".join(lines)


async def extract_document(
    text: str, llm: LLMClient, *, max_attempts: int = DEFAULT_MAX_ATTEMPTS
) -> ExtractionResult:
    messages = build_messages(text)
    input_tokens = output_tokens = 0
    latency_ms = 0.0
    model = ""
    error = ""

    for attempt in range(1, max_attempts + 1):
        response = await llm.chat(
            messages, json_schema=EXTRACTION_SCHEMA, schema_name="extracted_document"
        )
        model = response.model
        input_tokens += response.input_tokens
        output_tokens += response.output_tokens
        latency_ms += response.latency_ms
        try:
            document = LLMExtraction.model_validate_json(response.text).to_document()
        except ValidationError as exc:
            error = summarize_errors(exc)
            messages = [
                *messages,
                ChatMessage("assistant", response.text),
                ChatMessage("user", RETRY_PROMPT.format(errors=error)),
            ]
            continue
        return ExtractionResult(document, attempt, model, input_tokens, output_tokens, latency_ms)

    return ExtractionResult(
        None, max_attempts, model, input_tokens, output_tokens, latency_ms, error=error
    )
