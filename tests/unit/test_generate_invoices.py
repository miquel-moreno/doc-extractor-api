import json
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pypdf import PdfReader
from scripts.generate_invoices import (
    SyntheticDocument,
    date_long,
    generate_dataset,
    money_es,
    money_plain,
    tax_id_dashed,
)

from doc_extractor_api.services.document import DocumentType, ExtractedDocument
from doc_extractor_api.services.validation import ReviewStatus, validate_document

TODAY = date(2026, 9, 27)


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, list[SyntheticDocument]]:
    out = tmp_path_factory.mktemp("dataset")
    return out, generate_dataset(out, seed=1, invoices=6, delivery_notes=2, orders=4)


def pdf_text(path: Path) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(path).pages)


def test_writes_every_document_with_its_expected_json(
    dataset: tuple[Path, list[SyntheticDocument]],
) -> None:
    out, documents = dataset
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))

    assert len(manifest["documents"]) == len(documents) == 12
    for entry in manifest["documents"]:
        assert (out / entry["file"]).exists()
        expected = json.loads((out / entry["expected"]).read_text(encoding="utf-8"))
        assert ExtractedDocument.model_validate(expected).document_type == entry["document_type"]


def test_mix_of_document_types_and_layouts(dataset: tuple[Path, list[SyntheticDocument]]) -> None:
    _, documents = dataset

    assert Counter(d.document.document_type for d in documents) == {
        DocumentType.INVOICE: 6,
        DocumentType.DELIVERY_NOTE: 2,
        DocumentType.ORDER: 4,
    }
    invoice_layouts = Counter(
        d.layout for d in documents if d.document.document_type == DocumentType.INVOICE
    )
    assert invoice_layouts == {"classic": 2, "compact": 2, "modern": 2}


def test_every_expected_document_passes_business_rules(
    dataset: tuple[Path, list[SyntheticDocument]],
) -> None:
    _, documents = dataset

    for sd in documents:
        report = validate_document(sd.document, today=TODAY)
        assert report.status == ReviewStatus.VALID, (sd.doc_id, report.issues)


def test_money_always_has_two_decimals(dataset: tuple[Path, list[SyntheticDocument]]) -> None:
    out, documents = dataset

    for sd in documents:
        data = json.loads((out / f"{sd.doc_id}.json").read_text(encoding="utf-8"))
        values = [data["subtotal"], data["vat_amount"], data["total"]]
        values += [line[key] for line in data["lines"] for key in ("unit_price", "amount")]
        for value in filter(None, values):
            assert value.split(".")[1:] and len(value.split(".")[1]) == 2, (sd.doc_id, value)


def test_same_seed_produces_identical_files(tmp_path: Path) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    generate_dataset(first, seed=3, invoices=3, delivery_notes=1, orders=1)
    generate_dataset(second, seed=3, invoices=3, delivery_notes=1, orders=1)

    files = sorted(p.name for p in first.iterdir())
    assert files == sorted(p.name for p in second.iterdir())
    for name in files:
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


def test_pdfs_contain_the_key_data(dataset: tuple[Path, list[SyntheticDocument]]) -> None:
    out, documents = dataset

    for sd in (d for d in documents if d.filename.endswith(".pdf")):
        text = pdf_text(out / sd.filename)
        assert sd.document.document_number is not None
        assert sd.document.document_number in text, sd.doc_id
        for line in sd.document.lines:
            assert line.description in text, (sd.doc_id, line.description)
        if sd.document.total is not None:
            total = sd.document.total
            assert money_es(total) in text or money_plain(total) in text, sd.doc_id


def test_emails_contain_lines_and_only_mention_tax_id_when_expected(
    dataset: tuple[Path, list[SyntheticDocument]],
) -> None:
    out, documents = dataset

    for sd in (d for d in documents if d.layout == "email"):
        text = (out / sd.filename).read_text(encoding="utf-8")
        for line in sd.document.lines:
            assert line.description in text
        assert ("CIF:" in text) == (sd.document.issuer_tax_id is not None)


def test_text_files_use_lf_line_endings(dataset: tuple[Path, list[SyntheticDocument]]) -> None:
    out, _ = dataset

    for path in [*out.glob("*.json"), *out.glob("*.txt")]:
        assert b"\r\n" not in path.read_bytes(), path.name


def test_regenerating_removes_stale_files(tmp_path: Path) -> None:
    generate_dataset(tmp_path, seed=1, invoices=3, delivery_notes=0, orders=0)
    generate_dataset(tmp_path, seed=1, invoices=1, delivery_notes=0, orders=0)

    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "invoice-001.json",
        "invoice-001.pdf",
        "manifest.json",
    ]


@pytest.mark.parametrize(
    ("value", "expected"),
    [(Decimal("1234.5"), "1.234,50 €"), (Decimal("7.67"), "7,67 €")],
)
def test_money_es(value: Decimal, expected: str) -> None:
    assert money_es(value) == expected


def test_other_formats() -> None:
    assert money_plain(Decimal("44.17")) == "EUR 44.17"
    assert date_long(date(2026, 9, 1)) == "1 de septiembre de 2026"
    assert tax_id_dashed("B12345674") == "B-12345674"
    assert tax_id_dashed("12345678Z") == "12345678-Z"
