from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from doc_extractor_api.services.document import DocumentType, ExtractedDocument, LineItem
from doc_extractor_api.services.validation import (
    IssueCode,
    ReviewStatus,
    ValidationReport,
    validate_document,
)

TODAY = date(2026, 9, 27)


def make_invoice(**overrides: Any) -> ExtractedDocument:
    """A correct synthetic invoice: 2 x 10.00 + 3 x 5.50 = 36.50, VAT 21% = 7.67."""
    data: dict[str, Any] = {
        "document_type": DocumentType.INVOICE,
        "document_number": "F-2026-0001",
        "issuer_name": "Suministros Ejemplo S.L.",
        "issuer_tax_id": "B12345674",
        "issue_date": date(2026, 9, 1),
        "lines": [
            LineItem(
                description="Filtro de aceite",
                quantity=Decimal(2),
                unit_price=Decimal("10.00"),
                amount=Decimal("20.00"),
            ),
            LineItem(
                description="Bujía",
                quantity=Decimal(3),
                unit_price=Decimal("5.50"),
                amount=Decimal("16.50"),
            ),
        ],
        "subtotal": Decimal("36.50"),
        "vat_amount": Decimal("7.67"),
        "total": Decimal("44.17"),
    }
    data.update(overrides)
    return ExtractedDocument(**data)


def codes(report: ValidationReport) -> list[IssueCode]:
    return [issue.code for issue in report.issues]


def test_correct_invoice_is_valid() -> None:
    report = validate_document(make_invoice(), today=TODAY)

    assert report.status == ReviewStatus.VALID
    assert report.issues == []


def test_missing_required_fields_need_review() -> None:
    report = validate_document(make_invoice(issuer_tax_id=None, total=None), today=TODAY)

    assert report.status == ReviewStatus.NEEDS_REVIEW
    assert {i.field for i in report.issues if i.code == IssueCode.MISSING_FIELD} == {
        "issuer_tax_id",
        "total",
    }


def test_document_without_lines_needs_review() -> None:
    report = validate_document(make_invoice(lines=[]), today=TODAY)

    assert IssueCode.NO_LINES in codes(report)


def test_line_amount_must_match_quantity_times_price() -> None:
    bad_line = LineItem(
        description="Bujía",
        quantity=Decimal(3),
        unit_price=Decimal("5.50"),
        amount=Decimal("15.50"),
    )
    doc = make_invoice(lines=[make_invoice().lines[0], bad_line])

    report = validate_document(doc, today=TODAY)

    issue = next(i for i in report.issues if i.code == IssueCode.LINE_AMOUNT_MISMATCH)
    assert issue.field == "lines[1].amount"


def test_line_rounding_within_one_cent_is_accepted() -> None:
    line = LineItem(
        description="Tornillo",
        quantity=Decimal(3),
        unit_price=Decimal("0.333"),
        amount=Decimal("1.00"),
    )
    doc = make_invoice(
        lines=[line], subtotal=Decimal("1.00"), vat_amount=Decimal("0.21"), total=Decimal("1.21")
    )

    assert validate_document(doc, today=TODAY).status == ReviewStatus.VALID


def test_lines_must_add_up_to_subtotal() -> None:
    report = validate_document(
        make_invoice(subtotal=Decimal("40.00"), total=Decimal("47.67")), today=TODAY
    )

    assert codes(report) == [IssueCode.SUBTOTAL_MISMATCH]


def test_subtotal_plus_vat_must_equal_total() -> None:
    report = validate_document(make_invoice(total=Decimal("45.00")), today=TODAY)

    assert codes(report) == [IssueCode.TOTAL_MISMATCH]


def test_total_off_by_one_cent_is_accepted() -> None:
    report = validate_document(make_invoice(total=Decimal("44.18")), today=TODAY)

    assert report.status == ReviewStatus.VALID


def test_invalid_tax_id_needs_review() -> None:
    report = validate_document(make_invoice(issuer_tax_id="B12345675"), today=TODAY)

    assert codes(report) == [IssueCode.INVALID_TAX_ID]


@pytest.mark.parametrize(
    ("issue_date", "code"),
    [(date(2026, 9, 28), IssueCode.DATE_IN_FUTURE), (date(1999, 12, 31), IssueCode.DATE_TOO_OLD)],
)
def test_implausible_dates_need_review(issue_date: date, code: IssueCode) -> None:
    report = validate_document(make_invoice(issue_date=issue_date), today=TODAY)

    assert codes(report) == [code]


def test_low_confidence_fields_need_review() -> None:
    doc = make_invoice(confidence={"total": 0.95, "issuer_tax_id": 0.4})

    report = validate_document(doc, today=TODAY)

    assert [(i.code, i.field) for i in report.issues] == [
        (IssueCode.LOW_CONFIDENCE, "issuer_tax_id")
    ]


def test_delivery_note_without_prices_is_valid() -> None:
    doc = ExtractedDocument(
        document_type=DocumentType.DELIVERY_NOTE,
        issuer_name="Suministros Ejemplo S.L.",
        issue_date=date(2026, 9, 1),
        lines=[LineItem(description="Neumático 205/55 R16", quantity=Decimal(4))],
    )

    assert validate_document(doc, today=TODAY).status == ReviewStatus.VALID


def test_several_problems_are_all_reported() -> None:
    doc = make_invoice(issuer_tax_id="12345678A", total=Decimal("99.00"), issuer_name=None)

    assert set(codes(validate_document(doc, today=TODAY))) == {
        IssueCode.MISSING_FIELD,
        IssueCode.TOTAL_MISMATCH,
        IssueCode.INVALID_TAX_ID,
    }


def test_schema_rejects_confidence_out_of_range() -> None:
    with pytest.raises(ValidationError):
        make_invoice(confidence={"total": 1.5})


def test_schema_rejects_non_positive_quantity() -> None:
    with pytest.raises(ValidationError):
        LineItem(description="Filtro", quantity=Decimal(0))
