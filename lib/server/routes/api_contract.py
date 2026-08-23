"""Version metadata and the terminal miss handler for ``/api/v1``."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from server import views
from server.api_contract import API_CONTRACT_VERSION, error_response
from server.routes import status

router = APIRouter()
terminal_router = APIRouter()


@router.get("/api/v1", response_class=JSONResponse)
async def api_v1_root() -> JSONResponse:
    """Keep the bare namespace inside the versioned JSON error contract."""
    return error_response(
        status_code=404,
        code="not_found",
        message="API route not found",
    )


@router.get("/api/v1/meta", response_class=JSONResponse)
async def api_v1_meta() -> JSONResponse:
    """Describe the contract a local client must use before other v1 calls."""
    return JSONResponse(
        {
            "api_version": API_CONTRACT_VERSION,
            "service": "alfred-serve",
            "scope": "localhost",
            "mutation_token_header": views.SERVER_TOKEN_HEADER,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/v1/status", response_class=JSONResponse)
async def api_v1_status(request: Request) -> JSONResponse:
    """Return fleet readiness through the explicit v1 contract."""
    return JSONResponse(status.status_payload(request))


@terminal_router.get("/api/v1/{full_path:path}", response_class=JSONResponse)
async def api_v1_not_found(full_path: str) -> JSONResponse:
    """Keep unknown v1 GETs inside the versioned JSON error contract."""
    del full_path
    return error_response(
        status_code=404,
        code="not_found",
        message="API route not found",
    )
