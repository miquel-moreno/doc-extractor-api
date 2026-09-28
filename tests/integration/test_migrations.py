from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from doc_extractor_api.adapters.db import Base

ROOT = Path(__file__).resolve().parents[2]


def alembic_config(url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configure_logger"] = False
    return config


def test_migrations_create_exactly_the_orm_schema(tmp_path: Path) -> None:
    url = f"sqlite:///{(tmp_path / 'db.sqlite').as_posix()}"
    command.upgrade(alembic_config(url), "head")

    engine = create_engine(url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()

    assert diff == [], "the ORM models changed without a migration"


def test_migrations_can_be_rolled_back(tmp_path: Path) -> None:
    url = f"sqlite:///{(tmp_path / 'db.sqlite').as_posix()}"
    config = alembic_config(url)

    command.upgrade(config, "head")
    command.downgrade(config, "base")

    engine = create_engine(url)
    assert "documents" not in inspect(engine).get_table_names()
    engine.dispose()
