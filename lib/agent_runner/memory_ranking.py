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

The delta (last-injected) counter lives in-process only: it is a within-firing
signal that has no meaning across processes. The reinforce (reuse) counter, by
contrast, is now DURABLE (Phase 3): when a persisted :class:`ReuseStore` is wired
via :func:`set_reuse_store` (the runtime derives one from the configured
provider), reuse survives across firings and process restarts, with the
in-process table kept as a write-through cache. Absent a store (the default in
tests and any store that lacks the reuse table) the counter degrades to the
original in-process-only behaviour, byte-identical to before.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import math
import os
from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from envflags import truthy

_LOG = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Config knobs (env-driven, conservative defaults)
# --------------------------------------------------------------------------

_RANK_ENABLED_ENV = "ALFRED_MEMORY_RANK"
_DELTA_ENABLED_ENV = "ALFRED_MEMORY_DELTA"
_TYPED_RECALL_ENV = "ALFRED_MEMORY_TYPED_RECALL"
_INJECT_OPS_ENV = "ALFRED_MEMORY_INJECT_OPS"
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
# same way (oldest-first). Completed firings are not explicitly cleared in the
# single-call firing path, so this cap is the hard bound that keeps a long-lived
# process (serve/MCP) from leaking delta state. Kept small: delta only needs the
# handful of firings currently in flight.
_DELTA_TABLE_MAX = 512


def rank_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether the weighted rank + decay + reinforce scoring is active.

    OFF by default: recalled lessons keep their existing recall order and no
    reuse state is accumulated.
    """
    return truthy((env or os.environ).get(_RANK_ENABLED_ENV))


def delta_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether per-firing delta injection (no re-injecting a lesson) is active.

    OFF by default: every turn injects the full ranked set regardless of what an
    earlier turn of the same firing already showed.
    """
    return truthy((env or os.environ).get(_DELTA_ENABLED_ENV))


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
# Reinforce (reuse) state: durable store + in-process write-through cache
# --------------------------------------------------------------------------

_REUSE_COUNTS: OrderedDict[str, int] = OrderedDict()


@runtime_checkable
class ReuseStore(Protocol):
    """The durable backend for reinforce-on-reuse counts.

    Both the FleetBrain SQLite store and the SQLite hybrid recall store satisfy
    it. Declared structurally so this policy module never imports a concrete
    store package. ``scope_key`` is the exact key :func:`lesson_key` builds.
    """

    def get_reuse_count(self, scope_key: str) -> int: ...

    def bump_reuse_counts(self, scope_keys: Sequence[str]) -> None: ...


# The process-wide durable reuse backend, or ``None`` for in-process-only
# behaviour (the default). The runtime binds one via ``set_reuse_store`` when the
# configured provider exposes a persisted reuse store; tests leave it unset so
# the legacy in-process path is exercised unchanged.
_REUSE_STORE: ReuseStore | None = None


def set_reuse_store(store: ReuseStore | None) -> None:
    """Bind (or clear) the durable reuse backend the reinforce path writes to."""
    global _REUSE_STORE
    _REUSE_STORE = store


def reuse_store_for(provider: Any) -> ReuseStore | None:
    """Find a persisted :class:`ReuseStore` reachable from a memory provider.

    The recall provider may itself be a reuse store (the SQLite hybrid), wrap a
    :class:`FleetBrain` that is one (``FleetBrainProvider.brain``), or be a chain
    of providers. Returns the first durable store found, or ``None`` when no
    member persists reuse (e.g. a pure Redis chain), in which case reinforce
    stays in-process. Never raises."""
    seen: set[int] = set()

    def _probe(obj: Any) -> ReuseStore | None:
        if obj is None or id(obj) in seen:
            return None
        seen.add(id(obj))
        if isinstance(obj, ReuseStore):
            return obj
        # A provider that wraps a FleetBrain (which is a ReuseStore).
        brain = getattr(obj, "brain", None)
        if isinstance(brain, ReuseStore):
            return brain
        # A chain: probe each member in order.
        for member in getattr(obj, "providers", None) or []:
            found = _probe(member)
            if found is not None:
                return found
        return None

    try:
        return _probe(provider)
    except Exception:
        return None


# Field separator for composite keys. A control char that cannot appear in a
# codename or a ``org/repo`` slug, so no key can be forged by a repo name that
# happens to contain the delimiter.
_KEY_SEP = "\x1f"


def lesson_key(lesson: Any, *, codename: str | None = None, repo: str | None = None) -> str:
    """Scoped, stable identity for reuse/delta tracking.

    The reuse and delta tables are process-global, so the key MUST carry the
    firing's ``codename`` and ``repo`` scope; otherwise two unrelated firings on
    different repos that recall a lesson sharing the same id (or body) would
    collide and cross-contaminate each other's reuse/delta state. ``codename``
    and ``repo`` are taken from the caller when given, else from the lesson's own
    attributes, else empty. The lesson id is preferred over the body as the
    within-scope identity; a scoped body hash is the fallback for stores that do
    not assign ids.
    """
    scope_codename = str(
        codename if codename is not None else getattr(lesson, "codename", "") or ""
    )
    scope_repo = str(repo if repo is not None else getattr(lesson, "repo", "") or "")
    lesson_id = getattr(lesson, "id", None)
    if lesson_id:
        return scope_key(lesson_id=str(lesson_id), codename=scope_codename, repo=scope_repo)
    body = " ".join(str(getattr(lesson, "body", "") or "").split()).strip().casefold()
    return f"{scope_codename}{_KEY_SEP}{scope_repo}{_KEY_SEP}body:{body}"


