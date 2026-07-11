"""Consolidation, decay, semantic dedup, and provenance-union merge.

The periodic consolidation pass (OFF by default, gated by
``ALFRED_MEMORY_CONSOLIDATE``) decays stale promoted lessons, collapses
duplicate auto-promoted lessons (lexically, and optionally semantically via an
embedder), and evicts the lowest-value lessons past a configured cap. Every
operation is invalidate-not-delete and confirms the recall lesson is actually
gone/superseded before it records the change locally.

The pure helpers here -- cosine similarity, greedy semantic clustering, the
provenance union, and the provider-unwrapping used to find a ``merge_lesson``
capability nested in a chain -- are import-safe and unit-tested directly.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from .base import LedgerBase
from .config import (
    consolidate_enabled,
    consolidate_semantic_enabled,
    consolidate_sim_threshold,
    max_lessons_cap,
)
from .promotion import _lesson_memory_id
from .store import LessonAnchor, MemoryCandidate, new_id

_LOG = logging.getLogger(__name__)

# Embedder contract shared with the SQLite hybrid dense arm: text -> vector, or
# ``None`` when the embedder is unreachable. The semantic merge degrades to
# lexical-only whenever this returns ``None`` for any body (so a down embedder is
# never a hard failure).
Embedder = Callable[[str], "list[float] | None"]


def _aware_utc(value: datetime | None) -> datetime | None:
    """Return ``value`` as a UTC-aware datetime, or None. A naive datetime is
    assumed to already be UTC (the store persists UTC)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _canonical_memory_body(body: str) -> str:
    return " ".join((body or "").strip().lower().split())


def _union_provenance(survivor: str | None, loser: str | None) -> str | None:
    """Comma-join two provenance strings, survivor first, de-duped in order.

    Mirrors the SQLite hybrid store's union so a FleetBrain-backed merge keeps the
    full firing/PR history of both copies. ``None`` when both are empty."""
    out: list[str] = []
    seen: set[str] = set()
    for source in (survivor, loser):
        for part in (source or "").split(","):
            token = part.strip()
            if token and token not in seen:
                seen.add(token)
                out.append(token)
    return ", ".join(out) if out else None


def _provider_members(provider: Any) -> Iterator[Any]:
    """Yield ``provider`` and every nested member, breadth-first, each once.

    A recall provider may be a plain store, a wrapper that holds a
    ``FleetBrain`` (``.brain``), or a ``ChainedMemoryProvider`` (``.providers``)
    that itself nests either. Breadth-first from the top so an outer member is
    visited before the members it wraps, and chain order is preserved. Used to
    reach a capability (e.g. ``merge_lesson``) that lives on a store nested inside
    the wrapper/chain, not on the wrapper itself."""
    seen: set[int] = set()
    queue: list[Any] = [provider]
    while queue:
        obj = queue.pop(0)
        if obj is None or id(obj) in seen:
            continue
        seen.add(id(obj))
        yield obj
        brain = getattr(obj, "brain", None)
        if brain is not None:
            queue.append(brain)
        members = getattr(obj, "providers", None)
        if members:
            queue.extend(members)


def _first_member_with(provider: Any, attr: str) -> Any | None:
    """First member of ``provider`` (unwrapping chain/wrapper) with callable ``attr``.

    Returns ``None`` when no member anywhere in the chain exposes the capability,
    so a caller can fall back cleanly (e.g. a Redis-only chain has no
    ``merge_lesson`` member and falls back to a plain forget)."""
    for member in _provider_members(provider):
        fn = getattr(member, attr, None)
        if callable(fn):
            return member
    return None


def _cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two equal-length vectors in ``[-1, 1]``.

    Returns ``0.0`` for a length mismatch or a zero-magnitude vector, so a
    degenerate embedding never merges two lessons by accident."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=False):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / math.sqrt(na * nb)


