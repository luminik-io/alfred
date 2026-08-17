"""Explicit, repo-scoped transcript imports for saved Alfred runs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

IMPORT_SCHEMA_VERSION = 1
SUPPORTED_ENGINES = frozenset({"claude", "codex", "opencode"})
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_REPO_PART_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_TRANSCRIPT_NAMES = {
    "claude": "transcript.jsonl",
    "codex": "stdout.txt",
    "opencode": "events.jsonl",
}
MANAGED_TRANSCRIPT_NAMES = frozenset(_TRANSCRIPT_NAMES.values())


class ImportScopeError(ValueError):
    """The requested repository does not own the selected run or import."""


class ImportConflictError(RuntimeError):
    """Managed import state conflicts with the requested operation."""


@dataclass(frozen=True)
class RunImportResult:
    status: str
    transcript_path: Path
    sha256: str
    imported_at: str


def import_run_transcript(
    *,
    state_root: Path,
    agent: str,
    run_id: str,
    repo: str,
    engine: str,
    source: Path,
) -> RunImportResult:
    """Copy one transcript into managed state after verifying its run scope."""

    agent = _validated_name(agent, "agent")
    run_id = _validated_name(run_id, "run ID")
    repo = _normalize_repo(repo)
    engine = engine.strip().lower()
    if engine not in SUPPORTED_ENGINES:
        supported = ", ".join(sorted(SUPPORTED_ENGINES))
        raise ValueError(f"engine must be one of: {supported}")

    events_path = _find_events_path(state_root, agent=agent, run_id=run_id)
    if events_path is None:
        raise FileNotFoundError(f"run events not found for {agent}/{run_id}")
    event_repos = _read_event_repositories(events_path)
    if repo not in event_repos:
        raise ImportScopeError(f"run {agent}/{run_id} does not record repository {repo}")

    try:
        source_path = source.expanduser().resolve(strict=True)
    except OSError as exc:
        raise FileNotFoundError(f"transcript source not found: {source}") from exc
    if not source_path.is_file():
        raise FileNotFoundError(f"transcript source is not a regular file: {source}")

    digest, size_bytes = _hash_file(source_path)
    import_dir = _managed_import_dir(state_root, agent=agent, run_id=run_id)
    transcript_name = _TRANSCRIPT_NAMES[engine]
    transcript_path = import_dir / transcript_name
    manifest_path = import_dir / "manifest.json"

    if import_dir.exists():
        manifest = _read_manifest(manifest_path)
        if (
            manifest.get("agent") == agent
            and manifest.get("run_id") == run_id
            and manifest.get("repo") == repo
            and manifest.get("engine") == engine
            and manifest.get("sha256") == digest
            and manifest.get("size_bytes") == size_bytes
            and manifest.get("transcript_name") == transcript_name
            and transcript_path.is_file()
        ):
            return RunImportResult(
                status="unchanged",
                transcript_path=transcript_path,
                sha256=digest,
                imported_at=str(manifest.get("imported_at") or ""),
            )
        raise ImportConflictError(
            f"a different import already exists for {agent}/{run_id}; "
            "remove the existing import first"
        )

    imported_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    manifest = {
        "schema_version": IMPORT_SCHEMA_VERSION,
        "run_id": run_id,
        "agent": agent,
        "repo": repo,
        "engine": engine,
        "transcript_name": transcript_name,
        "sha256": digest,
        "size_bytes": size_bytes,
        "imported_at": imported_at,
    }
    import_dir.mkdir(parents=True, exist_ok=False)
    try:
        copied_digest, copied_size = _copy_atomic(source_path, transcript_path)
        if (copied_digest, copied_size) != (digest, size_bytes):
            raise ImportConflictError("transcript source changed during import; retry the import")
        _write_json_atomic(manifest_path, manifest)
    except Exception:
        for path in (transcript_path, manifest_path):
            path.unlink(missing_ok=True)
        import_dir.rmdir()
        raise
    return RunImportResult(
        status="imported",
        transcript_path=transcript_path,
        sha256=digest,
        imported_at=imported_at,
    )


def remove_run_import(
    *,
    state_root: Path,
    agent: str,
    run_id: str,
    repo: str,
) -> RunImportResult:
    """Remove only Alfred-managed files for one verified repository import."""

    agent = _validated_name(agent, "agent")
    run_id = _validated_name(run_id, "run ID")
    repo = _normalize_repo(repo)
    import_dir = _managed_import_dir(state_root, agent=agent, run_id=run_id)
    manifest_path = import_dir / "manifest.json"
    if not import_dir.exists():
        return RunImportResult(
            status="not_found",
            transcript_path=import_dir / "transcript",
            sha256="",
            imported_at="",
        )
    manifest = _read_manifest(manifest_path)
    if manifest.get("agent") != agent or manifest.get("run_id") != run_id:
        raise ImportConflictError("import manifest does not match its managed directory")
    manifest_repo = str(manifest.get("repo") or "")
    if manifest_repo != repo:
        raise ImportScopeError(f"import {agent}/{run_id} belongs to {manifest_repo or 'unknown'}")
    transcript_name = str(manifest.get("transcript_name") or "")
    if transcript_name not in MANAGED_TRANSCRIPT_NAMES:
        raise ImportConflictError("import manifest has an unsupported transcript name")
    known_names = {"manifest.json", transcript_name}
    actual_names = {path.name for path in import_dir.iterdir()}
    unknown = sorted(actual_names - known_names)
    if unknown:
        raise ImportConflictError(f"import directory contains unknown files: {', '.join(unknown)}")
    transcript_path = import_dir / transcript_name
    result = RunImportResult(
        status="removed",
        transcript_path=transcript_path,
        sha256=str(manifest.get("sha256") or ""),
        imported_at=str(manifest.get("imported_at") or ""),
    )
    transcript_path.unlink(missing_ok=True)
    manifest_path.unlink()
    import_dir.rmdir()
    return result


def _validated_name(value: str, label: str) -> str:
    normalized = value.strip()
    if not _SAFE_NAME_RE.fullmatch(normalized):
        raise ValueError(f"{label} contains unsupported characters")
    return normalized


def _normalize_repo(value: str) -> str:
    candidate = value.strip()
    if "://" in candidate:
        parsed = urlparse(candidate)
        if parsed.scheme != "https" or parsed.netloc.lower() != "github.com":
            raise ValueError("repo URL must use https://github.com/owner/repo")
        candidate = parsed.path.strip("/")
    if candidate.endswith(".git"):
        candidate = candidate[:-4]
    parts = candidate.split("/")
    if len(parts) != 2 or not all(_REPO_PART_RE.fullmatch(part) for part in parts):
        raise ValueError("repo must use owner/repo")
    return f"{parts[0].lower()}/{parts[1].lower()}"


def _find_events_path(state_root: Path, *, agent: str, run_id: str) -> Path | None:
    candidates = (
        state_root / agent / "events" / f"{run_id}.jsonl",
        state_root / "codenames" / agent / "events" / f"{run_id}.jsonl",
    )
    return next((path for path in candidates if path.is_file()), None)


def _read_event_repositories(path: Path) -> set[str]:
    repos: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise FileNotFoundError(f"cannot read run events: {path}") from exc
    for raw in lines:
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        for key in ("repo", "repository"):
            value = event.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            try:
                repos.add(_normalize_repo(value))
            except ValueError:
                continue
    return repos


def _managed_import_dir(state_root: Path, *, agent: str, run_id: str) -> Path:
    imports_root = (state_root / "imports").resolve()
    target = imports_root / agent / run_id
    resolved_target = target.resolve()
    if not resolved_target.is_relative_to(imports_root):
        raise ValueError("managed import path escapes the state directory")
    return target


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _copy_atomic(source: Path, destination: Path) -> tuple[str, int]:
    fd, temporary_name = tempfile.mkstemp(prefix=".transcript-", dir=destination.parent)
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(fd, "wb") as output, source.open("rb") as input_handle:
            for chunk in iter(lambda: input_handle.read(1024 * 1024), b""):
                output.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, destination)
        return digest.hexdigest(), size
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    fd, temporary_name = tempfile.mkstemp(prefix=".manifest-", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImportConflictError(f"cannot read import manifest: {path}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != IMPORT_SCHEMA_VERSION:
        raise ImportConflictError("import manifest has an unsupported schema")
    return data


__all__ = [
    "IMPORT_SCHEMA_VERSION",
    "MANAGED_TRANSCRIPT_NAMES",
    "SUPPORTED_ENGINES",
    "ImportConflictError",
    "ImportScopeError",
    "RunImportResult",
    "import_run_transcript",
    "remove_run_import",
]
