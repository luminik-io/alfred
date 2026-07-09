"""Firing list, detail, and live-tail routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from server import views

router = APIRouter()


@router.get("/api/firings", response_class=JSONResponse)
async def api_firings(
    request: Request,
    codename: str | None = None,
    limit: int = 50,
) -> JSONResponse:
    rows = request.app.state.reader.list_recent_firings(
        limit=min(max(1, limit), 200),
        codename=codename,
    )
    return JSONResponse(views._jsonable({"rows": rows}))


@router.get("/api/firings/{firing_id}", response_class=JSONResponse)
async def api_firing_detail(request: Request, firing_id: str) -> JSONResponse:
    record = request.app.state.reader.get_firing(firing_id)
    if record is None:
        return JSONResponse({"error": "firing not found"}, status_code=404)
    return JSONResponse(views._jsonable(record))


@router.get("/api/firings/{firing_id}/tail")
async def api_firing_tail(
    request: Request,
    firing_id: str,
    offset: int = 0,
    poll: int = 0,
) -> Any:
    """Live-tail a running firing's transcript as it grows (#41).

    This is a READ over the on-disk transcript JSONL the runtime tees per
    firing, so it is an open GET like the other read routes (no token), and
    the client consumes it via ``EventSource``. Two transports share the
    same offset reader:

    * Default: a Server-Sent-Events stream that appends new whole lines as
      they land and closes with a ``done`` event once the firing completes
      (or a wall-clock ceiling is hit).
    * ``?poll=1&offset=N``: a single JSON snapshot
      (``{found, offset, lines, done}``) for clients that cannot hold an
      ``EventSource`` open, so the live tail degrades to plain polling
      instead of failing.

    The client always retains its existing 60s firing poll, so a missing
    route (older server) or a stream error never regresses the log view.
    """
    from server import streaming

    state_root = views._state_root(request)
    start_offset = max(0, int(offset))
    # The packaged webview reaches this open GET cross-origin (its bundle
    # loads from tauri://localhost), and a cross-origin EventSource is still
    # subject to CORS, so echo the allowed Origin. No token is required: the
    # tail is a read over the on-disk transcript, like the other GET routes.
    cors = views._streaming_cors_headers(request)
    if poll:
        snapshot = await run_in_threadpool(
            streaming.tail_transcript_chunk,
            state_root,
            firing_id,
            offset=start_offset,
        )
        return JSONResponse(snapshot, headers=cors)
    generator = streaming.tail_transcript_sse(state_root, firing_id, start_offset=start_offset)
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers=views._streaming_cors_headers(
            request,
            {
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        ),
    )
