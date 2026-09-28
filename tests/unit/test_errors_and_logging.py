import json
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from doc_extractor_api.core.errors import NotFoundError, register_error_handlers
from doc_extractor_api.core.logging import JsonFormatter, request_id_var


def test_app_error_is_returned_as_json() -> None:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/missing")
    async def missing() -> None:
        raise NotFoundError("Document 42 not found")

    response = TestClient(app).get("/missing")

    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Document 42 not found"}}


def test_json_formatter_includes_request_id() -> None:
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    token = request_id_var.set("req-1")
    try:
        line = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)

    assert line["msg"] == "hello world"
    assert line["request_id"] == "req-1"
    assert line["level"] == "INFO"
