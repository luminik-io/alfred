"""Route handlers for ``alfred serve``.

Three views:

* ``GET /``                  Fleet status (HTMX auto-refresh every 10s).
* ``GET /firings``           Recent firings (optionally filtered by codename).
* ``GET /firings/{id}``      Single firing detail.
* ``GET /plans``             Saved Architect plans.
* ``GET /plans/{id}``        Single saved architect plan.
* ``GET/POST /planning``     Local issue/spec readiness helper.

Two HTMX partials live behind the same URLs via the ``HX-Request`` header,
``htmx-only`` reduces the round trip to just the table body rather than
re-rendering the whole shell. Keeps the dashboard cheap to refresh.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import secrets
from contextlib import suppress
from dataclasses import asdict, is_dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import (
    JSONResponse,
    StreamingResponse,
)
from planning_assistant import (
    PlanningAssistantResult,
    refine_issue_draft,
)

# Re-exported so ``server.routes.conversation`` can resolve it at call time via
# ``views.SlackControlHandler``. Routing it through this module (rather than a
# direct import in the router) keeps the existing test monkeypatch of
# ``server.views.SlackControlHandler`` effective. No code in this module uses it
# directly, hence the noqa.
from slack_control import SlackControlHandler  # noqa: F401
from slack_trust import (
    normalize_slack_user_id,
    operator_user_id_from_env,
)
from spec_helper import IssueDraft, assess_issue_draft

from server.reader import FilesystemReader, PlanDraft

logger = logging.getLogger(__name__)

# Generic message returned to the client when a handler hits an unexpected
# failure. The exception detail (type, message, traceback) is logged
# server-side instead of being placed in the HTTP response body, so the
# localhost API never leaks internals to a same-origin page. Operators read the
# real cause in the runtime logs.
_GENERIC_ERROR = "internal error"

_MEMORY_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_LOCAL_CLIENT_USER_ID = "ULOCALCLIENT"

# Header the native client attaches to every state-mutating POST. It carries
# the per-launch server token written under ``state/server-token`` so a
# drive-by same-origin localhost page cannot arm work or mutate trust/plan
# state on the operator's behalf.
SERVER_TOKEN_HEADER = "X-Alfred-Token"
_SERVER_TOKEN_FILENAME = "server-token"


def server_token_path(state_root: Path) -> Path:
    """Path to the per-launch server token under a state root."""
    return Path(state_root) / _SERVER_TOKEN_FILENAME


def ensure_server_token(state_root: Path) -> str:
    """Generate (once per launch) and persist the mutation token.

    The token is written to ``state/server-token`` with ``0600`` perms so only
    the operator's account can read it. A fresh token is minted on every server
    start, which invalidates any token a previously-running instance handed out.
    """
    root = Path(state_root)
    root.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    path = server_token_path(root)
    # Write to a temp file then atomically replace so a reader never sees a
    # half-written token. Apply 0600 before the rename so the secret is never
    # briefly world-readable.
    tmp = path.with_name(f"{path.name}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, token.encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, path)
    with suppress(OSError):
        os.chmod(path, 0o600)
    return token


def _read_server_token(state_root: Path) -> str | None:
    try:
        token = server_token_path(state_root).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return token or None


def _authorized_mutation(request: Request) -> bool:
    """Require the per-launch token for a state-mutating POST.

    The token must match the value persisted at server start, compared in
    constant time. Every client presents it via the ``SERVER_TOKEN_HEADER``
    header: the desktop (Tauri) shell attaches it through its native bridge,
    and the browser build reads the token the server injects into the served
    ``index.html`` (a ``<meta name="alfred-token">`` tag a same-origin page can
    read but a cross-origin page cannot) and echoes it back. This is a
    synchronizer token: a cross-origin attacker cannot read the operator's
    ``0600`` token file nor a same-origin document, so it defeats CSRF.
    ``_same_origin_post`` remains an additional layer.
    """
    expected = _read_server_token(_state_root(request))
    if not expected:
        # No token on disk means the gate cannot be satisfied. Fail closed so a
        # missing/unreadable token never silently downgrades to same-origin-only.
        return False
    presented = request.headers.get(SERVER_TOKEN_HEADER)
    if not presented:
        return False
    return hmac.compare_digest(presented, expected)


# Origins the packaged Tauri webview presents. A built .app loads its bundle
# from a custom scheme, so its `Origin` is NOT the localhost server's host:
# macOS/Linux serve from ``tauri://localhost`` and Windows (WebView2) from
# ``http(s)://tauri.localhost``. The dev/browser preview is same-origin through
# the Vite proxy, but a direct localhost hit also needs to be allowed for the
# streaming routes the webview talks to directly (it cannot use the buffered
# Tauri JSON bridge for an incremental body).
_TAURI_WEBVIEW_ORIGINS = frozenset(
    {
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
    }
)
_LOCALHOST_STREAM_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]", "::1"})


def _streaming_origin_allowed(request: Request) -> str | None:
    """Return the request Origin if it may talk to a streaming route, else None.

    Allowed origins are: the packaged Tauri webview schemes, any localhost dev
    origin (``http://127.0.0.1:PORT`` / ``http://localhost:PORT``), and a
    same-origin request (Origin host == the server's Host). A missing Origin is
    treated as allowed (a same-origin ``EventSource`` GET / a CLI client omit
    it); the converse-stream POST is still gated on the per-launch token, which
    is the real CSRF defense, so a bare cross-origin POST without the token is
    rejected regardless.
    """
    origin = request.headers.get("origin")
    if origin is None:
        return None
    if origin in _TAURI_WEBVIEW_ORIGINS:
        return origin
    parsed = urlparse(origin)
    if parsed.hostname in _LOCALHOST_STREAM_HOSTS:
        return origin
    if parsed.netloc and parsed.netloc == request.headers.get("host", ""):
        return origin
    return None


def _streaming_cors_headers(request: Request, base: dict[str, str] | None = None) -> dict[str, str]:
    """Augment ``base`` with CORS headers when the Origin is an allowed webview.

    Echoes the exact Origin (never ``*``) so a credentialed cross-origin fetch
    from the packaged webview can read the stream, and advertises the token
    header on the preflight. Same-origin / no-Origin requests get no CORS
    headers (none are needed), keeping the surface minimal.
    """
    headers = dict(base or {})
    allowed = _streaming_origin_allowed(request)
    if allowed is not None:
        headers["Access-Control-Allow-Origin"] = allowed
        headers["Vary"] = "Origin"
        headers["Access-Control-Allow-Headers"] = f"{SERVER_TOKEN_HEADER}, content-type"
        headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return headers


def register_routes(app: FastAPI) -> None:
    """Bind the JSON ``/api/*`` routes (plus ``/healthz``) to ``app``.

    The browser UI at ``/`` is the built desktop React app, served separately
    by :func:`server.static_ui.register_ui`. Every route is JSON.

    Each cohesive route group lives in its own :mod:`server.routes` module as a
    FastAPI ``APIRouter``; this function includes them in the order below. The
    handlers were moved out of this function verbatim and still call the shared
    helpers defined in this module. The routers are imported here (not at module
    import time) so ``server.routes.*`` can import those helpers from a
    fully-initialized ``server.views`` without a circular import.
    """
    from server.routes import (
        conversation,
        converse,
        custom_agents,
        firings,
        health,
        memory,
        plans,
        roster,
        setup,
        slack,
        status,
        usage,
    )

    app.include_router(status.router)
    app.include_router(usage.router)
    app.include_router(setup.router)
    app.include_router(slack.router)
    app.include_router(roster.router)
    app.include_router(converse.router)
    app.include_router(custom_agents.router)
    app.include_router(conversation.router)
    app.include_router(memory.router)
    app.include_router(firings.router)
    app.include_router(plans.router)
    app.include_router(health.router)


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _candidate_to_api(candidate: Any) -> dict[str, Any]:
    payload = _jsonable(
        asdict(candidate)
        if is_dataclass(candidate) and not isinstance(candidate, type)
        else candidate
    )
    if isinstance(payload, dict):
        if payload.get("agent") and not payload.get("codename"):
            payload["codename"] = payload["agent"]
        if payload.get("id") is not None:
            payload["id"] = str(payload["id"])
        if not isinstance(payload.get("tags"), list):
            payload["tags"] = []
        if not payload.get("severity"):
            payload["severity"] = "info"
        if not payload.get("source"):
            payload["source"] = "memory"
        if "source_firing_id" not in payload:
            payload["source_firing_id"] = None
        if payload.get("confidence") is None:
            payload["confidence"] = 0.5
        evidence = payload.get("evidence")
        if evidence is None:
            payload["evidence"] = ""
        elif not isinstance(evidence, str):
            payload["evidence"] = json.dumps(evidence, sort_keys=True)
        return payload
    return {}


def _lesson_to_api(lesson: Any) -> dict[str, Any]:
    """Serialize a recall Lesson for the client. Simpler than a candidate:
    no review fields, just the fact Alfred is using."""
    payload = _jsonable(
        asdict(lesson) if is_dataclass(lesson) and not isinstance(lesson, type) else lesson
    )
    if isinstance(payload, dict):
        if payload.get("id") is not None:
            payload["id"] = str(payload["id"])
        if not isinstance(payload.get("tags"), list):
            payload["tags"] = []
        if not payload.get("severity"):
            payload["severity"] = "info"
        return payload
    return {}


def _lesson_display_key(lesson: Any) -> str | None:
    """A stable identity for one lesson, used to collapse duplicate promoted
    lessons in the client list. Mirrors the candidate dedup key (lowercased,
    whitespace-collapsed body) but SCOPES it by repo + codename: the same body
    under two different repos (or two different agents) is two distinct active
    lessons, each with its own row metadata and Undo, so they must not collapse
    into one. Only a body repeated for the same repo AND codename is a true
    duplicate (the same fact auto-promoted on several firings).

    Returns ``None`` when there is no body to key on, so such rows are left
    untouched rather than merged into a single empty-body entry."""
    body = _lesson_field(lesson, "body") or _lesson_field(lesson, "text")
    if not isinstance(body, str):
        return None
    normalized = re.sub(r"\s+", " ", body.strip().lower())
    if not normalized:
        return None
    repo = _lesson_field(lesson, "repo")
    codename = _lesson_field(lesson, "codename")
    scope_repo = repo.strip().lower() if isinstance(repo, str) else ""
    scope_codename = codename.strip().lower() if isinstance(codename, str) else ""
    return f"{scope_repo}\x1f{scope_codename}\x1f{normalized}"


def _dedupe_lessons_for_display(lessons: list[Any]) -> list[Any]:
    """Collapse promoted lessons that share an identical body.

    The recall chain merges backends by lesson id, so the same fact promoted
    more than once (e.g. auto-promoted on several firings before a human
    reviewed it) surfaces as several rows with distinct ids but identical text.
    Showing "Use the API fixture factory." five times is noise, so keep the
    first occurrence of each (repo, codename, body) (recall is already ordered by
    relevance and recency) and drop later identical ones. The same body under a
    different repo or agent is a distinct lesson and is kept. Lessons with no
    usable body key are always kept."""
    seen: set[str] = set()
    deduped: list[Any] = []
    for lesson in lessons:
        key = _lesson_display_key(lesson)
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
        deduped.append(lesson)
    return deduped


def _memory_status_filter(status: str) -> str | None:
    if status == "all":
        return None
    return status


def _lesson_field(lesson: Any, key: str) -> Any:
    payload = _jsonable(
        asdict(lesson) if is_dataclass(lesson) and not isinstance(lesson, type) else lesson
    )
    if isinstance(payload, dict):
        return payload.get(key)
    return None


def _recall_lessons_via_chain(request: Request, *, limit: int) -> list[Any]:
    """Recall the lessons Alfred is using across the whole provider chain.

    The promoted-lesson backend is Redis AMS, so the local SQLite ledger is
    empty by design on an AMS-primary install. Route the read through the
    configured chain (AMS + local, merged and deduped) so the client shows the
    lessons Alfred has actually promoted.

    A test/app-configured provider on ``request.app.state.memory_provider`` (or
    the shared ``planning_memory_provider``) is preferred so the recall reuses a
    single chain; otherwise it is built from env via ``recall_lessons``.
    """
    from memory.config import recall_lessons

    provider = getattr(request.app.state, "memory_provider", None) or _planning_memory_provider(
        request
    )
    return recall_lessons(limit=limit, provider=provider)


def _memory_brain(
    _request: Request,
    *,
    require_existing: bool,
) -> tuple[Any | None, str | None]:
    try:
        from fleet_brain import FleetBrain

        brain = FleetBrain.from_env()
        if require_existing:
            brain.health()
        return brain, None
    except Exception:  # pragma: no cover - defensive local API path
        logger.exception("_memory_brain: fleet brain unavailable")
        return None, _GENERIC_ERROR


async def _api_memory_candidate_action(
    request: Request,
    candidate_id: str,
    *,
    action: str,
) -> JSONResponse:
    if not _same_origin_post(request) or not _authorized_mutation(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    # Normalize a lesson recall id (``lesson:memory_candidate:<id>``) to the bare
    # candidate id BEFORE validation: /api/memory/lessons hands the client that
    # recall id, and ``_MEMORY_ID_RE`` rejects the colons in it, so without this
    # the retire route would 400 the very id it documents as accepted. Stripping
    # is a no-op for a bare id, so promote/reject stay unaffected. FleetBrain
    # owns the prefix, so import its stripper rather than re-encode it here.
    from fleet_brain import candidate_id_from_lesson_id

    candidate_id = candidate_id_from_lesson_id(candidate_id)
    if not _MEMORY_ID_RE.fullmatch(candidate_id):
        return JSONResponse({"error": "memory candidate id is invalid"}, status_code=400)
    body, error_response = await _read_json_body(request)
    if error_response is not None:
        return error_response
    note = str(body.get("note") or "").strip()
    reviewer = str(body.get("reviewer") or "local-client").strip() or "local-client"
    brain, error = _memory_brain(request, require_existing=True)
    if brain is None:
        return JSONResponse({"error": error or "fleet brain unavailable"}, status_code=500)
    from fleet_brain import MemoryPromotionError

    try:
        if action == "promote":
            lesson = brain.promote_memory_candidate(
                candidate_id,
                reviewer=reviewer,
                review_note=note,
            )
            if lesson is None:
                return JSONResponse({"error": "memory candidate not found"}, status_code=404)
            return JSONResponse(
                {
                    "candidate_id": candidate_id,
                    "lesson_id": _lesson_field(lesson, "id")
                    or f"lesson:memory_candidate:{candidate_id}",
                    "status": "validated",
                    "codename": _lesson_field(lesson, "codename") or _lesson_field(lesson, "agent"),
                    "repo": _lesson_field(lesson, "repo"),
                }
            )
        if action == "reject":
            candidate = brain.reject_memory_candidate(
                candidate_id,
                reviewer=reviewer,
                review_note=note,
            )
            if candidate is None:
                return JSONResponse({"error": "memory candidate not found"}, status_code=404)
            return JSONResponse(_candidate_to_api(candidate))
        if action == "retire":
            # Undo an auto-remembered lesson: forget it from AMS recall and flip
            # the row to retired. A 404 means the id is unknown or was never a
            # promoted lesson (nothing live to walk back).
            candidate = brain.retire_memory_candidate(
                candidate_id,
                reviewer=reviewer,
                note=note,
            )
            if candidate is None:
                return JSONResponse(
                    {"error": "memory lesson not found or not promoted"},
                    status_code=404,
                )
            return JSONResponse(_candidate_to_api(candidate))
    except MemoryPromotionError:
        # The promoted lesson is written to Redis AMS first; an unreachable AMS
        # leaves the candidate pending (no silent loss). Surface a retryable 503
        # with the generic body so no detail leaks.
        logger.exception("memory candidate %s promote: AMS write failed", candidate_id)
        return JSONResponse({"error": _GENERIC_ERROR}, status_code=503)
    except ValueError as exc:
        # FleetBrain.promote_memory_candidate / reject_memory_candidate raise
        # ValueError both for an unknown candidate id (a missing resource) and
        # for a found-but-inapplicable action. Distinguish on the message
        # INTERNALLY (it is never echoed) so a stale id stays a clean 404 while a
        # real validation rejection is a 400. Keep the generic body either way so
        # no exception detail leaks (py/stack-trace-exposure).
        logger.exception("memory candidate %s action %r failed", candidate_id, action)
        if "unknown candidate" in str(exc):
            return JSONResponse({"error": "memory candidate not found"}, status_code=404)
        return JSONResponse({"error": _GENERIC_ERROR}, status_code=400)
    return JSONResponse({"error": "unknown memory action"}, status_code=400)


async def _read_json_body(
    request: Request,
) -> tuple[dict[str, Any], JSONResponse | None]:
    raw = await request.body()
    if not raw:
        return {}, None
    try:
        body = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}, JSONResponse({"error": "request body must be JSON"}, status_code=400)
    if not isinstance(body, dict):
        return {}, JSONResponse({"error": "request body must be a JSON object"}, status_code=400)
    return body, None


def _today_cost_rollup(reader: Any) -> dict[str, Any]:
    """Aggregate today's spend + ok/fail counts across the fleet.

    Reuses the existing per-agent spend rollup in :mod:`metrics` over a
    one-day window, summing every agent's ``SpendTotals``. ``spend_usd`` is
    ``None`` (not ``0``) when no spend ledger exists for today, so the client
    can distinguish "no data surfaced" from a genuine zero-dollar day. Any
    failure (missing state root, import error) degrades to an empty rollup
    rather than blanking ``/api/status``.
    """
    empty = {
        "spend_usd": None,
        "firings": 0,
        "successes": 0,
        "failures": 0,
        "agents_with_spend": 0,
    }
    state_root = getattr(reader, "state_root", None)
    if not isinstance(state_root, Path):
        return empty
    try:
        from metrics import fleet_metrics

        report = fleet_metrics(state_root, days=1)
    except Exception:  # pragma: no cover - defensive: metrics is optional
        return empty
    spend = 0.0
    firings = 0
    successes = 0
    failures = 0
    agents_with_spend = 0
    saw_ledger = False
    for metric in report.metrics:
        totals = metric.spend
        if totals.firings or totals.cost_usd or totals.successes or totals.failures:
            saw_ledger = True
            agents_with_spend += 1
        spend += totals.cost_usd
        firings += totals.firings
        successes += totals.successes
        failures += totals.failures
    return {
        "spend_usd": round(spend, 4) if saw_ledger else None,
        "firings": firings,
        "successes": successes,
        "failures": failures,
        "agents_with_spend": agents_with_spend,
    }


def _active_intake_profile_name() -> str:
    """Return the active intake profile name ("plain" or "technical").

    Reads ``ALFRED_INTAKE_PROFILE`` via the same resolver the planning
    assistant uses, so the API and the refiner never disagree about which
    profile is live. Falls back to "technical" if the helper is unavailable.
    """
    try:
        from intake_profiles import active_intake_profile

        return active_intake_profile().name
    except Exception:  # pragma: no cover - defensive: profiles is optional
        return "technical"


def _selected_setup_repos() -> list[str]:
    try:
        from server import setup as setup_mod

        return list(setup_mod.selected_repos())
    except Exception:  # pragma: no cover - setup context is optional
        return []


def _selected_setup_repos_for_scope() -> list[str]:
    """Return setup repos only when they are safe to treat as confirmed scope."""
    repos = _selected_setup_repos()
    return repos if len(repos) == 1 else []


def _draft_with_selected_setup_scope(draft: IssueDraft) -> IssueDraft:
    """Use the selected setup repo as scope only when there is exactly one."""
    if draft.repos:
        return draft
    setup_scope = _selected_setup_repos_for_scope()
    return replace(draft, repos=setup_scope) if setup_scope else draft


def _compose_context_repos(body: dict[str, Any], *, base_draft: IssueDraft) -> list[str]:
    """Repos available for planning context, not necessarily implementation scope."""
    import compose_converse as cc

    return (
        cc.normalize_repos(body.get("context_repos"))
        or cc.normalize_repos(body.get("repos"))
        or list(base_draft.repos)
        or _selected_setup_repos()
    )


def _converse_memory_grounding(
    request: Request,
    *,
    messages: list[Any],
    base_draft: IssueDraft,
) -> str:
    """Recall relevant fleet lessons for a converse turn, gated on build intent.

    Memory is advisory grounding, not always-on injection: a "who are you?" or
    "how does review work?" turn should not pull lessons into the prompt. We
    pre-classify the latest user message with the same heuristic the turn parser
    uses (``compose_converse.resolve_intent``) and only recall when the turn
    plausibly describes work to plan. The recalled lessons are rendered with the
    existing planning-memory renderer and appended to the repo grounding, so no
    prompt-template change is needed and the lessons stay clearly advisory.

    Returns an empty string when the turn is conversational, no provider is
    configured, or nothing relevant is recalled, so the prompt is unchanged in
    the common case.
    """
    import compose_converse as cc

    # Reuse the single last-user-message extraction so this gate and the turn
    # parser always classify against identical input.
    last_user = cc.last_user_message(messages)
    intent = cc.resolve_intent(None, last_user_message=last_user, draft=base_draft, done=False)
    if intent != cc.INTENT_BUILD:
        return ""

    provider = _planning_memory_provider(request)
    if provider is None:
        return ""
    try:
        from planning_assistant import recall_planning_memory, render_planning_memory

        memory = recall_planning_memory(base_draft, provider, limit=3)
        if not memory:
            return ""
        rendered = render_planning_memory(memory).strip()
    except Exception:
        return ""
    if not rendered:
        return ""
    return (
        "\n\n## Lessons from past work\n\n"
        "Relevant lessons the fleet has already learned. Advisory only: use them "
        "to ask sharper questions, never to invent scope.\n\n" + rendered
    )


def _converse_operational_grounding(request: Request, *, conversation_engine: str) -> str:
    """Build the live fleet snapshot for a desktop Ask converse turn.

    Reads the same ``request.app.state.reader`` the Fleet view uses so the
    desktop Ask answers status questions ("what's the fleet doing?", "why did a
    run fail?", "what shipped today?") from real runtime state, matching the
    Slack surface. Best-effort: a missing reader or a read failure degrades to an
    empty string so the turn still answers from the repo grounding.
    """
    try:
        from converse_grounding import (
            build_engine_grounding,
            build_operational_grounding,
            operational_grounding_enabled,
        )
    except Exception:
        return ""
    if not operational_grounding_enabled():
        return ""

    sections: list[str] = []
    try:
        from server import setup as setup_mod

        sections.append(
            build_engine_grounding(
                setup_mod.engine_clis(),
                conversation_engine=conversation_engine,
            )
        )
    except Exception:
        pass
    try:
        reader = getattr(request.app.state, "reader", None)
        sections.append(build_operational_grounding(reader))
    except Exception:
        pass
    return "\n\n".join(section for section in sections if section)


def _selected_setup_repos_payload() -> dict[str, Any]:
    repos = _selected_setup_repos()
    return {"selected": repos, "count": len(repos)}


def _resolve_intake_profile_name(body: dict[str, Any]) -> str:
    """Pick the intake profile for one compose turn.

    A per-request ``plain`` boolean wins over the server env: ``true`` forces
    the plain (jargon-free) persona, ``false`` forces technical, and an absent
    flag falls back to ``ALFRED_INTAKE_PROFILE`` (the server default). Any
    non-boolean ``plain`` value is ignored so a malformed body cannot silently
    downgrade the persona. The toggle only changes the conversational surface;
    the structured draft and readiness scoring are identical in both modes.
    """
    plain = body.get("plain")
    if isinstance(plain, bool):
        return "plain" if plain else "technical"
    return _active_intake_profile_name()


def _compose_playbook_draft(
    request: Request, playbook: dict[str, Any], raw_repos: Any
) -> JSONResponse:
    """Compose a starter playbook into the same saved draft shape as Compose."""
    import compose_converse as cc

    spec = playbook.get("draft") or {}
    repos = cc.normalize_repos(raw_repos)
    if not repos:
        try:
            from server import setup as setup_mod

            repos = setup_mod.selected_repos()
        except Exception:  # pragma: no cover - setup is optional
            repos = []
    if not repos:
        repos = _payload_list(spec.get("repos"))
    draft = IssueDraft(
        title=str(spec.get("title") or playbook.get("title") or "").strip(),
        problem=str(spec.get("problem") or "").strip(),
        user=str(spec.get("user") or "").strip(),
        current_behavior=str(spec.get("current_behavior") or "").strip(),
        desired_behavior=str(spec.get("desired_behavior") or "").strip(),
        repos=cc.normalize_repos(repos),
        acceptance_criteria=_payload_list(spec.get("acceptance_criteria")),
        test_plan=str(spec.get("test_plan") or "").strip(),
        out_of_scope=str(spec.get("out_of_scope") or "").strip(),
        rollout=str(spec.get("rollout") or "").strip(),
        open_questions=str(spec.get("open_questions") or "").strip(),
    )
    memory_provider = _planning_memory_provider(request)
    assistant_result: PlanningAssistantResult = refine_issue_draft(
        draft, [], memory_provider=memory_provider
    )
    saved_path, draft_id = _save_compose_draft(
        request,
        draft=assistant_result.draft,
        assistant_result=assistant_result,
        draft_id=None,
        draft_path=None,
        prior_payload=None,
        revisions=[],
    )
    return JSONResponse(
        {
            "ok": True,
            "playbook": playbook.get("key"),
            "draft_id": draft_id,
            "saved_path": str(saved_path),
            "title": assistant_result.draft.title,
            "repos": list(assistant_result.draft.repos),
            "readiness": {
                "ok": assistant_result.readiness.ok,
                "score": assistant_result.readiness.score,
            },
        }
    )


def _theme_builder_prompt_path() -> Path:
    override = os.environ.get("ALFRED_THEME_BUILDER_PROMPT")
    if override:
        return Path(override)
    relative = Path("prompts") / "theme-builder.md"
    candidates: list[Path] = []
    runtime_home = os.environ.get("ALFRED_HOME")
    if runtime_home:
        candidates.append(Path(runtime_home) / relative)
    candidates.append(Path(__file__).resolve().parents[2] / relative)
    candidates.append(Path.cwd() / relative)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def _run_theme_builder_converse(request: Request, body: dict[str, Any]) -> JSONResponse:
    """One turn of the conversational roster theme builder.

    Reuses the compose converse plumbing (message parsing, untrusted transcript,
    action parsing, engine dispatch) but produces a draft-free theme turn: a
    reply plus an optional ``propose_theme`` action. Nothing is persisted here,
    the client saves a confirmed proposal via ``POST /api/roster-theme``. If no
    engine is configured (or it returns nothing usable) we degrade with a clear
    503 so the client falls back to the manual custom theme editor.
    """
    import compose_converse as cc
    import theme_builder as tb

    messages = cc.parse_messages(body.get("messages"))
    if not messages:
        return JSONResponse(
            {"error": "send at least one message to start the conversation"},
            status_code=400,
        )

    engine = tb.engine_from_env()
    if not engine:
        return JSONResponse(
            {
                "error": "live_session_unavailable",
                "detail": (
                    "No conversational engine is configured for the theme builder. "
                    "Set ALFRED_THEME_BUILDER_ENGINE (or the compose converse "
                    "engine) to enable the chat, or use the manual editor."
                ),
            },
            status_code=503,
        )

    try:
        from agent_runner.metadata import load_prompt
    except Exception:  # pragma: no cover - load_prompt is always importable
        return JSONResponse(
            {"error": "theme builder prompt loader unavailable"},
            status_code=503,
        )

    try:
        system_prompt = tb.render_system_prompt(
            prompt_path=_theme_builder_prompt_path(),
            loader=load_prompt,
        )
    except OSError:
        return JSONResponse(
            {
                "error": "live_session_unavailable",
                "detail": (
                    "The theme-builder prompt could not be loaded. Check the "
                    "runtime deploy, or use the manual editor."
                ),
            },
            status_code=503,
        )

    # ``run_turn`` distinguishes a terminal engine failure (returns ``None``) from
    # a transient malformed turn (returns a soft ``retry_turn`` with a reply and no
    # proposal). Only the terminal case degrades to the 503 the client treats as
    # engine-unavailable; the transient case flows through as a normal 200 turn so
    # the chat stays open and the person can just resend.
    turn = tb.run_turn(
        system_prompt=system_prompt,
        messages=messages,
        engine=engine,
        workdir=_planning_workdir(request),
        valid_slugs=tb.valid_codenames(),
        required_slugs=tb.required_codenames(),
    )
    if turn is None:
        return JSONResponse(
            {
                "error": "live_session_unavailable",
                "detail": (
                    "The theme-builder engine could not run this turn. "
                    "Check the runtime, or use the manual editor."
                ),
            },
            status_code=503,
        )
    return JSONResponse(tb.turn_payload(turn))


def _onboarding_prompt_path() -> Path:
    override = os.environ.get("ALFRED_ONBOARDING_PROMPT")
    if override:
        return Path(override)
    relative = Path("prompts") / "onboarding.md"
    candidates: list[Path] = []
    runtime_home = os.environ.get("ALFRED_HOME")
    if runtime_home:
        candidates.append(Path(runtime_home) / relative)
    candidates.append(Path(__file__).resolve().parents[2] / relative)
    candidates.append(Path.cwd() / relative)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def _run_onboarding_converse(request: Request, body: dict[str, Any]) -> JSONResponse:
    """One turn of the conversational Ask-driven onboarding guide.

    Reuses the compose converse plumbing (message parsing, untrusted transcript,
    action parsing, engine dispatch) but produces an onboarding turn: a reply
    plus an optional scoped action the client executes. Nothing is persisted
    here; the client runs the same setup handler the stepped flow uses. If no
    engine is configured (or it returns nothing usable) we degrade with a clear
    503 so the client falls back to the stepped onboarding flow.
    """
    import compose_converse as cc
    import onboarding_converse as ob

    messages = cc.parse_messages(body.get("messages"))
    if not messages:
        return JSONResponse(
            {"error": "send at least one message to start the conversation"},
            status_code=400,
        )

    engine = ob.engine_from_env()
    if not engine:
        return JSONResponse(
            {
                "error": "live_session_unavailable",
                "detail": (
                    "No conversational engine is configured for onboarding. Set "
                    "ALFRED_ONBOARDING_ENGINE (or the compose converse engine) to "
                    "enable the chat, or use the stepped setup."
                ),
            },
            status_code=503,
        )

    try:
        from agent_runner.metadata import load_prompt
    except Exception:  # pragma: no cover - load_prompt is always importable
        return JSONResponse(
            {"error": "onboarding prompt loader unavailable"},
            status_code=503,
        )

    try:
        system_prompt = ob.render_system_prompt(
            prompt_path=_onboarding_prompt_path(),
            loader=load_prompt,
        )
    except OSError:
        return JSONResponse(
            {
                "error": "live_session_unavailable",
                "detail": (
                    "The onboarding prompt could not be loaded. Check the runtime "
                    "deploy, or use the stepped setup."
                ),
            },
            status_code=503,
        )

    # ``run_turn`` distinguishes a terminal engine failure (returns ``None``) from
    # a transient malformed turn (returns a soft ``retry_turn`` with a reply and no
    # action). Only the terminal case degrades to the 503 the client treats as
    # engine-unavailable; the transient case flows through as a normal 200 turn so
    # the chat stays open and the person can just resend.
    turn = ob.run_turn(
        system_prompt=system_prompt,
        messages=messages,
        engine=engine,
        workdir=_planning_workdir(request),
    )
    if turn is None:
        return JSONResponse(
            {
                "error": "live_session_unavailable",
                "detail": (
                    "The onboarding engine could not run this turn. Check the "
                    "runtime, or use the stepped setup."
                ),
            },
            status_code=503,
        )
    return JSONResponse(ob.turn_payload(turn))


def _run_compose_converse(request: Request, body: dict[str, Any]) -> JSONResponse:
    """One turn of the conversational spec-builder; persists a compose draft.

    Reuses the same compose-draft storage as the one-shot path so the result
    shows in Plans and threads into the RequestThread. The live interrogator is
    routed through the existing agent-engine dispatch; if no engine is
    configured (or it returns nothing usable) we degrade with a clear 503 rather
    than fabricate a turn, and the client falls back to the one-shot form.
    """
    import compose_converse as cc

    messages = cc.parse_messages(body.get("messages"))
    if not messages:
        return JSONResponse(
            {"error": "send at least one message to start the conversation"},
            status_code=400,
        )

    engine = cc.converse_engine_from_env()
    if not engine:
        return JSONResponse(
            {
                "error": "live_session_unavailable",
                "detail": (
                    "No conversational engine is configured for Compose. Set "
                    "ALFRED_COMPOSE_CONVERSE_ENGINE (or the planning-assistant "
                    "engine) to enable the chat, or use the one-shot plan form."
                ),
            },
            status_code=503,
        )

    draft_id = _safe_compose_draft_id(body.get("draft_id"))
    prior_payload, prior_path = _read_compose_draft_payload(request, draft_id)
    base_draft = _converse_base_draft(body, prior_payload)
    base_draft = _draft_with_selected_setup_scope(base_draft)

    repos = _compose_context_repos(body, base_draft=base_draft)
    repo_grounding = cc.build_repo_grounding(
        repos,
        workspace_root=_compose_workspace_root(),
        repo_to_local=_compose_repo_to_local(),
    )
    # Recall fleet lessons only when this turn looks like real work (gated, not
    # always-on), and append them to the grounding as advisory context.
    repo_grounding += _converse_memory_grounding(request, messages=messages, base_draft=base_draft)
    code_map = cc.load_code_map(_compose_code_map_path())
    # Plain mode is per-request: the client toggle wins when present, and the
    # ALFRED_INTAKE_PROFILE server env is only the default when the body omits
    # the flag. This lets a non-developer flip jargon-free coaching on/off in
    # the app without restarting the runtime.
    intake_guidance = cc.intake_guidance_for(_resolve_intake_profile_name(body))
    operational_grounding = _converse_operational_grounding(
        request,
        conversation_engine=engine,
    )

    try:
        from agent_runner.metadata import load_prompt
    except Exception:  # pragma: no cover - load_prompt is always importable
        return JSONResponse(
            {"error": "compose interrogator prompt loader unavailable"},
            status_code=503,
        )

    try:
        system_prompt = cc.render_system_prompt(
            prompt_path=_compose_interrogator_prompt_path(),
            repo_grounding=repo_grounding,
            code_map=code_map,
            intake_guidance=intake_guidance,
            loader=load_prompt,
            operational_grounding=operational_grounding,
        )
    except OSError:
        return JSONResponse(
            {
                "error": "live_session_unavailable",
                "detail": (
                    "The spec-interrogator prompt could not be loaded. Check the "
                    "runtime deploy, or use the one-shot plan form."
                ),
            },
            status_code=503,
        )

    turn = cc.run_turn(
        system_prompt=system_prompt,
        messages=messages,
        repo_grounding=repo_grounding,
        code_map=code_map,
        intake_guidance=intake_guidance,
        base_draft=base_draft,
        engine=engine,
        workdir=_planning_workdir(request),
        on_condense=_converse_condense_recorder(request, draft_id=draft_id),
    )
    if turn is None:
        return JSONResponse(
            {
                "error": "live_session_unavailable",
                "detail": (
                    "The conversational engine did not return a usable turn. "
                    "Try again, or use the one-shot plan form."
                ),
            },
            status_code=503,
        )
    content_draft = replace(turn.draft, repos=[]) if turn.draft.repos else turn.draft
    if (
        getattr(turn, "intent", "build") == cc.INTENT_CONVERSATION
        and prior_payload is None
        and not _draft_has_signal(content_draft)
    ):
        return JSONResponse(_converse_turn_payload(turn, draft_id="", saved_path=None))

    saved_path, saved_id = _save_converse_draft(
        request,
        turn=turn,
        messages=messages,
        draft_id=draft_id,
        draft_path=prior_path,
        prior_payload=prior_payload,
    )
    return JSONResponse(_converse_turn_payload(turn, draft_id=saved_id, saved_path=saved_path))


def _stream_compose_converse(request: Request, body: dict[str, Any]) -> Any:
    """Token-stream one converse turn, then reconcile + persist (#36).

    Shares the converse contract of ``_run_compose_converse``: same validation,
    same engine resolution + degrade signals, same draft persistence, same final
    ``ConverseResponse`` payload. The only difference is the transport, the turn
    runs on a worker thread while the assistant text it tees to its transcript is
    streamed as ``token`` SSE events, and the reconciled response arrives as a
    ``result`` event. Request/auth/setup validation failures return normal JSON
    4xx/503 responses (no stream opened); "no live engine" opens a short stream
    and emits an ``error`` event so the client can fall back without a browser
    console resource error.
    """
    import compose_converse as cc

    from server import streaming

    # The packaged webview reaches this route cross-origin, so every response
    # (setup-stage 4xx/503 JSON and the SSE stream alike) must carry CORS
    # headers or the webview cannot read the degrade signal to fall back.
    cors = _streaming_cors_headers(request)

    messages = cc.parse_messages(body.get("messages"))
    if not messages:
        return JSONResponse(
            {"error": "send at least one message to start the conversation"},
            status_code=400,
            headers=cors,
        )

    engine = cc.converse_engine_from_env()
    if not engine:
        return _compose_stream_unavailable(
            request,
            message=(
                "No conversational engine is configured for Compose. Set "
                "ALFRED_COMPOSE_CONVERSE_ENGINE (or the planning-assistant "
                "engine) to enable the chat, or use the one-shot plan form."
            ),
        )

    draft_id = _safe_compose_draft_id(body.get("draft_id"))
    prior_payload, prior_path = _read_compose_draft_payload(request, draft_id)
    base_draft = _converse_base_draft(body, prior_payload)
    base_draft = _draft_with_selected_setup_scope(base_draft)

    repos = _compose_context_repos(body, base_draft=base_draft)
    repo_grounding = cc.build_repo_grounding(
        repos,
        workspace_root=_compose_workspace_root(),
        repo_to_local=_compose_repo_to_local(),
    )
    # Recall fleet lessons only when this turn looks like real work (gated, not
    # always-on), and append them to the grounding as advisory context.
    repo_grounding += _converse_memory_grounding(request, messages=messages, base_draft=base_draft)
    code_map = cc.load_code_map(_compose_code_map_path())
    # Plain mode is per-request: the client toggle wins when present, and the
    # ALFRED_INTAKE_PROFILE server env is only the default when the body omits
    # the flag. This lets a non-developer flip jargon-free coaching on/off in
    # the app without restarting the runtime.
    intake_guidance = cc.intake_guidance_for(_resolve_intake_profile_name(body))
    operational_grounding = _converse_operational_grounding(
        request,
        conversation_engine=engine,
    )

    try:
        from agent_runner.metadata import load_prompt
    except Exception:  # pragma: no cover - load_prompt is always importable
        return JSONResponse(
            {"error": "compose interrogator prompt loader unavailable"},
            status_code=503,
            headers=cors,
        )

    try:
        system_prompt = cc.render_system_prompt(
            prompt_path=_compose_interrogator_prompt_path(),
            repo_grounding=repo_grounding,
            code_map=code_map,
            intake_guidance=intake_guidance,
            loader=load_prompt,
            operational_grounding=operational_grounding,
        )
    except OSError:
        return JSONResponse(
            {
                "error": "live_session_unavailable",
                "detail": (
                    "The spec-interrogator prompt could not be loaded. Check the "
                    "runtime deploy, or use the one-shot plan form."
                ),
            },
            status_code=503,
            headers=cors,
        )

    # Pre-mint the firing id so we can tail its transcript while the model runs.
    firing_id = cc.converse_firing_id()
    transcript = _converse_transcript_path(request, firing_id)
    workdir = _planning_workdir(request)

    on_condense = _converse_condense_recorder(request, draft_id=draft_id)

    def _run() -> Any:
        return cc.run_turn(
            system_prompt=system_prompt,
            messages=messages,
            repo_grounding=repo_grounding,
            code_map=code_map,
            intake_guidance=intake_guidance,
            base_draft=base_draft,
            engine=engine,
            workdir=workdir,
            firing_id=firing_id,
            on_condense=on_condense,
        )

    def _reconcile(turn: Any) -> dict[str, Any]:
        content_draft = replace(turn.draft, repos=[]) if turn.draft.repos else turn.draft
        if (
            getattr(turn, "intent", "build") == cc.INTENT_CONVERSATION
            and prior_payload is None
            and not _draft_has_signal(content_draft)
        ):
            return _converse_turn_payload(turn, draft_id="", saved_path=None)
        saved_path, saved_id = _save_converse_draft(
            request,
            turn=turn,
            messages=messages,
            draft_id=draft_id,
            draft_path=prior_path,
            prior_payload=prior_payload,
        )
        return _converse_turn_payload(turn, draft_id=saved_id, saved_path=saved_path)

    generator = streaming.stream_converse_turn(
        run_turn=_run,
        extract_tokens=streaming.assistant_text_fragments,
        transcript_path=transcript,
        reconcile=_reconcile,
    )
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers=_streaming_cors_headers(
            request,
            {
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        ),
    )


def _compose_stream_unavailable(request: Request, *, message: str) -> StreamingResponse:
    """Emit the stream-route no-engine signal without a failing HTTP status.

    The Ask client treats ``detail: live_session_unavailable`` exactly like the
    buffered route's 503 and falls back to the one-shot draft endpoint. Keeping
    the streaming HTTP status 200 avoids a red browser resource error in the
    hosted desktop UI, while the non-streaming ``/api/compose/converse`` route
    still returns a plain 503 for API callers.
    """
    from server import streaming

    frames = (
        streaming._sse("open", {}),
        streaming._sse("error", {"detail": "live_session_unavailable", "message": message}),
    )
    return StreamingResponse(
        iter(frames),
        media_type="text/event-stream",
        headers=_streaming_cors_headers(
            request,
            {
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        ),
    )


def _converse_turn_payload(turn: Any, *, draft_id: str, saved_path: Path | None) -> dict[str, Any]:
    """The ``ConverseResponse`` dict both converse routes return.

    Kept identical to the non-streaming route's JSON body so the client's
    reconcile step gets the same shape whether it streamed or not.
    """
    return {
        "draft_id": draft_id,
        "saved_path": str(saved_path) if saved_path is not None else "",
        "reply": turn.reply,
        # The turn kind: "conversation" (a plain answer) or "build" (a planning
        # turn). The client renders the inline plan card only for "build" turns,
        # so a "who are you?" answer reads as a normal chat reply.
        "intent": getattr(turn, "intent", "build"),
        # OPTIONAL client-executable action REQUEST for this turn, or null. The
        # model names an allowlisted tool + args; a later client orchestrator
        # executes it under the token gate (nothing runs server-side here).
        # Backward-compatible: existing consumers that ignore this are unaffected.
        "action": _converse_action_payload(getattr(turn, "action", None)),
        "readiness": {
            "score": turn.readiness.score,
            "ready": turn.readiness.ready,
            "missing": list(turn.readiness.missing),
        },
        "done": turn.done,
        "draft": {
            "title": turn.draft.title,
            "problem": turn.draft.problem,
            "user": turn.draft.user,
            "current_behavior": turn.draft.current_behavior,
            "desired_behavior": turn.draft.desired_behavior,
            "repos": list(turn.draft.repos),
            "acceptance_criteria": list(turn.draft.acceptance_criteria),
            "test_plan": turn.draft.test_plan,
            "out_of_scope": turn.draft.out_of_scope,
            "rollout": turn.draft.rollout,
            "open_questions": turn.draft.open_questions,
        },
    }


def _converse_action_payload(action: Any) -> dict[str, Any] | None:
    """Serialize an optional ``ConverseAction`` as ``{tool, args}`` or ``None``.

    ``compose_converse.parse_action`` has already validated the tool against the
    allowlist and bounded the args, so this only projects the object to JSON.
    ``None`` (the common case: a turn requested no action) serializes to ``null``.
    """
    if action is None:
        return None
    return {
        "tool": getattr(action, "tool", ""),
        "args": dict(getattr(action, "args", {}) or {}),
    }


def _converse_transcript_path(request: Request, firing_id: str) -> Path:
    """Resolve the transcript JSONL a converse turn tees to under the state root.

    Mirrors ``agent_runner.transcript_path`` bucketing
    (``transcripts/<agent>/<YYYY-MM>/<firing_id>.jsonl``) against the serve
    state root so the token stream tails the same file the Claude streaming
    path writes, without importing the full runtime.
    """
    import compose_converse as cc

    month = datetime.now(UTC).strftime("%Y-%m")
    return _state_root(request) / "transcripts" / cc.CONVERSE_AGENT / month / f"{firing_id}.jsonl"


def _converse_base_draft(body: dict[str, Any], prior_payload: dict[str, Any] | None) -> IssueDraft:
    """Carry the spec forward across turns: prior saved draft, then body draft."""
    import compose_converse as cc

    raw_draft = body.get("draft")
    if isinstance(raw_draft, dict):
        return cc.draft_from_payload(raw_draft)
    if prior_payload is not None:
        prior_draft = prior_payload.get("draft")
        if isinstance(prior_draft, dict):
            return cc.draft_from_payload(prior_draft)
    return IssueDraft(title="")


def _save_converse_draft(
    request: Request,
    *,
    turn: Any,
    messages: list[Any],
    draft_id: str | None,
    draft_path: Path | None,
    prior_payload: dict[str, Any] | None,
) -> tuple[Path, str]:
    """Persist the conversation + accumulating spec as a compose planning draft.

    Reuses the compose-draft directory + readiness/spec_body shape so the saved
    record is interchangeable with the one-shot path in Plans listings, while
    adding the conversational transcript and the model-judged readiness.
    """
    from planning_assistant import render_development_spec

    root = _state_planning_root(request)
    root.mkdir(parents=True, exist_ok=True)
    if draft_path is None:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        draft_id = f"{_COMPOSE_PREFIX}{stamp}-{_slug(turn.draft.title)}"
        draft_path = root / f"{draft_id}.json"
    elif draft_id is None:
        draft_id = draft_path.stem
    created_at = (
        str(prior_payload.get("created_at"))
        if prior_payload and prior_payload.get("created_at")
        else datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    rubric = assess_issue_draft(turn.draft)
    spec_body = render_development_spec(turn.draft, readiness=rubric)
    conversation = [{"role": message.role, "content": message.content} for message in messages]
    conversation.append({"role": "assistant", "content": turn.reply})
    payload = {
        "source": "compose",
        "mode": "converse",
        "draft_id": draft_id,
        "created_at": created_at,
        "updated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "draft": asdict(turn.draft),
        "issue_body": rubric.issue_body,
        "spec_body": spec_body,
        # Persist BOTH readinesses: the model-judged verdict (primary, drives the
        # UI meter) and the deterministic rubric (the secondary signal), so a
        # later reader can see why a spec was or was not handed off.
        "readiness": {
            "ok": turn.readiness.ready,
            "score": turn.readiness.score,
            "missing": list(turn.readiness.missing),
        },
        "rubric_readiness": asdict(rubric),
        "questions": list(turn.readiness.missing),
        "done": turn.done,
        "conversation": conversation,
        "revision_count": len(conversation),
        "revisions": [message["content"] for message in conversation],
    }
    tmp = draft_path.with_name(f"{draft_path.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(draft_path)
    return draft_path, draft_id


def _compose_workspace_root() -> Path:
    try:
        from agent_runner.paths import WORKSPACE

        return Path(WORKSPACE)
    except Exception:  # pragma: no cover - defensive
        base = os.environ.get("WORKSPACE_ROOT") or os.path.expanduser("~/code")
        return Path(base) / "product"


def _compose_repo_to_local() -> dict[str, str]:
    try:
        from agent_runner.github import GH_REPO_TO_LOCAL

        return dict(GH_REPO_TO_LOCAL)
    except Exception:  # pragma: no cover - defensive
        return {}


def _compose_code_map_path() -> Path:
    base = os.environ.get("ALFRED_HOME") or os.path.expanduser("~/.alfred")
    return Path(base) / "state" / "code-map.json"


def _compose_interrogator_prompt_path() -> Path:
    override = os.environ.get("ALFRED_SPEC_INTERROGATOR_PROMPT")
    if override:
        return Path(override)
    relative = Path("prompts") / "spec-interrogator.md"
    candidates: list[Path] = []
    runtime_home = os.environ.get("ALFRED_HOME")
    if runtime_home:
        candidates.append(Path(runtime_home) / relative)
    candidates.append(Path(__file__).resolve().parents[2] / relative)
    candidates.append(Path.cwd() / relative)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def _first(form: dict[str, list[str]], key: str) -> str:
    return (form.get(key) or [""])[0].strip()


def _lines(value: str) -> list[str]:
    return [line.strip().lstrip("- ").strip() for line in value.splitlines() if line.strip()]


def _save_issue_draft(request: Request, draft: IssueDraft, body: str) -> Path:
    return _save_planning_text(request, draft, body, directory="planning-drafts", suffix="issue")


_COMPOSE_PREFIX = "compose-"


def _safe_compose_draft_id(raw: Any) -> str | None:
    """Validate a caller-supplied compose draft id, or return ``None``."""
    if raw is None:
        return None
    candidate = str(raw).strip()
    if not candidate:
        return None
    if not candidate.startswith(_COMPOSE_PREFIX):
        return None
    if "/" in candidate or "\\" in candidate or candidate.startswith("."):
        return None
    if not re.fullmatch(r"[A-Za-z0-9._-]+", candidate):
        return None
    return candidate


def _compose_base_draft(body: dict[str, Any], prior_payload: dict[str, Any] | None) -> IssueDraft:
    """Build the starting draft from an explicit ``draft`` block or the prior save."""
    raw_draft = body.get("draft")
    if isinstance(raw_draft, dict):
        return _draft_from_payload(raw_draft)
    if prior_payload is not None:
        prior_draft = prior_payload.get("draft")
        if isinstance(prior_draft, dict):
            return _draft_from_payload(prior_draft)
    return IssueDraft(title=str(body.get("title") or "").strip())


def _compose_draft_messages(
    text: str,
    base_draft: IssueDraft,
) -> list[str]:
    """Return messages for the deterministic compose fallback.

    The native Plan screen accepts plain prose. When no live refiner is
    configured, sending that prose directly through ``refine_issue_draft`` only
    stores it as an operator note, leaving the draft empty. For the reliable
    one-shot endpoint, synthesize a starter spec from plain prose so the UI can
    show a useful draft even offline. If a refiner is available or the text is
    already field-shaped, preserve the operator's exact message.
    """

    clean = str(text or "").strip()
    if not clean:
        return []
    if _compose_text_has_field_commands(clean):
        return [clean]
    return [_plain_compose_intent_to_fields(clean, base_draft)]


def _compose_text_has_field_commands(text: str) -> bool:
    for raw in text.splitlines():
        line = raw.strip()
        if not line or ":" not in line:
            continue
        field = line.split(":", 1)[0].strip()
        if not field or len(field) > 30 or not field[:1].isalpha():
            continue
        normalized = " ".join(field.replace("_", " ").replace("-", " ").lower().split())
        if normalized in {
            "acceptance",
            "acceptance criteria",
            "context",
            "current",
            "current behavior",
            "desired",
            "desired behavior",
            "non goal",
            "non goals",
            "open question",
            "open questions",
            "out of scope",
            "problem",
            "repo",
            "repos",
            "repositories",
            "rollout",
            "test",
            "test plan",
            "tests",
            "title",
            "user",
        }:
            return True
    return False


def _plain_compose_intent_to_fields(text: str, base_draft: IssueDraft) -> str:
    clean = _compact_plain_text(text)
    title = base_draft.title or _plain_compose_title(clean)
    user = base_draft.user or _plain_compose_user(clean)
    problem = base_draft.problem or f"The current flow does not yet make this outcome easy: {clean}"
    current = (
        base_draft.current_behavior
        or "The operator must manually turn the idea into implementation-ready work."
    )
    desired = base_draft.desired_behavior or clean
    acceptance = list(base_draft.acceptance_criteria) or _plain_compose_acceptance(clean)
    repos = base_draft.repos or _plain_compose_repos(clean)
    test_plan = (
        base_draft.test_plan
        or "From the desktop Plan screen, submit the plain-language request and verify Alfred uses the selected repo context, saves a clear plan, and asks only for genuinely missing details."
    )
    out_of_scope = (
        base_draft.out_of_scope
        or "Starting implementation, opening a PR, or merging work before human approval."
    )
    rollout = base_draft.rollout or "Use the normal Alfred plan review and GitHub issue flow."
    lines = [
        f"title: {title}",
        f"problem: {problem}",
        f"user: {user}",
        f"current: {current}",
        f"desired: {desired}",
        *(f"repo: {repo}" for repo in repos),
        *(f"acceptance: {item}" for item in acceptance),
        f"test: {test_plan}",
        f"out of scope: {out_of_scope}",
        f"rollout: {rollout}",
    ]
    return "\n".join(lines)


def _compact_plain_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


_SCAN_TITLE_SUFFIX = " is hard to scan at small window sizes"


def _scan_title_subject(text: str) -> str:
    """Extract the subject of a "the SUBJECT is hard to scan ..." request.

    Replaces a backtracking ``\\bthe (.+?) is hard to scan at small window
    sizes\\b`` regex that ran in quadratic time on a hostile body of repeated
    ``the ...`` prefixes (py/polynomial-redos on ``POST /api/plans/draft``).
    This walks the (already whitespace-collapsed) text with ``str.find`` only,
    so the cost is strictly linear in the input length: locate the fixed suffix
    once, then take the nearest preceding ``the `` token as the subject start.
    Returns the cleaned subject, or ``""`` when the phrase is absent.
    """
    lowered = text.lower()
    suffix_at = lowered.find(_SCAN_TITLE_SUFFIX)
    if suffix_at < 0:
        return ""
    # Honour the trailing ``\b`` the old regex required after "sizes": the suffix
    # must end at a word boundary (end of text or a non-word char), so a run-on
    # like "...window sizesxyz" does not count as a match.
    suffix_end = suffix_at + len(_SCAN_TITLE_SUFFIX)
    if suffix_end < len(lowered):
        nxt = lowered[suffix_end]
        if nxt.isalnum() or nxt == "_":
            return ""
    # ``\bthe `` before the subject: the first "the " token that begins on a word
    # boundary and falls before the suffix. Scanning left to right mirrors the
    # old ``\bthe`` anchor (which matched the earliest valid occurrence) without
    # any backtracking. Each find advances ``cursor`` past the rejected hit, so
    # the whole loop is linear in the input length.
    token = "the "
    cursor = 0
    while True:
        start = lowered.find(token, cursor, suffix_at)
        if start < 0:
            return ""
        prev = "" if start == 0 else lowered[start - 1]
        if start == 0 or not (prev.isalnum() or prev == "_"):
            # Word boundary before "the" (start of text, or a non-word char).
            break
        # Inside another word (e.g. "breathe "): skip this hit and keep scanning.
        cursor = start + 1
    subject_start = start + len(token)
    subject = _compact_plain_text(text[subject_start:suffix_at]).strip(" ,.;:")
    return subject


def _plain_compose_title(text: str) -> str:
    # Collapse all whitespace runs to single spaces up front. Every regex below
    # then matches single-space separators (" ") instead of unbounded "\s+", so
    # a hostile request body padded with long whitespace runs cannot drive
    # polynomial backtracking (py/polynomial-redos): the search space no longer
    # contains repeated-whitespace input for the quantifiers to chew through.
    # The scan-title heuristic is handled by _scan_title_subject, which uses a
    # single linear str.find pass instead of a backtracking "the (.+?) sizes"
    # regex, so repeated "the ..." prefixes can no longer drive quadratic time.
    text = _compact_plain_text(text)
    lowered = text.lower()
    if "plan work" in lowered and "github issue" in lowered:
        return "Plan work drafts reviewable GitHub issues"
    if "setup" in lowered and ("github" in lowered or "repo" in lowered):
        return "Improve Alfred setup flow"
    scan_subject = _scan_title_subject(text)
    if scan_subject:
        return f"Make {scan_subject} usable at small sizes"
    title = re.sub(
        r"^(please |can you |could you |i want |we need )",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    title = re.split(r" (?:so that|so|because) ", title, maxsplit=1, flags=re.IGNORECASE)[0]
    if len(title) > 92:
        title = title[:92].rsplit(" ", 1)[0].rstrip(" ,.;:")
    return title[:1].upper() + title[1:] if title else "Plan Alfred work"


def _compose_question_intent(text: str, base_draft: IssueDraft) -> bool:
    """True when ``text`` is a plain question, not a change request.

    Thin wrapper over the shared, deterministic
    ``compose_converse.classify_message_intent`` so the one-shot draft fallback
    routes questions to a conversational answer instead of a fabricated plan,
    using the exact same intent semantics the converse path uses (no forked
    logic). Imported lazily to keep the module import graph light.
    """
    import compose_converse as cc

    return cc.classify_message_intent(text, draft=base_draft) == cc.INTENT_CONVERSATION


def _compose_question_reply(draft_id: str | None) -> dict[str, Any]:
    """A conversational answer for a question sent to the one-shot draft endpoint.

    The Ask surface reaches this endpoint only as the no-live-engine fallback.
    When the turn is a plain question (classified with the shared
    ``compose_converse`` backstop), returning a fabricated starter plan is the
    wrong surface: questions get answers. Since no conversational engine is
    configured on this path, we cannot answer the question's content here, so we
    say so plainly and honestly (``intent: "conversation"`` so the client renders
    it as a normal chat reply with no plan card), instead of masquerading the
    engine gap as a plan. The empty ``draft``/``readiness`` keep the response
    shape compatible with ``ComposeDraftResponse`` while carrying no plan.
    """
    return {
        "draft_id": draft_id or "",
        "saved_path": "",
        "title": "",
        "intent": "conversation",
        "readiness": {"ok": False, "score": 0},
        "questions": [],
        "findings": [],
        "summary": (
            "That is a question, so I did not start a plan. Answering it needs "
            "Alfred's conversational engine, which is not configured on this "
            "connection. Open the desktop app with a live engine to get an "
            "answer, or describe a change you want made and I will shape a plan."
        ),
        "spec_body": "",
        "revision_count": 0,
        "draft": {
            "title": "",
            "problem": "",
            "user": "",
            "current_behavior": "",
            "desired_behavior": "",
            "repos": [],
            "acceptance_criteria": [],
            "test_plan": "",
            "out_of_scope": "",
            "rollout": "",
            "open_questions": "",
        },
    }


def _compose_draft_response_summary(
    result: PlanningAssistantResult,
    *,
    synthesized_plain_intent: bool,
) -> str:
    if not synthesized_plain_intent:
        return result.summary
    if result.readiness.ok:
        return "I saved a starter plan that is ready to review."
    missing_codes = {finding.code for finding in result.readiness.findings}
    if missing_codes == {"missing_repo_scope"}:
        return "I saved a starter plan. Tell Alfred which part of the workspace this should change."
    question_count = len(result.questions)
    if question_count:
        label = "question" if question_count == 1 else "questions"
        return (
            f"I saved a starter plan. Answer {question_count} remaining {label} to make it ready."
        )
    return "I saved a starter plan. Review the plan before filing the issue."


def _plain_compose_user(text: str) -> str:
    patterns = [
        r"\bhelp\s+(?:a|an|the)?\s*([^,.]+?)\s+(?:turn|create|file|review|approve|understand|connect|plan|ship|use)\b",
        r"\bfor\s+(?:a|an|the)?\s*([^,.]+?)(?:\s+to\b|\s+so\b|\s+with\b|$)",
        r"\bso\s+(?:a|an|the)?\s*([^,.]+?)\s+can\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            user = _compact_plain_text(match.group(1)).strip(" ,.;:")
            if 2 <= len(user) <= 80:
                return user[:1].upper() + user[1:]
    return "Not specified."


def _plain_compose_acceptance(text: str) -> list[str]:
    lowered = text.lower()
    items = [
        "A user can describe the desired outcome in plain language.",
        "Alfred saves a reviewable GitHub issue draft from the request.",
    ]
    if "acceptance" in lowered or "criteria" in lowered:
        items.append("The draft includes concrete acceptance criteria.")
    if "label" in lowered:
        items.append("The draft includes the Alfred agent labels needed for pickup.")
    if "approval" in lowered or "approve" in lowered:
        items.append("The UI shows a clear approval path before any agent starts.")
    if "non-technical" in lowered or "non technical" in lowered:
        items.append("The copy avoids unexplained technical jargon.")
    if len(items) < 4:
        items.append(
            "Alfred uses the selected repo context instead of asking the user to re-enter it."
        )
    return items


def _plain_compose_repos(text: str) -> list[str]:
    repos: list[str] = []
    for match in re.finditer(r"\b([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\b", text):
        repo = match.group(1).strip(".,;:()[]{}")
        if repo and repo not in repos:
            repos.append(repo)
    return repos


def _draft_from_payload(payload: dict[str, Any]) -> IssueDraft:
    # Route repos through the same slug gate the converse path uses
    # (cc.normalize_repos -> _valid_repo_slug). The one-shot draft loader must
    # not persist invalid slugs (e.g. "acme/..") into stored draft JSON, where a
    # future consumer resolving them to a workspace path would reopen the
    # traversal that the converse path closes at the chokepoint.
    import compose_converse as cc

    return IssueDraft(
        title=str(payload.get("title") or "").strip(),
        problem=str(payload.get("problem") or "").strip(),
        user=str(payload.get("user") or "").strip(),
        current_behavior=str(payload.get("current_behavior") or "").strip(),
        desired_behavior=str(payload.get("desired_behavior") or "").strip(),
        repos=cc.normalize_repos(_payload_list(payload.get("repos"))),
        acceptance_criteria=_payload_list(payload.get("acceptance_criteria")),
        test_plan=str(payload.get("test_plan") or "").strip(),
        out_of_scope=str(payload.get("out_of_scope") or "").strip(),
        rollout=str(payload.get("rollout") or "").strip(),
        open_questions=str(payload.get("open_questions") or "").strip(),
    )


def _payload_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return _lines(value)
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _draft_has_signal(draft: IssueDraft) -> bool:
    return bool(
        draft.title
        or draft.problem
        or draft.desired_behavior
        or draft.repos
        or draft.acceptance_criteria
    )


def _existing_revisions(prior_payload: dict[str, Any] | None) -> tuple[str, ...]:
    if not prior_payload:
        return ()
    raw = prior_payload.get("revisions")
    if not isinstance(raw, list):
        return ()
    return tuple(str(item).strip() for item in raw if str(item).strip())


def _read_compose_draft_payload(
    request: Request, draft_id: str | None
) -> tuple[dict[str, Any] | None, Path | None]:
    if not draft_id:
        return None, None
    root = _state_planning_root(request)
    if not root.is_dir():
        return None, None
    for path in root.glob(f"{_COMPOSE_PREFIX}*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        saved_id = str(payload.get("draft_id") or path.stem).strip()
        if saved_id == draft_id:
            return payload, path
    return None, None


def _save_compose_draft(
    request: Request,
    *,
    draft: IssueDraft,
    assistant_result: PlanningAssistantResult,
    draft_id: str | None,
    draft_path: Path | None,
    prior_payload: dict[str, Any] | None,
    revisions: list[str],
) -> tuple[Path, str]:
    root = _state_planning_root(request)
    root.mkdir(parents=True, exist_ok=True)
    if draft_path is None:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        draft_id = f"{_COMPOSE_PREFIX}{stamp}-{_slug(draft.title)}"
        draft_path = root / f"{draft_id}.json"
    elif draft_id is None:
        draft_id = draft_path.stem
    created_at = (
        str(prior_payload.get("created_at"))
        if prior_payload and prior_payload.get("created_at")
        else datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    payload = {
        "source": "compose",
        "draft_id": draft_id,
        "created_at": created_at,
        "updated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "draft": asdict(draft),
        "issue_body": assistant_result.issue_body,
        "spec_body": assistant_result.spec_body,
        "readiness": asdict(assistant_result.readiness),
        "questions": list(assistant_result.questions),
        "memory": [asdict(item) for item in assistant_result.memory],
        "revision_count": len(revisions),
        "revisions": revisions,
    }
    tmp = draft_path.with_name(f"{draft_path.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(draft_path)
    return draft_path, draft_id


def _list_compose_drafts(request: Request) -> list[dict[str, Any]]:
    root = _state_planning_root(request)
    if not root.is_dir():
        return []
    drafts: list[tuple[float, dict[str, Any]]] = []
    for path in root.glob(f"{_COMPOSE_PREFIX}*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        raw_draft = payload.get("draft")
        draft = raw_draft if isinstance(raw_draft, dict) else {}
        raw_readiness = payload.get("readiness")
        readiness = raw_readiness if isinstance(raw_readiness, dict) else {}
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        drafts.append(
            (
                mtime,
                {
                    "draft_id": path.stem,
                    "title": str(draft.get("title") or "Compose draft"),
                    "readiness": {
                        "ok": bool(readiness.get("ok")),
                        "score": readiness.get("score"),
                    },
                    "revision_count": payload.get("revision_count") or 0,
                    "updated_at": payload.get("updated_at") or payload.get("created_at"),
                },
            )
        )
    drafts.sort(key=lambda item: item[0], reverse=True)
    return [row for _mtime, row in drafts]


def _file_planning_draft_issue(state_root: Path, plan_id: str) -> dict[str, Any]:
    """Create fleet-pickup GitHub issue work from a saved planning draft.

    The native client calls this only after an explicit local File issue action.
    Safety still comes from the same bridge rules as Slack: readiness must pass,
    repos must be allowlisted, and an existing ``bridge.issue_url`` or bundle
    URL map makes the operation idempotent.
    """
    from slack_issue_bridge import BridgeConfig, SlackIssueBridge

    import server.setup as setup_mod

    draft_id = _safe_planning_draft_id(plan_id)
    if draft_id is None:
        raise ValueError("plan id is not a safe planning draft id")
    path = Path(state_root) / "planning-drafts" / f"{draft_id}.json"
    if not path.is_file():
        raise FileNotFoundError(draft_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"could not read planning draft: {type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise ValueError("planning draft is not a JSON object")

    base = BridgeConfig.from_env()
    repos = base.repos or frozenset(setup_mod.selected_repos())
    bridge = SlackIssueBridge(
        config=BridgeConfig(
            enabled=True,
            repos=repos,
            label=base.label,
            approval_phrases=base.approval_phrases,
            min_readiness_score=base.min_readiness_score,
            approval_reactions=base.approval_reactions,
        )
    )
    existing_issue_url = _planning_draft_issue_url(payload)
    outcome = bridge.convert(
        payload,
        trusted=True,
        thread_link="",
        already_converted=bool(existing_issue_url),
        origin="native-client",
    )
    issue_url = outcome.issue_url or existing_issue_url
    repo = outcome.repo or _first_draft_repo(payload)
    issue_urls = [issue_url] if issue_url else []
    issues_by_repo = {repo: issue_url} if repo and issue_url else {}
    repos_out = [repo] if repo else []
    labels_out = [base.label] if base.label else []

    if outcome.status == "already_converted" and issue_url:
        return {
            "ok": True,
            "status": "already_filed",
            "draft_id": draft_id,
            "issue_url": issue_url,
            "issue_urls": issue_urls,
            "issues_by_repo": issues_by_repo,
            "repo": repo,
            "repos": repos_out,
            "label": base.label,
            "labels": labels_out,
            "detail": outcome.detail,
        }
    if not outcome.created:
        return {
            "ok": False,
            "status": outcome.status,
            "draft_id": draft_id,
            "repo": repo,
            "label": base.label,
            "labels": labels_out,
            "error": outcome.detail or outcome.status,
        }

    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload["bridge"] = {
        "converted": True,
        "issue_url": issue_url,
        "issue_urls": issue_urls,
        "issues_by_repo": issues_by_repo,
        "repo": repo,
        "repos": repos_out,
        "label": base.label,
        "labels": labels_out,
        "filed_at": now,
        "source": "native-client",
    }
    payload["updated_at"] = now
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
    return {
        "ok": True,
        "status": "filed",
        "draft_id": draft_id,
        "issue_url": issue_url,
        "issue_urls": issue_urls,
        "issues_by_repo": issues_by_repo,
        "repo": repo,
        "repos": repos_out,
        "label": base.label,
        "labels": labels_out,
        "detail": outcome.detail,
    }


def _discard_planning_draft_group(state_root: Path, draft_id: str) -> dict[str, Any]:
    """Archive every visible duplicate represented by one planning draft card."""
    root = Path(state_root)
    plan = FilesystemReader(state_root=root).get_plan(draft_id)
    if plan is None:
        return _discard_planning_draft(root, draft_id)

    draft_ids = _planning_draft_discard_group_ids(root, plan)
    results: list[dict[str, Any]] = []
    for candidate_id in draft_ids:
        try:
            results.append(_discard_planning_draft(root, candidate_id))
        except FileNotFoundError:
            if candidate_id == draft_id:
                raise
            continue
    if not results:
        raise FileNotFoundError(draft_id)

    archived_paths = [
        str(result["archived_path"]) for result in results if result.get("archived_path")
    ]
    return {
        "ok": True,
        "status": (
            "discarded"
            if any(result.get("status") == "discarded" for result in results)
            else "already_discarded"
        ),
        "draft_id": draft_id,
        "draft_ids": [str(result.get("draft_id") or "") for result in results],
        "discarded_count": len(results),
        "archived_path": archived_paths[0] if archived_paths else None,
        "archived_paths": archived_paths,
    }


def _planning_draft_discard_group_ids(state_root: Path, plan: PlanDraft) -> list[str]:
    fallback = _safe_planning_draft_id(plan.plan_id)
    if not fallback or not _dedupeable_planning_draft(plan):
        return [fallback] if fallback else []

    title, repos = _plan_dedupe_key(plan)
    if not title or not repos:
        return [fallback]

    ids: list[str] = []
    for candidate in FilesystemReader(state_root=Path(state_root)).list_plans(limit=10_000):
        if not _dedupeable_planning_draft(candidate):
            continue
        if _plan_dedupe_key(candidate) != (title, repos):
            continue
        candidate_id = _safe_planning_draft_id(candidate.plan_id)
        if candidate_id:
            ids.append(candidate_id)
    return ids or [fallback]


def _dedupeable_planning_draft(plan: PlanDraft) -> bool:
    return plan.source in {"compose", "planning", "slack"} and not plan.parent


def _plan_dedupe_key(plan: PlanDraft) -> tuple[str, str]:
    title = re.sub(r"\s+", " ", (plan.title or "").strip().lower())
    if title == "alfred planning draft":
        title = ""
    repos = sorted(
        repo.strip().lower()
        for repo in re.split(r"[,\s]+", plan.affected_repos or "")
        if repo.strip()
    )
    return title, ",".join(repos)


def _discard_planning_draft(state_root: Path, draft_id: str) -> dict[str, Any]:
    """Archive a planning draft to ``planning-drafts/archive/``.

    Never hard-deletes: the draft JSON is moved under an ``archive/`` subdir so
    an accidental discard is recoverable. Idempotent: if the live draft is gone
    but an archived copy already exists, this is a no-op success.
    """
    draft_root = Path(state_root) / "planning-drafts"
    live_path = draft_root / f"{draft_id}.json"
    archive_dir = draft_root / "archive"
    archived_path = archive_dir / f"{draft_id}.json"

    if not live_path.is_file():
        existing_archive = _existing_planning_draft_archive(archive_dir, archived_path, draft_id)
        if existing_archive:
            return {
                "ok": True,
                "status": "already_discarded",
                "draft_id": draft_id,
                "archived_path": str(existing_archive),
            }
        raise FileNotFoundError(draft_id)

    archive_dir.mkdir(parents=True, exist_ok=True)
    target = archived_path
    if target.exists():
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        target = archive_dir / f"{draft_id}-{stamp}.json"
    try:
        live_path.replace(target)
    except FileNotFoundError:
        existing_archive = _existing_planning_draft_archive(archive_dir, archived_path, draft_id)
        if existing_archive:
            return {
                "ok": True,
                "status": "already_discarded",
                "draft_id": draft_id,
                "archived_path": str(existing_archive),
            }
        raise
    return {
        "ok": True,
        "status": "discarded",
        "draft_id": draft_id,
        "archived_path": str(target),
    }


def _existing_planning_draft_archive(
    archive_dir: Path,
    archived_path: Path,
    draft_id: str,
) -> Path | None:
    if archived_path.is_file():
        return archived_path
    return next(archive_dir.glob(f"{draft_id}-*.json"), None)


def _safe_planning_draft_id(raw: Any) -> str | None:
    candidate = str(raw or "").strip()
    if not candidate or "/" in candidate or "\\" in candidate or candidate.startswith("."):
        return None
    if not re.fullmatch(r"[A-Za-z0-9._-]+", candidate):
        return None
    return candidate


def _planning_draft_issue_url(payload: dict[str, Any]) -> str:
    bridge = payload.get("bridge")
    if isinstance(bridge, dict):
        issue_url = str(bridge.get("issue_url") or "").strip()
        if issue_url:
            return issue_url
        issue_urls = bridge.get("issue_urls")
        if isinstance(issue_urls, list):
            for item in issue_urls:
                text = str(item or "").strip()
                if text:
                    return text
        issues_by_repo = bridge.get("issues_by_repo")
        if isinstance(issues_by_repo, dict):
            for item in issues_by_repo.values():
                text = str(item or "").strip()
                if text:
                    return text
    return ""


def _first_draft_repo(payload: dict[str, Any]) -> str:
    draft = payload.get("draft")
    if not isinstance(draft, dict):
        return ""
    repos = draft.get("repos")
    if not isinstance(repos, list):
        return ""
    for repo in repos:
        text = str(repo or "").strip()
        if text:
            return text
    return ""


def _convert_and_archive_followup(request: Request, plan: PlanDraft) -> tuple[Path, Path]:
    draft_path = _convert_followup_to_planning_draft(request, plan)
    try:
        archived_path = _archive_followup(plan, action="converted", target_path=draft_path)
    except Exception:
        draft_path.unlink(missing_ok=True)
        raise
    return draft_path, archived_path


def _convert_followup_to_planning_draft(request: Request, plan: PlanDraft) -> Path:
    draft = _draft_from_followup(plan)
    memory_provider = _planning_memory_provider(request)
    assistant_result = refine_issue_draft(draft, [], memory_provider=memory_provider)
    issue_body = _with_followup_context(assistant_result.issue_body, plan)
    spec_body = _with_followup_context(assistant_result.spec_body, plan)
    root = _state_planning_root(request)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"followup-{_slug(plan.plan_id)}-{_slug(draft.title)}.json"
    payload = {
        "source": "planning",
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "converted_from": {
            "plan_id": plan.plan_id,
            "path": plan.path,
            "parent": plan.parent,
            "title": plan.title,
        },
        "draft": asdict(assistant_result.draft),
        "issue_body": issue_body,
        "spec_body": spec_body,
        "readiness": asdict(assistant_result.readiness),
        "memory": [asdict(item) for item in assistant_result.memory],
        "revision_count": 0,
        "revisions": [],
    }
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def _draft_from_followup(plan: PlanDraft) -> IssueDraft:
    clean_title = re.sub(r"^follow-up for\s+", "", plan.title, flags=re.IGNORECASE).strip()
    title = f"Follow up: {clean_title or 'captured Slack feedback'}"
    repos = _repos_from_followup(plan)
    return IssueDraft(
        title=title,
        problem=(
            "A trusted Slack follow-up was captured after Alfred posted a report "
            "or PR link. It needs an explicit planning pass before any code or "
            "docs change."
        ),
        user="Repo owner, teammate, or operator following up on shipped work",
        current_behavior=plan.preview or "Follow-up context is captured in the local Plans inbox.",
        desired_behavior=(
            "Decide whether the follow-up needs code, docs, tests, a scoped "
            "issue, or an explicit no-change response."
        ),
        repos=repos,
        acceptance_criteria=[
            "The captured follow-up is addressed or explicitly declined.",
            "Any resulting work links back to the original issue, PR, or Slack thread.",
        ],
        test_plan=(
            "Run the smallest relevant tests for the affected area and verify "
            "the follow-up is covered."
        ),
        out_of_scope=(
            "No automatic merge, deployment, or broad scope expansion from captured feedback."
        ),
        open_questions=(
            "Confirm the intended response before implementation if the follow-up changes scope."
        ),
    )


def _repos_from_followup(plan: PlanDraft) -> list[str]:
    repos: list[str] = []
    urls = [plan.parent or ""]
    urls.extend(re.findall(r"https://github\.com/[^\s),>`]+", plan.content))
    for url in urls:
        repo = _repo_from_github_url(url)
        if repo and repo not in repos:
            repos.append(repo)
    return repos


def _with_followup_context(body: str, plan: PlanDraft) -> str:
    return (
        body.rstrip()
        + "\n\n## Captured Follow-up Context\n\n"
        + f"- Source: `{plan.plan_id}`\n"
        + (f"- Parent: {plan.parent}\n" if plan.parent else "")
        + "\n"
        + plan.content.strip()
        + "\n"
    )


def _archive_followup(
    plan: PlanDraft,
    *,
    action: str,
    target_path: Path | None = None,
) -> Path:
    path = Path(plan.path)
    handled_dir = path.parent / "handled"
    handled_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    archive_path = handled_dir / path.name
    if archive_path.exists():
        archive_path = handled_dir / f"{path.stem}-{stamp}{path.suffix}"
    try:
        content = path.read_text(encoding="utf-8").rstrip()
    except OSError:
        content = ""
    metadata = [
        "",
        "---",
        "",
        f"- Follow-up action: {action}",
        f"- Follow-up action at: {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}",
    ]
    if target_path is not None:
        metadata.append(f"- Planning draft: {target_path}")
    tmp = archive_path.with_name(f"{archive_path.name}.tmp")
    tmp.write_text(content + "\n".join(metadata) + "\n", encoding="utf-8")
    tmp.replace(archive_path)
    path.unlink(missing_ok=True)
    return archive_path


def _state_planning_root(request: Request) -> Path:
    return _state_root(request) / "planning-drafts"


def _converse_condense_recorder(request: Request, *, draft_id: str | None) -> Any:
    """Return an ``on_condense`` callback that persists each condensation record.

    The record lands under ``<state>/condensations`` as auditable JSON listing
    which turns were summarized and what the summary said, so an operator (or a
    later memory-promote pass) can review it. Persistence failures are swallowed:
    a disk hiccup must never fail the user's converse turn.
    """
    import conversation_condenser as condenser

    record_dir = _state_root(request) / "condensations"

    def _record(record: Any) -> None:
        with suppress(OSError):
            condenser.persist_record(record, record_dir=record_dir, slug=draft_id or "")

    return _record


def _state_root(request: Request) -> Path:
    reader = request.app.state.reader
    state_root = getattr(reader, "state_root", None)
    if isinstance(state_root, Path):
        return state_root
    base = os.environ.get("ALFRED_HOME") or os.path.expanduser("~/.alfred")
    return Path(base) / "state"


def _local_conversation_actor(value: Any) -> str:
    return normalize_slack_user_id(value) or operator_user_id_from_env() or _LOCAL_CLIENT_USER_ID


def _save_planning_text(
    request: Request,
    draft: IssueDraft,
    body: str,
    *,
    directory: str,
    suffix: str,
) -> Path:
    root = _planning_root(request, directory=directory)
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    path = root / f"{stamp}-{_slug(draft.title)}-{suffix}.md"
    path.write_text(body, encoding="utf-8")
    return path


def _planning_root(request: Request, *, directory: str = "planning-drafts") -> Path:
    reader = request.app.state.reader
    state_root = getattr(reader, "state_root", None)
    if isinstance(state_root, Path):
        return state_root.parent / directory
    base = os.environ.get("ALFRED_HOME") or os.path.expanduser("~/.alfred")
    return Path(base) / directory


def _planning_memory_provider(request: Request):
    configured = getattr(request.app.state, "planning_memory_provider", None)
    if configured is not None:
        return configured
    if _env_disabled("ALFRED_PLANNING_MEMORY"):
        return None
    if not _planning_uses_runtime_state(request):
        return None
    return _load_planning_memory_provider_from_env()


def _load_planning_memory_provider_from_env():
    if not (os.environ.get("ALFRED_HOME") or os.environ.get("FLEET_BRAIN_HOST")):
        return None
    from memory.pgvector_provider import MemoryProviderMisconfigured

    try:
        from memory.config import load_provider

        return load_provider()
    except MemoryProviderMisconfigured:
        # A bad memory config value must surface, not silently disable planning
        # recall (mirrors load_runtime_memory).
        raise
    except Exception:
        return None


def _planning_uses_runtime_state(request: Request) -> bool:
    reader = getattr(request.app.state, "reader", None)
    state_root = getattr(reader, "state_root", None)
    if not isinstance(state_root, Path):
        return False
    base = os.environ.get("ALFRED_HOME")
    if base is None and os.environ.get("FLEET_BRAIN_HOST"):
        base = os.path.expanduser("~/.alfred")
    if not base:
        return False
    try:
        runtime_state = (Path(base).expanduser() / "state").resolve()
        return state_root.expanduser().resolve() == runtime_state
    except OSError:
        runtime_state = (Path(base).expanduser() / "state").absolute()
        return state_root.expanduser().absolute() == runtime_state


def _planning_memory_writer(request: Request, *, provider=None):
    configured = getattr(request.app.state, "planning_memory_writer", None)
    if configured is not None:
        return configured
    return _memory_candidate_writer(provider or _planning_memory_provider(request))


def _memory_candidate_writer(provider):
    if provider is None:
        return None
    if hasattr(provider, "propose_memory"):
        return provider
    brain = getattr(provider, "brain", None)
    if brain is not None and hasattr(brain, "propose_memory"):
        return brain
    providers = getattr(provider, "providers", None)
    if isinstance(providers, (list, tuple)):
        for child in providers:
            writer = _memory_candidate_writer(child)
            if writer is not None:
                return writer
    return None


def _propose_planning_memory_candidate(
    request: Request,
    draft: IssueDraft,
    *,
    spec_path: Path,
    spec_body: str,
    memory_provider=None,
) -> tuple[str, ...]:
    if _env_disabled("ALFRED_PLANNING_MEMORY_CANDIDATES"):
        return ()
    writer = _planning_memory_writer(request, provider=memory_provider)
    if writer is None or not hasattr(writer, "propose_memory"):
        return ()
    body = _memory_candidate_body(draft)
    evidence = {
        "kind": "planning_spec",
        "path": str(spec_path),
        "title": draft.title,
        "readiness_chars": len(spec_body),
    }
    ids: list[str] = []
    for repo in draft.repos or ["planning"]:
        try:
            candidate = writer.propose_memory(
                codename="planning",
                repo=repo,
                body=body,
                tags=["planning", "spec"],
                severity="info",
                source="planning-ui",
                evidence=json.dumps(evidence, sort_keys=True),
                confidence=0.72,
            )
            candidate_id = getattr(candidate, "id", candidate)
        except TypeError:
            try:
                candidate = writer.propose_memory(
                    agent="planning",
                    repo=repo,
                    topic="planning-spec",
                    body=body,
                    source="planning-ui",
                    evidence=[evidence],
                )
                candidate_id = getattr(candidate, "id", candidate)
            except Exception:
                continue
        except Exception:
            continue
        if candidate_id is not None:
            ids.append(str(candidate_id))
    return tuple(ids)


def _memory_candidate_body(draft: IssueDraft) -> str:
    criteria = "; ".join(draft.acceptance_criteria[:3]) or "No acceptance criteria."
    repos = ", ".join(draft.repos) or "unspecified repo"
    return (
        f"Planning spec saved for {draft.title or 'untitled work'} across {repos}. "
        f"Acceptance gates: {criteria}"
    )


def _env_disabled(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"0", "false", "no", "off"}


def _planning_workdir(request: Request) -> Path:
    reader = request.app.state.reader
    state_root = getattr(reader, "state_root", None)
    if isinstance(state_root, Path):
        return state_root.parent
    base = os.environ.get("ALFRED_HOME") or os.path.expanduser("~/.alfred")
    return Path(base)


def _repo_from_github_url(url: str) -> str:
    match = re.search(r"github\.com/([^/\s]+/[^/\s#?]+)(?:/|$)", url)
    if not match:
        return ""
    return match.group(1)


def _same_origin_post(request: Request) -> bool:
    """Reject browser form posts from another origin while preserving CLI use."""
    return _same_origin_request(request)


def _same_origin_request(request: Request) -> bool:
    """Reject browser requests from another origin while preserving CLI use."""
    expected_host = request.headers.get("host", "")
    for header in ("origin", "referer"):
        raw_value = request.headers.get(header)
        if not raw_value:
            continue
        parsed = urlparse(raw_value)
        if parsed.netloc != expected_host:
            return False
    return True


def _slug(value: str) -> str:
    text = value.strip().lower() or "draft"
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:80] or "draft"
