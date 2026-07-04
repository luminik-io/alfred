"""`alfred skills evolve`: turn promoted lessons into SKILL.md drafts.

Alfred's memory accumulates lessons a firing learned about a codename/repo (see
``lib/memory`` and ``lib/fleet_brain``). Over time, clusters of related lessons
describe a repeatable practice: "on repo X, always regenerate the client after
touching the schema", "auth changes need the object-level check". Those clusters
are the raw material for a *skill*.

This module reads promoted / high-confidence lessons through the existing recall
API (:func:`memory.config.recall_lessons`), clusters them by tag and repo, and
emits ``SKILL.md`` DRAFTS under ``skills/first_party/_proposed/`` for an operator
to review. It NEVER installs a skill and never writes into the live
``skills/first_party/`` set: the substrate rule is "the CLI never auto-installs",
and Alfred's product doctrine is an operator approval gate. A draft is a
proposal, nothing more.

Design:

* The lesson source is injectable (``recall`` callable) so the clustering and
  draft-emission logic is unit-testable against a stub without a live brain.
* Clustering is deterministic (sorted, stable ids) so re-running produces the
  same draft filenames and a diff shows what actually changed.
* ``--since`` filters lessons by ``created_at``; ``--dry-run`` reports the plan
  without writing any file.

Zero new third-party dependencies; stdlib only.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

__all__ = [
    "LessonCluster",
    "ProposedSkill",
    "cluster_lessons",
    "default_proposed_dir",
    "evolve_skills",
    "render_proposed_skill",
]

# A cluster needs at least this many lessons to be worth a skill draft. One
# lesson is an anecdote, not a practice; the floor keeps noise out of the
# proposal set. Deliberately low (2) so the feature is useful on a young brain.
_MIN_CLUSTER_SIZE = 2

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def default_proposed_dir() -> Path:
    """Where drafts land: ``skills/first_party/_proposed/`` under the skills root.

    Resolved via :func:`skill_packs.skills_root`, which already handles all three
    layouts (source checkout, deployed ``$ALFRED_HOME/lib`` runtime, and an
    installed wheel where ``skills/`` is packaged as a sibling of the module).
    A hand-rolled parent-of-parent walk would be wrong in the wheel layout
    (it points above ``site-packages``), so we reuse the canonical resolver.
    """
    import skill_packs

    return skill_packs.skills_root() / "first_party" / "_proposed"


# Length of the stable hash suffix appended to every draft name. 8 hex chars of
# BLAKE2b over the full cluster key is ample to keep distinct clusters apart
# while staying short and readable in a filename.
_NAME_HASH_LEN = 8


def _slugify(text: str, *, fallback: str = "lesson") -> str:
    """Lowercase-hyphen slug suitable for a SKILL.md ``name`` and directory."""
    slug = _SLUG_RE.sub("-", text.strip().lower()).strip("-")
    slug = slug[:48].strip("-")
    return slug or fallback


def _cluster_name(repo: str, tag: str) -> str:
    """Collision-proof draft name: a readable slug plus a stable key hash.

    The readable part (``slugify(repo-tag)``) is truncated to 48 chars, so two
    distinct ``(repo, tag)`` keys whose slugs share the same 48-char prefix would
    otherwise map to the same ``_proposed/<name>`` and clobber each other. We
    append a short BLAKE2b digest of the FULL, untruncated key so distinct
    clusters always get distinct files, while the same key always yields the same
    name (deterministic re-runs, reviewable diffs).
    """
    base = _slugify(f"{repo}-{tag}", fallback=_slugify(tag))
    digest = hashlib.blake2b(f"{repo}\x00{tag}".encode(), digest_size=_NAME_HASH_LEN).hexdigest()[
        :_NAME_HASH_LEN
    ]
    return f"{base}-{digest}"


@dataclass(frozen=True)
class LessonCluster:
    """A group of related lessons proposed as one skill.

    ``key`` is the ``(repo, tag)`` the lessons share. ``lessons`` is the raw
    source (each item is duck-typed: it exposes ``body``, ``tags``, ``repo``,
    ``created_at``). ``name`` is the derived skill slug.
    """

    name: str
    repo: str
    tag: str
    lessons: tuple[object, ...] = field(default_factory=tuple)

    @property
    def size(self) -> int:
        return len(self.lessons)


@dataclass(frozen=True)
class ProposedSkill:
    """One emitted (or would-be-emitted) SKILL.md draft."""

    name: str
    path: Path
    body: str
    cluster: LessonCluster
    written: bool


def _lesson_tags(lesson: object) -> list[str]:
    raw = getattr(lesson, "tags", None) or []
    if isinstance(raw, (list, tuple)):
        return [str(t).strip() for t in raw if str(t).strip()]
    return []


def _lesson_repo(lesson: object) -> str:
    return str(getattr(lesson, "repo", "") or "").strip()


def _lesson_created_at(lesson: object) -> datetime | None:
    """Return the lesson's ``created_at`` as an AWARE UTC datetime, or ``None``.

    Recalled lessons can carry a naive ``created_at`` (no tzinfo). ``--since`` is
    parsed as an aware UTC datetime, and comparing a naive to an aware datetime
    raises ``TypeError``. So a naive value is normalized to UTC here
    (``assume UTC``); an already-aware value is returned unchanged (its instant is
    correct regardless of the named zone). A missing or non-datetime value
    returns ``None`` and is treated as "no timestamp" by callers (never raises).
    """
    value = getattr(lesson, "created_at", None)
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def cluster_lessons(
    lessons: Sequence[object],
    *,
    since: datetime | None = None,
    min_cluster_size: int = _MIN_CLUSTER_SIZE,
) -> list[LessonCluster]:
    """Group lessons into candidate skills by ``(repo, tag)``.

    A lesson with several tags contributes to several clusters (a lesson tagged
    ``["auth", "backend"]`` informs both an auth and a backend proposal). Only
    clusters of at least ``min_cluster_size`` are returned. ``since`` drops
    lessons older than the given instant. Output is sorted (by repo, then tag)
    for deterministic draft filenames.
    """
    buckets: dict[tuple[str, str], list[object]] = defaultdict(list)
    for lesson in lessons:
        created = _lesson_created_at(lesson)
        if since is not None and created is not None and created < since:
            continue
        repo = _lesson_repo(lesson) or "unknown"
        tags = _lesson_tags(lesson) or ["general"]
        for tag in tags:
            buckets[(repo, tag)].append(lesson)

    clusters: list[LessonCluster] = []
    for (repo, tag), items in sorted(buckets.items()):
        if len(items) < min_cluster_size:
            continue
        name = _cluster_name(repo, tag)
        clusters.append(LessonCluster(name=name, repo=repo, tag=tag, lessons=tuple(items)))
    return clusters


def render_proposed_skill(cluster: LessonCluster) -> str:
    """Render a SKILL.md DRAFT body from a cluster.

    The draft carries valid frontmatter (name + a description-as-trigger) and a
    body seeded from the clustered lessons, clearly marked ``status: proposed``
    so it can never be mistaken for a shipped skill. An operator edits it into a
    real skill before it is registered; this is a starting point, not a finished
    artifact.
    """
    trigger = (
        f"Use when working on {cluster.repo} and the task involves {cluster.tag}. "
        f"Proposed from {cluster.size} recalled lessons; review before relying on it."
    )
    lines = [
        "---",
        f"name: {cluster.name}",
        f"description: {trigger}",
        "license: MIT",
        "status: proposed",
        "---",
        "",
        f"# {cluster.tag} on {cluster.repo} (proposed draft)",
        "",
        "> DRAFT proposed by `alfred skills evolve` from clustered memory. Not",
        "> installed. Review, edit, and register in `skills/packs.toml` before use.",
        "",
        "## Lessons this draft is built from",
        "",
    ]
    for lesson in cluster.lessons:
        body = str(getattr(lesson, "body", "") or "").strip().replace("\n", " ")
        if body:
            lines.append(f"- {body}")
    lines.extend(
        [
            "",
            "## When to use",
            "",
            f"When a task touches {cluster.tag} in {cluster.repo}.",
            "",
            "## Procedure",
            "",
            "1. TODO: an operator turns the lessons above into concrete steps.",
            "",
            "## Output",
            "",
            "TODO: state what the agent should produce.",
            "",
        ]
    )
    return "\n".join(lines)


def evolve_skills(
    *,
    recall: Callable[..., Sequence[object]],
    since: datetime | None = None,
    dry_run: bool = False,
    proposed_dir: Path | None = None,
    limit: int = 200,
    min_cluster_size: int = _MIN_CLUSTER_SIZE,
) -> list[ProposedSkill]:
    """Read lessons, cluster them, and emit SKILL.md drafts under ``_proposed/``.

    NEVER installs a skill and never touches the live ``skills/first_party`` set;
    it only writes drafts (or, on ``dry_run``, writes nothing and reports the
    plan). ``recall`` is the injectable lesson source -- in production this is
    :func:`memory.config.recall_lessons`; in tests a stub. Each draft is written
    to ``_proposed/<name>/SKILL.md``.
    """
    target = proposed_dir or default_proposed_dir()
    lessons = list(recall(limit=limit))
    clusters = cluster_lessons(lessons, since=since, min_cluster_size=min_cluster_size)

    proposals: list[ProposedSkill] = []
    for cluster in clusters:
        body = render_proposed_skill(cluster)
        dest = target / cluster.name / "SKILL.md"
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(body, encoding="utf-8")
        proposals.append(
            ProposedSkill(
                name=cluster.name,
                path=dest,
                body=body,
                cluster=cluster,
                written=not dry_run,
            )
        )
    return proposals
