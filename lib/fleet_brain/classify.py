"""Failure classification for the reliability governor.

Turns a raw ``(subtype, summary)`` failure event into a coarse bucket
(``local_setup`` / ``auth`` / ``provider_limit`` / ``timeout`` /
``agent_quality`` / ``unknown``) and a suggested operator action. The
``list_failure_patterns`` read path (see :mod:`fleet_brain.reliability`) groups
repeated failures and attaches these here.

The buckets here are the SAME set ``fleet_brain.taxonomy`` documents for the
failure-lesson kind, kept in sync so a classified failure and a promoted
failure-lesson describe the outcome the same way.
"""

from __future__ import annotations

from typing import Any

# Failure subtypes that are routine, non-actionable outcomes (a cap was hit, a
# run was a no-op, a green result). These never become a reliability action.
_NON_ACTIONABLE_FAILURE_SUBTYPES = {
    "already_implemented",
    "already-implemented",
    "daily-cap",
    "dedup-skip",
    "dedup_skip",
    "fixes-landed",
    "green",
    "idle-no-candidates",
    "idle-no-comments",
    "idle-no-pr",
    "noop",
    "ok",
    "pr-opened",
    "review-cap",
    "review-posted",
    "silent-no-work",
    "silent_no_work",
    "success",
    "test-ok",
    "test_ok",
    "triage-cap",
    "triaged",
}


def _classify_failure_pattern(subtype: str, summary: str) -> str:
    text = f"{subtype} {summary}".lower()
    if any(token in text for token in ("executable doesn't exist", "playwright", "chromium")):
        return "local_setup"
    if any(token in text for token in ("auth", "token", "sso", "accessdenied", "permission")):
        return "auth"
    if any(token in text for token in ("rate_limit", "quota", "budget", "too many requests")):
        return "provider_limit"
    if any(token in text for token in ("timeout", "timed out", "error_timeout")):
        return "timeout"
    if any(token in text for token in ("no-commit", "no commit", "wip", "salvage")):
        return "agent_quality"
    return "unknown"


def _is_non_actionable_failure_pattern(subtype: str, summary: str) -> bool:
    normalized = str(subtype or "").strip().lower()
    if normalized in _NON_ACTIONABLE_FAILURE_SUBTYPES:
        return True
    text = f"{normalized} {summary or ''}".lower()
    if any(token in text for token in ("error", "fail", "timeout", "blocked", "crash")):
        return False
    return normalized.endswith("-cap")


def _suggest_failure_action(*, classification: str, codename: str, count: int) -> str:
    if classification == "local_setup":
        return "file_setup_issue"
    if classification == "auth":
        return "ask_human"
    if classification == "provider_limit":
        return "retry_later"
    if classification == "agent_quality":
        return "review_prompt_or_checks"
    if classification == "timeout" and count >= 3:
        return "pause_agent"
    if classification == "timeout":
        return "retry_later"
    if count >= 3:
        return "pause_agent"
    return "inspect"


def _failure_action_summary(pattern: dict[str, Any]) -> str:
    repo = f" on {pattern['repo']}" if pattern.get("repo") else ""
    return (
        f"{pattern['codename']} has {pattern['count']} repeated "
        f"{pattern['classification']} failure(s){repo}: "
        f"{pattern['suggested_action']}"
    )
