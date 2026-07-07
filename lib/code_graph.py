"""Stable code-graph export helpers for Alfred's local code map.

``bin/code-map-refresh.py`` writes an implementation-shaped JSON snapshot. This
module turns that snapshot into a small public contract agents and local tools
can rely on: ``alfred-codegraph@1``.
"""

from __future__ import annotations

import json
import os
import posixpath
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CODEGRAPH_SCHEMA = "alfred-codegraph@1"
MAX_BLAST_RADIUS_PATHS = 200


def default_code_map_path() -> Path:
    """Return the installed code-map path for the current Alfred home."""

    alfred_home = Path(os.environ.get("ALFRED_HOME") or os.path.expanduser("~/.alfred"))
    return alfred_home / "state" / "code-map.json"


def load_code_map(path: Path | str | None = None) -> dict[str, Any]:
    """Load a code-map JSON file, returning an empty map when it is absent."""

    resolved = Path(path) if path is not None else default_code_map_path()
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"generated_at": None, "repos": {}, "contract_drift": []}
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid code-map JSON at {resolved}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"invalid code-map JSON at {resolved}: expected object")
    payload.setdefault("repos", {})
    payload.setdefault("contract_drift", [])
    return payload


def export_codegraph(
    code_map: dict[str, Any] | None = None,
    *,
    path: Path | str | None = None,
    include_files: bool = True,
) -> dict[str, Any]:
    """Export the code map using the stable ``alfred-codegraph@1`` schema."""

    resolved = Path(path) if path is not None else default_code_map_path()
    payload = code_map if code_map is not None else load_code_map(resolved)
    repos = _dict_value(payload.get("repos"))
    source = (
        {"kind": "alfred-code-map", "path": str(resolved)}
        if code_map is None or path is not None
        else {"kind": "in-memory-code-map", "path": None}
    )
    return {
        "schema": CODEGRAPH_SCHEMA,
        "generated_at": _str_or_none(payload.get("generated_at")),
        "exported_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source,
        "repos": [
            _export_repo(name, data, include_files=include_files)
            for name, data in sorted(repos.items())
            if isinstance(data, dict)
        ],
        "contract_drift": _list_of_dicts(payload.get("contract_drift")),
    }


