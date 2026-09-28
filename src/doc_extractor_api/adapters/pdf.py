"""PDF to text with pypdf. Only PDFs with a text layer: scanned images would need OCR."""

import io

from pypdf import PdfReader
from pypdf.errors import PyPdfError

PDF_MAGIC = b"%PDF-"


class PdfError(Exception):
    """The file is not a readable PDF."""


def is_pdf(content: bytes) -> bool:
    return content.startswith(PDF_MAGIC)


def pdf_to_text(content: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(content))
        pages = [page.extract_text() or "" for page in reader.pages]
    except (PyPdfError, ValueError, KeyError, TypeError) as exc:
        raise PdfError(f"could not read the PDF: {exc}") from exc
    return "\n".join(pages).strip()