def scope_key(*, lesson_id: str, codename: str | None, repo: str | None) -> str:
    """The reuse scope key for a lesson known by id + its ``codename``/``repo``.

    The by-id branch of :func:`lesson_key`, exposed so a store that only holds the
    lesson id (the consolidation merge, eviction) builds the EXACT same key the
    reinforce path wrote, guaranteeing a merged/evicted lesson's persisted reuse
    is addressed by the identical key."""
    return f"{codename or '':s}{_KEY_SEP}{repo or '':s}{_KEY_SEP}id:{lesson_id}"


def reuse_count(lesson: Any, *, codename: str | None = None, repo: str | None = None) -> int:
    """Times this lesson has been injected (0 if never), scoped by codename/repo.

    Reads the durable :class:`ReuseStore` when one is bound (so reuse accumulated
    by earlier firings/processes counts), falling back to the in-process cache
    otherwise. A store read failure degrades to the cache rather than raising, so
    a flaky store never breaks ranking.
    """
    key = lesson_key(lesson, codename=codename, repo=repo)
    store = _REUSE_STORE
    if store is not None:
        try:
            return int(store.get_reuse_count(key))
        except Exception:
            # Log a fingerprint, not the key itself: a body-backed key carries
            # the full normalized lesson text, which must not land in logs.
            digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
            _LOG.debug(
                "reuse-count store read failed for key sha256:%s; using in-memory cache",
                digest,
                exc_info=True,
            )
    return _REUSE_COUNTS.get(key, 0)


def record_reuse(
    lessons: Iterable[Any], *, codename: str | None = None, repo: str | None = None
) -> None:
    """Reinforce: increment the (scoped) reuse counter for each injected lesson.

    Bumps the durable :class:`ReuseStore` when one is bound (write-through), and
    always updates the in-process cache so a subsequent same-process read is fast
    and a store outage still reinforces for this run. A store write failure is
    swallowed after the cache is updated, so reinforce never breaks a firing.
    """
    keys: list[str] = []
    for lesson in lessons:
        key = lesson_key(lesson, codename=codename, repo=repo)
        keys.append(key)
        _REUSE_COUNTS[key] = _REUSE_COUNTS.get(key, 0) + 1
        _REUSE_COUNTS.move_to_end(key)
    _evict_if_needed(_REUSE_COUNTS, _REUSE_TABLE_MAX)
    store = _REUSE_STORE
    if store is not None and keys:
        # A store write failure must not break a firing; the in-process cache
        # above already reinforced for this run.
        with contextlib.suppress(Exception):
            store.bump_reuse_counts(keys)


def reset_reuse_state() -> None:
    """Clear the in-process reuse cache and unbind the durable store (test hook).

    Unbinding the store keeps test isolation clean: a test that wired a persisted
    store cannot leak it into a later in-process-only test.
    """
    _REUSE_COUNTS.clear()
    set_reuse_store(None)


# --------------------------------------------------------------------------
# In-process per-firing delta state
# --------------------------------------------------------------------------

_INJECTED_BY_FIRING: OrderedDict[str, set[str]] = OrderedDict()


def already_injected(
    firing_id: str, lesson: Any, *, codename: str | None = None, repo: str | None = None
) -> bool:
    """Whether ``lesson`` was already injected earlier in ``firing_id``."""
    seen = _INJECTED_BY_FIRING.get(firing_id)
    return bool(seen and lesson_key(lesson, codename=codename, repo=repo) in seen)


def record_injected(
    firing_id: str,
    lessons: Iterable[Any],
    *,
    codename: str | None = None,
    repo: str | None = None,
) -> None:
    """Remember the lessons injected on this turn of ``firing_id`` for delta.

    The per-firing table is bounded: when more than ``_DELTA_TABLE_MAX`` distinct
    firings are tracked, the oldest (least-recently-touched) firings are evicted,
    so a long-lived process (serve/MCP) can never accumulate firing state without
    limit even though completed firings are not explicitly cleared.
    """
    seen = _INJECTED_BY_FIRING.setdefault(firing_id, set())
    for lesson in lessons:
        seen.add(lesson_key(lesson, codename=codename, repo=repo))
    _INJECTED_BY_FIRING.move_to_end(firing_id)
    _evict_if_needed(_INJECTED_BY_FIRING, _DELTA_TABLE_MAX)