def summarize_codegraph(
    code_map: dict[str, Any] | None = None,
    *,
    repo: str | None = None,
    path: Path | str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Return repo-level graph summaries without raw file lists."""

    payload = code_map if code_map is not None else load_code_map(path)
    repos = _dict_value(payload.get("repos"))
    selected = {
        name: data
        for name, data in sorted(repos.items())
        if isinstance(data, dict) and (repo is None or name == repo)
    }
    if repo is not None and repo not in selected:
        raise ValueError(f"repo not found in code map: {repo}")
    max_items = _clamped_int(limit, default=25, max_value=100)
    summaries = [_repo_summary(name, data) for name, data in list(selected.items())[:max_items]]
    selected_names = {str(item["name"]) for item in summaries}
    filtered_drift = [
        drift
        for drift in _list_of_dicts(payload.get("contract_drift"))
        if drift.get("caller") in selected_names
    ]
    return {
        "schema": CODEGRAPH_SCHEMA,
        "generated_at": _str_or_none(payload.get("generated_at")),
        "repos": summaries,
        "repo_count": len(summaries),
        "contract_drift_count": len(filtered_drift),
    }


def impact_for_path(
    code_map: dict[str, Any] | None,
    *,
    repo: str,
    path: str,
    code_map_path: Path | str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Return local impact hints for a repo path from the code-map graph."""

    payload = code_map if code_map is not None else load_code_map(code_map_path)
    repos = _dict_value(payload.get("repos"))
    repo_data = repos.get(repo)
    if not isinstance(repo_data, dict):
        raise ValueError(f"repo not found in code map: {repo}")

    max_items = _clamped_int(limit, default=50, max_value=200)
    target_path = _normalize_file_path(path)
    files = _list_of_dicts(repo_data.get("files"))
    file_by_path = {_normalize_file_path(str(f.get("path") or "")): f for f in files}
    matched_path, match_status, candidate_matches = _match_path(target_path, file_by_path)
    matched_file = file_by_path.get(matched_path or "")
    candidate_paths = set(file_by_path)

    incoming: list[dict[str, Any]] = []
    outgoing: list[dict[str, Any]] = []
    for edge in _list_of_dicts(repo_data.get("edges")):
        source = _normalize_file_path(str(edge.get("from") or ""))
        target = str(edge.get("to") or "").strip()
        if not source or not target:
            continue
        resolved = _resolve_import(source, target, candidate_paths)
        row = {
            "from": source,
            "to": target,
            "resolved_to": resolved,
            "kind": str(edge.get("kind") or "import"),
        }
        if matched_path is not None and source == matched_path:
            outgoing.append(row)
        if matched_path is not None and (
            resolved == matched_path or _normalize_file_path(target) == matched_path
        ):
            incoming.append(row)

    contracts = _contracts_for_file(repo_data, matched_path)
    drift = [
        d
        for d in _list_of_dicts(payload.get("contract_drift"))
        if d.get("caller") == repo and _file_matches(str(d.get("file") or ""), matched_path)
    ]
    nearby_all = [
        _normalize_file_path(str(file_info.get("path") or ""))
        for file_info in files
        if _same_directory(str(file_info.get("path") or ""), matched_path)
        and _normalize_file_path(str(file_info.get("path") or "")) != matched_path
    ]
    symbols = _list_of_dicts(matched_file.get("symbols")) if matched_file else []
    imports = list(matched_file.get("imports") or []) if matched_file else []
    resolved_outgoing_count = len([row for row in outgoing if row.get("resolved_to")])

    return {
        "schema": CODEGRAPH_SCHEMA,
        "repo": repo,
        "path": target_path,
        "matched_file": matched_path,
        "match_status": match_status,
        "candidate_matches": candidate_matches[:max_items],
        "head_sha": _str_or_none(repo_data.get("head_sha")),
        "language": _str_or_none(matched_file.get("language")) if matched_file else None,
        "counts": {
            "candidate_matches": len(candidate_matches),
            "symbols": len(symbols),
            "imports": len(imports),
            "imported_by": len(incoming),
            "imports_resolved": resolved_outgoing_count,
            "contract_surfaces": _contract_surface_count(contracts),
            "contract_drift": len(drift),
            "nearby_files": len(nearby_all),
        },
        "symbols": symbols[:max_items],
        "imports": imports[:max_items],
        "imported_by": incoming[:max_items],
        "imports_resolved": outgoing[:max_items],
        "contracts": contracts,
        "contract_drift": drift[:max_items],
        "nearby_files": nearby_all[:max_items],
        "graph_summary": _clean_summary(repo_data.get("graph_summary")),
    }


def impact_brief_for_path(
    code_map: dict[str, Any] | None,
    *,
    repo: str,
    path: str,
    code_map_path: Path | str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Return a prompt-ready blast-radius briefing for a repo path.

    The brief is deterministic and entirely local: it summarizes the same graph
    facts as :func:`impact_for_path` without asking a model to infer risk from
    raw source. Agents can paste this into a plan, PR body, or review prompt as
    scoped context.
    """

    max_items = _clamped_int(limit, default=25, max_value=200)
    collection_limit = 200
    impact = impact_for_path(
        code_map,
        repo=repo,
        path=path,
        code_map_path=code_map_path,
        limit=collection_limit,
    )
    dependents = [
        {
            "path": row["from"],
            "via": row["to"],
            "kind": row["kind"],
        }
        for row in impact["imported_by"][:max_items]
    ]
    dependencies = [
        {
            "path": row["resolved_to"],
            "via": row["to"],
            "kind": row["kind"],
        }
        for row in impact["imports_resolved"]
        if row.get("resolved_to")
    ][:max_items]
    contract_surfaces = _brief_contract_surfaces(impact["contracts"], max_items=max_items)
    drift = [
        {
            "method": _str_or_none(row.get("method")),
            "path": _str_or_none(row.get("path")),
            "normalized": _str_or_none(row.get("normalized")),
            "file": _str_or_none(row.get("file")),
        }
        for row in impact["contract_drift"][:max_items]
    ]
    impact_counts = _dict_value(impact.get("counts"))
    dependent_count = _count_from(impact_counts, "imported_by", fallback=len(impact["imported_by"]))
    dependency_count = _count_from(
        impact_counts,
        "imports_resolved",
        fallback=len(dependencies),
    )
    contract_count = _count_from(
        impact_counts,
        "contract_surfaces",
        fallback=len(contract_surfaces),
    )
    drift_count = _count_from(impact_counts, "contract_drift", fallback=len(drift))
    nearby_count = _count_from(impact_counts, "nearby_files", fallback=len(impact["nearby_files"]))
    level, reasons = _impact_level(
        match_status=str(impact["match_status"]),
        dependent_count=dependent_count,
        dependency_count=dependency_count,
        contract_count=contract_count,
        drift_count=drift_count,
    )
    next_checks = _impact_next_checks(
        match_status=str(impact["match_status"]),
        dependent_count=dependent_count,
        contract_count=contract_count,
        drift_count=drift_count,
    )
    summary = _impact_summary(
        repo=repo,
        path=str(impact["matched_file"] or impact["path"]),
        level=level,
        reasons=reasons,
    )
    return {
        "schema": CODEGRAPH_SCHEMA,
        "kind": "impact-brief",
        "repo": repo,
        "path": impact["path"],
        "matched_file": impact["matched_file"],
        "match_status": impact["match_status"],
        "head_sha": impact["head_sha"],
        "language": impact["language"],
        "level": level,
        "reasons": reasons,
        "summary": summary,
        "counts": {
            "symbols": _count_from(impact_counts, "symbols", fallback=len(impact["symbols"])),
            "direct_dependents": dependent_count,
            "direct_dependencies": dependency_count,
            "contract_surfaces": contract_count,
            "contract_drift": drift_count,
            "nearby_files": nearby_count,
        },
        "symbols": impact["symbols"][:max_items],
        "direct_dependents": dependents,
        "direct_dependencies": dependencies,
        "contract_surfaces": contract_surfaces,
        "contract_drift": drift,
        "nearby_files": impact["nearby_files"][:max_items],
        "candidate_matches": impact["candidate_matches"][:max_items],
        "next_checks": next_checks,
    }


def blast_radius_for_paths(
    code_map: dict[str, Any] | None,
    *,
    repo: str,
    paths: list[str],
    code_map_path: Path | str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Aggregate deterministic impact briefs for a set of changed paths."""

    max_items = _clamped_int(limit, default=50, max_value=200)
    requested_paths = _unique_strings(_normalize_file_path(path) for path in paths)
    if len(requested_paths) > MAX_BLAST_RADIUS_PATHS:
        raise ValueError(f"blast-radius supports at most {MAX_BLAST_RADIUS_PATHS} paths")
    payload = code_map if code_map is not None else load_code_map(code_map_path)
    briefs = [
        impact_brief_for_path(
            payload,
            repo=repo,
            path=path,
            limit=200,
        )
        for path in requested_paths
    ]
    matched_paths = [
        str(brief["matched_file"])
        for brief in briefs
        if brief.get("match_status") in {"exact", "suffix"} and brief.get("matched_file")
    ]
    changed_set = set(matched_paths)
    unmapped = [brief for brief in briefs if brief.get("match_status") == "not_found"]
    ambiguous = [brief for brief in briefs if brief.get("match_status") == "ambiguous"]
    dependents_all = _dedupe_rows(
        (
            {
                "changed_path": brief.get("matched_file") or brief.get("path"),
                "path": row.get("path"),
                "via": row.get("via"),
                "kind": row.get("kind"),
                "also_changed": row.get("path") in changed_set,
            }
            for brief in briefs
            for row in _list_of_dicts(brief.get("direct_dependents"))
        ),
        keys=("changed_path", "path", "via", "kind"),
        limit=200,
    )
    dependencies_all = _dedupe_rows(
        (
            {
                "changed_path": brief.get("matched_file") or brief.get("path"),
                "path": row.get("path"),
                "via": row.get("via"),
                "kind": row.get("kind"),
                "also_changed": row.get("path") in changed_set,
            }
            for brief in briefs
            for row in _list_of_dicts(brief.get("direct_dependencies"))
        ),
        keys=("changed_path", "path", "via", "kind"),
        limit=200,
    )
    contract_surfaces_all = _dedupe_rows(
        (
            {
                "changed_path": brief.get("matched_file") or brief.get("path"),
                "kind": row.get("kind"),
                "method": row.get("method"),
                "path": row.get("path"),
                "file": row.get("file"),
            }
            for brief in briefs
            for row in _list_of_dicts(brief.get("contract_surfaces"))
        ),
        keys=("kind", "method", "path", "file"),
        limit=200,
    )
    drift_all = _dedupe_rows(
        (
            {
                "changed_path": brief.get("matched_file") or brief.get("path"),
                "method": row.get("method"),
                "path": row.get("path"),
                "normalized": row.get("normalized"),
                "file": row.get("file"),
            }
            for brief in briefs
            for row in _list_of_dicts(brief.get("contract_drift"))
        ),
        keys=("method", "path", "normalized", "file"),
        limit=200,
    )
    nearby = _unique_strings(
        str(row)
        for brief in briefs
        for row in brief.get("nearby_files", [])
        if str(row) not in changed_set
    )[:max_items]
    graph_counts = _aggregate_graph_counts(payload, repo=repo, matched_paths=matched_paths)
    dependent_count = graph_counts["direct_dependents"]
    dependency_count = graph_counts["direct_dependencies"]
    contract_count = sum(_brief_count(brief, "contract_surfaces") for brief in briefs)
    drift_count = sum(_brief_count(brief, "contract_drift") for brief in briefs)
    nearby_count = len(nearby)
    level, reasons = _blast_radius_level(
        changed_count=len(requested_paths),
        unmapped_count=len(unmapped),
        ambiguous_count=len(ambiguous),
        dependent_count=dependent_count,
        contract_count=contract_count,
        drift_count=drift_count,
    )
    return {
        "schema": CODEGRAPH_SCHEMA,
        "kind": "blast-radius",
        "repo": repo,
        "level": level,
        "summary": _blast_radius_summary(repo=repo, level=level, reasons=reasons),
        "reasons": reasons,
        "counts": {
            "changed_paths": len(requested_paths),
            "matched_paths": len(matched_paths),
            "unmapped_paths": len(unmapped),
            "ambiguous_paths": len(ambiguous),
            "direct_dependents": dependent_count,
            "direct_dependencies": dependency_count,
            "contract_surfaces": contract_count,
            "contract_drift": drift_count,
            "nearby_files": nearby_count,
        },
        "changed_paths": [
            {
                "path": brief.get("path"),
                "matched_file": brief.get("matched_file"),
                "match_status": brief.get("match_status"),
                "level": brief.get("level"),
                "summary": brief.get("summary"),
                "candidate_matches": brief.get("candidate_matches") or [],
            }
            for brief in briefs
        ],
        "direct_dependents": dependents_all[:max_items],
        "direct_dependencies": dependencies_all[:max_items],
        "contract_surfaces": contract_surfaces_all[:max_items],
        "contract_drift": drift_all[:max_items],
        "nearby_files": nearby,
        "next_checks": _blast_radius_next_checks(
            unmapped_count=len(unmapped),
            ambiguous_count=len(ambiguous),
            dependent_count=dependent_count,
            contract_count=contract_count,
            drift_count=drift_count,
        ),
    }


def render_blast_radius(blast_radius: dict[str, Any]) -> str:
    """Render a ``blast_radius_for_paths`` payload as concise prompt text."""

    repo = str(blast_radius.get("repo") or "")
    lines = [
        f"Blast radius: {repo}",
        f"Level: {blast_radius.get('level') or 'unknown'}",
        f"Summary: {blast_radius.get('summary') or 'No summary available.'}",
    ]
    reasons = [str(item) for item in blast_radius.get("reasons") or [] if str(item).strip()]
    if reasons:
        lines.append("Signals: " + "; ".join(reasons))
    changed = _list_of_dicts(blast_radius.get("changed_paths"))
    if changed:
        lines.append("Changed paths:")
        for row in changed:
            status = row.get("match_status")
            matched = row.get("matched_file") or row.get("path")
            lines.append(f"- {matched} ({status})")
    _append_brief_rows(
        lines,
        "Direct dependents",
        blast_radius.get("direct_dependents"),
        lambda row: f"{row.get('path')} depends on {row.get('changed_path')} via {row.get('via')}",
    )
    _append_brief_rows(
        lines,
        "Contract surfaces",
        blast_radius.get("contract_surfaces"),
        lambda row: " ".join(
            str(part)
            for part in (
                row.get("changed_path"),
                row.get("kind"),
                row.get("method"),
                row.get("path"),
            )
            if part
        ),
    )
    checks = [str(item) for item in blast_radius.get("next_checks") or [] if str(item).strip()]
    if checks:
        lines.append("Next checks:")
        lines.extend(f"- {item}" for item in checks)
    return "\n".join(lines)


def render_impact_brief(brief: dict[str, Any]) -> str:
    """Render an ``impact_brief_for_path`` payload as concise prompt text."""

    repo = str(brief.get("repo") or "")
    path = str(brief.get("matched_file") or brief.get("path") or "")
    lines = [
        f"Blast radius: {repo}:{path}",
        f"Level: {brief.get('level') or 'unknown'}",
        f"Summary: {brief.get('summary') or 'No summary available.'}",
    ]
    if brief.get("match_status") != "exact":
        lines.append(f"Match: {brief.get('match_status')}")
    reasons = [str(item) for item in brief.get("reasons") or [] if str(item).strip()]
    if reasons:
        lines.append("Signals: " + "; ".join(reasons))
    _append_brief_rows(
        lines,
        "Direct dependents",
        brief.get("direct_dependents"),
        lambda row: f"{row.get('path')} imports {row.get('via')}",
    )
    _append_brief_rows(
        lines,
        "Direct dependencies",
        brief.get("direct_dependencies"),
        lambda row: f"{row.get('path')} via {row.get('via')}",
    )
    _append_brief_rows(
        lines,
        "Contract surfaces",
        brief.get("contract_surfaces"),
        lambda row: " ".join(
            str(part)
            for part in (row.get("kind"), row.get("method"), row.get("path"), row.get("file"))
            if part
        ),
    )
    _append_brief_rows(
        lines,
        "Contract drift",
        brief.get("contract_drift"),
        lambda row: " ".join(
            str(part) for part in (row.get("method"), row.get("path"), row.get("file")) if part
        ),
    )
    checks = [str(item) for item in brief.get("next_checks") or [] if str(item).strip()]
    if checks:
        lines.append("Next checks:")
        lines.extend(f"- {item}" for item in checks)
    return "\n".join(lines)


def _export_repo(name: str, data: dict[str, Any], *, include_files: bool) -> dict[str, Any]:
    repo: dict[str, Any] = {
        "name": name,
        "head_sha": _str_or_none(data.get("head_sha")),
        "summary": _clean_summary(data.get("graph_summary")),
        "contracts": _repo_contracts(data),
    }
    if include_files:
        repo["files"] = [_clean_file(f) for f in _list_of_dicts(data.get("files"))]
        repo["edges"] = [_clean_edge(e) for e in _list_of_dicts(data.get("edges"))]
    return repo


def _repo_summary(name: str, data: dict[str, Any]) -> dict[str, Any]:
    contracts = _repo_contracts(data)
    return {
        "name": name,
        "head_sha": _str_or_none(data.get("head_sha")),
        "summary": _clean_summary(data.get("graph_summary")),
        "endpoint_count": len(contracts["endpoints"]),
        "route_count": len(contracts["routes"]),
        "api_call_count": len(contracts["api_calls"]),
    }


def _repo_contracts(data: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {
        "endpoints": _list_of_dicts(data.get("endpoints")),
        "routes": _list_of_dicts(data.get("routes")),
        "api_calls": _list_of_dicts(data.get("api_calls")),
    }


def _contracts_for_file(data: dict[str, Any], path: str | None) -> dict[str, list[dict[str, Any]]]:
    if not path:
        return {"endpoints": [], "routes": [], "api_calls": []}
    return {
        "endpoints": [
            row
            for row in _list_of_dicts(data.get("endpoints"))
            if _file_matches(row.get("file"), path)
        ],
        "routes": [
            row
            for row in _list_of_dicts(data.get("routes"))
            if _file_matches(row.get("file"), path)
        ],
        "api_calls": [
            row
            for row in _list_of_dicts(data.get("api_calls"))
            if _file_matches(row.get("file"), path)
        ],
    }


def _clean_summary(value: Any) -> dict[str, Any]:
    raw = _dict_value(value)
    languages = _dict_value(raw.get("languages"))
    return {
        "files": _nonnegative_int(raw.get("files")),
        "symbols": _nonnegative_int(raw.get("symbols")),
        "imports": _nonnegative_int(raw.get("imports")),
        "languages": {
            str(k): _nonnegative_int(v)
            for k, v in sorted(languages.items())
            if _nonnegative_int(v) > 0
        },
        "truncated": bool(raw.get("truncated")),
    }


def _clean_file(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": _normalize_file_path(str(value.get("path") or "")),
        "language": _str_or_none(value.get("language")),
        "symbols": _list_of_dicts(value.get("symbols")),
        "imports": list(value.get("imports") or []),
    }


def _clean_edge(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "from": _normalize_file_path(str(value.get("from") or "")),
        "to": str(value.get("to") or "").strip(),
        "kind": str(value.get("kind") or "import"),
    }


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _dict_value(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return value


def _nonnegative_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def _clamped_int(value: Any, *, default: int, max_value: int) -> int:
    try:
        parsed = int(value if value is not None else default)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, max_value))


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_file_path(path: str) -> str:
    clean = path.split(":", 1)[0].strip().replace("\\", "/")
    while clean.startswith("./"):
        clean = clean[2:]
    return clean.strip("/")


def _match_path(path: str, files: dict[str, dict[str, Any]]) -> tuple[str | None, str, list[str]]:
    if path in files:
        return path, "exact", [path]
    suffix_matches = sorted(candidate for candidate in files if candidate.endswith(f"/{path}"))
    if len(suffix_matches) == 1:
        return suffix_matches[0], "suffix", suffix_matches
    if suffix_matches:
        return None, "ambiguous", suffix_matches
    return None, "not_found", []


def _file_matches(file_ref: Any, path: str | None) -> bool:
    if not path:
        return False
    return _normalize_file_path(str(file_ref or "")) == path


def _same_directory(candidate: str, path: str | None) -> bool:
    if not path:
        return False
    return Path(_normalize_file_path(candidate)).parent == Path(path).parent


def _resolve_import(source: str, target: str, candidates: set[str]) -> str | None:
    if not target.startswith("."):
        return None
    normalized = _relative_import_path(source, target)
    stems = [
        normalized,
        f"{normalized}.py",
        f"{normalized}.ts",
        f"{normalized}.tsx",
        f"{normalized}.js",
        f"{normalized}.jsx",
        f"{normalized}.kt",
        f"{normalized}.go",
        f"{normalized}.rs",
        f"{normalized}.swift",
        f"{normalized}/index.ts",
        f"{normalized}/index.tsx",
        f"{normalized}/index.js",
        f"{normalized}/index.jsx",
        f"{normalized}/__init__.py",
    ]
    for candidate in stems:
        clean = _normalize_file_path(candidate)
        if clean in candidates:
            return clean
    return None


def _relative_import_path(source: str, target: str) -> str:
    base = Path(source).parent
    if target.startswith(".") and not target.startswith(("./", "../")) and "/" not in target:
        dot_count = len(target) - len(target.lstrip("."))
        module = target[dot_count:].replace(".", "/")
        for _ in range(max(0, dot_count - 1)):
            base = base.parent
        return posixpath.normpath((base / module).as_posix())
    return posixpath.normpath((base / target).as_posix())


def _brief_contract_surfaces(
    contracts: dict[str, list[dict[str, Any]]], *, max_items: int
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for kind in ("endpoints", "routes", "api_calls"):
        for row in contracts.get(kind, [])[:max_items]:
            rows.append(
                {
                    "kind": kind.removesuffix("s"),
                    "method": _str_or_none(row.get("method")),
                    "path": _str_or_none(row.get("path")),
                    "file": _str_or_none(row.get("file")),
                }
            )
            if len(rows) >= max_items:
                return rows
    return rows


def _contract_surface_count(contracts: dict[str, list[dict[str, Any]]]) -> int:
    return sum(len(contracts.get(kind, [])) for kind in ("endpoints", "routes", "api_calls"))


def _count_from(counts: dict[str, Any], key: str, *, fallback: int) -> int:
    value = counts.get(key)
    if isinstance(value, int) and value >= 0:
        return value
    return fallback


def _brief_count(brief: dict[str, Any], key: str) -> int:
    counts = _dict_value(brief.get("counts"))
    return _count_from(counts, key, fallback=len(_list_of_dicts(brief.get(key))))


def _aggregate_graph_counts(
    code_map: dict[str, Any],
    *,
    repo: str,
    matched_paths: list[str],
) -> dict[str, int]:
    matched_set = {_normalize_file_path(path) for path in matched_paths if path}
    if not matched_set:
        return {"direct_dependents": 0, "direct_dependencies": 0}
    repos = _dict_value(code_map.get("repos"))
    repo_data = _dict_value(repos.get(repo))
    files = _list_of_dicts(repo_data.get("files"))
    candidate_paths = {
        _normalize_file_path(str(file_info.get("path") or "")) for file_info in files
    }
    dependent_paths: set[str] = set()
    dependency_paths: set[str] = set()
    for edge in _list_of_dicts(repo_data.get("edges")):
        source = _normalize_file_path(str(edge.get("from") or ""))
        target = str(edge.get("to") or "").strip()
        if not source or not target:
            continue
        resolved = _resolve_import(source, target, candidate_paths)
        if source in matched_set and resolved:
            dependency_paths.add(resolved)
        if resolved in matched_set or _normalize_file_path(target) in matched_set:
            dependent_paths.add(source)
    return {
        "direct_dependents": len(dependent_paths),
        "direct_dependencies": len(dependency_paths),
    }


def _impact_level(
    *,
    match_status: str,
    dependent_count: int,
    dependency_count: int,
    contract_count: int,
    drift_count: int,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if match_status not in {"exact", "suffix"}:
        reasons.append(f"path match is {match_status}")
        return "unknown", reasons
    if match_status == "suffix":
        reasons.append("path matched by suffix")
    if drift_count:
        reasons.append(f"{drift_count} contract drift item(s)")
    if contract_count:
        reasons.append(f"{contract_count} API/route surface(s)")
    if dependent_count:
        reasons.append(f"{dependent_count} direct dependent file(s)")
    if dependency_count:
        reasons.append(f"{dependency_count} direct dependency file(s)")
    if drift_count or (contract_count and dependent_count):
        return "high", reasons
    if contract_count or dependent_count >= 3:
        return "medium", reasons
    if dependent_count or dependency_count:
        return "low", reasons
    reasons.append("no direct dependents or contract surfaces in the local graph")
    return "low", reasons


def _impact_next_checks(
    *,
    match_status: str,
    dependent_count: int,
    contract_count: int,
    drift_count: int,
) -> list[str]:
    if match_status == "not_found":
        return [
            "Refresh the code map, then verify the path is tracked before relying on this brief."
        ]
    if match_status == "ambiguous":
        return [
            "Use one exact path from candidate_matches before planning or reviewing the change."
        ]
    checks = ["Run the narrow test or typecheck command for the touched package."]
    if match_status == "suffix":
        checks.append("Prefer the exact matched path in follow-up commands and PR notes.")
    if dependent_count:
        checks.append("Inspect direct dependents before changing public behavior.")
    if contract_count:
        checks.append("Check API or route contract tests and generated clients.")
    if drift_count:
        checks.append("Resolve contract drift or document why the unmatched call is intentional.")
    return checks


def _impact_summary(*, repo: str, path: str, level: str, reasons: list[str]) -> str:
    if reasons:
        return f"{repo}:{path} has {level} local blast radius: " + "; ".join(reasons) + "."
    return f"{repo}:{path} has {level} local blast radius from the current code map."


def _blast_radius_level(
    *,
    changed_count: int,
    unmapped_count: int,
    ambiguous_count: int,
    dependent_count: int,
    contract_count: int,
    drift_count: int,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if changed_count:
        reasons.append(f"{changed_count} changed path(s)")
    if unmapped_count:
        reasons.append(f"{unmapped_count} unmapped path(s)")
    if ambiguous_count:
        reasons.append(f"{ambiguous_count} ambiguous path match(es)")
    if drift_count:
        reasons.append(f"{drift_count} contract drift item(s)")
    if contract_count:
        reasons.append(f"{contract_count} API/route surface(s)")
    if dependent_count:
        reasons.append(f"{dependent_count} direct dependent file(s)")
    if ambiguous_count or drift_count or contract_count >= 2:
        return "high", reasons
    if unmapped_count or contract_count or dependent_count >= 3 or changed_count >= 5:
        return "medium", reasons
    return "low", reasons or ["no direct dependents or contract surfaces in the local graph"]


def _blast_radius_summary(*, repo: str, level: str, reasons: list[str]) -> str:
    if reasons:
        return f"{repo} has {level} local blast radius: " + "; ".join(reasons) + "."
    return f"{repo} has {level} local blast radius from the current code map."


def _blast_radius_next_checks(
    *,
    unmapped_count: int,
    ambiguous_count: int,
    dependent_count: int,
    contract_count: int,
    drift_count: int,
) -> list[str]:
    checks = ["Run the narrow test or typecheck command for each touched package."]
    if unmapped_count:
        checks.append("Refresh the code map or inspect unmapped paths manually.")
    if ambiguous_count:
        checks.append("Replace ambiguous paths with exact paths before relying on the graph.")
    if dependent_count:
        checks.append("Review direct dependents for behavior changes.")
    if contract_count:
        checks.append("Run API or route contract checks for changed public surfaces.")
    if drift_count:
        checks.append("Resolve contract drift or document why the unmatched call is intentional.")
    return checks


def _unique_strings(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _dedupe_rows(
    values: Any,
    *,
    keys: tuple[str, ...],
    limit: int,
) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    result: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        marker = tuple(value.get(key) for key in keys)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(value)
        if len(result) >= limit:
            break
    return result


def _append_brief_rows(
    lines: list[str],
    title: str,
    rows: Any,
    render_row: Any,
) -> None:
    cleaned = [row for row in rows or [] if isinstance(row, dict)]
    if not cleaned:
        return
    lines.append(f"{title}:")
    lines.extend(f"- {render_row(row)}" for row in cleaned)
