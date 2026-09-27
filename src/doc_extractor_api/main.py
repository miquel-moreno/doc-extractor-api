"""FastAPI application factory."""

from fastapi import FastAPI

from doc_extractor_api import __version__
from doc_extractor_api.api import health
from doc_extractor_api.api.middleware import request_id_middleware
from doc_extractor_api.core.config import get_settings
from doc_extractor_api.core.errors import register_error_handlers
from doc_extractor_api.core.logging import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title=settings.app_name, version=__version__)
    app.middleware("http")(request_id_middleware)
    register_error_handlers(app)
    app.include_router(health.router)
    return app


app = create_app()
