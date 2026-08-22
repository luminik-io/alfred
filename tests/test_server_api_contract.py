"""Contract tests for the versioned ``alfred serve`` API surface."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB = REPO_ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from fastapi.testclient import TestClient  # noqa: E402
from server import FilesystemReader, create_app  # noqa: E402


def _client(tmp_path: Path) -> tuple[TestClient, object]:
    app = create_app(FilesystemReader(state_root=tmp_path / "state"))
    return TestClient(app), app


def test_v1_meta_declares_the_local_api_contract(tmp_path: Path) -> None:
    client, app = _client(tmp_path)

    response = client.get("/api/v1/meta")

    assert response.status_code == 200
    assert response.json() == {
        "api_version": "1",
        "service": "alfred-serve",
        "scope": "localhost",
        "mutation_token_header": "X-Alfred-Token",
    }
    assert response.headers["X-Alfred-API-Version"] == "1"
    assert response.headers["Cache-Control"] == "no-store"
    assert app.version == "1.0"


def test_v1_unknown_route_has_a_stable_json_error(tmp_path: Path) -> None:
    client, _app = _client(tmp_path)

    response = client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["X-Alfred-API-Version"] == "1"
    assert response.json() == {
        "api_version": "1",
        "error": {
            "code": "not_found",
            "message": "API route not found",
        },
    }


def test_v1_status_exposes_the_fleet_status_contract(tmp_path: Path) -> None:
    client, _app = _client(tmp_path)

    response = client.get("/api/v1/status")

    assert response.status_code == 200
    assert response.headers["X-Alfred-API-Version"] == "1"
    assert set(response.json()) == {
        "agents",
        "total_today",
        "reliability",
        "metrics",
        "intake_profile",
        "setup_repos",
    }


def test_v1_wrong_method_has_a_stable_json_error(tmp_path: Path) -> None:
    client, _app = _client(tmp_path)

    response = client.post("/api/v1/meta")

    assert response.status_code == 405
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["X-Alfred-API-Version"] == "1"
    assert response.json() == {
        "api_version": "1",
        "error": {
            "code": "method_not_allowed",
            "message": "HTTP method not allowed",
        },
    }
