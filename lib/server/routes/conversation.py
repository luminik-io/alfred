"""Local conversation-control route (Slack control handler over HTTP)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from slack_trust import SlackTrustStore, operator_user_id_from_env

from server import views

router = APIRouter()


@router.post(
    "/api/conversation/control",
    response_class=JSONResponse,
    dependencies=[Depends(views.require_mutation_token)],
)
async def api_conversation_control(request: Request) -> JSONResponse:
    try:
        body = json.loads((await request.body()).decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"error": "request body must be JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "request body must be a JSON object"}, status_code=400)

    text = str(body.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "text is required"}, status_code=400)

    actor_user_id = views._local_conversation_actor(body.get("actor_user_id"))
    handler = views.SlackControlHandler(
        trust_store=SlackTrustStore.from_state_root(views._state_root(request)),
        operator_user_id=operator_user_id_from_env() or actor_user_id,
        state_root=views._state_root(request),
        plan_reader=request.app.state.reader,
        memory_provider=views._planning_memory_provider(request),
    )
    result = handler.handle(text, trusted=True, actor_user_id=actor_user_id)
    return JSONResponse(
        {
            "handled": result.handled,
            "action": result.action,
            "text": result.text,
            "detail": result.detail,
            "actor_user_id": actor_user_id,
        }
    )
