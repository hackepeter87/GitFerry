from fastapi.testclient import TestClient
from gitferry.api import app

client = TestClient(app)


def test_health_reports_iteration_status() -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "iteration": 1}


def test_blueprint_validation_returns_structured_errors() -> None:
    response = client.post(
        "/api/v1/blueprints/validate",
        content="apiVersion: gitferry.io/v99\nkind: TransferBlueprint\n",
        headers={"content-type": "application/yaml"},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["valid"] is False
    assert body["errors"]


def test_blueprint_validation_rejects_oversized_body_before_parsing() -> None:
    response = client.post(
        "/api/v1/blueprints/validate",
        content=b"x" * (1024 * 1024 + 1),
        headers={"content-type": "application/yaml"},
    )
    assert response.status_code == 413
    assert response.json() == {
        "valid": False,
        "errors": ["blueprint exceeds the 1 MiB input limit"],
    }
