"""Per-agent Claude and Codex model controls."""

from __future__ import annotations

from typing import Any

from custom_agents import CustomAgentStore
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from server import runtime_facade, views
from server.agent_profiles import AGENT_PROFILES

router = APIRouter()


def _known_codenames(request: Request) -> list[str]:
    built_in = [profile.codename for profile in AGENT_PROFILES]
    custom = sorted(
        agent.codename
        for agent in CustomAgentStore.from_state_root(views._state_root(request)).load()
    )
    return [*built_in, *custom]


def _selection_payload(request: Request, agent: str, provider: str) -> dict[str, Any]:
    return runtime_facade.model_selection(
        agent,
        provider,
        state_root=views._state_root(request),
    )


def _agent_payload(request: Request, agent: str) -> dict[str, Any]:
    return {
        "agent": agent,
        "claude": _selection_payload(request, agent, "claude"),
        "codex": _selection_payload(request, agent, "codex"),
    }


@router.get("/api/agent-models", response_class=JSONResponse)
async def api_agent_models(request: Request) -> JSONResponse:
    agents = [_agent_payload(request, agent) for agent in _known_codenames(request)]
    return JSONResponse({"agents": agents, "count": len(agents)})


@router.post(
    "/api/agent-models",
    response_class=JSONResponse,
    dependencies=[Depends(views.require_mutation_token)],
)
async def api_save_agent_model(request: Request) -> JSONResponse:
    body, error_response = await views._read_json_body(request)
    if error_response is not None:
        return error_response

    agent = body.get("agent")
    provider = body.get("provider")
    if not isinstance(agent, str) or agent not in _known_codenames(request):
        return JSONResponse({"error": "unknown agent"}, status_code=404)
    if not isinstance(provider, str) or provider not in runtime_facade.model_providers():
        return JSONResponse({"error": "provider must be claude or codex"}, status_code=400)
    if "model" not in body or (body["model"] is not None and not isinstance(body["model"], str)):
        return JSONResponse({"error": "model must be a string or null"}, status_code=400)

    model = body["model"]
    try:
        if model is None or not model.strip():
            runtime_facade.clear_agent_model(agent, provider, state_root=views._state_root(request))
        else:
            runtime_facade.save_agent_model(
                agent, provider, model, state_root=views._state_root(request)
            )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    return JSONResponse(
        {
            "ok": True,
            "agent": agent,
            "provider": provider,
            "selection": _selection_payload(request, agent, provider),
        }
    )
