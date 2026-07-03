"""Tests for serving the built desktop React app as the browser UI.

``alfred serve`` serves the SAME built React app that the Tauri desktop shell
loads. These tests cover: the app is served at ``/`` with the per-launch token
injected, hashed assets and brand images resolve, an SPA deep-link falls back to
``index.html``, the JSON ``/api/*`` surface is never shadowed by the UI catch-all,
a "not built" placeholder is served when no build is present, and directory
traversal through the SPA fallback is refused.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from fastapi.testclient import TestClient  # noqa: E402
from server import (  # noqa: E402
    FilesystemReader,
    create_app,
    static_ui,
)


@pytest.fixture(autouse=True)
def _isolate_ui_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin the UI-dist resolution to the test's env so it never picks up the
    in-repo build (which would shadow the fixture dist and the not-built path)."""
    monkeypatch.delenv(static_ui.UI_DIST_ENV, raising=False)
    monkeypatch.setenv("ALFRED_HOME", "/nonexistent-alfred-home")
    # Point the in-repo fallback at an empty dir so `resolve_ui_dist` returns
    # None unless a test sets the env override explicitly.
    monkeypatch.setattr(static_ui, "_repo_root", lambda: Path("/nonexistent-repo"))
    yield


def _write_dist(root: Path) -> Path:
    """Create a minimal Vite-shaped dist/ (index.html + assets + brand)."""
    dist = root / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "brand").mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html>\n<html><head>\n<title>Alfred</title></head>"
        '<body><div id="root"></div>'
        '<script type="module" src="/assets/index.js"></script></body></html>',
        encoding="utf-8",
    )
    (dist / "assets" / "index.js").write_text("console.log('alfred')", encoding="utf-8")
    (dist / "assets" / "index.css").write_text(":root{}", encoding="utf-8")
    (dist / "brand" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return dist


def _client(state: Path, dist: Path | None, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    if dist is not None:
        monkeypatch.setenv(static_ui.UI_DIST_ENV, str(dist))
    reader = FilesystemReader(state_root=state)
    app = create_app(reader)
    return TestClient(app)


def test_root_serves_react_app_with_injected_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    dist = _write_dist(tmp_path)
    client = _client(state, dist, monkeypatch)

    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert 'id="root"' in response.text
    token = (state / "server-token").read_text(encoding="utf-8").strip()
    assert token
    assert 'name="alfred-token"' in response.text
    assert token in response.text


def test_assets_and_brand_resolve(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = tmp_path / "state"
    state.mkdir()
    dist = _write_dist(tmp_path)
    client = _client(state, dist, monkeypatch)

    js = client.get("/assets/index.js")
    assert js.status_code == 200
    assert "alfred" in js.text

    logo = client.get("/brand/logo.png")
    assert logo.status_code == 200
    assert logo.content.startswith(b"\x89PNG")


def test_spa_deep_link_falls_back_to_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = tmp_path / "state"
    state.mkdir()
    dist = _write_dist(tmp_path)
    client = _client(state, dist, monkeypatch)

    response = client.get("/inbox/some-plan")

    assert response.status_code == 200
    assert 'id="root"' in response.text


def test_api_routes_are_not_shadowed_by_ui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = tmp_path / "state"
    state.mkdir()
    dist = _write_dist(tmp_path)
    client = _client(state, dist, monkeypatch)

    status = client.get("/api/status")
    assert status.status_code == 200
    assert status.headers["content-type"].startswith("application/json")

    # An unknown API path must 404 as an API miss, not fall through to the HTML
    # SPA page.
    missing = client.get("/api/does-not-exist")
    assert missing.status_code == 404
    assert 'id="root"' not in missing.text

    # The liveness probe stays plain text.
    assert client.get("/healthz").text == "ok"


def test_not_built_placeholder_when_no_dist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    client = _client(state, None, monkeypatch)

    response = client.get("/")

    assert response.status_code == 200
    assert "not built yet" in response.text
    # No token is injected into the placeholder (it has no app to authorize).
    assert 'name="alfred-token"' not in response.text
    # The API still works with no UI build present.
    assert client.get("/api/status").status_code == 200


def test_spa_fallback_refuses_directory_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    dist = _write_dist(tmp_path)
    # A secret next to (but outside) dist/ must never be served through the
    # SPA fallback's real-file branch.
    (tmp_path / "secret.txt").write_text("do-not-serve", encoding="utf-8")
    client = _client(state, dist, monkeypatch)

    response = client.get("/..%2f..%2fsecret.txt")

    # Whatever the router does with the encoded path, the secret is never
    # returned; a refused traversal falls back to index.html.
    assert "do-not-serve" not in response.text


def test_mutation_still_requires_token_when_ui_is_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Serving the browser UI must not weaken auth: a mutation without the
    injected token is still rejected."""
    state = tmp_path / "state"
    state.mkdir()
    dist = _write_dist(tmp_path)
    client = _client(state, dist, monkeypatch)

    denied = client.post("/api/roster-theme", json={"theme": "noir"})
    assert denied.status_code == 403


def test_resolve_ui_dist_prefers_env_override(tmp_path: Path, monkeypatch) -> None:
    dist = _write_dist(tmp_path)
    monkeypatch.setenv(static_ui.UI_DIST_ENV, str(dist))
    assert static_ui.resolve_ui_dist() == dist

    # A directory without index.html is not a valid build.
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv(static_ui.UI_DIST_ENV, str(empty))
    # Falls through to the in-repo build (which may or may not exist); the point
    # is the invalid override is not returned.
    assert static_ui.resolve_ui_dist() != empty
