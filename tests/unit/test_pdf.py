from pathlib import Path

import pytest

from doc_extractor_api.adapters.pdf import PdfError, is_pdf, pdf_to_text

INVOICE_PDF = Path(__file__).resolve().parents[2] / "evals" / "dataset" / "invoice-001.pdf"


def test_detects_pdf_by_its_magic_bytes() -> None:
    assert is_pdf(b"%PDF-1.4 ...")
    assert not is_pdf(b"Hola, os paso el pedido")


def test_extracts_the_text_of_a_pdf() -> None:
    text = pdf_to_text(INVOICE_PDF.read_bytes())

    assert "FACTURA" in text
    assert text == text.strip()


def test_broken_pdf_raises_pdf_error() -> None:
    with pytest.raises(PdfError):
        pdf_to_text(b"%PDF-1.4 this is not a real pdf")
