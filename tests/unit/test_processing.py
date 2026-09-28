from datetime import date
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from doc_extractor_api.adapters import db
from doc_extractor_api.adapters.llm import FakeLLMClient, LLMError
from doc_extractor_api.services import processing
from doc_extractor_api.services.processing import fingerprint, process_document
from tests.factories import llm_answer

TODAY = date(2026, 9, 28)
CONTENT = b"%PDF-1.4 fake invoice bytes"
TEXT = "FACTURA F-2026-0001 ..."


async def run(session: AsyncSession, llm: FakeLLMClient, content: bytes = CONTENT) -> Any:
    return await process_document(
        sha256=fingerprint(content),
        text=TEXT,
        media_type="application/pdf",
        filename="factura.pdf",
        session=session,
        llm=llm,
        today=TODAY,
    )


async def count(session: AsyncSession) -> int:
    return (await session.execute(select(func.count()).select_from(db.DocumentRecord))).scalar_one()


def test_fingerprint_depends_only_on_content() -> None:
    assert fingerprint(b"abc") == fingerprint(b"abc")
    assert fingerprint(b"abc") != fingerprint(b"abd")
    assert len(fingerprint(b"abc")) == 64


async def test_valid_document_is_stored_as_valid(session: AsyncSession) -> None:
    result = await run(session, FakeLLMClient([llm_answer()]))

    assert result.created
    record = result.record
    assert record.status == "valid"
    assert record.issues == []
    assert record.data is not None and record.data["total"] == "44.17"
    assert record.sha256 == fingerprint(CONTENT)
    assert (record.filename, record.media_type) == ("factura.pdf", "application/pdf")
    assert (record.model, record.attempts) == ("fake-model", 1)
    assert (record.input_tokens, record.output_tokens) == (100, 50)
    assert await count(session) == 1


async def test_same_content_is_not_processed_twice(session: AsyncSession) -> None:
    llm = FakeLLMClient([llm_answer()])  # only one answer: a second LLM call would fail

    first = await run(session, llm)
    second = await run(session, llm)

    assert first.created and not second.created
    assert second.record.id == first.record.id
    assert len(llm.calls) == 1
    assert await count(session) == 1


async def test_different_content_is_a_new_document(session: AsyncSession) -> None:
    llm = FakeLLMClient([llm_answer(), llm_answer(document_number="F-2026-0002")])

    await run(session, llm, content=b"one")
    await run(session, llm, content=b"two")

    assert await count(session) == 2


async def test_business_rule_violations_go_to_review(session: AsyncSession) -> None:
    result = await run(session, FakeLLMClient([llm_answer(total=99.0)]))

    assert result.record.status == "needs_review"
    assert [i["code"] for i in result.record.issues] == ["total_mismatch"]
    assert result.record.data is not None  # the extracted data is kept for the reviewer


async def test_failed_extraction_is_stored_for_review(session: AsyncSession) -> None:
    result = await run(session, FakeLLMClient(["not json", "still not json"]))

    record = result.record
    assert record.status == "needs_review"
    assert record.data is None
    assert record.attempts == 2
    assert record.issues[0]["code"] == "extraction_failed"
    assert "2 attempts" in record.issues[0]["message"]


async def test_llm_provider_error_stores_nothing(session: AsyncSession) -> None:
    with pytest.raises(LLMError):
        await run(session, FakeLLMClient([]))

    assert await count(session) == 0


async def test_simultaneous_duplicate_returns_the_stored_record(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Another request stores the same document between our lookup and our insert.
    first = await run(session, FakeLLMClient([llm_answer()]))
    real_lookup = processing.get_by_sha256
    calls = 0

    async def lookup_missing_once(s: AsyncSession, sha256: str) -> db.DocumentRecord | None:
        nonlocal calls
        calls += 1
        return None if calls == 1 else await real_lookup(s, sha256)

    monkeypatch.setattr(processing, "get_by_sha256", lookup_missing_once)

    second = await run(session, FakeLLMClient([llm_answer()]))

    assert not second.created
    assert second.record.id == first.record.id
    assert await count(session) == 1