def clear_firing(firing_id: str) -> None:
    """Drop a finished firing's delta set.

    Called from the runner lifecycle when a firing completes, so a finished
    firing's injected-lesson set is released immediately rather than lingering in
    the process-global table until the size cap evicts it. Capping bounds growth;
    this clears state the moment it is no longer needed. Idempotent, and a no-op
    for an unknown ``firing_id``.

    Only the delta set is cleared. Reuse counters are deliberately left intact:
    reinforce-on-reuse is a cross-firing signal (a lesson that keeps proving
    useful across firings should keep its accumulated weight).
    """
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
    codename: str | None = None,
    repo: str | None = None,
    env: Mapping[str, str] | None = None,
) -> list[tuple[Any, float | None]]:
    """Drop lessons already injected earlier in ``firing_id`` (when delta is on).

    Returns ``pairs`` unchanged when delta is disabled or no ``firing_id`` is
    known, so the default path is untouched. ``codename``/``repo`` scope the key
    so the check is confined to this firing's own repo.
    """
    if not firing_id or not delta_enabled(env):
        return pairs
    return [
        pair
        for pair in pairs
        if not already_injected(firing_id, pair[0], codename=codename, repo=repo)
    ]


def rank_pairs(
    pairs: list[tuple[Any, float | None]],
    *,
    codename: str | None = None,
    repo: str | None = None,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> list[tuple[Any, float | None]]:
    """Order ``pairs`` by the weighted rank score (highest first).

    Returns ``pairs`` unchanged when ranking is disabled. The sort is stable on
    a descending total score, so ties keep their incoming recall order and the
    ordering is fully deterministic. ``codename``/``repo`` scope the reuse lookup
    so a lesson's reinforcement is read from this repo's counter only.
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
            reuse_count=reuse_count(lesson, codename=codename, repo=repo),
            now=moment,
        ).total

    # Stable descending sort: ties preserve recall order (deterministic).
    return sorted(pairs, key=total, reverse=True)


def typed_recall_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether type-aware recall preference is active (``ALFRED_MEMORY_TYPED_RECALL``).

    OFF by default: recalled lessons keep their existing order regardless of
    kind. When armed, :func:`apply_typed_recall` lifts the kinds that matter for
    editing code (conventions first, then review-patterns and fixes, then the
    failures to avoid) ahead of passive notes.
    """
    return truthy((env or os.environ).get(_TYPED_RECALL_ENV))


def apply_typed_recall(
    pairs: list[tuple[Any, float | None]],
    *,
    env: Mapping[str, str] | None = None,
) -> list[tuple[Any, float | None]]:
    """Prefer conventions + fixes by lesson kind. Returns ``pairs`` unchanged when off.

    A stable descending sort on each lesson's kind preference (see
    :data:`fleet_brain.taxonomy.KIND_RECALL_PREFERENCE`), so ties preserve the
    incoming order and the result is fully deterministic. Applied AFTER the
    weighted rank so, when both are armed, the kind preference has the final say
    on which lessons lead while relevance still orders within a kind bucket.
    """
    if not typed_recall_enabled(env) or not pairs:
        return pairs
    from fleet_brain.taxonomy import kind_recall_bonus

    def kind_pref(pair: tuple[Any, float | None]) -> float:
        return kind_recall_bonus(getattr(pair[0], "kind", None))

    return sorted(pairs, key=kind_pref, reverse=True)


def ops_deprioritized(env: Mapping[str, str] | None = None) -> bool:
    """Whether ops (Alfred-runtime) lessons are pushed below codebase lessons.

    ON by default, unlike the other injection knobs. The ops/codebase split
    exists precisely so coding prompts lead with lessons about the underlying
    codebase; leaving fleet-ops noise (provider quota, auth, engine timeouts)
    interleaved would defeat that. Set ``ALFRED_MEMORY_INJECT_OPS`` truthy to
    restore the pre-split behavior where ops lessons keep their recall position.
    """
    return not truthy((env or os.environ).get(_INJECT_OPS_ENV))


def deprioritize_ops(
    pairs: list[tuple[Any, float | None]],
    *,
    env: Mapping[str, str] | None = None,
) -> list[tuple[Any, float | None]]:
    """Stable-sort ops lessons below codebase lessons (a down-weight, not a drop).

    Ops lessons (see :func:`fleet_brain.taxonomy.is_ops_lesson`) are about
    Alfred's own runtime, so within the finite injection budget the codebase
    lessons an engineer actually needs should lead. This is a pure reorder:
    codebase lessons keep their incoming rank order, ops lessons keep theirs and
    are still injected when budget remains, so no lesson is lost and the change
    is fully deterministic. Returns ``pairs`` unchanged when disabled or empty.
    """
    if not pairs or not ops_deprioritized(env):
        return pairs
    from fleet_brain.taxonomy import is_ops_lesson

    # Stable sort on the ops flag: False (codebase, sorts as 0) leads, True (ops,
    # sorts as 1) trails; each bucket preserves its incoming order.
    return sorted(pairs, key=lambda pair: is_ops_lesson(getattr(pair[0], "tags", None)))
