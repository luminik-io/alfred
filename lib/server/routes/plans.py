"""Plan listing, follow-up conversion, approval decisions, and Compose drafts."""

from __future__ import annotations

import json
import logging
from dataclasses import replace

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from planning_assistant import (
    PlanningAssistantResult,
    engine_refiner_from_env,
    refine_issue_draft,
)
from starlette.concurrency import run_in_threadpool

from server import views
from server.plan_approvals import (
    DECISION_APPROVE,
    DECISION_DECLINE,
    issue_num_from_plan_id,
    write_decision,
)

logger = logging.getLogger(__name__)


router = APIRouter()


@router.get("/api/plans", response_class=JSONResponse)
async def api_plans(request: Request, limit: int = 50) -> JSONResponse:
    rows = request.app.state.reader.list_plans(limit=min(max(1, limit), 200))
    return JSONResponse(views._jsonable({"rows": rows}))


@router.get("/api/plans/drafts", response_class=JSONResponse)
async def api_list_compose_drafts(request: Request) -> JSONResponse:
    rows = views._list_compose_drafts(request)
    return JSONResponse({"rows": rows})


@router.get("/api/plans/{plan_id}", response_class=JSONResponse)
async def api_plan_detail(request: Request, plan_id: str) -> JSONResponse:
    plan = request.app.state.reader.get_plan(plan_id)
    if plan is None:
        return JSONResponse({"error": "plan not found"}, status_code=404)
    return JSONResponse(views._jsonable(plan))


@router.post(
    "/api/plans/{plan_id}/convert-followup",
    response_class=JSONResponse,
    dependencies=[Depends(views.require_mutation_token)],
)
async def api_convert_followup(request: Request, plan_id: str) -> JSONResponse:
    plan = request.app.state.reader.get_plan(plan_id)
    if plan is None:
        return JSONResponse({"error": "plan not found"}, status_code=404)
    if plan.source != "followup":
        return JSONResponse({"error": "plan is not a follow-up"}, status_code=400)
    draft_path, archived_path = views._convert_and_archive_followup(request, plan)
    return JSONResponse(
        {
            "draft_id": draft_path.stem,
            "draft_path": str(draft_path),
            "archived_path": str(archived_path),
        }
    )


@router.post(
    "/api/plans/{plan_id}/mark-handled",
    response_class=JSONResponse,
    dependencies=[Depends(views.require_mutation_token)],
)
async def api_mark_followup_handled(request: Request, plan_id: str) -> JSONResponse:
    plan = request.app.state.reader.get_plan(plan_id)
    if plan is None:
        return JSONResponse({"error": "plan not found"}, status_code=404)
    if plan.source != "followup":
        return JSONResponse({"error": "plan is not a follow-up"}, status_code=400)
    archived_path = views._archive_followup(plan, action="handled")
    return JSONResponse({"archived_path": str(archived_path)})


@router.post(
    "/api/plans/{plan_id}/discard",
    response_class=JSONResponse,
    dependencies=[Depends(views.require_mutation_token)],
)
async def api_discard_plan(request: Request, plan_id: str) -> JSONResponse:
    """Discard a local planning draft by archiving, never hard-deleting it."""
    draft_id = views._safe_planning_draft_id(plan_id)
    if draft_id is None:
        return JSONResponse({"error": "plan not found"}, status_code=404)
    try:
        result = await run_in_threadpool(
            views._discard_planning_draft_group,
            views._state_root(request),
            draft_id,
        )
    except FileNotFoundError:
        return JSONResponse({"error": "plan not found"}, status_code=404)
    except Exception:  # never let an IO edge crash the server
        logger.exception("api_discard_plan: failed to archive planning draft")
        return JSONResponse({"error": views._GENERIC_ERROR}, status_code=500)
    return JSONResponse(result)


@router.post(
    "/api/plans/{plan_id}/decision",
    response_class=JSONResponse,
    dependencies=[Depends(views.require_mutation_token)],
)
async def api_plan_decision(request: Request, plan_id: str) -> JSONResponse:
    """Record an in-app go/no-go on a genuine architect plan.

    Writes the same ``{issue_num}.approved`` / ``.rejected`` marker
    the architect's approval gate watches (see ``lib.architect_lifecycle``), so the operator
    can approve or decline without a Slack round-trip and architect consumes
    it through the real go/no-go path. Token-gated via
    ``_authorized_mutation`` and same-origin so a
    drive-by localhost page cannot arm or stop work on the operator's
    behalf.
    """
    body, error_response = await views._read_json_body(request)
    if error_response is not None:
        return error_response
    decision = str(body.get("decision") or "").strip().lower()
    if decision not in (DECISION_APPROVE, DECISION_DECLINE):
        return JSONResponse(
            {"error": "decision must be 'approve' or 'decline'"},
            status_code=400,
        )
    plan = request.app.state.reader.get_plan(plan_id)
    if plan is None:
        return JSONResponse({"error": "plan not found"}, status_code=404)
    if plan.source != "architect":
        return JSONResponse(
            {"error": "only architect go/no-go plans can be decided here"},
            status_code=400,
        )
    issue_num = issue_num_from_plan_id(plan.plan_id)
    if issue_num is None:
        return JSONResponse(
            {"error": "plan id has no issue number to signal"},
            status_code=400,
        )
    reason = str(body.get("reason") or "").strip()
    marker_path = write_decision(views._state_root(request), issue_num, decision, reason=reason)
    return JSONResponse(
        {
            "plan_id": plan.plan_id,
            "issue_number": issue_num,
            "decision": decision,
            "status": "approved" if decision == DECISION_APPROVE else "declined",
            "marker_path": str(marker_path),
        }
    )


