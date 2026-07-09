"""Concrete :class:`MemoryProvider` implementations.

Three providers ship in-tree:

* :class:`FleetBrainProvider` -- wraps the in-tree :mod:`fleet_brain`
  so the rest of Alfred only depends on the Protocol.
* :class:`ChainedMemoryProvider` -- consults a list of providers in
  order; the first non-empty ``recall`` wins. ``reflect`` writes to
  the first provider that does not raise :class:`NotImplementedError`.
* :class:`NullMemoryProvider` -- no-op fallback. Returned by
  :func:`alfred.memory.config.load_provider` when no provider is
  configured. Lets a runner depend on a non-optional
  :class:`MemoryProvider` field without branching on ``None``.

New providers go in their own module (e.g. ``gbrain_stub.py``) and
register themselves in :mod:`alfred.memory.config` via the provider
registry, never by editing this file -- Open-Closed.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from fleet_brain import FleetBrain, Lesson, Severity

if TYPE_CHECKING:
    from . import MemoryProvider

_LOG = logging.getLogger(__name__)


def _recall_accepts_anchor_refs(provider: object) -> bool:
    """Whether ``provider.recall`` declares the Phase 2 ``anchor_refs`` kwarg.

    Lets :class:`ChainedMemoryProvider` thread ``anchor_refs`` only to members
    that accept it, so a provider written against the pre-Phase-2 protocol (or a
    test double) is never handed an unexpected keyword. A ``**kwargs`` recall is
    treated as accepting it; any introspection failure conservatively returns
    ``False`` (the member simply does not get anchor-grounded recall).
    """
    recall = getattr(provider, "recall", None)
    if recall is None:
        return False
    try:
        params = inspect.signature(recall).parameters
    except (TypeError, ValueError):
        return False
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return True
    return "anchor_refs" in params


@dataclass
class FleetBrainProvider:
    """Adapter that exposes :class:`FleetBrain` as a
    :class:`MemoryProvider`.

    Owns the underlying brain instance. Constructed by
    :func:`alfred.memory.config.load_provider` with the operator's
    configured SQLite path; tests inject an in-memory brain via
    ``brain=``.
    """

    brain: FleetBrain = field(default_factory=FleetBrain)
    name: str = "fleet"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> FleetBrainProvider:
        """Build the local operational ledger from the same env map as config."""
        return cls(brain=FleetBrain.from_env(env))

    def recall(
        self,
        *,
        query: str | None = None,
        codename: str | None = None,
        repo: str | None = None,
        limit: int = 5,
        anchor_refs: Iterable[str] | None = None,
    ) -> list[Lesson]:
        base = self.brain.recall(
            codename=codename,
            repo=repo,
            query=query,
            limit=limit,
        )
        refs = [r.strip() for r in (anchor_refs or []) if r and r.strip()]
        if not refs:
            return base
        # Phase 2 code-grounding: surface lessons anchored to the edited files
        # first, then fill with the normal recall order, deduped, capped.
        ordered: list[Lesson] = []
        seen: set[str] = set()
        for ref in refs:
            for lesson in self.brain.lessons_for_anchor(anchor_ref=ref, repo=repo, limit=limit):
                if lesson.id in seen:
                    continue
                seen.add(lesson.id)
                ordered.append(lesson)
        for lesson in base:
            if lesson.id in seen:
                continue
            seen.add(lesson.id)
            ordered.append(lesson)
        return ordered[: max(1, int(limit))]

    def reflect(
        self,
        *,
        codename: str,
        repo: str,
        body: str,
        tags: Iterable[str] | None = None,
        severity: Severity = "info",
        firing_id: str | None = None,
        created_at: datetime | None = None,
        memory_id: str | None = None,
        kind: str | None = None,
        provenance: str | None = None,
    ) -> Lesson:
        # ``memory_id`` lets the promote path address FleetBrain's own lessons
        # table as a lesson store (a deterministic id makes a re-promote
        # idempotent and lets forget_lesson remove exactly what was written).
        # The recall chain calls reflect without it, so a new id is generated
        # then, preserving the prior behavior. ``kind``/``provenance`` are the
        # Phase 2 typed + provenance fields, threaded through when the promote
        # path supplies them.
        return self.brain.reflect(
            codename=codename,
            repo=repo,
            body=body,
            tags=tags,
            severity=severity,
            firing_id=firing_id,
            created_at=created_at,
            lesson_id=memory_id,
            kind=kind,
            provenance=provenance,
        )

    def forget_lesson(self, lesson_id: str) -> bool:
        """Remove one lesson from FleetBrain's lessons table by id.

        Lets FleetBrain serve as the promoted-lesson store for a fleet-only
        chain: the revert / retire / decay levers forget the promoted lesson
        from the SAME table recall reads. A blank id is a no-op ``False`` so a
        caller that gates a destructive follow-up on a ``True`` return is safe.
        """
        clean = (lesson_id or "").strip()
        if not clean:
            return False
        return self.brain.forget(clean)

    def merge_lesson(self, loser_id: str, survivor_id: str) -> bool:
        """Union-merge two lessons in FleetBrain's own lessons table.

        Exposes :meth:`FleetBrain.merge_lesson` under the same name the
        consolidation member-walker looks for, so a fleet-only recall chain gets
        the provenance + anchor + reuse UNION merge (invalidate-not-delete) rather
        than a forget that would orphan the loser's persisted reuse count. Mirrors
        the SQLite hybrid provider's ``merge_lesson`` contract.
        """
        return self.brain.merge_lesson(loser_id, survivor_id)


@dataclass
class NullMemoryProvider:
    """No-op provider. ``recall`` returns ``[]``; ``reflect`` raises.

    Used when the operator explicitly disables memory. Keeps the runner
    code branch-free.
    """

    name: str = "null"

    def recall(
        self,
        *,
        query: str | None = None,
        codename: str | None = None,
        repo: str | None = None,
        limit: int = 5,
        anchor_refs: Iterable[str] | None = None,
    ) -> list[Lesson]:
        return []

    def reflect(
        self,
        *,
        codename: str,
        repo: str,
        body: str,
        tags: Iterable[str] | None = None,
        severity: Severity = "info",
        firing_id: str | None = None,
        created_at: datetime | None = None,
    ) -> Lesson:
        raise NotImplementedError(
            "NullMemoryProvider is read-only; configure a writable "
            "provider (e.g. fleet) to record lessons."
        )


@dataclass
class ChainedMemoryProvider:
    """Consults a list of providers in order.

    ``recall`` merges results from every readable provider in order. This keeps
    the default ``redis,fleet`` chain honest: Redis provides semantic recall,
    while freshly reviewed FleetBrain lessons still appear in prompts before a
    separate Redis sync has run.

    ``reflect`` writes to the first provider that does not raise
    :class:`NotImplementedError`. Read-only providers later in the
    chain are skipped silently.

    Construction is explicit: callers pass the ordered list. The
    config layer is responsible for parsing env into that list.
    """

    providers: list[MemoryProvider]
    name: str = "chained"

    def __post_init__(self) -> None:
        if not self.providers:
            raise ValueError(
                "ChainedMemoryProvider needs at least one provider; "
                "use NullMemoryProvider for a no-op default."
            )

    def recall(
        self,
        *,
        query: str | None = None,
        codename: str | None = None,
        repo: str | None = None,
        limit: int = 5,
        anchor_refs: Iterable[str] | None = None,
    ) -> list[Lesson]:
        provider_lessons: list[list[Lesson]] = []
        seen: set[str] = set()
        for provider in self.providers:
            # Only thread ``anchor_refs`` when the caller supplied it AND this
            # member's recall accepts it. Passing it unconditionally would break
            # a provider (or test double) written against the pre-Phase-2
            # protocol; with no anchor refs the call is byte-identical to before.
            use_anchors = anchor_refs is not None and _recall_accepts_anchor_refs(provider)
            try:
                if use_anchors:
                    lessons = provider.recall(
                        query=query,
                        codename=codename,
                        repo=repo,
                        limit=limit,
                        anchor_refs=anchor_refs,
                    )
                else:
                    lessons = provider.recall(
                        query=query,
                        codename=codename,
                        repo=repo,
                        limit=limit,
                    )
            except Exception:
                # One flaky backend must not break the chain. Log and
                # try the next provider; the firing still gets context.
                _LOG.exception(
                    "memory.chained: provider %r recall raised; falling through",
                    provider.name,
                )
                continue
            bucket: list[Lesson] = []
            for lesson in lessons:
                key = lesson.id or f"{lesson.codename}:{lesson.repo}:{lesson.body}"
                if key in seen:
                    continue
                seen.add(key)
                bucket.append(lesson)
            if lessons:
                _LOG.debug(
                    "memory.chained: %r returned %d lesson(s)",
                    provider.name,
                    len(lessons),
                )
            if bucket:
                provider_lessons.append(bucket)
        return _round_robin_lessons(provider_lessons, limit=max(1, int(limit)))

    def reflect(
        self,
        *,
        codename: str,
        repo: str,
        body: str,
        tags: Iterable[str] | None = None,
        severity: Severity = "info",
        firing_id: str | None = None,
        created_at: datetime | None = None,
    ) -> Lesson:
        last_error: Exception | None = None
        for provider in self.providers:
            try:
                return provider.reflect(
                    codename=codename,
                    repo=repo,
                    body=body,
                    tags=tags,
                    severity=severity,
                    firing_id=firing_id,
                    created_at=created_at,
                )
            except NotImplementedError as exc:
                last_error = exc
                _LOG.debug(
                    "memory.chained: provider %r is read-only; trying next",
                    provider.name,
                )
                continue
        raise NotImplementedError(
            "ChainedMemoryProvider: no writable provider in chain"
        ) from last_error


def _round_robin_lessons(provider_lessons: list[list[Lesson]], *, limit: int) -> list[Lesson]:
    out: list[Lesson] = []
    indexes = [0 for _ in provider_lessons]
    while len(out) < limit:
        added = False
        for idx, lessons in enumerate(provider_lessons):
            if indexes[idx] >= len(lessons):
                continue
            out.append(lessons[indexes[idx]])
            indexes[idx] += 1
            added = True
            if len(out) >= limit:
                break
        if not added:
            break
    return out
