#!/usr/bin/env python3
"""Read-only MCP-style stdio tools for Alfred local memory.

The script speaks the JSON-RPC methods used by MCP clients:
``initialize``, ``tools/list``, and ``tools/call``. It intentionally
depends only on the standard library and exposes allowlisted summaries,
not raw transcripts, prompts, stdout, stderr, or secrets.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
for candidate in (
    Path(os.environ.get("ALFRED_HOME", "")) / "lib",
    # Processed last, so source checkout lib lands at sys.path[0].
    _HERE.parent / "lib",
):
    if candidate.exists():
        candidate_path = str(candidate)
        while candidate_path in sys.path:
            sys.path.remove(candidate_path)
        sys.path.insert(0, candidate_path)

from agent_runner import WORKSPACE, local_repo_dir  # noqa: E402
from agent_runner.read_ledger import (  # noqa: E402
    ReadLedger,
    delta_context_lines,
    delta_max_chars,
    delta_max_ratio,
    ledger_root_for,
    read_delta_available,
    read_delta_enabled,
)
from code_graph import (  # noqa: E402
    blast_radius_for_paths,
    impact_for_path,
    skeleton_for_path,
    summarize_codegraph,
)
from fleet_brain import FleetBrain, default_db_path  # noqa: E402
from fleet_brain.doctor import run_memory_doctor  # noqa: E402

TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "alfred_brain_status",
        "description": "Return local fleet-brain row counts and health status.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "alfred_memory_recall",
        "description": "Recall trusted lessons scoped by codename or repo.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "codename": {"type": "string"},
                "repo": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "alfred_memory_candidates",
        "description": "List reviewable memory candidates scoped by codename or repo.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["candidate", "validated", "rejected", "retired", "all"],
                },
                "repo": {"type": "string"},
                "codename": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "alfred_recent_file_touches",
        "description": "List recent files touched by Alfred firings, scoped by codename or repo.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "codename": {"type": "string"},
                "path": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "alfred_failure_patterns",
        "description": "List normalized non-success events scoped by codename or repo.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "codename": {"type": "string"},
                "subtype": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "alfred_who_owns",
        "description": "Resolve the CODEOWNERS owner(s) for a repo path from the fleet graph.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["repo", "path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "alfred_recent_changes_near",
        "description": "List recent fleet file touches in the same directory as a repo path.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "path": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["repo", "path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "alfred_prs_touching",
        "description": "List pull requests that changed a repo path from the materialized graph edges.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "path": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["repo", "path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "alfred_code_graph_summary",
        "description": "Summarize Alfred's local code graph by repo without returning raw source.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "alfred_code_impact",
        "description": "Return local import, symbol, route, API-call, and drift hints for a repo path.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "path": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["repo", "path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "alfred_code_blast_radius",
        "description": "Aggregate local graph impact for changed repo paths.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 200,
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["repo", "paths"],
            "additionalProperties": False,
        },
    },
    {
        "name": "alfred_code_skeleton",
        "description": (
            "Return a structure-only skeleton (signatures, first docstring line, "
            "elided bodies) of a repo file from the local code graph. Orientation "
            "only: read the real file for any body you need or intend to edit."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "path": {"type": "string"},
                "symbol": {"type": "string"},
            },
            "required": ["repo", "path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "alfred_read_delta",
        "description": (
            "Read a repo file, returning only a unified diff versus what was last "
            "surfaced to this firing (full content on first read or large change)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["repo", "path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "alfred_memory_doctor",
        "description": "Run read-only health checks over fleet-brain memory.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
)


def _brain(db_path: str | None = None) -> FleetBrain:
    return FleetBrain(
        db_path=db_path or os.environ.get("ALFRED_FLEET_BRAIN_DB") or default_db_path()
    )


def call_tool(
    name: str, arguments: dict[str, Any] | None = None, *, db_path: str | None = None
) -> Any:
    args = arguments or {}
    if name == "alfred_brain_status":
        return run_memory_doctor(db_path or os.environ.get("ALFRED_FLEET_BRAIN_DB"))
    if name == "alfred_memory_doctor":
        return run_memory_doctor(db_path or os.environ.get("ALFRED_FLEET_BRAIN_DB"))
    if name == "alfred_code_graph_summary":
        return summarize_codegraph(
            repo=_str_or_none(args.get("repo")),
            limit=_int_limit(args.get("limit"), default=25, max_value=100),
        )
    if name == "alfred_code_impact":
        repo, path = _require_repo_path(args)
        return impact_for_path(
            None,
            repo=repo,
            path=path,
            limit=_int_limit(args.get("limit"), default=50, max_value=200),
        )
    if name == "alfred_code_blast_radius":
        repo, paths = _require_repo_paths(args)
        return blast_radius_for_paths(
            None,
            repo=repo,
            paths=paths,
            limit=_int_limit(args.get("limit"), default=50, max_value=200),
        )
    if name == "alfred_code_skeleton":
        repo, path = _require_repo_path(args)
        _reject_unsafe_relpath(path)
        return skeleton_for_path(
            None,
            repo=repo,
            path=path,
            repo_root=_repo_root(repo),
            symbol=_str_or_none(args.get("symbol")),
        )
    if name == "alfred_read_delta":
        repo, path = _require_repo_path(args)
        _reject_unsafe_relpath(path)
        return _read_delta(repo, path)
    brain = _brain(db_path)
    if name == "alfred_memory_recall":
        _require_scope(args)
        return [
            _lesson_to_dict(L)
            for L in brain.recall(
                codename=_str_or_none(args.get("codename")),
                repo=_str_or_none(args.get("repo")),
                query=_str_or_none(args.get("query")),
                limit=int(args.get("limit") or 8),
            )
        ]
    if name == "alfred_memory_candidates":
        _require_scope(args)
        status = args.get("status") or "candidate"
        return [
            _candidate_to_dict(C, include_raw=_raw_memory_allowed())
            for C in brain.list_memory_candidates(
                status=None if status == "all" else status,
                repo=_str_or_none(args.get("repo")),
                codename=_str_or_none(args.get("codename")),
                limit=int(args.get("limit") or 50),
            )
        ]
    if name == "alfred_recent_file_touches":
        _require_scope(args)
        return [
            _touch_to_dict(T)
            for T in brain.list_file_touches(
                repo=_str_or_none(args.get("repo")),
                codename=_str_or_none(args.get("codename")),
                path=_str_or_none(args.get("path")),
                limit=int(args.get("limit") or 50),
            )
        ]
    if name == "alfred_failure_patterns":
        _require_scope(args)
        failures = brain.list_failures(
            repo=_str_or_none(args.get("repo")),
            codename=_str_or_none(args.get("codename")),
            subtype=_str_or_none(args.get("subtype")),
            limit=int(args.get("limit") or 50),
        )
        by_subtype: dict[str, int] = {}
        for event in failures:
            by_subtype[event.subtype] = by_subtype.get(event.subtype, 0) + 1
        return {"by_subtype": by_subtype, "events": [_failure_to_dict(F) for F in failures]}
    if name == "alfred_who_owns":
        repo, path = _require_repo_path(args)
        return {"repo": repo, "path": path, "owners": brain.who_owns(repo=repo, path=path)}
    if name == "alfred_recent_changes_near":
        repo, path = _require_repo_path(args)
        return brain.recent_changes_near(repo=repo, path=path, limit=int(args.get("limit") or 20))
    if name == "alfred_prs_touching":
        repo, path = _require_repo_path(args)
        return brain.prs_touching(repo=repo, path=path, limit=int(args.get("limit") or 20))
    raise ValueError(f"unknown tool: {name}")


def handle_request(request: dict[str, Any], *, db_path: str | None = None) -> dict[str, Any] | None:
    method = request.get("method")
    req_id = request.get("id")
    try:
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "alfred-memory", "version": "0.1.0"},
                    "capabilities": {"tools": {}},
                },
            }
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": list(TOOLS)}}
        if method == "tools/call":
            params = request.get("params") or {}
            result = call_tool(
                str(params.get("name") or ""),
                params.get("arguments") if isinstance(params.get("arguments"), dict) else {},
                db_path=db_path,
            )
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
                    "isError": False,
                },
            }
        if isinstance(method, str) and method.startswith("notifications/"):
            return None
        raise ValueError(f"unsupported method: {method}")
    except Exception as exc:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32000, "message": str(exc)},
        }


def serve_stdio(*, db_path: str | None = None) -> int:
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"parse error: {exc}"},
            }
        else:
            response = handle_request(request, db_path=db_path)
        if response is not None:
            print(json.dumps(response), flush=True)
    return 0


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _require_scope(args: dict[str, Any]) -> None:
    """Require local MCP callers to narrow row-returning memory queries."""
    if _str_or_none(args.get("codename")) or _str_or_none(args.get("repo")):
        return
    raise ValueError("memory tools require a codename or repo scope")


def _require_repo_path(args: dict[str, Any]) -> tuple[str, str]:
    """Require both ``repo`` and ``path`` for the graph read tools."""
    repo = _str_or_none(args.get("repo"))
    path = _str_or_none(args.get("path"))
    if not repo or not path:
        raise ValueError("graph tools require both a repo and a path")
    return repo, path


def _require_repo_paths(args: dict[str, Any]) -> tuple[str, list[str]]:
    """Require ``repo`` plus at least one path for multi-path graph tools."""
    repo = _str_or_none(args.get("repo"))
    raw_paths = args.get("paths")
    if raw_paths is None:
        raise ValueError("graph tools require a repo and at least one path")
    if not isinstance(raw_paths, list):
        raise ValueError("graph tools require paths as a list of strings")
    paths = []
    for item in raw_paths:
        if not isinstance(item, str):
            raise ValueError("graph tools require paths as a list of strings")
        text = item.strip()
        if text:
            paths.append(text)
    if len(paths) > 200:
        raise ValueError("graph tools accept at most 200 paths")
    if not repo or not paths:
        raise ValueError("graph tools require a repo and at least one path")
    return repo, paths


def _repo_root(repo: str) -> Path:
    """Resolve a repo slug to its on-disk checkout, rejecting workspace escapes.

    The ``repo`` slug is untrusted input just like ``path``: a slug of ``..``,
    ``../../x``, or an absolute component would resolve outside the workspace
    root via :func:`local_repo_dir`. Reject upward/absolute slugs and require the
    resolved checkout to stay within the realpath of ``WORKSPACE``.
    """
    _reject_unsafe_relpath(repo)
    root = WORKSPACE / local_repo_dir(repo)
    workspace_real = Path(os.path.realpath(WORKSPACE))
    root_real = Path(os.path.realpath(root))
    if root_real != workspace_real and workspace_real not in root_real.parents:
        raise ValueError("repo slug escapes the workspace root")
    return root


def _reject_unsafe_relpath(path: str) -> None:
    """Reject an untrusted repo path that is absolute or traverses upward.

    A cheap boundary check for both source-reading tools: it refuses an absolute
    component or any ``..`` segment before the path is joined to a repo root.
    The realpath containment in :func:`_safe_source_path` and
    ``code_graph.skeleton_for_path`` is the authoritative guard (it also catches
    symlink escapes); this just returns a clearer, earlier error.
    """
    if os.path.isabs(path) or Path(path).is_absolute():
        raise ValueError("path must be relative to the repo root")
    parts = Path(path.replace("\\", "/")).parts
    if ".." in parts:
        raise ValueError("path must not traverse outside the repo root")


def _safe_source_path(root: Path, path: str) -> Path:
    """Resolve ``path`` under ``root``, rejecting any escape from the checkout.

    The MCP ``repo``/``path`` arguments are untrusted. A ``..`` segment, an
    absolute component, or a symlink pointing outside the checkout would let a
    caller read arbitrary host files. Resolve both the root and the candidate to
    real paths and require the candidate to stay inside the root; raise
    ``ValueError`` otherwise so the tool returns an error instead of leaking a
    file.
    """
    if os.path.isabs(path) or Path(path).is_absolute():
        raise ValueError("path must be relative to the repo root")
    root_real = Path(os.path.realpath(root))
    candidate = Path(os.path.realpath(root / path))
    if candidate != root_real and root_real not in candidate.parents:
        raise ValueError("path escapes the repo root")
    return candidate


def _read_delta(repo: str, path: str) -> dict[str, Any]:
    """Read ``repo``/``path`` through the per-worktree delta ledger.

    First read returns full content; a re-read within the same firing returns a
    unified diff against the previously surfaced copy, falling back to full
    content when the change is too large to diff usefully. When the delta gate
    is off, always returns full content.
    """
    root = _repo_root(repo)
    source_path = _safe_source_path(root, path)
    try:
        content = source_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"repo": repo, "path": path, "mode": "error", "reason": str(exc)}

    if not read_delta_enabled() or not read_delta_available():
        # Delta disabled, or the ledger cannot be scoped to a single firing
        # (no ALFRED_FIRING_ID and no explicit ledger dir). Full reads avoid any
        # cross-firing diff corruption.
        return {"repo": repo, "path": path, "mode": "full", "content": content}

    ledger = ReadLedger(ledger_root_for(root))
    result = ledger.surface(
        f"{repo}:{path}",
        content,
        max_ratio=delta_max_ratio(),
        context_lines=delta_context_lines(),
        max_diff_chars=delta_max_chars(),
    )
    payload: dict[str, Any] = {"repo": repo, **result.as_raw()}
    payload["path"] = path
    if result.mode == "delta":
        payload["diff"] = result.diff
    elif result.mode == "full":
        payload["content"] = result.content
    return payload


def _int_limit(value: Any, *, default: int, max_value: int) -> int:
    try:
        parsed = int(value if value is not None else default)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, max_value))


def _raw_memory_allowed() -> bool:
    return os.environ.get("ALFRED_MCP_ALLOW_RAW_MEMORY", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _lesson_to_dict(lesson) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return {
        "id": lesson.id,
        "codename": lesson.codename,
        "repo": lesson.repo,
        "body": lesson.body,
        "tags": lesson.tags,
        "severity": lesson.severity,
        "firing_id": lesson.firing_id,
        "created_at": lesson.created_at.astimezone(UTC).isoformat(),
    }


def _candidate_to_dict(candidate, *, include_raw: bool = False) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    payload = {
        "id": candidate.id,
        "codename": candidate.codename,
        "repo": candidate.repo,
        "tags": candidate.tags,
        "severity": candidate.severity,
        "source": candidate.source,
        "source_firing_id": candidate.source_firing_id,
        "confidence": candidate.confidence,
        "status": candidate.status,
        "created_at": candidate.created_at.astimezone(UTC).isoformat(),
        "promoted_lesson_id": candidate.promoted_lesson_id,
    }
    if include_raw:
        payload.update(
            {
                "body": candidate.body,
                "evidence": candidate.evidence,
                "reviewed_at": candidate.reviewed_at.astimezone(UTC).isoformat()
                if candidate.reviewed_at
                else None,
                "reviewed_by": candidate.reviewed_by,
                "review_note": candidate.review_note,
            }
        )
    else:
        payload["body_preview"] = _preview(candidate.body)
        payload["has_evidence"] = bool(candidate.evidence)
        payload["reviewed"] = bool(candidate.reviewed_at or candidate.reviewed_by)
    return payload


def _preview(value: str, limit: int = 120) -> str:
    text = " ".join((value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "..."


def _touch_to_dict(touch) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return {
        "id": touch.id,
        "repo": touch.repo,
        "path": touch.path,
        "codename": touch.codename,
        "firing_id": touch.firing_id,
        "pr_url": touch.pr_url,
        "change_type": touch.change_type,
        "touched_at": touch.touched_at.astimezone(UTC).isoformat(),
    }


def _failure_to_dict(event) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return {
        "id": event.id,
        "codename": event.codename,
        "repo": event.repo,
        "firing_id": event.firing_id,
        "subtype": event.subtype,
        "summary": event.summary,
        "engine": event.engine,
        "severity": event.severity,
        "created_at": event.created_at.astimezone(UTC).isoformat(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alfred-mcp", description="Read-only Alfred MCP tools")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="serve JSON-RPC over stdio")
    serve.add_argument("--db", help="path to the SQLite brain file")
    serve.set_defaults(func=lambda args: serve_stdio(db_path=args.db))
    parser.set_defaults(func=lambda args: serve_stdio(db_path=getattr(args, "db", None)))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
