"""Compare an extracted document with its expected JSON and aggregate the metrics.

Pure functions (no LLM, no files), so they are unit tested like the rest of the code.
"""

import statistics
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from doc_extractor_api.services.tax_id import normalize_tax_id

FIELDS = (
    "document_type",
    "document_number",
    "issuer_name",
    "issuer_tax_id",
    "issue_date",
    "subtotal",
    "vat_amount",
    "total",
)
MONEY_FIELDS = {"subtotal", "vat_amount", "total"}
TEXT_FIELDS = {"document_number", "issuer_name"}


def _text(value: Any) -> str:
    return " ".join(str(value).split()).casefold()


def _number(value: Any) -> Decimal | str:
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return str(value)  # not a number: compared as text, so it never matches a number


def normalize(field_name: str, value: Any) -> Any:
    """Make equivalent values equal: '57.0' == '57.00', 'ES-B1234...' == 'B1234...'."""
    if value is None or value == "":
        return None
    if field_name == "issuer_tax_id":
        return normalize_tax_id(str(value))
    if field_name in MONEY_FIELDS:
        return _number(value)
    if field_name in TEXT_FIELDS:
        return _text(value)
    return str(value)


def normalize_lines(lines: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    return [
        (
            _text(line.get("description", "")),
            _number(line.get("quantity")),
            None if line.get("unit_price") is None else _number(line["unit_price"]),
            None if line.get("amount") is None else _number(line["amount"]),
        )
        for line in lines
    ]


@dataclass(frozen=True)
class DocumentScore:
    fields: dict[str, bool]
    lines: bool

    @property
    def perfect(self) -> bool:
        return self.lines and all(self.fields.values())

    @property
    def wrong_fields(self) -> list[str]:
        wrong = [name for name, ok in self.fields.items() if not ok]
        return wrong if self.lines else [*wrong, "lines"]


def score_document(expected: dict[str, Any], got: dict[str, Any] | None) -> DocumentScore:
    if got is None:
        return DocumentScore(fields=dict.fromkeys(FIELDS, False), lines=False)
    fields = {
        name: normalize(name, got.get(name)) == normalize(name, expected.get(name))
        for name in FIELDS
    }
    lines = normalize_lines(got.get("lines", [])) == normalize_lines(expected.get("lines", []))
    return DocumentScore(fields=fields, lines=lines)


@dataclass(frozen=True)
class DocumentResult:
    doc_id: str
    document_type: str
    layout: str
    score: DocumentScore
    status: str  # "valid" or "needs_review"
    latency_ms: float
    input_tokens: int
    output_tokens: int
    attempts: int
    extraction_failed: bool = False
    issues: list[str] = field(default_factory=list)


def _pct(part: int, total: int) -> float:
    return round(100 * part / total, 1) if total else 0.0


def summarize(results: list[DocumentResult]) -> dict[str, Any]:
    n = len(results)
    perfect = [r for r in results if r.score.perfect]
    wrong = [r for r in results if not r.score.perfect]
    latencies = [r.latency_ms / 1000 for r in results]
    field_accuracy = {name: _pct(sum(r.score.fields[name] for r in results), n) for name in FIELDS}
    field_accuracy["lines"] = _pct(sum(r.score.lines for r in results), n)
    by_type: dict[str, dict[str, Any]] = {}
    for doc_type in sorted({r.document_type for r in results}):
        group = [r for r in results if r.document_type == doc_type]
        by_type[doc_type] = {
            "documents": len(group),
            "perfect": sum(r.score.perfect for r in group),
        }
    return {
        "documents": n,
        "perfect": len(perfect),
        "perfect_pct": _pct(len(perfect), n),
        "field_accuracy_pct": field_accuracy,
        "by_type": by_type,
        # Safety: a wrong document must never be marked as valid.
        "wrong_documents": len(wrong),
        "wrong_sent_to_review": sum(r.status == "needs_review" for r in wrong),
        "wrong_marked_valid": sum(r.status == "valid" for r in wrong),
        "correct_sent_to_review": sum(r.status == "needs_review" for r in perfect),
        "extraction_failures": sum(r.extraction_failed for r in results),
        "retried": sum(r.attempts > 1 for r in results),
        "seconds_per_document_mean": round(statistics.mean(latencies), 2) if latencies else 0,
        "seconds_per_document_median": round(statistics.median(latencies), 2) if latencies else 0,
        "input_tokens": sum(r.input_tokens for r in results),
        "output_tokens": sum(r.output_tokens for r in results),
    }
