"""Subscription-usage headroom routes (Claude + Codex meters)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from server import views

logger = logging.getLogger(__name__)


router = APIRouter()


@router.get("/api/usage", response_class=JSONResponse)
async def api_usage(request: Request) -> JSONResponse:
    """Real subscription-usage headroom from local Claude/Codex logs.

    Reports the active Claude 5-hour rolling-window token usage, time to
    reset, a simple burn projection, and a latest-day Codex row. The
    per-token dollar figure is meaningless under a Max/Pro subscription (and
    $0 for Codex), so this is usage headroom rather than billed spend.

    Reads local JSONL logs in a worker thread so filesystem work never
    stalls the event loop, and degrades to ``{"available": false, "error":
    ...}`` when both sources fail.
    """
    from starlette.concurrency import run_in_threadpool

    from server.usage import build_usage, unavailable_usage_payload

    try:
        payload = await run_in_threadpool(build_usage)
    except Exception:  # never break the client on a usage failure
        logger.exception("api_usage: failed to build usage payload")
        return JSONResponse(unavailable_usage_payload(views._GENERIC_ERROR))
    return JSONResponse(views._jsonable(payload))


@router.get("/api/usage/providers", response_class=JSONResponse)
async def api_usage_providers(request: Request) -> JSONResponse:
    """Provider-normalized usage meters: ``{"claude": {...}, "codex": {...}}``.

    A flat re-projection of ``/api/usage`` that surfaces each engine's
    5-hour and weekly rolling windows under uniform keys (``used_percent``,
    ``remaining_percent``, ``reset_at``, ``minutes_to_reset``). Alfred drives
    Claude Code and Codex through their local subscription CLIs, so there is
    no billing API: figures come straight from the CLIs' own local state
    files. A provider whose local state cannot be read degrades to
    ``available: false`` with an ``unavailable_reason`` rather than guessing.

    Reads run in a worker thread so filesystem work never stalls the event
    loop, and any failure degrades to an honest both-unavailable shape.
    """
    from starlette.concurrency import run_in_threadpool

    from server.usage import build_provider_usage

    try:
        payload = await run_in_threadpool(build_provider_usage)
    except Exception:  # never break the client on a usage failure
        logger.exception("api_usage_providers: failed to build provider usage")
        payload = {
            "available": False,
            "error": views._GENERIC_ERROR,
            "claude": {
                "available": False,
                "five_hour": None,
                "weekly": None,
                "unavailable_reason": views._GENERIC_ERROR,
            },
            "codex": {
                "available": False,
                "five_hour": None,
                "weekly": None,
                "unavailable_reason": views._GENERIC_ERROR,
            },
        }
    return JSONResponse(views._jsonable(payload))
