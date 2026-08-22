"""Shared contract metadata and errors for the versioned local API."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from fastapi.responses import JSONResponse

API_CONTRACT_VERSION: Final[str] = "1"
API_VERSION_HEADER: Final[str] = "X-Alfred-API-Version"


def error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Return the stable error envelope used by ``/api/v1`` routes."""
    return JSONResponse(
        {
            "api_version": API_CONTRACT_VERSION,
            "error": {
                "code": code,
                "message": message,
            },
        },
        status_code=status_code,
        headers=headers,
    )


__all__ = [
    "API_CONTRACT_VERSION",
    "API_VERSION_HEADER",
    "error_response",
]