@router.post(
    "/api/plans/{plan_id}/file-issue",
    response_class=JSONResponse,
    dependencies=[Depends(views.require_mutation_token)],
)
async def api_file_plan_issue(request: Request, plan_id: str) -> JSONResponse:
    """File labeled GitHub issue work from a ready local planning draft.

    This is the native-client issue filing route for Plan work. It does not run an
    agent, touch a worktree, or bypass the fleet gates: it creates one
    ``agent:implement`` issue, then the normal Alfred queue decides when
    and how to claim it. The route is
    same-origin and token-gated like other local mutations, and it is
    idempotent via the saved draft's ``bridge.issue_url`` field.
    """
    try:
        result = await run_in_threadpool(
            views._file_planning_draft_issue,
            views._state_root(request),
            plan_id,
        )
    except FileNotFoundError:
        return JSONResponse({"error": "plan not found"}, status_code=404)
    except ValueError:
        # ValueErrors here are rejection reasons (unsafe id, unreadable
        # draft, failed conversion). Log the detail server-side and return a
        # generic 400 so the response never carries exception text; the 400
        # status (the client's "filing rejected" contract) is unchanged.
        logger.exception("api_file_plan_issue: plan draft rejected")
        return JSONResponse(
            {"error": "could not file plan issue from this draft"},
            status_code=400,
        )
    except Exception:  # never let a gh/IO edge crash the server
        logger.exception("api_file_plan_issue: failed to file plan issue")
        return JSONResponse(
            {"error": views._GENERIC_ERROR},
            status_code=500,
        )
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    return JSONResponse(result)


@router.post(
    "/api/plans/draft",
    response_class=JSONResponse,
    dependencies=[Depends(views.require_mutation_token)],
)
async def api_compose_draft(request: Request) -> JSONResponse:
    try:
        body = json.loads((await request.body()).decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"error": "request body must be JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "request body must be a JSON object"}, status_code=400)

    text = str(body.get("text") or "").strip()
    draft_id = views._safe_compose_draft_id(body.get("draft_id"))
    prior_payload, prior_path = views._read_compose_draft_payload(request, draft_id)
    base_draft = views._compose_base_draft(body, prior_payload)

    # A question must get an answer, not a fabricated plan. This one-shot
    # endpoint is the reliable fallback the Ask surface drops to when no live
    # conversational engine is configured; without this branch a plain status
    # question ("what is the current state of the fleet?") is synthesized into
    # a starter plan, which is the wrong surface. Classify the turn with the
    # SAME deterministic backstop the converse path shares
    # (compose_converse.classify_message_intent): a fresh, question-shaped
    # message that carries no build signal is a conversation turn and gets a
    # plain reply that points at the answer path, instead of a plan. A change
    # request, or any refinement of an existing draft, still drafts as before.
    # Repos are excluded from the "has signal" judgment here: they are
    # grounding context, not evidence this turn is work. The desktop client
    # sends the selected repo in draft.repos with EVERY fallback turn (and
    # the setup-scope injection below adds one server-side), so counting
    # repos would suppress the conversation path for any configured setup.
    content_draft = replace(base_draft, repos=[]) if base_draft.repos else base_draft
    if (
        text
        and prior_payload is None
        and not views._draft_has_signal(content_draft)
        and views._compose_question_intent(text, content_draft)
    ):
        return JSONResponse(views._compose_question_reply(draft_id))

    if not base_draft.repos:
        setup_scope = views._selected_setup_repos_for_scope()
        if setup_scope:
            base_draft = replace(base_draft, repos=setup_scope)

    if not text and prior_payload is None and not views._draft_has_signal(base_draft):
        return JSONResponse(
            {"error": "describe the work in the text field before drafting"},
            status_code=400,
        )

    refiner = engine_refiner_from_env(workdir=views._planning_workdir(request)) if text else None
    messages = views._compose_draft_messages(text, base_draft)
    synthesized_plain_intent = bool(messages and text and messages[0] != text)
    memory_provider = views._planning_memory_provider(request)
    assistant_result: PlanningAssistantResult = refine_issue_draft(
        base_draft,
        messages,
        refiner=refiner if messages else None,
        memory_provider=memory_provider,
    )
    draft = assistant_result.draft
    readiness = assistant_result.readiness
    revisions = list(views._existing_revisions(prior_payload))
    if text:
        revisions.append(text)
    saved_path, draft_id = views._save_compose_draft(
        request,
        draft=draft,
        assistant_result=assistant_result,
        draft_id=draft_id,
        draft_path=prior_path,
        prior_payload=prior_payload,
        revisions=revisions,
    )
    return JSONResponse(
        {
            "draft_id": draft_id,
            "saved_path": str(saved_path),
            "title": draft.title,
            "readiness": {
                "ok": readiness.ok,
                "score": readiness.score,
            },
            "questions": list(assistant_result.questions),
            "findings": [
                {
                    "code": finding.code,
                    "severity": finding.severity,
                    "message": finding.message,
                }
                for finding in readiness.findings
            ],
            "summary": views._compose_draft_response_summary(
                assistant_result,
                synthesized_plain_intent=synthesized_plain_intent,
            ),
            "spec_body": assistant_result.spec_body,
            "revision_count": len(revisions),
            "draft": {
                "title": draft.title,
                "problem": draft.problem,
                "user": draft.user,
                "current_behavior": draft.current_behavior,
                "desired_behavior": draft.desired_behavior,
                "repos": list(draft.repos),
                "acceptance_criteria": list(draft.acceptance_criteria),
                "test_plan": draft.test_plan,
                "out_of_scope": draft.out_of_scope,
                "rollout": draft.rollout,
                "open_questions": draft.open_questions,
            },
        }
    )
