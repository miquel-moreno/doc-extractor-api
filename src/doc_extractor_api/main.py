"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from doc_extractor_api import __version__
from doc_extractor_api.adapters.db import make_engine, make_session_factory
from doc_extractor_api.api import demo, documents, health, jobs
from doc_extractor_api.api.middleware import request_id_middleware
from doc_extractor_api.core.config import get_settings
from doc_extractor_api.core.errors import register_error_handlers
from doc_extractor_api.core.logging import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # One connection pool for the whole process, closed on shutdown.
    engine = make_engine(get_settings().database_url)
    app.state.session_factory = make_session_factory(engine)
    app.state.queue = None
    yield
    if app.state.queue is not None:
        await app.state.queue.close()
    await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)
    app.middleware("http")(request_id_middleware)
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(documents.router)
    app.include_router(jobs.router)
    app.include_router(demo.router)
    return app


app = create_app()
