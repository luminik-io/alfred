"""First-run setup routes: bootstrap status, repo picker, playbooks, demo."""

from __future__ import annotations

import logging
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from server import views

logger = logging.getLogger(__name__)


router = APIRouter()


@router.get("/api/setup/status", response_class=JSONResponse)
async def api_setup_status(request: Request) -> JSONResponse:
    """First-run bootstrap status for the Set up tab.

    Read-only. Surfaces GitHub auth, installed engine CLIs (claude/codex),
    the watched-repo selection, whether a demo is seeded, and a ``ready``
    golden-path flag: gh authed + an engine + a repo, with no AWS or Slack
    requirement.
    """
    from server import setup as setup_mod

    try:
        payload = await run_in_threadpool(setup_mod.bootstrap_status)
    except Exception:  # never break the client on a probe failure
        logger.exception("api_setup_status: bootstrap probe failed")
        return JSONResponse(
            {
                "github": {"ok": False, "account": None, "detail": views._GENERIC_ERROR},
                "engines": [],
                "engine_ready": False,
                "repos": {"selected": [], "count": 0, "keys": []},
                "demo": {"present": False},
                "ready": False,
                "error": views._GENERIC_ERROR,
            }
        )
    return JSONResponse(views._jsonable(payload))


@router.get("/api/setup/repos", response_class=JSONResponse)
async def api_setup_repos(request: Request) -> JSONResponse:
    """List GitHub repos for the onboarding repo picker."""
    from server import setup as setup_mod

    params = parse_qs(urlparse(str(request.url)).query)
    try:
        limit = int((params.get("limit") or ["100"])[0])
    except (TypeError, ValueError):
        limit = 100
    try:
        payload = await run_in_threadpool(setup_mod.list_owner_repos, limit)
    except Exception:
        logger.exception("api_setup_repos: failed to list owner repos")
        return JSONResponse({"repos": [], "selected": [], "error": views._GENERIC_ERROR})
    return JSONResponse(views._jsonable(payload))


@router.post("/api/setup/repos", response_class=JSONResponse)
async def api_setup_select_repos(request: Request) -> JSONResponse:
    """Persist the repos Alfred may work in.

    Body: ``{"repos": ["owner/repo", ...], "queue_repos": [...]}``. Writes
    the board allowlist keys to ``$ALFRED_HOME/.env`` and mirrors them into
    the live process so the new scope is effective without a restart. The
    queue mutation allowlist is initialized from ``queue_repos`` only when no
    queue scope exists yet; replacing an existing queue scope requires the
    dedicated ``replace_queue_repos`` flag.
    """
    if not views._same_origin_post(request) or not views._authorized_mutation(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    body, error_response = await views._read_json_body(request)
    if error_response is not None:
        return error_response
    raw_repos = body.get("repos")
    if not isinstance(raw_repos, list):
        return JSONResponse(
            {"error": "repos must be a list of owner/repo slugs"},
            status_code=400,
        )
    raw_queue_repos = body.get("queue_repos")
    if raw_queue_repos is not None and not isinstance(raw_queue_repos, list):
        return JSONResponse(
            {"error": "queue_repos must be a list of owner/repo slugs"},
            status_code=400,
        )
    replace_queue_repos = body.get("replace_queue_repos", False)
    if not isinstance(replace_queue_repos, bool):
        return JSONResponse(
            {"error": "replace_queue_repos must be a boolean"},
            status_code=400,
        )
    from server import setup as setup_mod

    try:
        result = setup_mod.persist_selected_repos(
            raw_repos,
            queue_repos=raw_queue_repos,
            replace_queue_repos=replace_queue_repos,
        )
    except (OSError, ValueError):
        logger.exception("api_setup_select_repos: failed to persist repo selection")
        return JSONResponse(
            {"error": "could not persist repo selection"},
            status_code=400,
        )
    result["ok"] = True
    return JSONResponse(views._jsonable(result))


@router.get("/api/setup/playbooks", response_class=JSONResponse)
async def api_setup_playbooks(request: Request) -> JSONResponse:
    """Starter playbooks the client offers as first jobs."""
    from server import setup as setup_mod

    rows = [
        {"key": p["key"], "title": p["title"], "summary": p["summary"]}
        for p in setup_mod.STARTER_PLAYBOOKS
    ]
    return JSONResponse({"playbooks": rows})


@router.post("/api/setup/playbook", response_class=JSONResponse)
async def api_setup_compose_playbook(request: Request) -> JSONResponse:
    """Compose a starter playbook into a saved request draft."""
    if not views._same_origin_post(request) or not views._authorized_mutation(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    body, error_response = await views._read_json_body(request)
    if error_response is not None:
        return error_response
    from server import setup as setup_mod

    key = str(body.get("key") or "").strip()
    playbook = setup_mod.playbook_by_key(key)
    if playbook is None:
        return JSONResponse({"error": "unknown playbook key"}, status_code=400)
    return views._compose_playbook_draft(request, playbook, body.get("repos"))


@router.post("/api/setup/demo", response_class=JSONResponse)
async def api_setup_seed_demo(request: Request) -> JSONResponse:
    """Seed local demo cards so an empty board teaches the workflow."""
    if not views._same_origin_post(request) or not views._authorized_mutation(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    from server import setup as setup_mod

    try:
        result = setup_mod.seed_demo(views._state_root(request))
    except OSError:
        logger.exception("api_setup_seed_demo: failed to seed demo cards")
        return JSONResponse({"error": "could not seed demo"}, status_code=400)
    return JSONResponse(views._jsonable(result))


@router.post("/api/setup/demo/clear", response_class=JSONResponse)
async def api_setup_clear_demo(request: Request) -> JSONResponse:
    """Remove seeded demo cards. Token-gated and idempotent."""
    if not views._same_origin_post(request) or not views._authorized_mutation(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    from server import setup as setup_mod

    result = setup_mod.clear_demo(views._state_root(request))
    return JSONResponse(views._jsonable(result))
