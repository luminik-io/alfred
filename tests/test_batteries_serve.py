"""Serve endpoint + setup-module tests for the battery picker.

Covers the shared manifest surfaced at GET /api/setup/batteries and the
token-gated POST that writes a battery's env flag(s) to $ALFRED_HOME/.env and
mirrors them into the live process.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB = REPO_ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import server.views as server_views  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from server import FilesystemReader, create_app  # noqa: E402
from server import setup as setup_mod  # noqa: E402


def _server_token(state: Path) -> str:
    return (state / "server-token").read_text(encoding="utf-8").strip()


def _auth_headers(state: Path) -> dict[str, str]:
    return {
        server_views.SERVER_TOKEN_HEADER: _server_token(state),
        "origin": "http://testserver",
    }


@pytest.fixture
def alfred_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / ".alfred"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ALFRED_HOME", str(home))
    # Deterministic detection so tests never depend on the host.
    import batteries

    monkeypatch.setattr(batteries, "_ams_reachable", lambda env: False)
    monkeypatch.setattr(batteries, "_code_memory_binary", lambda env: False)
    monkeypatch.setattr(batteries, "_headroom_available", lambda env: False)
    monkeypatch.setattr(batteries, "_find_spec", lambda name: False)
    return home


# --------------------------------------------------------------------------- #
# setup module logic
# --------------------------------------------------------------------------- #
def test_battery_manifest_is_read_only(alfred_home: Path) -> None:
    payload = setup_mod.battery_manifest()
    assert payload["summary"]["included"] == 4
    ids = {row["id"] for row in payload["batteries"]}
    assert "redis-ams" in ids and "sqlite-memory" in ids
    # Nothing written by a read.
    assert not (alfred_home / ".env").exists()


def test_set_battery_writes_env_and_mirrors_process(
    alfred_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ALFRED_MEMORY_SQLITE_DENSE", raising=False)
    result = setup_mod.set_battery("dense-embeddings", enabled=True)
    assert result["ok"] is True
    assert result["keys"] == ["ALFRED_MEMORY_SQLITE_DENSE"]
    env_text = (alfred_home / ".env").read_text(encoding="utf-8")
    assert "ALFRED_MEMORY_SQLITE_DENSE=1" in env_text
    import os

    assert os.environ["ALFRED_MEMORY_SQLITE_DENSE"] == "1"


def test_set_battery_rejects_builtin_and_unknown(alfred_home: Path) -> None:
    with pytest.raises(ValueError):
        setup_mod.set_battery("sqlite-memory", enabled=True)
    with pytest.raises(ValueError):
        setup_mod.set_battery("does-not-exist", enabled=True)


# --------------------------------------------------------------------------- #
# HTTP routes
# --------------------------------------------------------------------------- #
def test_get_batteries_endpoint(alfred_home: Path, tmp_path: Path) -> None:
    state = tmp_path / "state"
    client = TestClient(create_app(FilesystemReader(state_root=state)))
    resp = client.get("/api/setup/batteries")
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == 1
    assert body["summary"]["total"] == len(body["batteries"])


def test_post_battery_requires_auth(alfred_home: Path, tmp_path: Path) -> None:
    state = tmp_path / "state"
    client = TestClient(create_app(FilesystemReader(state_root=state)))
    # No token -> forbidden, and nothing written.
    resp = client.post("/api/setup/batteries", json={"battery": "dense-embeddings"})
    assert resp.status_code == 403


def test_post_battery_enables_with_auth(alfred_home: Path, tmp_path: Path) -> None:
    state = tmp_path / "state"
    client = TestClient(create_app(FilesystemReader(state_root=state)))
    resp = client.post(
        "/api/setup/batteries",
        json={"battery": "dense-embeddings", "enabled": True},
        headers=_auth_headers(state),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["battery"] == "dense-embeddings"
    row = {r["id"]: r for r in body["manifest"]["batteries"]}["dense-embeddings"]
    assert row["status"] == "enabled"


def test_post_battery_rejects_builtin(alfred_home: Path, tmp_path: Path) -> None:
    state = tmp_path / "state"
    client = TestClient(create_app(FilesystemReader(state_root=state)))
    resp = client.post(
        "/api/setup/batteries",
        json={"battery": "sqlite-memory"},
        headers=_auth_headers(state),
    )
    assert resp.status_code == 400
