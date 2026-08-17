"""Dependency-free source labels for Alfred event facts."""

from __future__ import annotations

import enum


class EventSource(enum.StrEnum):
    """System that directly observed the fact recorded by an event."""

    ALFRED = "alfred"
    ENGINE = "engine"
    GITHUB = "github"
    OPERATOR = "operator"


EVENT_SOURCE_VALUES = frozenset(source.value for source in EventSource)

_ENGINE_EVENT_TYPES = frozenset(
    {
        "llm_invoke_done",
        "claude_invoke_done",
        "llm_fallback",
    }
)
_OPERATOR_EVENT_TYPES = frozenset(
    {
        "plan_approved",
        "plan_feedback_captured",
        "plan_repo_scope_amended",
    }
)
_GITHUB_EVENT_TYPES = frozenset(
    {
        "repo_picked",
        "issue_picked",
        "issues_inspected",
        "pr_picked",
        "pr_opened",
        "fix_pushed",
        "branch_pushed",
        "review_posted",
        "triaged",
        "triages_rejected",
        "triage_implement_stripped",
        "triage_refetch_failed",
    }
)


def infer_event_source(event_type: str) -> str:
    """Return the system that directly observed ``event_type``."""

    if event_type in _ENGINE_EVENT_TYPES:
        return EventSource.ENGINE.value
    if event_type in _OPERATOR_EVENT_TYPES:
        return EventSource.OPERATOR.value
    if event_type in _GITHUB_EVENT_TYPES:
        return EventSource.GITHUB.value
    return EventSource.ALFRED.value


__all__ = ["EVENT_SOURCE_VALUES", "EventSource", "infer_event_source"]
