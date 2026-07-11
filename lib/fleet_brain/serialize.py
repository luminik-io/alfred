"""Snapshot export/import for the fleet brain.

``export`` returns a JSON-serializable snapshot of the durable memory + ledger;
``import_snapshot`` restores it on another host in FK-safe order. The transfer
is the operator's job -- the brain never phones home. Regenerable caches
(GitHub items, bundle items, worker heartbeats) are exported for inspection but
not re-imported (the pollers rebuild them).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from .base import LedgerBase
from .store import (
    FailureEvent,
    FileTouch,
    FiringLog,
    Lesson,
    LessonAnchor,
    MemoryCandidate,
    RepoNote,
)
from .taxonomy import normalize_anchor_relation, normalize_anchor_type, normalize_kind

_LOG = logging.getLogger(__name__)


def _snap_dt(value: Any) -> datetime:
    """Parse an exported ISO timestamp back to a UTC-aware datetime.

    Total: a missing, blank, or unparseable value falls back to ``now`` so an
    :meth:`SerializationMixin.import_snapshot` row is never dropped for a bad
    timestamp.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value or "").strip()
    if not text:
        return datetime.now(UTC)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(UTC)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _serialize(d: dict[str, Any]) -> dict[str, Any]:
    """Best-effort JSON serialization: datetime -> ISO, everything else
    passes through. Used for export only."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, datetime):
            out[k] = v.astimezone(UTC).isoformat()
        else:
            out[k] = v
    return out


class SerializationMixin(LedgerBase):
    """Export/import of the brain snapshot, composed into :class:`FleetBrain`."""

    def export(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of the entire brain.

        Format::

            {
              "schema_version": 3,
              "exported_at": "2026-05-23T...Z",
              "lessons": [{...}, ...],
              "lesson_anchors": [{...}, ...],
              "repo_notes": [{...}, ...],
              "firings": [{...}, ...],
              "file_touches": [{...}, ...],
              "memory_candidates": [{...}, ...],
              "failure_events": [{...}, ...]
            }

        ``alfred brain export`` writes this to disk; :meth:`import_snapshot`
        restores the durable memory + ledger (lessons, their Phase 2 anchors,
        repo notes, candidates, firings, file touches, failures) on the target
        host so a backup round-trips, including the ``lesson_anchors`` that
        ground lessons to code (an earlier export dropped them and lost the
        links on restore). Regenerable caches (GitHub items, bundle items,
        worker heartbeats) are exported for inspection but not re-imported: the
        pollers rebuild them.
        """
        from .schema import SCHEMA_VERSION

        return {
            "schema_version": SCHEMA_VERSION,
            "exported_at": datetime.now(UTC).isoformat(),
            "lessons": [_serialize(asdict(L)) for L in self.list_lessons()],
            "lesson_anchors": [_serialize(asdict(a)) for a in self.store.list_all_lesson_anchors()],
            "repo_notes": [_serialize(asdict(n)) for n in self._all_repo_notes()],
            "firings": [_serialize(asdict(F)) for F in self.list_firings(limit=10_000)],
            "file_touches": [_serialize(asdict(T)) for T in self.list_file_touches(limit=10_000)],
            "memory_candidates": [
                _serialize(asdict(C))
                for C in self.list_memory_candidates(status=None, limit=10_000)
            ],
            "failure_events": [_serialize(asdict(F)) for F in self.list_failures(limit=10_000)],
            "github_items": [_serialize(asdict(G)) for G in self.list_github_items(limit=10_000)],
            "bundle_items": [_serialize(asdict(B)) for B in self.list_bundle_items(limit=10_000)],
            "worker_heartbeats": [
                _serialize(asdict(H)) for H in self.list_worker_heartbeats(limit=10_000)
            ],
        }

    def import_snapshot(self, snapshot: Mapping[str, Any]) -> dict[str, int]:
        """Restore a brain from an :meth:`export` snapshot. Returns per-type counts.

        Restores the durable memory + ledger, in FK-safe order (lessons before
        their anchors): repo notes, lessons (with Phase 2 kind/validity/
        provenance), ``lesson_anchors``, memory candidates, firings, file
        touches, and failure events. Regenerable GitHub/bundle/worker caches are
        skipped (the pollers rebuild them). Best-effort and idempotent-ish: a row
        that fails to insert (e.g. a duplicate id on a non-empty target) is
        skipped and not counted, so re-importing into a fresh brain restores the
        anchors that a pre-fix export would have lost.
        """
        counts: dict[str, int] = {}

        def _restore(key: str, builder: Any, insert: Any) -> None:
            done = 0
            for raw in snapshot.get(key) or []:
                if not isinstance(raw, Mapping):
                    continue
                try:
                    insert(builder(dict(raw)))
                except Exception:
                    _LOG.debug("import_snapshot: skipped a %s row", key, exc_info=True)
                    continue
                done += 1
            counts[key] = done

        _restore(
            "repo_notes",
            lambda r: RepoNote(
                repo=str(r["repo"]),
                body=str(r.get("body", "")),
                updated_at=_snap_dt(r.get("updated_at")),
            ),
            self.store.upsert_repo_note,
        )
        _restore(
            "lessons",
            lambda r: Lesson(
                id=str(r["id"]),
                codename=str(r["codename"]),
                repo=str(r["repo"]),
                body=str(r.get("body", "")),
                tags=list(r.get("tags") or []),
                created_at=_snap_dt(r.get("created_at")),
                firing_id=r.get("firing_id"),
                severity=r.get("severity", "info"),
                kind=normalize_kind(r.get("kind")),
                valid_until=_snap_dt(r["valid_until"]) if r.get("valid_until") else None,
                superseded_by=r.get("superseded_by"),
                provenance=r.get("provenance"),
            ),
            self.store.insert_lesson,
        )
        _restore(
            "lesson_anchors",
            lambda r: LessonAnchor(
                id=str(r["id"]),
                lesson_id=str(r["lesson_id"]),
                anchor_type=normalize_anchor_type(r.get("anchor_type")),
                anchor_ref=str(r["anchor_ref"]),
                relation=normalize_anchor_relation(r.get("relation")),
                repo=r.get("repo"),
                created_at=_snap_dt(r.get("created_at")),
            ),
            self.store.add_lesson_anchor,
        )
        _restore(
            "memory_candidates",
            lambda r: MemoryCandidate(
                id=str(r["id"]),
                codename=str(r["codename"]),
                repo=str(r["repo"]),
                body=str(r.get("body", "")),
                tags=list(r.get("tags") or []),
                severity=r.get("severity", "info"),
                source=str(r.get("source", "manual")),
                source_firing_id=r.get("source_firing_id"),
                evidence=str(r.get("evidence", "")),
                confidence=float(r.get("confidence", 0.5)),
                status=r.get("status", "candidate"),
                created_at=_snap_dt(r.get("created_at")),
                reviewed_at=_snap_dt(r["reviewed_at"]) if r.get("reviewed_at") else None,
                reviewed_by=r.get("reviewed_by"),
                review_note=r.get("review_note"),
                promoted_lesson_id=r.get("promoted_lesson_id"),
                kind=normalize_kind(r.get("kind")),
            ),
            self.store.insert_memory_candidate,
        )
        _restore(
            "firings",
            lambda r: FiringLog(
                firing_id=str(r["firing_id"]),
                codename=str(r["codename"]),
                repo=r.get("repo"),
                status=r.get("status", "ok"),
                summary=str(r.get("summary", "")),
                started_at=_snap_dt(r.get("started_at")),
                finished_at=_snap_dt(r.get("finished_at")),
                cost_cents=int(r.get("cost_cents", 0)),
                pr_url=r.get("pr_url"),
                sentinel=r.get("sentinel"),
            ),
            self.store.insert_firing_log,
        )
        _restore(
            "file_touches",
            lambda r: FileTouch(
                id=str(r["id"]),
                repo=str(r["repo"]),
                path=str(r["path"]),
                codename=str(r["codename"]),
                touched_at=_snap_dt(r.get("touched_at")),
                firing_id=r.get("firing_id"),
                pr_url=r.get("pr_url"),
                change_type=r.get("change_type", "modified"),
            ),
            self.store.insert_file_touch,
        )
        _restore(
            "failure_events",
            lambda r: FailureEvent(
                id=str(r["id"]),
                codename=str(r["codename"]),
                subtype=str(r.get("subtype", "")),
                summary=str(r.get("summary", "")),
                severity=r.get("severity", "warning"),
                created_at=_snap_dt(r.get("created_at")),
                repo=r.get("repo"),
                firing_id=r.get("firing_id"),
                engine=r.get("engine"),
            ),
            self.store.insert_failure_event,
        )
        return counts

    def _all_repo_notes(self) -> list[RepoNote]:
        """Pull every repo note via a list_lessons-style sweep.

        The store doesn't expose a list method for notes today (the
        operator queries by repo); export needs everything, so we
        derive the repo set from existing lessons + any note we have.
        For now we use the lessons table as the source of repo keys.
        """
        seen: set[str] = set()
        out: list[RepoNote] = []
        for L in self.list_lessons():
            if L.repo in seen:
                continue
            seen.add(L.repo)
            note = self.store.get_repo_note(L.repo)
            if note is not None:
                out.append(note)
        return out
