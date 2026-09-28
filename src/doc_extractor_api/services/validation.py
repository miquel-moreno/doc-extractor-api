"""Business rules that decide whether an extracted document can be trusted.

Pure functions, no network: the LLM proposes, these rules check. Any issue
sends the document to human review instead of letting a wrong number through.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from pydantic import BaseModel

from doc_extractor_api.services.document import DocumentType, ExtractedDocument
from doc_extractor_api.services.tax_id import validate_tax_id

CENT = Decimal("0.01")
# Invoices round line by line, so totals may be off by a cent or two.
LINE_TOLERANCE = Decimal("0.01")
TOTAL_TOLERANCE = Decimal("0.02")
OLDEST_ACCEPTED_DATE = date(2000, 1, 1)
DEFAULT_CONFIDENCE_THRESHOLD = 0.8

REQUIRED_FIELDS: dict[DocumentType, tuple[str, ...]] = {
    DocumentType.INVOICE: (
        "issuer_name",
        "issuer_tax_id",
        "issue_date",
        "subtotal",
        "vat_amount",
        "total",
    ),
    DocumentType.DELIVERY_NOTE: ("issuer_name", "issue_date"),
    DocumentType.ORDER: ("issuer_name", "issue_date"),
}


class IssueCode(StrEnum):
    EXTRACTION_FAILED = "extraction_failed"
    MISSING_FIELD = "missing_field"
    NO_LINES = "no_lines"
    LINE_AMOUNT_MISMATCH = "line_amount_mismatch"
    SUBTOTAL_MISMATCH = "subtotal_mismatch"
    TOTAL_MISMATCH = "total_mismatch"
    INVALID_TAX_ID = "invalid_tax_id"
    DATE_IN_FUTURE = "date_in_future"
    DATE_TOO_OLD = "date_too_old"
    LOW_CONFIDENCE = "low_confidence"


class ReviewStatus(StrEnum):
    VALID = "valid"
    NEEDS_REVIEW = "needs_review"


class ValidationIssue(BaseModel):
    code: IssueCode
    field: str
    message: str


class ValidationReport(BaseModel):
    status: ReviewStatus
    issues: list[ValidationIssue]


def validate_document(
    doc: ExtractedDocument,
    *,
    today: date | None = None,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> ValidationReport:
    issues = [
        *_check_required_fields(doc),
        *_check_line_amounts(doc),
        *_check_subtotal(doc),
        *_check_total(doc),
        *_check_tax_id(doc),
        *_check_date(doc, today or date.today()),
        *_check_confidence(doc, confidence_threshold),
    ]
    status = ReviewStatus.NEEDS_REVIEW if issues else ReviewStatus.VALID
    return ValidationReport(status=status, issues=issues)


def _check_required_fields(doc: ExtractedDocument) -> list[ValidationIssue]:
    issues = [
        ValidationIssue(code=IssueCode.MISSING_FIELD, field=name, message=f"{name} is missing")
        for name in REQUIRED_FIELDS[doc.document_type]
        if getattr(doc, name) in (None, "")
    ]
    if not doc.lines:
        issues.append(
            ValidationIssue(code=IssueCode.NO_LINES, field="lines", message="document has no lines")
        )
    return issues


def _check_line_amounts(doc: ExtractedDocument) -> list[ValidationIssue]:
    issues = []
    for i, line in enumerate(doc.lines):
        if line.unit_price is None or line.amount is None:
            continue
        expected = (line.quantity * line.unit_price).quantize(CENT, rounding=ROUND_HALF_UP)
        if abs(expected - line.amount) > LINE_TOLERANCE:
            issues.append(
                ValidationIssue(
                    code=IssueCode.LINE_AMOUNT_MISMATCH,
                    field=f"lines[{i}].amount",
                    message=f"{line.quantity} x {line.unit_price} = {expected}, not {line.amount}",
                )
            )
    return issues


def _check_subtotal(doc: ExtractedDocument) -> list[ValidationIssue]:
    amounts = [line.amount for line in doc.lines]
    if doc.subtotal is None or not amounts or any(a is None for a in amounts):
        return []
    lines_sum = sum((a for a in amounts if a is not None), Decimal(0))
    if abs(lines_sum - doc.subtotal) <= TOTAL_TOLERANCE:
        return []
    return [
        ValidationIssue(
            code=IssueCode.SUBTOTAL_MISMATCH,
            field="subtotal",
            message=f"lines add up to {lines_sum}, but subtotal is {doc.subtotal}",
        )
    ]


def _check_total(doc: ExtractedDocument) -> list[ValidationIssue]:
    if doc.subtotal is None or doc.vat_amount is None or doc.total is None:
        return []
    expected = doc.subtotal + doc.vat_amount
    if abs(expected - doc.total) <= TOTAL_TOLERANCE:
        return []
    return [
        ValidationIssue(
            code=IssueCode.TOTAL_MISMATCH,
            field="total",
            message=f"subtotal + VAT = {expected}, but total is {doc.total}",
        )
    ]


def _check_tax_id(doc: ExtractedDocument) -> list[ValidationIssue]:
    if not doc.issuer_tax_id or validate_tax_id(doc.issuer_tax_id).is_valid:
        return []
    return [
        ValidationIssue(
            code=IssueCode.INVALID_TAX_ID,
            field="issuer_tax_id",
            message=f"{doc.issuer_tax_id} is not a valid NIF, NIE or CIF",
        )
    ]


def _check_date(doc: ExtractedDocument, today: date) -> list[ValidationIssue]:
    if doc.issue_date is None:
        return []
    if doc.issue_date > today:
        return [
            ValidationIssue(
                code=IssueCode.DATE_IN_FUTURE,
                field="issue_date",
                message=f"{doc.issue_date} is in the future",
            )
        ]
    if doc.issue_date < OLDEST_ACCEPTED_DATE:
        return [
            ValidationIssue(
                code=IssueCode.DATE_TOO_OLD,
                field="issue_date",
                message=f"{doc.issue_date} is before {OLDEST_ACCEPTED_DATE}",
            )
        ]
    return []


def _check_confidence(doc: ExtractedDocument, threshold: float) -> list[ValidationIssue]:
    # Only values the extractor actually filled in: an empty field that is required
    # is already reported as missing, and an empty optional one (no totals on a
    # delivery note) is correct.
    return [
        ValidationIssue(
            code=IssueCode.LOW_CONFIDENCE,
            field=name,
            message=f"confidence {score:.2f} is below {threshold:.2f}",
        )
        for name, score in sorted(doc.confidence.items())
        if score < threshold and getattr(doc, name, None) not in (None, "")
    ]
