from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from doc_extractor_api.adapters.db import Base, make_session_factory
from doc_extractor_api.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """A fresh in-memory SQLite database per test (no PostgreSQL needed in CI)."""
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with make_session_factory(engine)() as s:
        yield s
    await engine.dispose()
