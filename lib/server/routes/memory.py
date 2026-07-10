"""Memory candidate/lesson recall and review routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from server import views

logger = logging.getLogger(__name__)


router = APIRouter()


@router.get("/api/memory/candidates", response_class=JSONResponse)
async def api_memory_candidates(
    request: Request,
    status: str = "candidate",
    limit: int = 50,
) -> JSONResponse:
    if status not in {
        "candidate",
        "validated",
        "rejected",
        "retired",
        "all",
    }:
        return JSONResponse({"error": "unknown memory candidate status"}, status_code=400)
    brain, error = views._memory_brain(request, require_existing=True)
    if brain is None:
        return JSONResponse({"rows": [], "error": error})
    status_filter = views._memory_status_filter(status)
    try:
        rows = brain.list_memory_candidates(
            status=status_filter,
            limit=min(max(1, limit), 200),
        )
    except Exception:  # pragma: no cover - local bridge can be down
        logger.exception("api_memory_candidates: failed to list candidates")
        return JSONResponse({"rows": [], "error": views._GENERIC_ERROR})
    return JSONResponse({"rows": [views._candidate_to_api(row) for row in rows]})


@router.post("/api/memory/candidates/{candidate_id}/promote", response_class=JSONResponse)
async def api_promote_memory_candidate(request: Request, candidate_id: str) -> JSONResponse:
    return await views._api_memory_candidate_action(request, candidate_id, action="promote")


@router.post("/api/memory/candidates/{candidate_id}/reject", response_class=JSONResponse)
async def api_reject_memory_candidate(request: Request, candidate_id: str) -> JSONResponse:
    return await views._api_memory_candidate_action(request, candidate_id, action="reject")


@router.post("/api/memory/candidates/{candidate_id}/retire", response_class=JSONResponse)
async def api_retire_memory_candidate(request: Request, candidate_id: str) -> JSONResponse:
    # Undo an auto-remembered lesson: forget it from AMS recall and retire
    # the row. The ``candidate_id`` may be the raw id or the
    # ``lesson:memory_candidate:<id>`` recall id a lesson surfaces under.
    return await views._api_memory_candidate_action(request, candidate_id, action="retire")


@router.get("/api/memory/lessons", response_class=JSONResponse)
async def api_memory_lessons(request: Request, limit: int = 50) -> JSONResponse:
    """The lessons Alfred is actually using in recall (promoted + auto-promoted),
    as opposed to the pending review queue served by /api/memory/candidates.

    Routed through the memory provider chain (Redis AMS + local FleetBrain,
    merged and deduped) rather than the local SQLite ledger alone: the
    promoted-lesson backend is AMS, so ``FleetBrain.list_lessons`` returns
    nothing on an AMS-primary install and the client would show an empty
    "lessons Alfred is using" section even when it has promoted lessons.
    """
    display_limit = min(max(1, limit), 200)
    # Over-fetch before deduping so the display limit is honored in UNIQUE
    # rows. If we recalled exactly `display_limit` rows and some were
    # duplicates, dedupe would underfill the list (return fewer than
    # `display_limit` unique lessons even when more exist further down
    # recall). Pull a larger pool (bounded by the recall ceiling), dedupe,
    # then cap to the requested number of unique lessons.
    fetch_limit = min(max(display_limit * 4, display_limit + 50), 200)
    try:
        lessons = views._recall_lessons_via_chain(request, limit=fetch_limit)
    except Exception:  # pragma: no cover - local bridge can be down
        logger.exception("api_memory_lessons: failed to recall lessons")
        return JSONResponse({"rows": [], "error": views._GENERIC_ERROR})
    lessons = views._dedupe_lessons_for_display(lessons)[:display_limit]
    return JSONResponse({"rows": [views._lesson_to_api(lesson) for lesson in lessons]})


@router.get("/api/memory/stats", response_class=JSONResponse)
async def api_memory_stats(request: Request) -> JSONResponse:
    """Lesson-quality metrics for the desktop client: candidate counts by
    state, auto-promote acceptance rate, judge rejection rate.

    Additive and read-only. Backed by the local FleetBrain ledger (cheap
    COUNT(*) rollups), so it is safe to poll and works even when Redis AMS is
    down. Returns an ``error`` with a null ``stats`` if the ledger is
    unreachable, matching the other memory read endpoints.
    """
    brain, error = views._memory_brain(request, require_existing=True)
    if brain is None:
        return JSONResponse({"stats": None, "error": error})
    try:
        stats = brain.lesson_stats()
    except Exception:  # pragma: no cover - local bridge can be down
        logger.exception("api_memory_stats: failed to compute lesson stats")
        return JSONResponse({"stats": None, "error": views._GENERIC_ERROR})
    return JSONResponse({"stats": views._jsonable(stats)})
