from fastapi.testclient import TestClient

from doc_extractor_api import __version__


def test_health_returns_ok_and_version(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_response_includes_generated_request_id(client: TestClient) -> None:
    response = client.get("/health")

    assert len(response.headers["X-Request-ID"]) == 32


def test_incoming_request_id_is_propagated(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "abc-123"})

    assert response.headers["X-Request-ID"] == "abc-123"
