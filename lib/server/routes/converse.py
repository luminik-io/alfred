"""Conversational-engine routes: theme builder, onboarding, and Compose."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from server import views

router = APIRouter()


@router.post("/api/theme-builder/converse", response_class=JSONResponse)
async def api_theme_builder_converse(request: Request) -> JSONResponse:
    """Run one turn of the conversational roster theme builder.

    Body: ``{ messages: [{role, content}] }``. Each call runs ONE assistant
    turn via the agent-engine dispatch, seeded with the theme-builder system
    prompt + the roster contract (role-slugs, role labels, current names) read
    server-side from ``roster_manifest.json``. The model asks a short vibe
    question, then proposes a full role-slug -> display-name mapping as a
    ``propose_theme`` action.

    Returns ``{ reply, action }`` where ``action`` is either ``null`` (a plain
    vibe-asking turn) or ``{tool: "propose_theme", args: {custom_names,
    custom_roles}}``. Nothing is saved here: the client pre-fills the custom
    theme editor with the proposal, the person confirms, and the client saves
    it via ``POST /api/roster-theme`` with ``theme: "custom"``. Degrades with a
    503 when no live engine is configured so the client falls back to the
    manual editor.
    """
    if not views._same_origin_post(request) or not views._authorized_mutation(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        body = json.loads((await request.body()).decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"error": "request body must be JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "request body must be a JSON object"}, status_code=400)
    return views._run_theme_builder_converse(request, body)


@router.post("/api/onboarding/converse", response_class=JSONResponse)
async def api_onboarding_converse(request: Request) -> JSONResponse:
    """Run one turn of the conversational Ask-driven onboarding guide.

    Body: ``{ messages: [{role, content}] }``. Each call runs ONE assistant
    turn via the agent-engine dispatch, seeded with the onboarding system
    prompt. Alfred asks a short setup question, then REQUESTS a structured
    action (check the engines, connect GitHub, pick repos, name the team, set
    a schedule, finish) that the DESKTOP CLIENT executes under the same token
    gate the stepped flow uses. The model never writes config or a token: it
    only proposes the next step.

    Returns ``{ reply, action, done }`` where ``action`` is either ``null`` (a
    plain question turn) or ``{tool, args}`` for one scoped onboarding action.
    Nothing is executed here: the client runs the SAME setup handler the
    stepped OnboardingView already drives, so the two paths cannot drift.
    Degrades with a 503 when no live engine is configured so the client falls
    back to the stepped flow.
    """
    if not views._same_origin_post(request) or not views._authorized_mutation(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        body = json.loads((await request.body()).decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"error": "request body must be JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "request body must be a JSON object"}, status_code=400)
    return views._run_onboarding_converse(request, body)


@router.post("/api/compose/converse", response_class=JSONResponse)
async def api_compose_converse(request: Request) -> JSONResponse:
    """Run one turn of the conversational, repo-grounded spec-builder.

    Body: ``{ draft_id?, context_repos?: [owner/repo], repos?: [owner/repo], plain?: bool,
    messages: [{role, content}] }``. Each call runs ONE assistant turn via
    the agent-engine dispatch, seeded with the spec-interrogator system
    prompt + repo grounding + code map. ``plain`` toggles jargon-free
    coaching for this turn (it wins over the ALFRED_INTAKE_PROFILE env
    default); the structured draft and readiness are unchanged either way.
    Persists the accumulating spec and conversation as a compose planning
    draft so it shows in Plans and threads into the RequestThread.

    Returns ``{ reply, draft, readiness:{score, ready, missing[]}, done }``.
    Degrades with a 503 when no live engine is configured (the off-Tauri
    browser preview never calls this; it stays on the one-shot rubric form).
    """
    if not views._same_origin_post(request) or not views._authorized_mutation(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        body = json.loads((await request.body()).decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"error": "request body must be JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "request body must be a JSON object"}, status_code=400)
    return views._run_compose_converse(request, body)


@router.post("/api/compose/converse/stream")
async def api_compose_converse_stream(request: Request) -> Any:
    """Token-stream one converse turn so chat renders as the model writes (#36).

    Same body + auth + persistence as ``/api/compose/converse`` (a
    token-gated mutation). Because this is a POST it cannot ride
    ``EventSource`` (which is GET-only and cannot send ``X-Alfred-Token``);
    the native client consumes it via ``fetch()`` + a streamed
    ``ReadableStream``, which carries the token header. The response is an
    SSE byte stream:

    * ``open``   once, when the turn starts.
    * ``token``  each new assistant text fragment teed to the transcript
      (BEST EFFORT progress; a model that does not tee interim text simply
      emits none and the reply lands whole on ``result``).
    * ``result`` once, the full reconciled ``ConverseResponse`` (also
      persisted as a compose draft, exactly like the non-streaming route).
    * ``error``  when no live engine is configured or the turn failed, so
      the client falls back to the non-streaming converse / one-shot form.

    Auth is checked BEFORE the stream opens so a forbidden caller gets a
    clean 403 JSON, never a half-open stream.

    Unlike the buffered mutations, the packaged Tauri webview reaches this
    route cross-origin (its bundle loads from ``tauri://localhost``, not the
    server's Host), and it must to stream an incremental body the buffered
    Tauri JSON bridge cannot carry. So instead of strict same-origin we
    require (a) an allowed webview/localhost Origin AND (b) the per-launch
    token via ``_authorized_mutation`` (constant-time compare). The token is
    the real CSRF defense: a drive-by page cannot read the operator's
    ``0600`` token file, so a bare cross-origin POST without it is rejected.
    """
    cors = views._streaming_cors_headers(request)
    if (
        views._streaming_origin_allowed(request) is None and not views._same_origin_post(request)
    ) or not views._authorized_mutation(request):
        return JSONResponse({"error": "forbidden"}, status_code=403, headers=cors)
    try:
        body = json.loads((await request.body()).decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"error": "request body must be JSON"}, status_code=400, headers=cors)
    if not isinstance(body, dict):
        return JSONResponse(
            {"error": "request body must be a JSON object"},
            status_code=400,
            headers=cors,
        )
    return views._stream_compose_converse(request, body)


@router.options("/api/compose/converse/stream")
async def api_compose_converse_stream_preflight(request: Request) -> Response:
    """CORS preflight for the cross-origin converse stream (#36).

    The packaged webview's token-bearing POST is a non-simple request, so
    the browser sends an ``OPTIONS`` preflight first. Answer it for allowed
    webview/localhost origins; this carries no body and runs no turn, so it
    is not token-gated (the actual POST still is).
    """
    return Response(status_code=204, headers=views._streaming_cors_headers(request))
