"""Slack trusted-user management routes."""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from slack_surface.trust import (
    SlackTrustStore,
    env_trusted_user_ids,
    normalize_slack_user_id,
    operator_user_id_from_env,
)

from server import views

router = APIRouter()


@router.get("/api/slack/trusted-users", response_class=JSONResponse)
async def api_slack_trusted_users(request: Request) -> JSONResponse:
    store = SlackTrustStore.from_state_root(views._state_root(request))
    return JSONResponse(
        store.snapshot(
            operator_user_id=operator_user_id_from_env(),
            env_trusted_user_ids=env_trusted_user_ids(),
        ).to_dict()
    )


@router.post("/api/slack/trusted-users", response_class=JSONResponse)
async def api_slack_trust_user(request: Request) -> JSONResponse:
    if not views._same_origin_post(request) or not views._authorized_mutation(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        body = json.loads((await request.body()).decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"error": "request body must be JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "request body must be a JSON object"}, status_code=400)
    user_id = normalize_slack_user_id(body.get("user_id"))
    if user_id is None:
        return JSONResponse({"error": "user_id must be a Slack user id"}, status_code=400)
    store = SlackTrustStore.from_state_root(views._state_root(request))
    added, _user = store.add(user_id, added_by="local-client")
    snapshot = store.snapshot(
        operator_user_id=operator_user_id_from_env(),
        env_trusted_user_ids=env_trusted_user_ids(),
    ).to_dict()
    snapshot["added"] = added
    return JSONResponse(snapshot)


@router.post("/api/slack/trusted-users/{user_id}/remove", response_class=JSONResponse)
async def api_slack_untrust_user(request: Request, user_id: str) -> JSONResponse:
    if not views._same_origin_post(request) or not views._authorized_mutation(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    normalized = normalize_slack_user_id(user_id)
    if normalized is None:
        return JSONResponse({"error": "user_id must be a Slack user id"}, status_code=400)
    store = SlackTrustStore.from_state_root(views._state_root(request))
    removed = store.remove(normalized)
    snapshot = store.snapshot(
        operator_user_id=operator_user_id_from_env(),
        env_trusted_user_ids=env_trusted_user_ids(),
    ).to_dict()
    snapshot["removed"] = removed
    return JSONResponse(snapshot)
