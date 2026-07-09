"""Deterministic ranking, decay, reinforce, and delta for injected lessons.

This module is the *policy* layer above Alfred's existing recall. It never
recalls, writes, or migrates the memory store. It takes the `(lesson, score)`
pairs that :mod:`agent_runner.memory_runtime` already gates by relevance and:

1. **Ranks** them by a single legible weighted score that fuses the AMS
   relevance signal, a validity/ROI signal (severity), age-decayed recency, and
   a reinforce-on-reuse bonus. The formula is a plain weighted sum, not an
   opaque model, so every ordering is explainable from four numbers.
2. **Decays** a lesson's recency weight by age (older lessons fade) and
   **reinforces** a lesson each time it is actually injected, so a repeatedly
   useful lesson keeps its place while a stale one drifts down.
3. **Deltas** injection within a single firing: a lesson injected on an earlier
   turn of the same firing is not injected again, freeing the budget for fresh
   material.

Every knob is config-driven and OFF by default, so the historical
"inject in recall order" behavior is preserved byte-for-byte unless an operator
opts in. See ``docs/MEMORY_PROVIDERS.md`` for the operator-facing description.

The reinforce and delta counters live in-process only. The Redis AMS store has
no reuse/last-injected field and this battery deliberately does not
schema-migrate it (see the "Follow-ups" note in the docs); persisting reuse
across restarts is a clean future extension.
"""

from __future__ import annotations

import math
import os
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

# --------------------------------------------------------------------------
# Config knobs (env-driven, conservative defaults)
# --------------------------------------------------------------------------

_RANK_ENABLED_ENV = "ALFRED_MEMORY_RANK"
_DELTA_ENABLED_ENV = "ALFRED_MEMORY_DELTA"
_HALFLIFE_ENV = "ALFRED_MEMORY_DECAY_HALFLIFE_DAYS"
_W_RELEVANCE_ENV = "ALFRED_MEMORY_RANK_W_RELEVANCE"
_W_ROI_ENV = "ALFRED_MEMORY_RANK_W_ROI"
_W_RECENCY_ENV = "ALFRED_MEMORY_RANK_W_RECENCY"
_W_REUSE_ENV = "ALFRED_MEMORY_RANK_W_REUSE"

# Half-life for the age-decay curve. A lesson's recency weight halves every
# this-many days. 30 days is a conservative "a month-old lesson is worth half a
# fresh one" default; raise it to make memory fade more slowly.
_DEFAULT_HALFLIFE_DAYS = 30.0

# Default rank weights. Relevance leads (the AMS similarity is the strongest
# signal Alfred has), ROI and recency are meaningful tie-breakers, and reuse is
# a gentle nudge so a proven lesson edges out an equally relevant unproven one.
_DEFAULT_W_RELEVANCE = 1.0
_DEFAULT_W_ROI = 0.5
_DEFAULT_W_RECENCY = 0.5
_DEFAULT_W_REUSE = 0.25

# A recalled lesson whose backend reports no similarity is scored at this
# neutral midpoint rather than 0.0, so an unscored-but-recalled lesson is not
# unfairly buried beneath a weakly-scored one.
_NEUTRAL_RELEVANCE = 0.5

# Validity / ROI signal derived from a lesson's severity. A blocker lesson
# ("this breaks the build") is worth more injected room than a stylistic info
# note. Kept as three fixed points so the mapping is fully explainable.
_ROI_BY_SEVERITY = {
    "info": 0.34,
    "warning": 0.67,
    "blocker": 1.0,
}
_DEFAULT_ROI = _ROI_BY_SEVERITY["info"]

# Cap on the in-process reuse table so a long-lived process cannot grow it
# without bound. When exceeded, the oldest half of the entries are evicted
# (insertion-ordered). Single-operator scale never approaches this.
_REUSE_TABLE_MAX = 20_000

# Cap on the number of distinct firings tracked for delta at once, evicted the
# same way. A firing's set is normally cleared explicitly when it finishes.
_DELTA_TABLE_MAX = 2_000


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def rank_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether the weighted rank + decay + reinforce scoring is active.

    OFF by default: recalled lessons keep their existing recall order and no
    reuse state is accumulated.
    """
    return _truthy((env or os.environ).get(_RANK_ENABLED_ENV))


def delta_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether per-firing delta injection (no re-injecting a lesson) is active.

    OFF by default: every turn injects the full ranked set regardless of what an
    earlier turn of the same firing already showed.
    """
    return _truthy((env or os.environ).get(_DELTA_ENABLED_ENV))


def _env_float(env: Mapping[str, str], key: str, default: float, *, minimum: float) -> float:
    raw = env.get(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value) or value < minimum:
        return default
    return value


