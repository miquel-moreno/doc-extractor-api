"""Database access: SQLAlchemy 2 async engine, ORM model and repository functions.

PostgreSQL in production (psycopg), SQLite (aiosqlite) in tests: the model only
uses portable column types (JSON instead of JSONB) so both work.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, Integer, String, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


class DocumentRecord(Base):
    """One processed document: its fingerprint, extracted data and review status."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # SHA-256 of the raw content. UNIQUE: the database itself refuses duplicates.
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    filename: Mapped[str | None] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), index=True)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    model: Mapped[str] = mapped_column(String(100))
    attempts: Mapped[int] = mapped_column(Integer)
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    latency_ms: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


def make_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_by_sha256(session: AsyncSession, sha256: str) -> DocumentRecord | None:
    result = await session.execute(select(DocumentRecord).where(DocumentRecord.sha256 == sha256))
    return result.scalar_one_or_none()


async def get_by_id(session: AsyncSession, document_id: str) -> DocumentRecord | None:
    return await session.get(DocumentRecord, document_id)


async def list_by_status(
    session: AsyncSession, status: str, *, limit: int = 50, offset: int = 0
) -> Sequence[DocumentRecord]:
    result = await session.execute(
        select(DocumentRecord)
        .where(DocumentRecord.status == status)
        .order_by(DocumentRecord.created_at.desc(), DocumentRecord.id)
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()
