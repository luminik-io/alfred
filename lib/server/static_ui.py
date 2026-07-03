"""Serve the built desktop React app as the browser UI for ``alfred serve``.

``alfred serve`` exposes a JSON ``/api/*`` surface. The desktop client
(``clients/desktop``) is a React app that talks to exactly that surface over
plain HTTP. Rather than ship a second, hand-maintained server-rendered
dashboard, ``alfred serve`` serves the SAME built React app as the browser UI.
One UI codebase, two shells: the Tauri native window and any browser.

Resolution of the built app (``dist/``):

1. ``ALFRED_SERVE_UI_DIST`` env var, if set and a directory.
2. ``$ALFRED_HOME/clients/desktop/dist`` (where ``deploy.sh`` ships it).
3. ``<repo>/clients/desktop/dist`` (a local ``npm run build`` in a checkout).

When no built app is found, ``/`` serves a small static page explaining how to
build it. Every ``/api/*`` route is untouched.

Auth model (unchanged from the retired Jinja dashboard): state-mutating POSTs
require the per-launch token via the ``X-Alfred-Token`` header. The desktop
(Tauri) shell injects it through its native bridge. When the app is served in a
browser BY ``alfred serve``, the server injects that same token into the served
``index.html`` as a ``<meta name="alfred-token">`` tag. A same-origin page can
read its own document and echo the token back on mutations; a cross-origin
drive-by page cannot read another origin's document (the same-origin policy),
and the token file stays ``0600`` on disk. This is the exact synchronizer-token
model the Jinja plan page used, carried over verbatim. It does not weaken auth.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from . import views

# Env override for the built-app directory. Points at a ``dist/`` folder that
# contains ``index.html`` plus hashed asset files.
UI_DIST_ENV = "ALFRED_SERVE_UI_DIST"

# The meta tag name the browser build reads to attach the per-launch token to
# mutating requests. Keep in sync with ``clients/desktop/src/api.ts``.
_TOKEN_META_NAME = "alfred-token"

# Marker so the token meta is injected at most once even if index.html already
# carries a placeholder from a future build step.
_TOKEN_META_MARKER = f'name="{_TOKEN_META_NAME}"'


def _repo_root() -> Path:
    # lib/server/static_ui.py -> lib/server -> lib -> <repo root>
    return Path(__file__).resolve().parent.parent.parent


def resolve_ui_dist() -> Path | None:
    """Resolve the built desktop app directory, or ``None`` if not present.

    Checks the env override first, then the deployed location under
    ``$ALFRED_HOME``, then the in-repo build. Only returns a path that exists
    and contains an ``index.html``.
    """
    candidates: list[Path] = []
    override = os.environ.get(UI_DIST_ENV, "").strip()
    if override:
        candidates.append(Path(override).expanduser())
    alfred_home = os.environ.get("ALFRED_HOME", "").strip()
    if alfred_home:
        candidates.append(Path(alfred_home).expanduser() / "clients" / "desktop" / "dist")
    candidates.append(_repo_root() / "clients" / "desktop" / "dist")

    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    return None


def _inject_token_meta(html: str, token: str) -> str:
    """Insert a ``<meta name="alfred-token">`` tag into the document ``<head>``.

    The token lets a same-origin browser page attach ``X-Alfred-Token`` to
    mutations. If the document already carries the meta (idempotent re-serve),
    the HTML is returned unchanged.
    """
    if not token or _TOKEN_META_MARKER in html:
        return html
    # HTML-attribute-escape the token so an unexpected value can never break out
    # of the attribute. The token is url-safe base64, so this is belt-and-braces.
    safe = token.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
    meta = f'<meta name="{_TOKEN_META_NAME}" content="{safe}" />'
    lower = html.lower()
    head_index = lower.find("<head>")
    if head_index != -1:
        insert_at = head_index + len("<head>")
        return html[:insert_at] + "\n    " + meta + html[insert_at:]
    # No <head>: fall back to prepending so the tag is still present.
    return meta + "\n" + html


_NOT_BUILT_PAGE = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Alfred</title>
    <style>
      :root { color-scheme: dark; }
      body {
        margin: 0; min-height: 100vh; display: grid; place-items: center;
        font-family: ui-sans-serif, -apple-system, system-ui, sans-serif;
        background: linear-gradient(180deg, #121823, #07090f 42%, #05070b);
        color: #f6f8fb;
      }
      .card {
        max-width: 34rem; padding: 2rem 2.25rem; border-radius: 12px;
        background: rgba(25, 31, 42, 0.74);
        border: 1px solid rgba(255, 255, 255, 0.12);
        box-shadow: 0 24px 70px rgba(0, 0, 0, 0.34);
      }
      h1 { margin: 0 0 0.75rem; font-size: 1.2rem; }
      p { margin: 0 0 0.75rem; color: #cbd3e1; line-height: 1.5; }
      code {
        background: rgba(255, 255, 255, 0.08); padding: 0.1rem 0.4rem;
        border-radius: 6px; font-size: 0.9em;
      }
      a { color: #7aa2ff; }
    </style>
  </head>
  <body>
    <div class="card">
      <h1>The Alfred UI is not built yet</h1>
      <p>
        <code>alfred serve</code> serves the desktop app in the browser, but no
        built copy was found. Build it once, then reload this page:
      </p>
      <p><code>cd clients/desktop &amp;&amp; npm ci &amp;&amp; npm run build</code></p>
      <p>
        Or point <code>ALFRED_SERVE_UI_DIST</code> at an existing
        <code>dist/</code> directory. The desktop app also works without a
        browser build.
      </p>
      <p>The JSON API is unaffected: try <a href="/api/status">/api/status</a>.</p>
    </div>
  </body>
</html>
"""


