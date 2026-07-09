"""Reviewable memory candidates from scoped Slack planning work.

When a Slack planning draft reaches a ready readiness check, the listener
proposes a reviewable memory candidate per repo so the operator can promote a
durable lesson from the work. This module owns that proposal: picking the
writer out of the (possibly nested) memory provider, adapting to the modern or
legacy ``propose_memory`` signature, de-duplicating per repo on a stable
candidate key, and bookkeeping the accepted candidate ids/keys back onto the
saved draft payload.

The proposer takes the draft-revision lock as an injected dependency so it
serializes against the listener's own draft writes on the SAME per-path lock
(the concurrency contract is unchanged from when this lived on the listener).
"""

from __future__ import annotations

import inspect
import json
import os
import re
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from spec_helper import IssueDraft


class SlackMemoryCandidateProposer:
    """Queue reviewable memory candidates from ready Slack planning drafts."""

    def __init__(
        self,
        memory_provider: Any | None,
        *,
        draft_lock: Callable[[Path], Any],
    ) -> None:
        self._memory_provider = memory_provider
        self._draft_lock = draft_lock

    def propose(
        self,
        event: Any,
        result: Any,
        draft_path: Path,
        *,
        source: str,
    ) -> tuple[str, ...]:
        """Queue reviewable memory candidates from scoped Slack planning work."""
        if _env_disabled("ALFRED_SLACK_MEMORY_CANDIDATES"):
            return ()
        readiness = getattr(result, "readiness", None)
        if readiness is None or not getattr(readiness, "ok", False):
            return ()
        writer = _memory_candidate_writer(self._memory_provider)
        if writer is None or not hasattr(writer, "propose_memory"):
            return ()
        draft = getattr(result, "draft", None)
        if not isinstance(draft, IssueDraft):
            return ()
        body = _slack_memory_candidate_body(draft)
        if not body:
            return ()
        evidence = {
            "kind": "slack_planning",
            "source": source,
            "draft_path": str(draft_path),
            "event_id": event.event_id,
            "channel": event.channel,
            "thread_ts": event.root_ts,
            "readiness_score": getattr(readiness, "score", None),
            "amendments": list(getattr(result, "amendments", ()) or ()),
        }
        ids: list[str] = []
        proposed_keys: list[str] = []
        propose_memory = writer.propose_memory
        use_modern_signature = _propose_memory_supports_modern_signature(propose_memory)
        with self._draft_lock(draft_path):
            existing_keys = _draft_memory_candidate_keys(draft_path)
            for repo in draft.repos or ["planning"]:
                candidate_key = _slack_memory_candidate_key(repo)
                if candidate_key in existing_keys:
                    continue
                repo_evidence = {
                    **evidence,
                    "repo": repo,
                    "candidate_key": candidate_key,
                }
                if use_modern_signature:
                    kwargs = {
                        "codename": "planning",
                        "repo": repo,
                        "body": body,
                        "tags": ["slack", "planning"],
                        "severity": "info",
                        "source": source,
                        "evidence": json.dumps(repo_evidence, sort_keys=True),
                        "confidence": 0.68,
                    }
                else:
                    kwargs = {
                        "agent": "planning",
                        "repo": repo,
                        "topic": "slack-planning",
                        "body": body,
                        "source": source,
                        "evidence": [repo_evidence],
                    }
                try:
                    candidate = propose_memory(**kwargs)
                except Exception as exc:
                    print(
                        f"[SLACK-LISTENER-WARN] could not queue {source} memory "
                        f"candidate for {repo}: {exc}",
                        file=sys.stderr,
                    )
                    continue
                candidate_id = getattr(candidate, "id", candidate)
                ids.append(str(candidate_id))
                proposed_keys.append(candidate_key)
            if proposed_keys:
                _append_memory_candidate_keys(draft_path, proposed_keys)
        return tuple(ids)


def _memory_candidate_writer(provider: Any | None) -> Any | None:
    if provider is None:
        return None
    if hasattr(provider, "propose_memory"):
        return provider
    brain = getattr(provider, "brain", None)
    if brain is not None and hasattr(brain, "propose_memory"):
        return brain
    providers = getattr(provider, "providers", None)
    if isinstance(providers, (list, tuple)):
        for child in providers:
            writer = _memory_candidate_writer(child)
            if writer is not None:
                return writer
    return None


def _env_disabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
        "disabled",
    }


def _propose_memory_supports_modern_signature(method: Any) -> bool:
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return True
    parameters = signature.parameters.values()
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters):
        return True
    return {"codename", "tags", "severity", "confidence"}.issubset(signature.parameters)


def _append_memory_candidate_ids(path: Path, candidate_ids: Iterable[str]) -> None:
    _append_draft_list_values(path, "memory_candidate_ids", candidate_ids)


def _append_memory_candidate_keys(path: Path, candidate_keys: Iterable[str]) -> None:
    _append_draft_list_values(path, "memory_candidate_keys", candidate_keys)


def _draft_memory_candidate_keys(path: Path) -> set[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(payload, dict):
        return set()
    existing = payload.get("memory_candidate_keys")
    if isinstance(existing, list):
        return {str(item) for item in existing if str(item)}
    return set()


def _append_draft_list_values(path: Path, field: str, values: Iterable[str]) -> None:
    clean_values = [str(value) for value in values if str(value)]
    if not clean_values:
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(payload, dict):
        return
    existing = payload.get(field)
    merged = [str(item) for item in existing] if isinstance(existing, list) else []
    for value in clean_values:
        if value not in merged:
            merged.append(value)
    payload[field] = merged
    tmp = path.with_name(f"{path.name}.tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError:
        return


def _slack_memory_candidate_key(repo: str) -> str:
    return f"slack-planning:{repo.strip() or 'planning'}"


def _slack_memory_candidate_body(draft: IssueDraft) -> str:
    parts = [
        f"Slack planning lesson for {draft.title.strip() or 'untitled work'}.",
        f"Problem: {_short_plain(draft.problem, 220)}" if draft.problem else "",
        (
            f"Desired behavior: {_short_plain(draft.desired_behavior, 220)}"
            if draft.desired_behavior
            else ""
        ),
    ]
    if draft.acceptance_criteria:
        parts.append(
            "Acceptance: "
            + "; ".join(_short_plain(item, 140) for item in draft.acceptance_criteria[:3])
        )
    if draft.test_plan:
        parts.append(f"Verification: {_short_plain(draft.test_plan, 180)}")
    body = " ".join(part for part in parts if part).strip()
    return body[:900]


def _short_plain(value: str, limit: int) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip()
    if len(cleaned) <= limit:
        return cleaned
    if limit <= 3:
        return cleaned[: max(0, limit)]
    return cleaned[: limit - 3].rstrip() + "..."
