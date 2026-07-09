"""Lesson taxonomy: typed kinds, anchor relations, and validity helpers.

Phase 2 gives a lesson STRUCTURE on top of the flat body/tags/severity row
Phase 1 shipped. Three orthogonal axes are modelled here, and nowhere else, so
both the embedded SQLite hybrid store and the FleetBrain ledger agree on one
vocabulary:

* **kind** -- what class of thing a lesson is (``convention`` | ``fix`` |
  ``failure`` | ``decision`` | ``review-pattern``). Recall can then prefer the
  kinds that matter for the work at hand (conventions and fixes when editing a
  file) instead of treating every lesson the same.
* **anchor relation** -- how a lesson is linked to a code entity or to another
  lesson (``about`` a file/symbol/node, ``supersedes`` an older lesson,
  ``related`` to a sibling, ``contradicts`` a stale one).
* **validity** -- a superseded or expired lesson is invalidated, never deleted,
  so recall stops surfacing it while the audit row survives.

This module is stdlib-only and imports nothing from the memory layer, so it is
the LOWEST layer: ``fleet_brain`` and ``memory.sqlite_hybrid`` both import it
without a cycle. Every normalizer is total (never raises) and collapses unknown
input to a safe default, so a malformed ``kind`` from an engine reflection or an
old row can never break a write or a read.
"""

from __future__ import annotations

from typing import Final

# The canonical typed-lesson taxonomy. ``note`` is the neutral fallback bucket:
# it is NOT one of the five differentiating kinds, it is where an untyped legacy
# lesson or an unrecognized kind lands, so back-compat never mislabels an old
# lesson as, say, a "convention" it was never asserted to be.
LESSON_KINDS: Final[tuple[str, ...]] = (
    "convention",
    "fix",
    "failure",
    "decision",
    "review-pattern",
    "note",
)

# The default kind for a lesson written without an explicit kind, and the value
# an existing untyped row reads back as. A backfill migration stamps this on
# pre-Phase-2 rows.
DEFAULT_LESSON_KIND: Final[str] = "note"

# Aliases an engine reflection or an operator might type, folded to a canonical
# kind. Kept small and obvious; anything unrecognized falls through to the
# default, never an error.
_KIND_ALIASES: Final[dict[str, str]] = {
    "conventions": "convention",
    "style": "convention",
    "pattern": "convention",
    "bugfix": "fix",
    "bug-fix": "fix",
    "fixes": "fix",
    "repair": "fix",
    "failures": "failure",
    "regression": "failure",
    "incident": "failure",
    "gotcha": "failure",
    "decisions": "decision",
    "adr": "decision",
    "choice": "decision",
    "review": "review-pattern",
    "review_pattern": "review-pattern",
    "reviewpattern": "review-pattern",
    "code-review": "review-pattern",
}

# Anchor relations: how a ``lesson_anchors`` row links a lesson to its target.
ANCHOR_RELATIONS: Final[tuple[str, ...]] = (
    "about",  # the lesson is about this code entity (file / symbol / node)
    "supersedes",  # the lesson replaces the target lesson
    "related",  # the lesson is related to the target lesson
    "contradicts",  # the lesson contradicts the target lesson
)
DEFAULT_ANCHOR_RELATION: Final[str] = "about"

# Anchor target types.
ANCHOR_TYPES: Final[tuple[str, ...]] = (
    "file",  # anchor_ref is ``<repo>/<path>`` or a bare path
    "symbol",  # anchor_ref is a symbol name (``module.func``)
    "node",  # anchor_ref is a graph node id (``file:...``/``pr:...``)
    "lesson",  # anchor_ref is another lesson id (a lesson-to-lesson link)
)
DEFAULT_ANCHOR_TYPE: Final[str] = "file"

# Type-aware recall preference. When typed recall is armed, a lesson's kind adds
# this bonus to its rank so the kinds that matter for editing code (conventions
# first, then fixes and the mistakes to avoid) surface ahead of passive notes.
# A plain, explainable point map -- never an opaque model.
KIND_RECALL_PREFERENCE: Final[dict[str, float]] = {
    "convention": 1.0,
    "review-pattern": 0.8,
    "fix": 0.7,
    "failure": 0.6,
    "decision": 0.4,
    "note": 0.0,
}


def normalize_kind(value: str | None) -> str:
    """Fold ``value`` to a canonical lesson kind, defaulting on anything unknown.

    Total and case-insensitive: a blank, ``None``, alias, or unrecognized kind
    all resolve to :data:`DEFAULT_LESSON_KIND` so a malformed engine reflection
    or a legacy row can never raise here.
    """
    text = (value or "").strip().lower()
    if not text:
        return DEFAULT_LESSON_KIND
    if text in LESSON_KINDS:
        return text
    return _KIND_ALIASES.get(text, DEFAULT_LESSON_KIND)


def normalize_anchor_relation(value: str | None) -> str:
    """Fold ``value`` to a canonical anchor relation (default ``about``)."""
    text = (value or "").strip().lower()
    if text in ANCHOR_RELATIONS:
        return text
    return DEFAULT_ANCHOR_RELATION


def normalize_anchor_type(value: str | None) -> str:
    """Fold ``value`` to a canonical anchor target type (default ``file``)."""
    text = (value or "").strip().lower()
    if text in ANCHOR_TYPES:
        return text
    return DEFAULT_ANCHOR_TYPE


def kind_recall_bonus(kind: str | None) -> float:
    """Return the type-aware recall bonus for ``kind`` (0.0 for unknown/note)."""
    return KIND_RECALL_PREFERENCE.get(normalize_kind(kind), 0.0)