def _semantic_dup_groups(
    items: list[tuple[str, str]],
    embedder: Embedder,
    threshold: float,
) -> list[list[str]]:
    """Greedy-cluster ``(id, body)`` pairs into semantic near-duplicate groups.

    ``items`` are already scoped to one ``(repo, codename)`` bucket by the caller,
    so proximity here means "the same lesson said two ways". Each body is embedded
    once; a body is added to the first existing cluster whose seed it is within
    ``threshold`` cosine of, else it seeds a new cluster. Any body the embedder
    cannot embed is left as its own singleton (never merged on missing signal),
    which is how the pass DEGRADES to lexical-only when embeddings are absent.

    Returns only the multi-member groups (a singleton is not a duplicate),
    preserving the input order of ``items`` both across and within groups so the
    caller's "keep the oldest" choice stays deterministic."""
    vectors: list[tuple[str, list[float] | None]] = []
    for lesson_id, body in items:
        try:
            vec = embedder(body)
        except Exception:
            vec = None
        vectors.append((lesson_id, vec if vec else None))

    clusters: list[dict[str, Any]] = []
    for lesson_id, vec in vectors:
        if vec is None:
            # No embedding signal: never fold it into another lesson.
            clusters.append({"seed": None, "ids": [lesson_id]})
            continue
        placed = False
        for cluster in clusters:
            seed = cluster["seed"]
            if seed is None:
                continue
            if _cosine_similarity(vec, seed) >= threshold:
                cluster["ids"].append(lesson_id)
                placed = True
                break
        if not placed:
            clusters.append({"seed": vec, "ids": [lesson_id]})
    return [c["ids"] for c in clusters if len(c["ids"]) > 1]