def decay_half_life_days(env: Mapping[str, str] | None = None) -> float:
    """Age (in days) at which a lesson's recency weight halves.

    Config-driven via ``ALFRED_MEMORY_DECAY_HALFLIFE_DAYS`` (a positive float).
    A non-positive or unparseable value falls back to the default rather than
    disabling decay.
    """
    # Half-life must be strictly positive; a zero would divide by zero in the
    # decay curve, so the minimum is a tiny epsilon above zero.
    return _env_float(env or os.environ, _HALFLIFE_ENV, _DEFAULT_HALFLIFE_DAYS, minimum=1e-9)


@dataclass(frozen=True)
class RankWeights:
    """The four weights of the rank formula. All non-negative."""

    relevance: float = _DEFAULT_W_RELEVANCE
    roi: float = _DEFAULT_W_ROI
    recency: float = _DEFAULT_W_RECENCY
    reuse: float = _DEFAULT_W_REUSE


def rank_weights(env: Mapping[str, str] | None = None) -> RankWeights:
    """Load the rank weights from env, each clamped to ``>= 0``."""
    envmap = env or os.environ
    return RankWeights(
        relevance=_env_float(envmap, _W_RELEVANCE_ENV, _DEFAULT_W_RELEVANCE, minimum=0.0),
        roi=_env_float(envmap, _W_ROI_ENV, _DEFAULT_W_ROI, minimum=0.0),
        recency=_env_float(envmap, _W_RECENCY_ENV, _DEFAULT_W_RECENCY, minimum=0.0),
        reuse=_env_float(envmap, _W_REUSE_ENV, _DEFAULT_W_REUSE, minimum=0.0),
    )


# --------------------------------------------------------------------------
# Signal helpers (each maps to [0, 1], each independently unit-testable)
# --------------------------------------------------------------------------


def relevance_weight(score: float | None) -> float:
    """Clamp an AMS similarity to ``[0, 1]``; ``None`` -> neutral midpoint."""
    if score is None:
        return _NEUTRAL_RELEVANCE
    try:
        value = float(score)
    except (TypeError, ValueError):
        return _NEUTRAL_RELEVANCE
    if not math.isfinite(value):
        return _NEUTRAL_RELEVANCE
    return max(0.0, min(1.0, value))


def severity_roi(severity: Any) -> float:
    """Map a lesson severity to its validity/ROI weight in ``[0, 1]``."""
    key = str(severity or "info").strip().lower()
    return _ROI_BY_SEVERITY.get(key, _DEFAULT_ROI)


def recency_weight(age_days: float, half_life_days: float) -> float:
    """Age-decay curve: ``0.5 ** (age / half_life)`` in ``(0, 1]``.

    A fresh lesson (age 0) weighs ``1.0``; a lesson one half-life old weighs
    ``0.5``; older lessons fade toward zero but never reach it. A negative age
    (clock skew) is treated as fresh.
    """
    if half_life_days <= 0:
        return 1.0
    age = max(0.0, age_days)
    return 0.5 ** (age / half_life_days)


def reuse_weight(reuse_count: int) -> float:
    """Reinforce-on-reuse bonus in ``[0, 1)``, saturating.

    ``0`` uses -> ``0.0``; each further injection halves the remaining gap to
    ``1.0`` (1 use -> ``0.5``, 2 -> ``0.75``, ...). This rewards a lesson that
    keeps proving useful without ever letting reuse dominate a truly irrelevant
    lesson (its weight is bounded and the ``reuse`` rank weight is the smallest).
    """
    count = max(0, int(reuse_count))
    return 1.0 - 0.5**count


def _lesson_age_days(lesson: Any, now: datetime) -> float:
    """Best-effort age in days from ``lesson.created_at``.

    A missing or unparseable timestamp yields ``0.0`` (treated as fresh) so a
    store without a usable ``created_at`` never has its lessons decayed away.
    """
    created = getattr(lesson, "created_at", None)
    if not isinstance(created, datetime):
        return 0.0
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    delta = now - created
    return delta.total_seconds() / 86400.0


@dataclass(frozen=True)
class RankScore:
    """A lesson's total rank score plus its four component signals.

    Kept so callers (and logs) can explain exactly why one lesson outranked
    another: ``total`` is the weighted sum of the four ``[0, 1]`` signals.
    """

    total: float
    relevance: float
    roi: float
    recency: float
    reuse: float


def score_lesson(
    lesson: Any,
    score: float | None,
    *,
    weights: RankWeights,
    half_life_days: float,
    reuse_count: int,
    now: datetime,
) -> RankScore:
    """Compute the explainable weighted rank score for one lesson."""
    relevance = relevance_weight(score)
    roi = severity_roi(getattr(lesson, "severity", "info"))
    recency = recency_weight(_lesson_age_days(lesson, now), half_life_days)
    reuse = reuse_weight(reuse_count)
    total = (
        weights.relevance * relevance
        + weights.roi * roi
        + weights.recency * recency
        + weights.reuse * reuse
    )
    return RankScore(
        total=total,
        relevance=relevance,
        roi=roi,
        recency=recency,
        reuse=reuse,
    )


