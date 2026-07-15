from __future__ import annotations

import json
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


def _write_code_map(alfred_home: Path) -> None:
    state = alfred_home / "state"
    state.mkdir(parents=True)
    (state / "code-map.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-07-15T12:00:00Z",
                "repos": {
                    "web": {
                        "head_sha": "a" * 40,
                        "graph_summary": {
                            "files": 2,
                            "symbols": 2,
                            "imports": 1,
                            "languages": {"typescript": 2},
                            "truncated": False,
                        },
                        "files": [
                            {
                                "path": "src/App.tsx",
                                "language": "typescript",
                                "symbols": [{"name": "App", "line": 4}],
                                "imports": ["./api"],
                            },
                            {
                                "path": "src/api.ts",
                                "language": "typescript",
                                "symbols": [{"name": "loadData", "line": 8}],
                                "imports": [],
                            },
                        ],
                        "edges": [{"from": "src/App.tsx", "to": "./api", "kind": "import"}],
                        "api_calls": [{"method": "GET", "path": "/api/data", "file": "src/api.ts"}],
                    },
                    "worker": {
                        "head_sha": "b" * 40,
                        "graph_summary": {
                            "files": 1,
                            "symbols": 0,
                            "imports": 0,
                            "languages": {"python": 1},
                            "truncated": False,
                        },
                        "files": [],
                        "edges": [],
                    },
                },
                "contract_drift": [
                    {
                        "caller": "web",
                        "method": "GET",
                        "path": "/api/data",
                        "normalized": "/data",
                        "file": "src/api.ts",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_code_intelligence_lists_repos_and_analyzes_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALFRED_HOME", str(tmp_path))
    _write_code_map(tmp_path)
    client = TestClient(create_app(FilesystemReader(state_root=tmp_path / "state")))

    summary = client.get("/api/code-intelligence")
    assert summary.status_code == 200
    assert summary.json()["repos"][0]["name"] == "web"
    assert summary.json()["repos"][0]["contract_drift_count"] == 1
    assert summary.json()["repos"][1]["contract_drift_count"] == 0
    assert summary.json()["impact"] is None

    impact = client.get(
        "/api/code-intelligence",
        params={"repo": "web", "path": "src/api.ts"},
    )
    assert impact.status_code == 200
    body = impact.json()
    assert [repo["name"] for repo in body["repos"]] == ["web", "worker"]
    assert body["impact"]["match_status"] == "exact"
    assert body["impact"]["counts"]["direct_dependents"] == 1
    assert body["impact"]["contract_drift"][0]["normalized"] == "/data"


def test_code_intelligence_validates_scope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALFRED_HOME", str(tmp_path))
    _write_code_map(tmp_path)
    client = TestClient(create_app(FilesystemReader(state_root=tmp_path / "state")))

    missing_repo = client.get("/api/code-intelligence", params={"path": "src/api.ts"})
    assert missing_repo.status_code == 400

    unknown_repo = client.get("/api/code-intelligence", params={"repo": "missing"})
    assert unknown_repo.status_code == 404


def test_code_intelligence_uses_the_reader_state_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    unrelated_home = tmp_path / "unrelated-home"
    configured_home = tmp_path / "configured-home"
    monkeypatch.setenv("ALFRED_HOME", str(unrelated_home))
    _write_code_map(configured_home)
    client = TestClient(create_app(FilesystemReader(state_root=configured_home / "state")))

    response = client.get("/api/code-intelligence")

    assert response.status_code == 200
    assert [repo["name"] for repo in response.json()["repos"]] == ["web", "worker"]
