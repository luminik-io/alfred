"""Per-agent Claude and Codex model controls."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_capabilities import BUILTIN_ENGINE_SCRIPTS, ENGINE_AGENT_CODENAMES
from custom_agents import CustomAgentStore
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from server import runtime_facade, views

router = APIRouter()


def _known_codenames(request: Request) -> list[str]:
    built_in = list(ENGINE_AGENT_CODENAMES)
    custom = sorted(
        agent.codename
        for agent in CustomAgentStore.from_state_root(views._state_root(request)).load()
    )
    runtime = _scheduled_engine_codenames(views._state_root(request))
    known: list[str] = []
    for agent in [*built_in, *custom, *runtime]:
        if agent in known:
            continue
        try:
            _selection_payload(request, agent, "claude")
        except ValueError:
            continue
        known.append(agent)
    return known


def _scheduled_engine_codenames(state_root: Path) -> list[str]:
    """Return scheduled aliases backed by a built-in LLM runner script."""

    runtime_home = state_root.parent
    conf = next(
        (
            candidate
            for candidate in (
                runtime_home / "launchd" / "agents.conf",
                runtime_home / "infra" / "agents" / "launchd" / "agents.conf",
            )
            if candidate.is_file()
        ),
        None,
    )
    if conf is None:
        return []
    try:
        lines = conf.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    codenames: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = raw.split("\t")
        if len(fields) < 2 or Path(fields[1].strip()).name not in BUILTIN_ENGINE_SCRIPTS:
            continue
        codename = fields[0].strip().rsplit(".", 1)[-1]
        if codename and codename not in codenames:
            codenames.append(codename)
    return codenames


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
    except ValueError:
        return JSONResponse({"error": "model name is invalid"}, status_code=400)

    return JSONResponse(
        {
            "ok": True,
            "agent": agent,
            "provider": provider,
            "selection": _selection_payload(request, agent, provider),
        }
    )
