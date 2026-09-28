import io
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from reportlab.pdfgen.canvas import Canvas
from sqlalchemy.ext.asyncio import AsyncSession

from doc_extractor_api.adapters.llm import FakeLLMClient, LLMClient, LLMError
from doc_extractor_api.api import documents
from doc_extractor_api.api.dependencies import get_llm, get_session
from doc_extractor_api.main import create_app
from tests.factories import llm_answer

DATASET = Path(__file__).resolve().parents[2] / "evals" / "dataset"
INVOICE_PDF = (DATASET / "invoice-001.pdf").read_bytes()


class Harness:
    def __init__(self, session: AsyncSession) -> None:
        self.app = create_app()
        self.llm: LLMClient = FakeLLMClient([])
        self.app.dependency_overrides[get_session] = lambda: session
        self.app.dependency_overrides[get_llm] = lambda: self.llm
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )

    def answers(self, *texts: str) -> FakeLLMClient:
        fake = FakeLLMClient(list(texts))
        self.llm = fake
        return fake


@pytest.fixture
async def api(session: AsyncSession) -> AsyncIterator[Harness]:
    harness = Harness(session)
    async with harness.client:
        yield harness


def blank_pdf() -> bytes:
    buffer = io.BytesIO()
    canvas = Canvas(buffer)
    canvas.rect(50, 50, 200, 200)  # a drawing, no text: like a scanned page
    canvas.showPage()
    canvas.save()
    return buffer.getvalue()


async def test_upload_pdf_returns_201_with_the_extracted_data(api: Harness) -> None:
    llm = api.answers(llm_answer())

    response = await api.client.post(
        "/extract", files={"file": ("factura.pdf", INVOICE_PDF, "application/pdf")}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "valid"
    assert body["media_type"] == "application/pdf"
    assert body["filename"] == "factura.pdf"
    assert body["data"]["total"] == "44.17"
    # The LLM received the text of the PDF, not the raw bytes.
    assert "FACTURA" in llm.calls[0][-1].content


async def test_same_document_twice_returns_200_and_the_same_record(api: Harness) -> None:
    llm = api.answers(llm_answer())
    files = {"file": ("factura.pdf", INVOICE_PDF, "application/pdf")}

    first = await api.client.post("/extract", files=files)
    second = await api.client.post("/extract", files=files)

    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id"] == second.json()["id"]
    assert len(llm.calls) == 1


async def test_email_text_can_be_sent_as_a_form_field(api: Harness) -> None:
    api.answers(llm_answer(document_type="order", subtotal=None, vat_amount=None, total=None))

    response = await api.client.post("/extract", data={"text": "Hola, os paso el pedido..."})

    assert response.status_code == 201
    assert response.json()["media_type"] == "text/plain"
    assert response.json()["filename"] is None


async def test_text_file_upload_is_accepted(api: Harness) -> None:
    api.answers(llm_answer())

    response = await api.client.post(
        "/extract", files={"file": ("pedido.txt", b"Pedido PED-1", "text/plain")}
    )

    assert response.status_code == 201
    assert response.json()["media_type"] == "text/plain"


async def test_documents_with_problems_appear_in_the_review_queue(api: Harness) -> None:
    api.answers(llm_answer(total=99.0), llm_answer(document_number="F-2"))
    bad = await api.client.post("/extract", data={"text": "factura con cuentas mal"})
    await api.client.post("/extract", data={"text": "factura correcta"})

    reviews = await api.client.get("/reviews")

    assert reviews.status_code == 200
    assert [d["id"] for d in reviews.json()] == [bad.json()["id"]]
    assert reviews.json()[0]["issues"][0]["code"] == "total_mismatch"


async def test_get_document_by_id(api: Harness) -> None:
    api.answers(llm_answer())
    created = (await api.client.post("/extract", data={"text": "factura"})).json()

    found = await api.client.get(f"/documents/{created['id']}")
    missing = await api.client.get("/documents/does-not-exist")

    assert found.status_code == 200 and found.json() == created
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize(
    ("kwargs", "status", "code"),
    [
        ({}, 422, "unprocessable_document"),
        (
            {"data": {"text": "x"}, "files": {"file": ("a.txt", b"y", "text/plain")}},
            422,
            "unprocessable_document",
        ),
        ({"data": {"text": "   "}}, 422, "unprocessable_document"),
        (
            {"files": {"file": ("a.bin", b"\xff\xfe\x00\x81", "application/octet-stream")}},
            415,
            "unsupported_media_type",
        ),
        (
            {"files": {"file": ("rota.pdf", b"%PDF-1.4 not really", "application/pdf")}},
            422,
            "unprocessable_document",
        ),
    ],
)
async def test_invalid_input_is_rejected_without_calling_the_llm(
    api: Harness, kwargs: dict[str, object], status: int, code: str
) -> None:
    llm = api.answers()

    response = await api.client.post("/extract", **kwargs)  # type: ignore[arg-type]

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert llm.calls == []


async def test_scanned_pdf_without_text_is_rejected(api: Harness) -> None:
    response = await api.client.post(
        "/extract", files={"file": ("escaneo.pdf", blank_pdf(), "application/pdf")}
    )

    assert response.status_code == 422
    assert "OCR" in response.json()["error"]["message"]


async def test_too_large_upload_is_rejected(api: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(documents, "MAX_UPLOAD_BYTES", 10)

    response = await api.client.post(
        "/extract", files={"file": ("big.txt", b"x" * 11, "text/plain")}
    )

    assert response.status_code == 413


async def test_llm_provider_down_returns_503_and_stores_nothing(api: Harness) -> None:
    class DownLLM:
        async def chat(self, *args: object, **kwargs: object) -> None:
            raise LLMError("connection refused")

    api.llm = DownLLM()  # type: ignore[assignment]

    response = await api.client.post("/extract", data={"text": "factura"})
    reviews = await api.client.get("/reviews")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"
    assert reviews.json() == []


async def test_missing_api_key_is_a_clear_503(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "change-me")
    from doc_extractor_api.core.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/extract", data={"text": "factura"})
    finally:
        get_settings.cache_clear()

    assert response.status_code == 503
    assert "OPENAI_API_KEY" in response.json()["error"]["message"]


async def test_review_queue_pagination_is_validated(api: Harness) -> None:
    assert (await api.client.get("/reviews?limit=0")).status_code == 422
    assert (await api.client.get("/reviews?limit=5&offset=0")).status_code == 200
