"""Plain recall explanations shared by runtime, CLI, API, and Desktop."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from typing import Any

from fleet_brain import Lesson
from memory_tokens import tokenize


@dataclass(frozen=True)
class RecalledLesson(Lesson):
    """A stored lesson plus metadata about this specific recall."""

    recall_provider: str | None = None
    match_reason: str | None = None


def memory_match_reason(lesson: object, *, query: str | None, repo: str | None) -> str:
    """Explain why one recalled lesson is relevant without claiming hidden evidence."""

    clean_query = str(query or "").strip()
    if clean_query:
        query_tokens = tokenize(clean_query)
        lesson_text = " ".join(
            [
                str(getattr(lesson, "body", "") or ""),
                " ".join(str(tag) for tag in (getattr(lesson, "tags", []) or [])),
            ]
        )
        lesson_tokens = set(tokenize(lesson_text))
        matches = [token for token in query_tokens if token in lesson_tokens]
        if matches:
            return f"Matches request terms: {', '.join(matches[:3])}."
        return "Provider ranked this lesson for the request."
    clean_repo = str(repo or getattr(lesson, "repo", "") or "").strip()
    if clean_repo:
        return f"Active lesson for {clean_repo}."
    return "Recent active lesson."


def annotate_recalled_lesson(
    lesson: Any,
    *,
    provider: str,
    query: str | None,
    repo: str | None,
) -> Any:
    """Return a lesson with transient recall metadata when its type supports it."""

    reason = memory_match_reason(lesson, query=query, repo=repo)
    if isinstance(lesson, Lesson):
        values = {item.name: getattr(lesson, item.name) for item in fields(Lesson)}
        return RecalledLesson(
            **values,
            recall_provider=getattr(lesson, "recall_provider", None) or provider,
            match_reason=getattr(lesson, "match_reason", None) or reason,
        )
    try:
        if not getattr(lesson, "recall_provider", None):
            lesson.recall_provider = provider
        if not getattr(lesson, "match_reason", None):
            lesson.match_reason = reason
    except (AttributeError, TypeError):
        pass
    return lesson


def annotate_recalled_lessons(
    lessons: Iterable[Any],
    *,
    provider: str,
    query: str | None,
    repo: str | None,
) -> list[Any]:
    return [
        annotate_recalled_lesson(
            lesson,
            provider=provider,
            query=query,
            repo=repo,
        )
        for lesson in lessons
    ]


def memory_status(lesson: object, *, now: datetime | None = None) -> str:
    if getattr(lesson, "superseded_by", None):
        return "superseded"
    valid_until = getattr(lesson, "valid_until", None)
    current = now or datetime.now(UTC)
    if isinstance(valid_until, datetime):
        expiry = valid_until if valid_until.tzinfo else valid_until.replace(tzinfo=UTC)
        if expiry <= current:
            return "expired"
    return "active"


def age_seconds(lesson: object, *, now: datetime | None = None) -> int | None:
    created_at = getattr(lesson, "created_at", None)
    if not isinstance(created_at, datetime):
        return None
    created = created_at if created_at.tzinfo else created_at.replace(tzinfo=UTC)
    return max(0, int(((now or datetime.now(UTC)) - created).total_seconds()))


def format_age(seconds: int | None) -> str:
    """Format a bounded age for operator-facing text."""

    if seconds is None:
        return "unknown"
    if seconds < 60:
        return "less than 1 minute"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''}"