def register_ui(app: FastAPI, *, ui_dist: Path | None = None) -> None:
    """Serve the built React app (or a "not built" page) plus SPA fallback.

    Mounts the built asset directory and binds ``/`` (and a catch-all SPA
    fallback for any non-API path) to the app's ``index.html`` with the
    per-launch token injected. Must be called AFTER ``views.register_routes``
    so the concrete ``/api/*`` and ``/healthz`` routes win over the catch-all.
    """
    resolved = ui_dist if ui_dist is not None else resolve_ui_dist()

    # Mount the built assets. Vite emits hashed files under ``assets/`` plus a
    # few root-level files (favicon, brand images). Mount the whole dist so
    # ``/assets/*`` and sibling files resolve, without shadowing ``/api`` or the
    # existing ``/static`` mount (both registered before this call).
    if resolved is not None:
        assets_dir = resolved / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="ui-assets")
        brand_dir = resolved / "brand"
        if brand_dir.is_dir():
            app.mount("/brand", StaticFiles(directory=str(brand_dir)), name="ui-brand")

    def _serve_index(request: Request) -> Response:
        if resolved is None:
            return HTMLResponse(_NOT_BUILT_PAGE, status_code=200)
        index_path = resolved / "index.html"
        try:
            html = index_path.read_text(encoding="utf-8")
        except OSError:
            return HTMLResponse(_NOT_BUILT_PAGE, status_code=200)
        token = views._read_server_token(views._state_root(request)) or ""
        return HTMLResponse(_inject_token_meta(html, token))

    @app.get("/", response_class=HTMLResponse)
    async def ui_root(request: Request) -> Response:
        return _serve_index(request)

    # SPA fallback: any GET that is not an API route, not the assets/brand
    # mounts, and not a real file in dist gets index.html so client-side routing
    # (if any) works on refresh/deep-link. A missing static asset still 404s
    # because the mounts above answer first; only unmatched paths reach here.
    @app.get("/{full_path:path}", response_class=HTMLResponse)
    async def ui_spa_fallback(request: Request, full_path: str) -> Response:
        # Never shadow the JSON API or health probe. FastAPI matches the
        # concrete routes first, but guard defensively so a future ordering
        # change cannot swallow an API 404 into an HTML page.
        if full_path.startswith("api/") or full_path == "healthz":
            return Response(status_code=404)
        # If the path maps to a real file in dist (e.g. a root-level asset that
        # is not under /assets or /brand), serve it directly.
        if resolved is not None:
            candidate = (resolved / full_path).resolve()
            try:
                candidate.relative_to(resolved.resolve())
            except ValueError:
                # Path traversal attempt: refuse and fall through to index.
                candidate = None  # type: ignore[assignment]
            if candidate is not None and candidate.is_file():
                return Response(
                    candidate.read_bytes(),
                    media_type=_guess_media_type(candidate),
                )
        return _serve_index(request)


def _guess_media_type(path: Path) -> str:
    import mimetypes

    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"
