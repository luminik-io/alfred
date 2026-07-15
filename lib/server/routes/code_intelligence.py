"""Read-only code intelligence over Alfred's local code map."""

from __future__ import annotations

import logging
from pathlib import Path

from code_graph import impact_brief_for_path, load_code_map, summarize_codegraph
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from server import views

logger = logging.getLogger(__name__)

router = APIRouter()


def _code_intelligence_payload(
    repo: str | None,
    path: str | None,
    limit: int,
    code_map_path: Path,
) -> dict[str, object]:
    code_map = load_code_map(code_map_path)
    if repo:
        # Validate the selected repository without narrowing the catalog the
        # client needs to switch scope after an analysis.
        summarize_codegraph(code_map, repo=repo, limit=1)
    summary = summarize_codegraph(code_map, limit=100)
    drift_by_repo: dict[str, int] = {}
    drift_rows = code_map.get("contract_drift")
    if isinstance(drift_rows, list):
        for row in drift_rows:
            if not isinstance(row, dict):
                continue
            caller = row.get("caller")
            if isinstance(caller, str):
                drift_by_repo[caller] = drift_by_repo.get(caller, 0) + 1
    for repo_summary in summary["repos"]:
        repo_summary["contract_drift_count"] = drift_by_repo.get(repo_summary["name"], 0)
    impact = (
        impact_brief_for_path(code_map, repo=repo, path=path, limit=limit)
        if repo and path
        else None
    )
    return {
        **summary,
        "selected_repo": repo,
        "query_path": path,
        "impact": impact,
    }


@router.get("/api/code-intelligence", response_class=JSONResponse)
async def api_code_intelligence(
    request: Request,
    repo: str | None = Query(default=None, min_length=1, max_length=200),
    path: str | None = Query(default=None, min_length=1, max_length=1_024),
    limit: int = Query(default=25, ge=1, le=100),
) -> JSONResponse:
    """Summarize indexed repos or explain one file's bounded blast radius."""

    repo = repo.strip() if repo else None
    path = path.strip() if path else None
    repo = repo or None
    path = path or None
    if path and not repo:
        return JSONResponse(
            {"error": "Select a repository before analyzing a file."},
            status_code=400,
        )
    try:
        payload = await run_in_threadpool(
            _code_intelligence_payload,
            repo,
            path,
            limit,
            views._state_root(request) / "code-map.json",
        )
    except ValueError as exc:
        if "repo not found in code map" in str(exc):
            return JSONResponse(
                {"error": "That repository is not in the current code map."},
                status_code=404,
            )
        logger.exception("api_code_intelligence: invalid local code map")
        return JSONResponse({"error": views._GENERIC_ERROR}, status_code=500)
    except Exception:
        logger.exception("api_code_intelligence: failed to read local code map")
        return JSONResponse({"error": views._GENERIC_ERROR}, status_code=500)
    return JSONResponse(views._jsonable(payload))