# --------------------------------------------------------------------------
# In-process reinforce (reuse) state
# --------------------------------------------------------------------------

_REUSE_COUNTS: OrderedDict[str, int] = OrderedDict()


def lesson_key(lesson: Any) -> str:
    """Stable identity for reuse/delta tracking: the lesson id, else its body."""
    lesson_id = getattr(lesson, "id", None)
    if lesson_id:
        return f"id:{lesson_id}"
    body = " ".join(str(getattr(lesson, "body", "") or "").split()).strip().casefold()
    return f"body:{body}"


def reuse_count(lesson: Any) -> int:
    """Times this lesson has been injected so far this process (0 if never)."""
    return _REUSE_COUNTS.get(lesson_key(lesson), 0)


def record_reuse(lessons: Iterable[Any]) -> None:
    """Reinforce: increment the reuse counter for each injected lesson."""
    for lesson in lessons:
        key = lesson_key(lesson)
        _REUSE_COUNTS[key] = _REUSE_COUNTS.get(key, 0) + 1
        _REUSE_COUNTS.move_to_end(key)
    _evict_if_needed(_REUSE_COUNTS, _REUSE_TABLE_MAX)


def reset_reuse_state() -> None:
    """Clear all reuse counters (test hook)."""
    _REUSE_COUNTS.clear()


# --------------------------------------------------------------------------
# In-process per-firing delta state
# --------------------------------------------------------------------------

_INJECTED_BY_FIRING: OrderedDict[str, set[str]] = OrderedDict()


def already_injected(firing_id: str, lesson: Any) -> bool:
    """Whether ``lesson`` was already injected earlier in ``firing_id``."""
    seen = _INJECTED_BY_FIRING.get(firing_id)
    return bool(seen and lesson_key(lesson) in seen)


def record_injected(firing_id: str, lessons: Iterable[Any]) -> None:
    """Remember the lessons injected on this turn of ``firing_id`` for delta."""
    seen = _INJECTED_BY_FIRING.setdefault(firing_id, set())
    for lesson in lessons:
        seen.add(lesson_key(lesson))
    _INJECTED_BY_FIRING.move_to_end(firing_id)
    _evict_if_needed(_INJECTED_BY_FIRING, _DELTA_TABLE_MAX)


def clear_firing(firing_id: str) -> None:
    """Drop a finished firing's delta set (call when a firing completes)."""
    _INJECTED_BY_FIRING.pop(firing_id, None)


def reset_delta_state() -> None:
    """Clear all per-firing delta sets (test hook)."""
    _INJECTED_BY_FIRING.clear()


def _evict_if_needed(table: OrderedDict[str, Any], maximum: int) -> None:
    """Bound an insertion-ordered table by evicting its oldest half."""
    if len(table) <= maximum:
        return
    drop = len(table) - maximum // 2
    for _ in range(drop):
        table.popitem(last=False)


# --------------------------------------------------------------------------
# Public ranking + delta entry points used by memory_runtime
# --------------------------------------------------------------------------


def apply_delta(
    pairs: list[tuple[Any, float | None]],
    firing_id: str | None,
    *,
    env: Mapping[str, str] | None = None,
) -> list[tuple[Any, float | None]]:
    """Drop lessons already injected earlier in ``firing_id`` (when delta is on).

    Returns ``pairs`` unchanged when delta is disabled or no ``firing_id`` is
    known, so the default path is untouched.
    """
    if not firing_id or not delta_enabled(env):
        return pairs
    return [pair for pair in pairs if not already_injected(firing_id, pair[0])]


def rank_pairs(
    pairs: list[tuple[Any, float | None]],
    *,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> list[tuple[Any, float | None]]:
    """Order ``pairs`` by the weighted rank score (highest first).

    Returns ``pairs`` unchanged when ranking is disabled. The sort is stable on
    a descending total score, so ties keep their incoming recall order and the
    ordering is fully deterministic.
    """
    if not rank_enabled(env):
        return pairs
    weights = rank_weights(env)
    half_life = decay_half_life_days(env)
    moment = now or datetime.now(UTC)

    def total(pair: tuple[Any, float | None]) -> float:
        lesson, score = pair
        return score_lesson(
            lesson,
            score,
            weights=weights,
            half_life_days=half_life,
            reuse_count=reuse_count(lesson),
            now=moment,
        ).total

    # Stable descending sort: ties preserve recall order (deterministic).
    return sorted(pairs, key=total, reverse=True)
