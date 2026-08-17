"""Compact, replayable evidence derived from one local Alfred run.

The record contains only stable facts and artifact locations. It never reads
GitHub, an engine, or transcript contents while building the firing list.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from event_provenance import EVENT_SOURCE_VALUES, infer_event_source
from run_import import IMPORT_SCHEMA_VERSION, MANAGED_TRANSCRIPT_NAMES

RUN_EVIDENCE_SCHEMA_VERSION = 1
_SAFE_NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


@dataclass(frozen=True)
class EvidenceFact:
    kind: str
    source: str
    event_type: str
    event_seq: int | None
    data: dict[str, Any]


@dataclass(frozen=True)
class EvidenceArtifact:
    kind: str
    status: str
    path: str | None


@dataclass(frozen=True)
class RunEvidenceRecord:
    schema_version: int
    run_id: str
    agent: str
    event_count: int
    facts: list[EvidenceFact]
    artifacts: list[EvidenceArtifact]


_FACT_SPECS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "repo_picked": (("repository", ("repo", "url")),),
    "issue_picked": (
        ("repository", ("repo",)),
        ("issue", ("repo", "number", "url", "title")),
    ),
    "pr_picked": (
        ("repository", ("repo",)),
        ("pull_request", ("repo", "number", "url", "title")),
    ),
    "worktree_created": (("worktree", ("path", "branch", "repo")),),
    "llm_invoke_done": (
        ("engine_session", ("engine", "session_id", "turns", "subtype", "success")),
        ("run_configuration", ("configuration",)),
    ),
    "claude_invoke_done": (
        ("engine_session", ("engine", "session_id", "turns", "subtype", "success")),
        ("run_configuration", ("configuration",)),
    ),
    "plan_approved": (("approval", ("number", "issue", "repo", "decision", "decision_record")),),
    "pre_push_checks_passed": (("check", ("command", "repo", "success")),),
    "checks_done": (("check", ("command", "repo", "success", "checks")),),
    "branch_pushed": (
        ("branch", ("branch", "repo")),
        ("commit", ("commit_sha", "sha", "repo", "pull_request")),
    ),
    "fix_pushed": (
        ("branch", ("branch", "repo")),
        ("commit", ("commit_sha", "sha", "repo", "pull_request")),
    ),
    "pr_opened": (("pull_request", ("repo", "number", "url", "title")),),
    "review_posted": (("review", ("repo", "number", "url", "p0_count", "p1_count", "result")),),
}


def _fact_data(event: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: event[key] for key in keys if key in event and event[key] is not None}


def derive_run_evidence(
    *,
    agent: str,
    run_id: str,
    events: list[dict[str, Any]],
    events_path: str | Path,
    transcript_path: str | Path | None,
    imported_transcript_paths: Sequence[str | Path] | None = None,
) -> RunEvidenceRecord:
    """Derive stable facts and local artifact locations without external reads."""

    facts: list[EvidenceFact] = []
    seen: set[str] = set()
    for event in events:
        event_type = str(event.get("type") or event.get("event") or "")
        specs = _FACT_SPECS.get(event_type, ())
        if not specs:
            continue
        raw_source = str(event.get("source") or "").strip()
        source = raw_source if raw_source in EVENT_SOURCE_VALUES else infer_event_source(event_type)
        seq_value = event.get("seq")
        event_seq = seq_value if isinstance(seq_value, int) else None
        for kind, keys in specs:
            data = _fact_data(event, keys)
            if not data:
                continue
            fingerprint = json.dumps(
                {"kind": kind, "source": source, "data": data},
                sort_keys=True,
                default=str,
            )
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            facts.append(
                EvidenceFact(
                    kind=kind,
                    source=source,
                    event_type=event_type,
                    event_seq=event_seq,
                    data=data,
                )
            )

    event_artifact = EvidenceArtifact(
        kind="events",
        status="available",
        path=str(events_path),
    )
    transcript_artifact = EvidenceArtifact(
        kind="transcript",
        status="available" if transcript_path is not None else "unavailable",
        path=str(transcript_path) if transcript_path is not None else None,
    )
    imported_artifacts = [
        EvidenceArtifact(
            kind="imported_session",
            status="available",
            path=str(path),
        )
        for path in (imported_transcript_paths or [])
    ]
    return RunEvidenceRecord(
        schema_version=RUN_EVIDENCE_SCHEMA_VERSION,
        run_id=run_id,
        agent=agent,
        event_count=len(events),
        facts=facts,
        artifacts=[event_artifact, transcript_artifact, *imported_artifacts],
    )


def discover_transcript_artifact(
    state_root: Path,
    *,
    agent: str,
    run_id: str,
) -> Path | None:
    """Find one known local transcript artifact without reading its contents."""

    if not _safe_name(agent) or not _safe_name(run_id):
        return None
    candidates = (
        (state_root / "transcripts" / agent).glob(f"*/{run_id}.jsonl"),
        (state_root / "opencode" / agent).glob(f"*/{run_id}.events.jsonl"),
        (state_root / "codex" / agent).glob(f"*/{run_id}.stdout.txt"),
    )
    for paths in candidates:
        for path in sorted(paths, reverse=True):
            if path.is_file():
                return path
    return None


def discover_imported_transcript_artifacts(
    state_root: Path,
    *,
    agent: str,
    run_id: str,
) -> list[Path]:
    """List explicit managed transcript imports for one run."""

    if not _safe_name(agent) or not _safe_name(run_id):
        return []
    import_dir = state_root / "imports" / agent / run_id
    if not import_dir.is_dir():
        return []
    try:
        manifest = json.loads((import_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(manifest, dict) or manifest.get("schema_version") != IMPORT_SCHEMA_VERSION:
        return []
    if manifest.get("agent") != agent or manifest.get("run_id") != run_id:
        return []
    transcript_name = str(manifest.get("transcript_name") or "")
    if transcript_name not in MANAGED_TRANSCRIPT_NAMES:
        return []
    transcript_path = import_dir / transcript_name
    return [transcript_path] if transcript_path.is_file() else []


def discover_transcript_by_run_id(state_root: Path, run_id: str) -> Path | None:
    """Find one known transcript artifact when the agent name is unavailable."""

    if not _safe_name(run_id):
        return None
    patterns = (
        (state_root / "transcripts", f"*/*/{run_id}.jsonl"),
        (state_root / "opencode", f"*/*/{run_id}.events.jsonl"),
        (state_root / "codex", f"*/*/{run_id}.stdout.txt"),
    )
    for root, pattern in patterns:
        if not root.is_dir():
            continue
        for path in sorted(root.glob(pattern), reverse=True):
            if path.is_file():
                return path
    imports_root = state_root / "imports"
    if imports_root.is_dir():
        for agent_dir in sorted(imports_root.iterdir(), reverse=True):
            if not agent_dir.is_dir() or not _safe_name(agent_dir.name):
                continue
            imported = discover_imported_transcript_artifacts(
                state_root,
                agent=agent_dir.name,
                run_id=run_id,
            )
            if imported:
                return imported[0]
    return None


def _safe_name(value: str) -> bool:
    return bool(value) and all(char in _SAFE_NAME_CHARS for char in value)


__all__ = [
    "RUN_EVIDENCE_SCHEMA_VERSION",
    "EvidenceArtifact",
    "EvidenceFact",
    "RunEvidenceRecord",
    "derive_run_evidence",
    "discover_imported_transcript_artifacts",
    "discover_transcript_artifact",
    "discover_transcript_by_run_id",
]
