"""FastAPI app factory for ``alfred serve``.

The factory takes a :class:`FleetReader` so tests can swap the source of
truth. The default driver in ``bin/alfred-serve.py`` constructs a
:class:`FilesystemReader` and passes it in.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import static_ui, views
from .reader import FleetReader

logger = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
STATIC_DIR = _HERE / "static"


def create_app(reader: FleetReader) -> FastAPI:
    """Build the FastAPI application bound to ``reader``.

    The app serves a JSON ``/api/*`` surface plus the built desktop React app
    as the browser UI (see :mod:`server.static_ui`). It is meant to be served
    on ``127.0.0.1`` only; binding to any other interface is a deliberate
    choice the operator must make at the CLI level.
    """
    app = FastAPI(
        title="alfred serve",
        description="Localhost-only dashboard + JSON API over $ALFRED_HOME/state.",
        version="0.5.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Render the shared mutation-token gate's rejection once. The
    # ``require_mutation_token`` dependency raises ``MutationForbidden``; this
    # handler emits the exact ``403 {"error": "forbidden"}`` body every mutating
    # route used to inline, so declaring the dependency is behaviorally
    # identical to the old per-route check.
    async def _mutation_forbidden(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    app.add_exception_handler(views.MutationForbidden, _mutation_forbidden)

    # Attach the reader to app.state so view functions can pull it without a
    # global. Keeps create_app the only place wiring happens.
    app.state.reader = reader

    # Mint a fresh per-launch token and persist it (0600) under the state root.
    # State-mutating POSTs require it via the X-Alfred-Token header, so a
    # drive-by same-origin localhost page (which cannot read the token file)
    # can never arm work or mutate fleet/trust/plan state.
    state_root = getattr(reader, "state_root", None)
    if isinstance(state_root, Path):
        try:
            views.ensure_server_token(state_root)
        except OSError as exc:
            # A serve start must not be blocked by a token-write failure; the
            # gate then fails closed (mutating POSTs return 403) rather than
            # silently downgrading to same-origin-only.
            logger.warning("could not write server token under %s: %s", state_root, exc)

    views.register_routes(app)
    # Serve the built React app (or a "not built" placeholder) at ``/`` plus an
    # SPA fallback. Registered AFTER the API routes so ``/api/*`` and
    # ``/healthz`` win over the catch-all fallback.
    static_ui.register_ui(app)
    return app
