"""First-run setup routes: bootstrap status, repo picker, playbooks, demo."""

from __future__ import annotations

import logging
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Depends, Request
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
                "repos": {"selected": [], "count": 0, "keys": [], "repo_checkouts": []},
                "demo": {"present": False},
                "first_run": {
                    "version": 1,
                    "ready": False,
                    "status": "needs_action",
                    "headline": "Setup status is unavailable.",
                    "summary": {
                        "required_ready": 0,
                        "required_total": 0,
                        "recommended_ready": 0,
                        "recommended_total": 0,
                        "optional_ready": 0,
                        "optional_total": 0,
                        "blockers": ["setup_status"],
                    },
                    "checks": [],
                },
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
        return JSONResponse(
            {"repos": [], "selected": [], "repo_checkouts": [], "error": views._GENERIC_ERROR}
        )
    return JSONResponse(views._jsonable(payload))


@router.post(
    "/api/setup/repos",
    response_class=JSONResponse,
    dependencies=[Depends(views.require_mutation_token)],
)
async def api_setup_select_repos(request: Request) -> JSONResponse:
    """Persist the repos Alfred may work in.

    Body: ``{"repos": ["owner/repo", ...], "queue_repos": [...],
    "repo_checkouts": [{"repo": "owner/repo", "path": "/absolute/path"}]}``. Writes
    the board allowlist keys to ``$ALFRED_HOME/.env`` and mirrors them into
    the live process so the new scope is effective without a restart. The
    queue mutation allowlist is initialized from ``queue_repos`` only when no
    queue scope exists yet; replacing an existing queue scope requires the
    dedicated ``replace_queue_repos`` flag.
    """
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
    raw_repo_checkouts = body.get("repo_checkouts")
    if not isinstance(raw_repo_checkouts, list):
        return JSONResponse(
            {"error": "repo_checkouts must be a list of repo/path objects"},
            status_code=400,
        )
    if len(raw_repo_checkouts) > 200 or any(
        not isinstance(entry, dict) for entry in raw_repo_checkouts
    ):
        return JSONResponse(
            {"error": "repo_checkouts must contain at most 200 repo/path objects"},
            status_code=400,
        )
    from server import setup as setup_mod

    try:
        result = await run_in_threadpool(
            lambda: setup_mod.persist_selected_repos(
                raw_repos,
                queue_repos=raw_queue_repos,
                replace_queue_repos=replace_queue_repos,
                repo_checkouts=raw_repo_checkouts,
            )
        )
    except setup_mod.RepoCheckoutValidationError as exc:
        return JSONResponse(
            {
                "error": "repo checkout validation failed",
                "repo_checkouts": views._jsonable(exc.rows),
            },
            status_code=400,
        )
    except ValueError:
        return JSONResponse(
            {"error": "invalid repository selection"},
            status_code=400,
        )
    except OSError:
        logger.exception("api_setup_select_repos: failed to persist repo selection")
        return JSONResponse(
            {"error": "could not persist repo selection"},
            status_code=400,
        )
    result["ok"] = True
    return JSONResponse(views._jsonable(result))


@router.get("/api/setup/batteries", response_class=JSONResponse)
async def api_setup_batteries(request: Request) -> JSONResponse:
    """Battery manifest for the onboarding picker.

    Read-only. Returns the shared battery manifest (built-in and opt-in
    enhancements) with each battery's status on this host, so the GUI and the
    CLI agree on one list. Nothing is installed or started by this call.
    """
    from server import setup as setup_mod

    try:
        payload = await run_in_threadpool(setup_mod.battery_manifest)
    except Exception:  # never break the client on a probe failure
        logger.exception("api_setup_batteries: manifest probe failed")
        return JSONResponse(
            {"version": 1, "summary": {}, "batteries": [], "error": views._GENERIC_ERROR}
        )
    return JSONResponse(views._jsonable(payload))


@router.post(
    "/api/setup/batteries",
    response_class=JSONResponse,
    dependencies=[Depends(views.require_mutation_token)],
)
async def api_setup_set_battery(request: Request) -> JSONResponse:
    """Enable or disable one opt-in battery.

    Body: ``{"battery": "<id>", "enabled": true|false}``. Writes the battery's
    env flag(s) to ``$ALFRED_HOME/.env`` and mirrors them into the live process.
    This only flips the flag; it never installs a pip extra, fetches a binary, or
    starts a daemon (Redis / Postgres). The manifest tells the client what still
    needs installing so the choice stays explicit.
    """
    body, error_response = await views._read_json_body(request)
    if error_response is not None:
        return error_response
    battery_id = str(body.get("battery") or "").strip()
    if not battery_id:
        return JSONResponse({"error": "battery id is required"}, status_code=400)
    enabled = body.get("enabled", True)
    if not isinstance(enabled, bool):
        return JSONResponse({"error": "enabled must be a boolean"}, status_code=400)
    from server import setup as setup_mod

    # Validate first and return a message we construct here, so raw exception text
    # (a stack trace sink flagged by CodeQL) never reaches the client.
    reason = await run_in_threadpool(
        lambda: setup_mod.battery_action_error(battery_id, enabled=enabled)
    )
    if reason is not None:
        return JSONResponse({"error": reason}, status_code=400)
    try:
        result = await run_in_threadpool(lambda: setup_mod.set_battery(battery_id, enabled=enabled))
    except (OSError, ValueError):
        logger.exception("api_setup_set_battery: failed to persist battery selection")
        return JSONResponse({"error": "could not persist battery selection"}, status_code=400)
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


@router.post(
    "/api/setup/playbook",
    response_class=JSONResponse,
    dependencies=[Depends(views.require_mutation_token)],
)
async def api_setup_compose_playbook(request: Request) -> JSONResponse:
    """Compose a starter playbook into a saved request draft."""
    body, error_response = await views._read_json_body(request)
    if error_response is not None:
        return error_response
    from server import setup as setup_mod

    key = str(body.get("key") or "").strip()
    playbook = setup_mod.playbook_by_key(key)
    if playbook is None:
        return JSONResponse({"error": "unknown playbook key"}, status_code=400)
    return views._compose_playbook_draft(request, playbook, body.get("repos"))


@router.post(
    "/api/setup/demo",
    response_class=JSONResponse,
    dependencies=[Depends(views.require_mutation_token)],
)
async def api_setup_seed_demo(request: Request) -> JSONResponse:
    """Seed local demo cards so an empty board teaches the workflow."""
    from server import setup as setup_mod

    try:
        result = setup_mod.seed_demo(views._state_root(request))
    except OSError:
        logger.exception("api_setup_seed_demo: failed to seed demo cards")
        return JSONResponse({"error": "could not seed demo"}, status_code=400)
    return JSONResponse(views._jsonable(result))


@router.post(
    "/api/setup/demo/clear",
    response_class=JSONResponse,
    dependencies=[Depends(views.require_mutation_token)],
)
async def api_setup_clear_demo(request: Request) -> JSONResponse:
    """Remove seeded demo cards. Token-gated and idempotent."""
    from server import setup as setup_mod

    result = setup_mod.clear_demo(views._state_root(request))
    return JSONResponse(views._jsonable(result))
