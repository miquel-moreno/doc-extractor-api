from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from doc_extractor_api.adapters.db import DocumentRecord, get_by_id, get_by_sha256, list_by_status

T0 = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def record(sha: str, status: str = "valid", minutes: int = 0) -> DocumentRecord:
    return DocumentRecord(
        sha256=sha.ljust(64, "0"),
        filename=None,
        media_type="text/plain",
        status=status,
        data=None,
        issues=[],
        model="fake",
        attempts=1,
        input_tokens=0,
        output_tokens=0,
        latency_ms=0.0,
        created_at=T0 + timedelta(minutes=minutes),
    )


async def test_ids_are_generated_and_records_found(session: AsyncSession) -> None:
    doc = record("a")
    session.add(doc)
    await session.commit()

    assert len(doc.id) == 36
    assert await get_by_id(session, doc.id) is doc
    assert await get_by_sha256(session, "a".ljust(64, "0")) is doc
    assert await get_by_id(session, "missing") is None
    assert await get_by_sha256(session, "b".ljust(64, "0")) is None


async def test_sha256_is_unique(session: AsyncSession) -> None:
    session.add_all([record("a"), record("a")])

    with pytest.raises(IntegrityError):
        await session.commit()


async def test_list_by_status_filters_and_returns_newest_first(session: AsyncSession) -> None:
    session.add_all(
        [
            record("a", "needs_review", minutes=0),
            record("b", "valid", minutes=1),
            record("c", "needs_review", minutes=2),
            record("d", "needs_review", minutes=3),
        ]
    )
    await session.commit()

    page = await list_by_status(session, "needs_review", limit=2)
    rest = await list_by_status(session, "needs_review", limit=2, offset=2)

    assert [r.sha256[0] for r in page] == ["d", "c"]
    assert [r.sha256[0] for r in rest] == ["a"]
