"""Fleet status, schedule, actions, shipped board, and queue-control routes."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from server import views

logger = logging.getLogger(__name__)


router = APIRouter()


@router.get("/api/status", response_class=JSONResponse)
async def api_status(request: Request) -> JSONResponse:
    reader = request.app.state.reader
    agents = reader.list_agents()
    reliability = reader.reliability_report()
    return JSONResponse(
        views._jsonable(
            {
                "agents": agents,
                "total_today": sum(agent.firings_today for agent in agents),
                "reliability": reliability,
                # Today's aggregate spend + ok/fail counts, rolled up from
                # the same per-agent spend-YYYY-MM-DD.json ledgers metrics
                # reads. Lets the Review cost strip show real spend instead
                # of "not surfaced". Stays an honest empty rollup (all
                # zeros, spend_usd null) when no ledgers exist today.
                "metrics": views._today_cost_rollup(reader),
                # The active intake profile (server env only), so Compose can
                # adapt its copy/behavior to plain mode. Defaults to
                # "technical" when ALFRED_INTAKE_PROFILE is unset.
                "intake_profile": views._active_intake_profile_name(),
                # Planning context from guided setup. The client can seed
                # plans from this instead of asking the operator to type an
                # owner/repo slug Alfred already knows.
                "setup_repos": views._selected_setup_repos_payload(),
            }
        )
    )


@router.get("/api/schedule", response_class=JSONResponse)
async def api_schedule(request: Request) -> JSONResponse:
    """Upcoming scheduled runs read from ``launchd/agents.conf``.

    ``cron:`` rows carry a computed ``next_fire_at`` (local ISO-8601);
    ``interval:`` rows carry only a ``cadence`` string ("every 15m")
    because the read-only server has no trustworthy last-fired anchor to
    compute the next fire from. Never 500s: an unreadable/missing conf
    degrades to an empty ``runs`` list so the lane shows an honest empty
    state.
    """
    from server.schedule import upcoming_runs

    try:
        state_root = getattr(request.app.state.reader, "state_root", None)
        runs = upcoming_runs(state_root=state_root if isinstance(state_root, Path) else None)
    except Exception:  # never break the client on a parse failure
        logger.exception("api_schedule: failed to read upcoming runs")
        return JSONResponse({"runs": [], "error": views._GENERIC_ERROR})
    return JSONResponse(views._jsonable({"runs": [run.to_dict() for run in runs]}))


@router.get("/api/actions", response_class=JSONResponse)
async def api_actions(request: Request) -> JSONResponse:
    reliability = request.app.state.reader.reliability_report()
    return JSONResponse(
        views._jsonable(
            {
                "status": reliability.get("status", "unknown"),
                "actions": reliability.get("actions", []),
                "failure_patterns": reliability.get("failure_patterns", []),
                "stale_workers": reliability.get("stale_workers", []),
                "promotion_suggestions": reliability.get("promotion_suggestions", []),
                "error": reliability.get("error"),
                "errors": reliability.get("errors", {}),
            }
        )
    )


@router.get("/api/shipped", response_class=JSONResponse)
async def api_shipped(request: Request) -> JSONResponse:
    """Kanban feed: what shipped / is in progress / is queued.

    Human-readable cards (title + repo + age + author), not bare links, so
    the native client and the Slack board render the same payload. Never
    500s: a GitHub/auth failure returns an ``error`` field with empty
    columns.
    """
    from shipped_board import DEFAULT_LOOKBACK_DAYS, build_board, resolve_repos

    params = parse_qs(urlparse(str(request.url)).query)
    try:
        days = int((params.get("days") or [str(DEFAULT_LOOKBACK_DAYS)])[0])
    except (TypeError, ValueError):
        days = DEFAULT_LOOKBACK_DAYS
    days = max(1, min(days, 90))
    repos = (params.get("repos") or [""])[0]
    repo_list = [r.strip() for r in repos.split(",") if r.strip()] or None
    # Demo cards are opt-in: the live board shows only real Alfred work
    # unless the client explicitly asks for the seeded sample via ?demo=1.
    include_demo = (params.get("demo") or ["0"])[0].strip().lower() in (
        "1",
        "true",
        "yes",
    )
    try:

        def _build() -> dict[str, Any]:
            return build_board(resolve_repos(repo_list), days=days, include_demo=include_demo)

        board = await run_in_threadpool(_build)
    except Exception:  # never break the client on a board failure
        logger.exception("api_shipped: failed to build board")
        return JSONResponse(
            views._jsonable(
                {
                    "columns": {
                        "queued": [],
                        "in_progress": [],
                        "shipped": [],
                        "awaiting_approval": [],
                    },
                    "counts": {
                        "queued": 0,
                        "in_progress": 0,
                        "shipped": 0,
                        "awaiting_approval": 0,
                    },
                    "repos": repo_list or [],
                    "lookback_days": days,
                    "error": views._GENERIC_ERROR,
                }
            )
        )
    return JSONResponse(views._jsonable(board))


@router.post("/api/queue", response_class=JSONResponse)
async def api_queue(request: Request) -> JSONResponse:
    """Operator queue control: assign, arm, hold, or close an issue.

    Body: ``{"repo": "owner/repo", "number": 12, "action": "assign"|"queue"|"hold"|"done"}``.
    ``assign`` chooses architect or Lucius and labels the issue for that lane;
    callers may pass ``target_agent`` / ``agent`` as ``batman`` or ``lucius``
    to override the heuristic without bypassing safety gates;
    ``queue`` labels the issue ``agent:implement``; ``hold`` labels it
    ``do-not-pickup`` so no agent claims it; ``done`` closes the issue
    using GitHub's native closed state (no new label taxonomy).

    Each action mutates fleet/repo state, so all require the operator's
    per-launch token (the ``X-Alfred-Token`` header), not just a
    same-origin request. A drive-by localhost page cannot read the
    ``0600`` token file, so it can never arm or close work.
    """
    if not views._same_origin_post(request) or not views._authorized_mutation(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        body = json.loads((await request.body()).decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"error": "request body must be JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "request body must be a JSON object"}, status_code=400)
    from issue_assignment import assign_issue
    from issue_queue import QUEUE_ACTIONS, close_issue, set_issue_pickup

    repo = str(body.get("repo") or "").strip()
    action = str(body.get("action") or "").strip().lower()
    target_agent = str(body.get("target_agent") or body.get("agent") or "").strip()
    number_raw = body.get("number")
    if not isinstance(number_raw, (str, int)):
        return JSONResponse({"error": "number must be an integer"}, status_code=400)
    try:
        number = int(number_raw)
    except (TypeError, ValueError):
        return JSONResponse({"error": "number must be an integer"}, status_code=400)
    allowed_actions = set(QUEUE_ACTIONS) | {"assign"}
    if action not in allowed_actions:
        return JSONResponse(
            {"error": "action must be 'assign', 'queue', 'hold', or 'done'"},
            status_code=400,
        )
    if action == "done":
        ok, detail = close_issue(repo, number)
        response_target_agent = ""
    elif action == "assign":
        assignment = assign_issue(repo, number, target_agent=target_agent)
        ok, detail = assignment.ok, assignment.detail
        response_target_agent = assignment.decision.agent or target_agent or "auto"
        if not ok:
            detail = assignment.error or detail
    else:
        ok, detail = set_issue_pickup(repo, number, hold=(action == "hold"))
        response_target_agent = ""
    if not ok:
        return JSONResponse({"error": detail}, status_code=400)
    payload = {
        "ok": True,
        "repo": repo,
        "number": number,
        "action": action,
        "detail": detail,
    }
    if response_target_agent:
        payload["target_agent"] = response_target_agent
    return JSONResponse(payload)
