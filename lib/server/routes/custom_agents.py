"""Custom-agent CRUD routes."""

from __future__ import annotations

import logging

from custom_agents import CustomAgentError, CustomAgentStore
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from server import views

logger = logging.getLogger(__name__)


router = APIRouter()


@router.get("/api/custom-agents", response_class=JSONResponse)
async def api_custom_agents(request: Request) -> JSONResponse:
    store = CustomAgentStore.from_state_root(views._state_root(request))
    include_prompt = request.query_params.get("include_prompt", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if include_prompt and (
        not views._same_origin_request(request) or not views._authorized_mutation(request)
    ):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return JSONResponse(store.snapshot(include_prompt=include_prompt))


@router.post("/api/custom-agents", response_class=JSONResponse)
async def api_save_custom_agent(request: Request) -> JSONResponse:
    if not views._same_origin_post(request) or not views._authorized_mutation(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    body, error_response = await views._read_json_body(request)
    if error_response is not None:
        return error_response
    store = CustomAgentStore.from_state_root(views._state_root(request))
    try:
        agent = store.upsert(body)
    except CustomAgentError:
        logger.warning("api_save_custom_agent: rejected invalid payload", exc_info=True)
        return JSONResponse({"error": "invalid custom agent payload"}, status_code=400)
    return JSONResponse(
        {
            "ok": True,
            "agent": agent.to_dict(),
            "deploy_required": True,
            "detail": "Run `bash deploy.sh` from the source checkout to render or reload this agent's scheduler job.",
        }
    )


@router.delete("/api/custom-agents/{codename}", response_class=JSONResponse)
async def api_delete_custom_agent(request: Request, codename: str) -> JSONResponse:
    if not views._same_origin_post(request) or not views._authorized_mutation(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    store = CustomAgentStore.from_state_root(views._state_root(request))
    try:
        removed = store.delete(codename)
    except CustomAgentError:
        return JSONResponse({"error": "invalid custom agent codename"}, status_code=400)
    return JSONResponse(
        {
            "ok": True,
            "removed": removed,
            "deploy_required": removed,
            "detail": (
                "Run `bash deploy.sh` from the source checkout to remove the scheduler job."
                if removed
                else "No custom agent matched that codename."
            ),
        }
    )