class ConsolidationMixin(LedgerBase):
    """The consolidation/decay/merge pass, composed into ``FleetBrain``."""

    def merge_lesson(self, loser_id: str, survivor_id: str) -> bool:
        """Merge ``loser_id`` into ``survivor_id`` in FleetBrain's own lessons table.

        The FleetBrain-backed counterpart to the SQLite hybrid's ``merge_lesson``,
        so a fleet-only recall chain gets the SAME union merge instead of a
        forget that would orphan the loser's persisted reuse. It UNIONS the
        loser's provenance, anchors, and durable reuse count onto the survivor,
        then INVALIDATES the loser (``superseded_by``, via ``supersede_lesson``)
        and cleans up its now-orphaned ``lesson_reuse`` row. No-op ``False`` on
        blank/identical ids or a missing survivor/loser lesson."""
        loser = (loser_id or "").strip()
        survivor = (survivor_id or "").strip()
        if not loser or not survivor or loser == survivor:
            return False
        loser_lesson = self.store.get_lesson(loser)
        survivor_lesson = self.store.get_lesson(survivor)
        if loser_lesson is None or survivor_lesson is None:
            return False
        # Union provenance (survivor's history first).
        merged_provenance = _union_provenance(survivor_lesson.provenance, loser_lesson.provenance)
        self.store.set_lesson_provenance(survivor, merged_provenance)
        # Union anchors: copy each of the loser's links onto the survivor.
        for anchor in self.store.list_lesson_anchors(loser):
            self.store.add_lesson_anchor(
                LessonAnchor(
                    id=new_id(),
                    lesson_id=survivor,
                    anchor_type=anchor.anchor_type,
                    anchor_ref=anchor.anchor_ref,
                    relation=anchor.relation,
                    repo=anchor.repo,
                    created_at=datetime.now(UTC),
                )
            )
        # Union durable reuse, then invalidate the loser (which supersede records).
        from agent_runner import memory_ranking

        survivor_key = memory_ranking.scope_key(
            lesson_id=survivor, codename=survivor_lesson.codename, repo=survivor_lesson.repo
        )
        loser_key = memory_ranking.scope_key(
            lesson_id=loser, codename=loser_lesson.codename, repo=loser_lesson.repo
        )
        self.store.union_reuse_counts(survivor_key, loser_key)
        return self.store.supersede_lesson(loser, survivor)

    def consolidate_lessons(
        self,
        *,
        stale_days: int = 180,
        dry_run: bool = False,
        env: Mapping[str, str] | None = None,
        lesson_forgetter: Any | None = None,
        embedder: Embedder | None = None,
    ) -> dict[str, Any]:
        """Periodic consolidation/decay pass over promoted lessons (OFF by default).

        Invalidate-not-delete operations over validated (promoted) candidates
        whose lesson lives in the recall store (Redis AMS or the SQLite hybrid):

          * decay: a promoted candidate older than ``stale_days`` has its recall
            lesson forgotten and its row flipped to ``retired`` so recall stops
            surfacing it, but the audit row is kept (never deleted);
          * merge: auto-promoted (``reviewed_by == "auto"``) promoted candidates
            that are DUPLICATES are collapsed to the oldest. Duplicates are found
            lexically (bodies normalize to the same text) and, when
            ``ALFRED_MEMORY_CONSOLIDATE_SEMANTIC`` is armed AND an ``embedder`` is
            available, ALSO semantically (near-duplicate bodies, cosine >=
            ``ALFRED_MEMORY_CONSOLIDATE_SIM_THRESHOLD``) on top of the lexical
            pass. With no embedder it degrades to lexical-only, byte-identical to
            before. When the recall store supports it (``merge_lesson``), a merge
            UNIONS the loser's provenance and anchors onto the surviving lesson
            and INVALIDATES the loser (``superseded_by``), so no provenance is
            lost; otherwise it falls back to forgetting the loser (Redis AMS);
          * evict: when ``ALFRED_MEMORY_MAX_LESSONS`` is set and the store grows
            past it, the lowest-value lessons (by the #452 value score) are
            invalidated down to the cap (reversible, never deleted).

        A candidate is retired/merged ONLY once its recall lesson is actually
        forgotten or superseded: if the store op fails (a transient outage, or
        forgetting disabled server-side) the row is left ``validated`` and logged,
        so the ledger never claims a decay/merge while the lesson is still live in
        recall. This mirrors the revert lever and keeps the paths honest.

        Gated behind ``ALFRED_MEMORY_CONSOLIDATE`` so it never runs unless armed;
        ``dry_run`` reports counts without writing. ``lesson_forgetter`` is the
        recall provider; tests inject a stub. ``embedder`` is the optional dense
        embedder (the same contract the SQLite hybrid dense arm uses); tests
        inject a deterministic stub. Returns a summary dict (always safe to log)."""
        # A negative stale_days is invalid input, not "0". Clamping it to 0 would
        # set the cutoff to NOW and forget/retire every promoted lesson, so
        # reject it up front (fail fast, before any read or write).
        if int(stale_days) < 0:
            raise ValueError(f"stale_days must be >= 0, got {stale_days}")
        summary: dict[str, Any] = {
            "enabled": consolidate_enabled(env),
            "dry_run": bool(dry_run),
            "decayed": 0,
            "merged": 0,
            "provenance_unioned": 0,
            "evicted": 0,
            "ams_forget_attempted": 0,
            "ams_forgotten": 0,
            "ams_forget_failed": 0,
        }
        if not summary["enabled"]:
            # No-op when disarmed: do not even read the ledger.
            return summary

        # Enumerate every validated (promoted) candidate via offset paging. A
        # promoted candidate carries a promoted_lesson_id; that is the AMS key.
        validated: list[MemoryCandidate] = []
        page = 500
        offset = 0
        while True:
            batch = self.list_memory_candidates(status="validated", limit=page, offset=offset)
            validated.extend(cand for cand in batch if cand.promoted_lesson_id is not None)
            if len(batch) < page:
                break
            offset += page

        cutoff = datetime.now(UTC) - timedelta(days=int(stale_days))
        stale: list[MemoryCandidate] = []
        fresh_auto: list[MemoryCandidate] = []
        for cand in validated:
            # Age from PROMOTION time (when the lesson entered AMS recall), not
            # the original proposal time: a candidate that sat in review past
            # stale_days and was promoted today has a FRESH active lesson and
            # must not be forgotten on the next pass. reviewed_at is the
            # promotion timestamp for a promoted candidate; fall back to
            # created_at only if it is somehow missing.
            promoted_at = _aware_utc(cand.reviewed_at) or _aware_utc(cand.created_at)
            if promoted_at is not None and promoted_at < cutoff:
                stale.append(cand)
            elif cand.reviewed_by == "auto":
                # Only auto-promoted lessons are merge-eligible; human-reviewed
                # lessons are left alone (a human deliberately kept both).
                fresh_auto.append(cand)

        # Merge pairs: among still-fresh auto-promoted candidates, keep the OLDEST
        # of each duplicate set and pair every loser with that survivor. Scope by
        # repo + codename because recall is topic-scoped: two identical-body
        # lessons for DIFFERENT repos/codenames are not redundant (each answers a
        # different recall scope), so they must not collapse into one. (Stale rows
        # already decay above and are excluded here to avoid double-counting.)
        merge_pairs = self._merge_pairs(
            fresh_auto,
            env=env,
            embedder=embedder,
        )

        decay_reason = f"consolidate: decayed (stale > {int(stale_days)}d)"
        decayed = self._retire_consolidated(
            stale,
            reason=decay_reason,
            dry_run=dry_run,
            lesson_forgetter=lesson_forgetter,
            summary=summary,
            env=env,
        )
        merged = self._merge_consolidated(
            merge_pairs,
            dry_run=dry_run,
            lesson_forgetter=lesson_forgetter,
            summary=summary,
            env=env,
        )
        summary["decayed"] = decayed
        summary["merged"] = merged

        # Pressure/budget eviction (Phase 3): when the store has grown past the
        # configured cap, invalidate the lowest-value lessons down to it. Purely
        # store-level and reversible; a store without the capability is skipped.
        summary["evicted"] = self._evict_to_cap(
            dry_run=dry_run,
            lesson_forgetter=lesson_forgetter,
            summary=summary,
            env=env,
        )
        return summary

    def _retire_consolidated(
        self,
        candidates: list[MemoryCandidate],
        *,
        reason: str,
        dry_run: bool,
        lesson_forgetter: Any | None,
        summary: dict[str, Any],
        env: Mapping[str, str] | None = None,
    ) -> int:
        """Forget each candidate's AMS lesson then retire the row.

        Retires ONLY once the AMS lesson is forgotten, so the ledger never marks
        a lesson consolidated while it is still live in AMS recall. In dry-run
        mode nothing is forgotten or written; the count is what WOULD change.
        Shared by the decay and merge passes; increments the ams_forget counters
        on ``summary`` in place. Returns the number retired (or, in dry-run, the
        number that would be).

        ``env`` is the SAME merged env ``consolidate_lessons`` gated on (the
        persisted ``$ALFRED_HOME/.env`` in the scheduled case). It is threaded
        into the AMS forgetter so the destructive forget uses the operator's
        configured AMS URL/namespace/token from ``.env`` instead of falling back
        to ``os.environ`` defaults and forgetting from the wrong server."""
        if dry_run:
            return len(candidates)
        if not candidates:
            return 0
        forgetter = lesson_forgetter
        if forgetter is None:
            try:
                forgetter = self._lesson_provider(env)
            except Exception:
                summary["ams_forget_failed"] += len(candidates)
                _LOG.exception("consolidate_lessons: could not build AMS lesson forgetter")
                return 0
        if forgetter is None:
            # Memory disabled (ALFRED_MEMORY_PROVIDERS=null): no store to forget
            # from, so decay/merge cannot run. Controlled no-op rather than
            # crashing on a None forgetter.
            _LOG.debug("consolidate_lessons: runtime memory disabled; skipping forget/retire")
            return 0
        retired = 0
        for candidate in candidates:
            lesson_id = candidate.promoted_lesson_id or _lesson_memory_id(candidate.id)
            summary["ams_forget_attempted"] += 1
            forgotten = False
            try:
                forgotten = bool(forgetter.forget_lesson(lesson_id))
            except Exception:
                _LOG.exception(
                    "consolidate_lessons: AMS forget failed for candidate %s",
                    candidate.id,
                )
            if not forgotten:
                summary["ams_forget_failed"] += 1
                continue
            summary["ams_forgotten"] += 1
            self.store.update_memory_candidate(
                replace(
                    candidate,
                    status="retired",
                    reviewed_at=datetime.now(UTC),
                    reviewed_by="consolidate",
                    review_note=reason,
                    promoted_lesson_id=None,
                )
            )
            retired += 1
        return retired

    def _merge_pairs(
        self,
        fresh_auto: list[MemoryCandidate],
        *,
        env: Mapping[str, str] | None,
        embedder: Embedder | None,
    ) -> list[tuple[MemoryCandidate, str]]:
        """Pair each duplicate loser with the surviving (oldest) lesson id.

        Lexical duplicates (bodies that normalize to the same text) collapse
        first; the survivor of each lexical set then feeds an OPTIONAL semantic
        pass that also collapses near-duplicate survivors when the semantic switch
        is armed AND an embedder is available. Both passes keep the OLDEST as the
        survivor, so the same lesson is never re-merged. Returns ``(loser,
        survivor_lesson_id)`` pairs; the survivor id is the recall-store key the
        loser's provenance/anchors are unioned onto."""
        semantic = bool(embedder) and consolidate_semantic_enabled(env)
        threshold = consolidate_sim_threshold(env)
        # Scope by (repo, codename): duplicates only collapse within one recall
        # scope (see the caller's note).
        by_scope: dict[tuple[str, str], list[MemoryCandidate]] = {}
        for cand in fresh_auto:
            by_scope.setdefault((cand.repo, cand.codename), []).append(cand)

        pairs: list[tuple[MemoryCandidate, str]] = []
        for scope_cands in by_scope.values():
            # Lexical pass: keep the oldest per normalized body.
            by_body: dict[str, list[MemoryCandidate]] = {}
            for cand in scope_cands:
                by_body.setdefault(_canonical_memory_body(cand.body), []).append(cand)
            survivors: list[MemoryCandidate] = []
            for group in by_body.values():
                ordered = sorted(group, key=lambda c: (c.created_at, c.id))
                survivor = ordered[0]
                survivors.append(survivor)
                survivor_id = str(survivor.promoted_lesson_id)
                for loser in ordered[1:]:
                    pairs.append((loser, survivor_id))
            if not semantic or len(survivors) < 2:
                continue
            # Semantic pass over the lexical survivors: near-duplicate bodies that
            # are not lexically identical still collapse to the oldest. ``embedder``
            # is truthy here (guarded by ``semantic``).
            assert embedder is not None
            by_id = {s.id: s for s in survivors}
            groups = _semantic_dup_groups([(s.id, s.body) for s in survivors], embedder, threshold)
            for id_group in groups:
                members = sorted((by_id[i] for i in id_group), key=lambda c: (c.created_at, c.id))
                survivor = members[0]
                survivor_id = str(survivor.promoted_lesson_id)
                for loser in members[1:]:
                    pairs.append((loser, survivor_id))
        return pairs

    def _merge_consolidated(
        self,
        pairs: list[tuple[MemoryCandidate, str]],
        *,
        dry_run: bool,
        lesson_forgetter: Any | None,
        summary: dict[str, Any],
        env: Mapping[str, str] | None = None,
    ) -> int:
        """Merge each duplicate loser into its survivor, then retire the row.

        When a member of the recall store exposes ``merge_lesson`` (the SQLite
        hybrid does), the loser's provenance, anchors, and durable reuse are
        UNIONED onto the survivor and the loser is INVALIDATED (``superseded_by``)
        rather than deleted, so the surviving lesson keeps the full history and
        nothing is lost. The capability is found by UNWRAPPING the provider: a
        union-capable store nested inside a ``ChainedMemoryProvider`` (e.g. the
        default ``sqlite,fleet`` chain) is still used for the merge. Only when NO
        member anywhere in the chain supports it (e.g. a Redis-only chain) does it
        fall back to forgetting the loser, exactly as the pre-Phase-3 merge did.
        A row is retired ONLY once the store op confirms, mirroring the decay
        path. Dry-run reports the would-merge count without writing."""
        merge_reason = "consolidate: merged (duplicate of an older lesson)"
        if dry_run:
            return len(pairs)
        if not pairs:
            return 0
        forgetter = lesson_forgetter
        if forgetter is None:
            try:
                forgetter = self._lesson_provider(env)
            except Exception:
                summary["ams_forget_failed"] += len(pairs)
                _LOG.exception("consolidate_lessons: could not build recall provider for merge")
                return 0
        if forgetter is None:
            _LOG.debug("consolidate_lessons: runtime memory disabled; skipping merge")
            return 0
        # Unwrap the chain/wrapper: the union-capable store (and the forget target)
        # may be nested inside a ChainedMemoryProvider, not the top-level object.
        merger = _first_member_with(forgetter, "merge_lesson")
        forget_target = _first_member_with(forgetter, "forget_lesson")
        merged = 0
        for loser, survivor_lesson_id in pairs:
            loser_lesson_id = loser.promoted_lesson_id or _lesson_memory_id(loser.id)
            summary["ams_forget_attempted"] += 1
            ok = False
            try:
                if merger is not None and survivor_lesson_id:
                    ok = bool(merger.merge_lesson(loser_lesson_id, survivor_lesson_id))
                    if ok:
                        summary["provenance_unioned"] += 1
                elif forget_target is not None:
                    ok = bool(forget_target.forget_lesson(loser_lesson_id))
            except Exception:
                _LOG.exception(
                    "consolidate_lessons: recall merge failed for candidate %s",
                    loser.id,
                )
            if not ok:
                summary["ams_forget_failed"] += 1
                continue
            summary["ams_forgotten"] += 1
            self.store.update_memory_candidate(
                replace(
                    loser,
                    status="retired",
                    reviewed_at=datetime.now(UTC),
                    reviewed_by="consolidate",
                    review_note=merge_reason,
                    promoted_lesson_id=None,
                )
            )
            merged += 1
        return merged

    def _evict_to_cap(
        self,
        *,
        dry_run: bool,
        lesson_forgetter: Any | None,
        summary: dict[str, Any],
        env: Mapping[str, str] | None = None,
    ) -> int:
        """Invalidate the lowest-value lessons down to ``ALFRED_MEMORY_MAX_LESSONS``.

        Delegates to the recall store's ``evict_to_cap`` (the SQLite hybrid ranks
        by the #452 value score and expires the lowest, invalidate-not-delete). A
        store without that capability (Redis AMS) is skipped. Disabled when the
        cap is unset or non-positive. Returns the number evicted (or, in dry-run,
        the number that would be)."""
        cap = max_lessons_cap(env)
        if cap <= 0:
            return 0
        forgetter = lesson_forgetter
        if forgetter is None:
            try:
                forgetter = self._lesson_provider(env)
            except Exception:
                _LOG.exception("consolidate_lessons: could not build recall provider for eviction")
                return 0
        if forgetter is None or not hasattr(forgetter, "evict_to_cap"):
            return 0
        try:
            evicted = forgetter.evict_to_cap(max_lessons=cap, env=env, dry_run=dry_run)
        except Exception:
            _LOG.exception("consolidate_lessons: eviction failed")
            return 0
        return len(evicted) if evicted else 0
