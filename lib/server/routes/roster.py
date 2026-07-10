"""Roster-theme (agent naming) routes."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from roster_theme_store import RosterThemeError, RosterThemeStore

from server import views

logger = logging.getLogger(__name__)


router = APIRouter()


@router.get("/api/roster-theme", response_class=JSONResponse)
async def api_roster_theme(request: Request) -> JSONResponse:
    # The active roster theme plus any operator-
    # authored custom names. Read-only and unauthenticated like the other
    # GETs: it carries no secret and lets any surface read the same choice.
    store = RosterThemeStore.from_state_root(views._state_root(request))
    return JSONResponse(store.load().to_dict())


@router.post("/api/roster-theme", response_class=JSONResponse)
async def api_set_roster_theme(request: Request) -> JSONResponse:
    # Persist the chosen theme + custom name/role maps so the desktop and the
    # Slack message path honor the same roster. Token-gated like every other
    # state-mutating POST so a drive-by same-origin page cannot rename agents.
    if not views._same_origin_post(request) or not views._authorized_mutation(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        body = json.loads((await request.body()).decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"error": "request body must be JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "request body must be a JSON object"}, status_code=400)
    store = RosterThemeStore.from_state_root(views._state_root(request))
    try:
        state = store.save(
            theme=str(body.get("theme") or ""),
            custom_names=body.get("custom_names"),
            custom_roles=body.get("custom_roles"),
        )
    except RosterThemeError:
        # The validation message can echo back attacker-controlled payload
        # fragments (the offending codename/label), so we never surface the
        # exception text. Log the detail server-side and return a generic 400.
        logger.warning("api_set_roster_theme: rejected invalid payload", exc_info=True)
        return JSONResponse({"error": "invalid roster theme payload"}, status_code=400)
    return JSONResponse(state.to_dict())
