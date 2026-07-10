"""Liveness probe route."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/healthz", response_class=HTMLResponse)
async def healthz() -> HTMLResponse:
    # Minimal liveness probe. Returns 200 with "ok" body, no template.
    return HTMLResponse("ok")
