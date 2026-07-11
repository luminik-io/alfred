"""The store-backed CRUD ledger behind :class:`fleet_brain.FleetBrain`.

``LedgerBase`` holds the ``store``/``_env`` state and every THIN operation that
is a direct pass to the :class:`~fleet_brain.store.Store`: the write paths
(``reflect``, ``firing_log``, ``record_file_touch``, ``propose_memory``, the
GitHub/worker upserts), the graph-densification writes, the durable reuse
counters, and the bounded read/list/count paths. The policy layers
(:mod:`fleet_brain.promotion`, :mod:`fleet_brain.consolidate`,
:mod:`fleet_brain.reliability`, :mod:`fleet_brain.serialize`) are mixins that
compose on top of this base, and ``FleetBrain`` is the facade that binds them
together. Keeping the CRUD here lets each policy mixin call a shared read
(``list_memory_candidates``, ``list_lessons``) without re-declaring it.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from .graph import (
    CodeOwnerRule,
    densify_enabled,
    edges_for_file_touch,
    file_node,
    owners_for_path,
    parse_codeowners,
)
from .store import (
    BundleItem,
    CodeOwnerRow,
    FailureEvent,
    FileChangeType,
    FileTouch,
    FiringLog,
    FiringStatus,
    GitHubItem,
    GitHubItemKind,
    GitHubItemState,
    GraphEdgeRow,
    Lesson,
    LessonAnchor,
    MemoryCandidate,
    MemoryCandidateStatus,
    RepoNote,
    Severity,
    Store,
    WorkerHeartbeat,
    WorkerStatus,
    new_id,
)
from .taxonomy import normalize_anchor_relation, normalize_anchor_type, normalize_kind

_LOG = logging.getLogger(__name__)

# Cap recall output so a runaway codename can't blow up a prompt.
_RECALL_DEFAULT = 8
_RECALL_MAX = 50


def _bundle_slug_from_labels(labels: list[str]) -> str | None:
    for label in labels:
        if label.startswith("agent:bundle:"):
            return label.removeprefix("agent:bundle:").strip() or None
        if label.startswith("bundle:"):
            return label.removeprefix("bundle:").strip() or None
    return None


class LedgerBase:
    """Thin, store-backed CRUD shared by every :class:`FleetBrain` policy mixin.

    ``store`` and ``_env`` are bound by :meth:`FleetBrain.__init__`; declared here
    so the mixins type-check against them.
    """

    store: Store
    # Optional env override for config-driven toggles (e.g. graph
    # densification). ``None`` means read the live process environment.
    _env: Mapping[str, str] | None

    def _lesson_provider(self, env: Mapping[str, str] | None = None) -> Any:
        """Build the promoted-lesson backend from the configured memory chain.

        Resolves ``ALFRED_MEMORY_PROVIDERS`` via
        :func:`memory.config.load_lesson_writer`: the embedded SQLite hybrid
        store by default (zero-daemon), Redis AMS when ``redis`` leads the chain.
        The same backend serves the promote WRITE and the revert/retire/decay
        forget, so recall and writes stay aligned. Imported lazily to avoid an
        import cycle: the memory providers import ``Lesson`` from this package.
        """
        from memory.config import load_lesson_writer

        return load_lesson_writer(env=env)

    # ----- write paths --------------------------------------------------

    def reflect(
        self,
        *,
        codename: str,
        repo: str,
        body: str,
        tags: Iterable[str] | None = None,
        firing_id: str | None = None,
        severity: Severity = "info",
        lesson_id: str | None = None,
        created_at: datetime | None = None,
        kind: str | None = None,
        provenance: str | None = None,
    ) -> Lesson:
        """File a lesson the firing learned. Returns the persisted row.

        ``severity`` follows the same taxonomy as the fleet's Slack
        severity routing: ``info`` (recall-only context), ``warning``
        (worth bubbling into a future prompt), ``blocker`` (the next
        firing must read this before doing anything).

        ``kind`` types the lesson (convention/fix/failure/decision/
        review-pattern; unknown folds to ``note``). ``provenance`` records the
        firing/PR that created a promoted lesson. Both are optional and
        backward-compatible.
        """
        if not codename or not repo or not body:
            raise ValueError("reflect: codename, repo, and body are required")
        if severity not in ("info", "warning", "blocker"):
            raise ValueError(f"reflect: unknown severity {severity!r}")
        lesson = Lesson(
            id=lesson_id or new_id(),
            codename=codename,
            repo=repo,
            body=body.strip(),
            tags=sorted({t.strip() for t in (tags or []) if t.strip()}),
            created_at=created_at or datetime.now(UTC),
            firing_id=firing_id,
            severity=severity,
            kind=normalize_kind(kind),
            provenance=(provenance or firing_id or None),
        )
        _LOG.debug(
            "reflect: codename=%s repo=%s kind=%s tags=%s",
            codename,
            repo,
            lesson.kind,
            lesson.tags,
        )
        return self.store.insert_lesson(lesson)

    # ----- code-grounding + validity (Phase 2) --------------------------

    def anchor_lesson(
        self,
        *,
        lesson_id: str,
        anchor_ref: str,
        anchor_type: str = "file",
        relation: str = "about",
        repo: str | None = None,
        created_at: datetime | None = None,
    ) -> LessonAnchor:
        """Link a lesson to a code entity (a file/symbol/node) or another lesson.

        This is the code-grounding write: after it, ``lessons_for_anchor`` can
        surface "editing ``auth.py`` -> the convention + the fix that worked".
        Idempotent on ``(lesson_id, anchor_type, anchor_ref, relation)``.
        """
        if not lesson_id or not anchor_ref:
            raise ValueError("anchor_lesson: lesson_id and anchor_ref are required")
        anchor = LessonAnchor(
            id=new_id(),
            lesson_id=lesson_id.strip(),
            anchor_type=normalize_anchor_type(anchor_type),
            anchor_ref=anchor_ref.strip(),
            relation=normalize_anchor_relation(relation),
            repo=(repo or None),
            created_at=created_at or datetime.now(UTC),
        )
        return self.store.add_lesson_anchor(anchor)

    def lesson_anchors(self, lesson_id: str, *, limit: int = 100) -> list[LessonAnchor]:
        """Return the anchors linked to ``lesson_id``, most recent first."""
        return self.store.list_lesson_anchors(lesson_id, limit=limit)

    # ----- durable reuse counters (Phase 3) -----------------------------

    def get_reuse_count(self, scope_key: str) -> int:
        """Persisted reinforce-on-reuse count for a ranking scope key (0 if absent).

        Delegates to the store so the ranking layer can read a lesson's reuse
        weight from durable state instead of an in-process table. See
        :meth:`fleet_brain.store.SQLiteStore.get_reuse_count`."""
        return self.store.get_reuse_count(scope_key)

    def bump_reuse_counts(self, scope_keys: Sequence[str]) -> None:
        """Increment the persisted reuse count for each scope key by one."""
        self.store.bump_reuse_counts(scope_keys)

    def union_reuse_counts(self, survivor_key: str, loser_key: str) -> None:
        """Move the loser scope key's reuse count onto the survivor, then drop it.

        The reuse counterpart to the provenance/anchor union at merge time: a
        merged-away lesson's accumulated reuse is added to the survivor and its
        orphaned row deleted, so the survivor keeps the full reinforcement."""
        self.store.union_reuse_counts(survivor_key, loser_key)

    def lessons_for_anchor(
        self,
        *,
        anchor_ref: str,
        anchor_type: str | None = None,
        repo: str | None = None,
        limit: int = 50,
    ) -> list[Lesson]:
        """Return the still-valid lessons anchored to ``anchor_ref`` (e.g. a file)."""
        return self.store.lessons_for_anchor(
            anchor_ref=anchor_ref,
            anchor_type=anchor_type,
            repo=repo,
            limit=limit,
        )

    def supersede_lesson(self, *, old_id: str, new_id: str, at: datetime | None = None) -> bool:
        """Invalidate ``old_id`` in favour of ``new_id`` (invalidate, not delete).

        Recall stops surfacing the old lesson; the audit row is kept. Returns
        ``False`` for a blank/unknown old id so callers can gate on it.
        """
        return self.store.supersede_lesson(old_id, new_id, at=at)

    def firing_log(
        self,
        *,
        firing_id: str,
        codename: str,
        status: FiringStatus,
        summary: str = "",
        repo: str | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        cost_cents: int = 0,
        pr_url: str | None = None,
        sentinel: str | None = None,
    ) -> FiringLog:
        """Persist one firing's audit row. Upserts on ``firing_id``."""
        if not firing_id or not codename:
            raise ValueError("firing_log: firing_id and codename are required")
        if status not in ("ok", "blocked", "partial", "silent"):
            raise ValueError(f"firing_log: unknown status {status!r}")
        now = datetime.now(UTC)
        log = FiringLog(
            firing_id=firing_id,
            codename=codename,
            repo=repo,
            status=status,
            summary=summary or "",
            started_at=started_at or now,
            finished_at=finished_at or now,
            cost_cents=int(cost_cents),
            pr_url=pr_url,
            sentinel=sentinel,
        )
        return self.store.insert_firing_log(log)

    def note_repo(self, *, repo: str, body: str, updated_at: datetime | None = None) -> RepoNote:
        """Upsert the free-text rollup for ``repo``."""
        if not repo or not body:
            raise ValueError("note_repo: repo and body are required")
        note = RepoNote(
            repo=repo,
            body=body.strip(),
            updated_at=updated_at or datetime.now(UTC),
        )
        return self.store.upsert_repo_note(note)

    def record_file_touch(
        self,
        *,
        repo: str,
        path: str,
        codename: str,
        firing_id: str | None = None,
        pr_url: str | None = None,
        change_type: FileChangeType = "modified",
        touch_id: str | None = None,
        touched_at: datetime | None = None,
    ) -> FileTouch:
        """Persist one repo file touched by an agent firing or PR."""
        if not repo or not path or not codename:
            raise ValueError("record_file_touch: repo, path, and codename are required")
        if change_type not in ("added", "modified", "deleted", "renamed", "unknown"):
            raise ValueError(f"record_file_touch: unknown change_type {change_type!r}")
        touch = FileTouch(
            id=touch_id or new_id(),
            repo=repo.strip(),
            path=path.strip(),
            codename=codename.strip(),
            firing_id=firing_id,
            pr_url=pr_url,
            change_type=change_type,
            touched_at=touched_at or datetime.now(UTC),
        )
        stored = self.store.insert_file_touch(touch)
        # Densify the graph with the edges this touch implies. Best-effort
        # and gated by ALFRED_GRAPH_DENSIFY (default on); a projection error
        # must never lose the recorded touch, which is the load-bearing row.
        if densify_enabled(self._env):
            try:
                self.project_file_touch_edges(stored)
            except Exception:  # densification is advisory; never lose the touch
                _LOG.warning("graph densify failed for touch %s", stored.id, exc_info=True)
        return stored

    # ----- graph densification ------------------------------------------

    def project_file_touch_edges(
        self, touch: FileTouch, *, now: datetime | None = None
    ) -> list[GraphEdgeRow]:
        """Materialize the fleet-authored edges implied by a file touch.

        Writes ``file -[in]-> repo`` always, ``PR -[changed]-> file`` when
        the touch carries a ``pr_url``, and ``file -[owned_by]-> owner`` for
        every CODEOWNERS owner currently resolved for the path. Idempotent:
        re-projecting the same touch bumps ``last_seen``/``weight`` rather
        than duplicating edges.
        """
        ts = now or touch.touched_at or datetime.now(UTC)
        owners = self.who_owns(repo=touch.repo, path=touch.path)
        specs = edges_for_file_touch(
            repo=touch.repo,
            path=touch.path,
            pr_url=touch.pr_url,
            owners=owners,
        )
        written: list[GraphEdgeRow] = []
        for spec in specs:
            row = GraphEdgeRow(
                id=new_id(),
                kind=spec.kind,
                src_type=spec.src_type,
                src=spec.src,
                dst_type=spec.dst_type,
                dst=spec.dst,
                repo=spec.repo,
                first_seen=ts,
                last_seen=ts,
                weight=1,
            )
            written.append(self.store.upsert_graph_edge(row))
        return written

    def ingest_codeowners(
        self, *, repo: str, content: str, updated_at: datetime | None = None
    ) -> int:
        """Parse and persist a repo's CODEOWNERS file.

        The new file replaces any earlier rules for the repo (CODEOWNERS is
        the single source of truth). After ingest, ``who_owns`` and future
        ``owned_by`` projections resolve against these rules. Returns the
        number of stored ``(pattern, owner)`` rules.
        """
        if not repo or not repo.strip():
            raise ValueError("ingest_codeowners: repo is required")
        repo = repo.strip()
        ts = updated_at or datetime.now(UTC)
        rules = parse_codeowners(repo, content or "")
        rows = [
            CodeOwnerRow(
                id=new_id(),
                repo=rule.repo,
                pattern=rule.pattern,
                owner=rule.owner,
                rank=rule.rank,
                updated_at=ts,
            )
            for rule in rules
        ]
        return self.store.replace_code_owners(repo, rows)

    def who_owns(self, *, repo: str, path: str) -> list[str]:
        """Return the CODEOWNERS owner(s) for ``repo``/``path``.

        Resolves against the rules ingested via :meth:`ingest_codeowners`
        using CODEOWNERS "last matching pattern wins" semantics. Returns an
        empty list when the repo has no CODEOWNERS data or nothing matches.
        """
        if not repo or not path:
            return []
        stored = self.store.list_code_owners(repo.strip())
        if not stored:
            return []
        rules = [
            CodeOwnerRule(repo=row.repo, pattern=row.pattern, owner=row.owner, rank=row.rank)
            for row in stored
        ]
        return owners_for_path(path, rules)

    def recent_changes_near(self, *, repo: str, path: str, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent file touches in the same directory as ``path``.

        "Near" means siblings under the same directory prefix in the same
        repo, most-recent-first. This is the graph read that answers "what
        else has the fleet been changing around here lately" without an AST.
        """
        if not repo or not path:
            return []
        repo = repo.strip()
        directory = path.strip().rsplit("/", 1)[0] if "/" in path.strip() else ""
        clamped = max(1, min(int(limit), 200))
        # Pull a generous window, then filter to the directory in Python so we
        # do not push a LIKE prefix scan into the hot list path.
        touches = self.store.list_file_touches(repo=repo, limit=500)
        out: list[dict[str, Any]] = []
        for touch in touches:
            touch_dir = touch.path.rsplit("/", 1)[0] if "/" in touch.path else ""
            if touch_dir != directory:
                continue
            out.append(
                {
                    "repo": touch.repo,
                    "path": touch.path,
                    "codename": touch.codename,
                    "change_type": touch.change_type,
                    "firing_id": touch.firing_id,
                    "pr_url": touch.pr_url,
                    "touched_at": touch.touched_at.astimezone(UTC).isoformat(),
                    "is_self": touch.path == path.strip(),
                }
            )
            if len(out) >= clamped:
                break
        return out

    def prs_touching(self, *, repo: str, path: str, limit: int = 20) -> list[dict[str, Any]]:
        """Return the pull requests that changed ``repo``/``path``.

        Reads the materialized ``PR -[changed]-> file`` edges. Falls back to
        scanning ``file_touches`` for a ``pr_url`` when graph projection is
        off, so the helper still answers correctly on a non-densified brain.
        Most-recently-seen first.
        """
        if not repo or not path:
            return []
        repo = repo.strip()
        fnode = file_node(repo, path)
        edges = self.store.list_graph_edges(kind="changed", dst=fnode, limit=500)
        clamped = max(1, min(int(limit), 200))
        if edges:
            out = [
                {
                    "pr": edge.src.split(":", 1)[1] if ":" in edge.src else edge.src,
                    "repo": edge.repo,
                    "weight": edge.weight,
                    "last_seen": edge.last_seen.astimezone(UTC).isoformat(),
                }
                for edge in edges
            ]
            return out[:clamped]
        # Fallback: derive from raw touches when no edges were projected.
        seen: dict[str, dict[str, Any]] = {}
        for touch in self.store.list_file_touches(repo=repo, path=path.strip(), limit=500):
            if not touch.pr_url:
                continue
            existing = seen.get(touch.pr_url)
            iso = touch.touched_at.astimezone(UTC).isoformat()
            if existing is None:
                seen[touch.pr_url] = {
                    "pr": touch.pr_url,
                    "repo": touch.repo,
                    "weight": 1,
                    "last_seen": iso,
                }
            else:
                existing["weight"] = int(existing["weight"]) + 1
                if iso > str(existing["last_seen"]):
                    existing["last_seen"] = iso
        ordered = sorted(seen.values(), key=lambda item: str(item["last_seen"]), reverse=True)
        return ordered[:clamped]

    def propose_memory(
        self,
        *,
        codename: str,
        repo: str,
        body: str,
        tags: Iterable[str] | None = None,
        severity: Severity = "info",
        source: str = "manual",
        source_firing_id: str | None = None,
        evidence: str = "",
        confidence: float = 0.5,
        candidate_id: str | None = None,
        created_at: datetime | None = None,
        kind: str | None = None,
    ) -> MemoryCandidate:
        """Stage a lesson candidate without adding it to prompt recall.

        ``reflect`` is intentionally direct for trusted operator input.
        ``propose_memory`` is the safer path for automated summaries,
        imported notes, and speculative engine reflections: the row is
        visible to ``alfred brain candidates`` and can later be promoted
        into a real lesson.
        """
        if not codename or not repo or not body:
            raise ValueError("propose_memory: codename, repo, and body are required")
        if severity not in ("info", "warning", "blocker"):
            raise ValueError(f"propose_memory: unknown severity {severity!r}")
        if not 0.0 <= float(confidence) <= 1.0:
            raise ValueError("propose_memory: confidence must be between 0 and 1")
        candidate = MemoryCandidate(
            id=candidate_id or new_id(),
            codename=codename.strip(),
            repo=repo.strip(),
            body=body.strip(),
            tags=sorted({t.strip() for t in (tags or []) if t.strip()}),
            severity=severity,
            source=(source or "manual").strip(),
            source_firing_id=source_firing_id,
            evidence=evidence.strip(),
            confidence=float(confidence),
            status="candidate",
            created_at=created_at or datetime.now(UTC),
            kind=normalize_kind(kind),
        )
        return self.store.insert_memory_candidate(candidate)

    def record_failure(
        self,
        *,
        codename: str,
        subtype: str,
        summary: str,
        repo: str | None = None,
        firing_id: str | None = None,
        engine: str | None = None,
        severity: Severity = "warning",
        event_id: str | None = None,
        created_at: datetime | None = None,
    ) -> FailureEvent:
        """Persist a normalized non-success event for later diagnosis."""
        if not codename or not subtype:
            raise ValueError("record_failure: codename and subtype are required")
        if severity not in ("info", "warning", "blocker"):
            raise ValueError(f"record_failure: unknown severity {severity!r}")
        event = FailureEvent(
            id=event_id or new_id(),
            codename=codename.strip(),
            repo=repo.strip() if repo else None,
            firing_id=firing_id,
            subtype=subtype.strip(),
            summary=(summary or "").strip(),
            engine=engine.strip() if engine else None,
            severity=severity,
            created_at=created_at or datetime.now(UTC),
        )
        return self.store.insert_failure_event(event)

    def upsert_github_item(
        self,
        *,
        repo: str,
        number: int,
        kind: GitHubItemKind,
        state: GitHubItemState,
        title: str = "",
        url: str = "",
        labels: Iterable[str] | None = None,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
        last_seen_at: datetime | None = None,
        closed_at: datetime | None = None,
        merged_at: datetime | None = None,
        head_ref: str | None = None,
        base_ref: str | None = None,
        bundle_slug: str | None = None,
        changed_files: int | None = None,
        additions: int | None = None,
        deletions: int | None = None,
    ) -> GitHubItem:
        """Cache one GitHub issue or PR row.

        The poller is deliberately pull-based and idempotent: every run
        replaces the cached row for ``repo#number`` / ``kind`` with the
        latest shape it saw.
        """
        if not repo or not int(number):
            raise ValueError("upsert_github_item: repo and number are required")
        if kind not in ("issue", "pr"):
            raise ValueError(f"upsert_github_item: unknown kind {kind!r}")
        if state not in ("open", "closed", "merged", "unknown"):
            raise ValueError(f"upsert_github_item: unknown state {state!r}")
        now = datetime.now(UTC)
        clean_labels = sorted(
            {str(label).strip() for label in (labels or []) if str(label).strip()}
        )
        resolved_bundle = (bundle_slug or "").strip() or _bundle_slug_from_labels(clean_labels)
        item = GitHubItem(
            id=f"{repo}#{int(number)}:{kind}",
            repo=repo.strip(),
            number=int(number),
            kind=kind,
            state=state,
            title=(title or "").strip(),
            url=(url or "").strip(),
            labels=clean_labels,
            created_at=created_at,
            updated_at=updated_at or now,
            last_seen_at=last_seen_at or now,
            closed_at=closed_at,
            merged_at=merged_at,
            head_ref=head_ref,
            base_ref=base_ref,
            bundle_slug=resolved_bundle,
            changed_files=max(0, int(changed_files)) if changed_files is not None else None,
            additions=max(0, int(additions)) if additions is not None else None,
            deletions=max(0, int(deletions)) if deletions is not None else None,
        )
        persisted = self.store.upsert_github_item(item)
        if persisted.bundle_slug:
            self.store.upsert_bundle_item(
                BundleItem(
                    id=f"{persisted.bundle_slug}:{persisted.repo}#{persisted.number}:{persisted.kind}",
                    bundle_slug=persisted.bundle_slug,
                    repo=persisted.repo,
                    item_kind=persisted.kind,
                    number=persisted.number,
                    state=persisted.state,
                    title=persisted.title,
                    url=persisted.url,
                    labels=persisted.labels,
                    updated_at=persisted.updated_at,
                    last_seen_at=persisted.last_seen_at,
                )
            )
        return persisted

    def upsert_bundle_item(
        self,
        *,
        bundle_slug: str,
        repo: str,
        item_kind: GitHubItemKind,
        number: int,
        state: GitHubItemState,
        title: str = "",
        url: str = "",
        labels: Iterable[str] | None = None,
        updated_at: datetime | None = None,
        last_seen_at: datetime | None = None,
    ) -> BundleItem:
        """Upsert bundle membership without requiring a full GitHub row."""
        if not bundle_slug or not repo or not int(number):
            raise ValueError("upsert_bundle_item: bundle_slug, repo, and number are required")
        now = datetime.now(UTC)
        item = BundleItem(
            id=f"{bundle_slug}:{repo}#{int(number)}:{item_kind}",
            bundle_slug=bundle_slug.strip(),
            repo=repo.strip(),
            item_kind=item_kind,
            number=int(number),
            state=state,
            title=(title or "").strip(),
            url=(url or "").strip(),
            labels=sorted({str(label).strip() for label in (labels or []) if str(label).strip()}),
            updated_at=updated_at or now,
            last_seen_at=last_seen_at or now,
        )
        return self.store.upsert_bundle_item(item)

    def upsert_worker_heartbeat(
        self,
        *,
        codename: str,
        firing_id: str,
        status: WorkerStatus = "running",
        started_at: datetime | None = None,
        heartbeat_at: datetime | None = None,
        repo: str | None = None,
        pid: int | None = None,
        detail: str = "",
    ) -> WorkerHeartbeat:
        """Record the latest liveness signal for one worker firing."""
        if not codename or not firing_id:
            raise ValueError("upsert_worker_heartbeat: codename and firing_id are required")
        if status not in ("running", "ok", "failed", "stale", "cancelled"):
            raise ValueError(f"upsert_worker_heartbeat: unknown status {status!r}")
        now = datetime.now(UTC)
        heartbeat = WorkerHeartbeat(
            id=f"{codename.strip()}:{firing_id.strip()}",
            codename=codename.strip(),
            firing_id=firing_id.strip(),
            status=status,
            started_at=started_at or now,
            heartbeat_at=heartbeat_at or now,
            repo=repo.strip() if repo else None,
            pid=int(pid) if pid is not None else None,
            detail=(detail or "").strip(),
        )
        return self.store.upsert_worker_heartbeat(heartbeat)

    # ----- read paths ---------------------------------------------------

    def recall(
        self,
        codename: str | None = None,
        repo: str | None = None,
        query: str | None = None,
        *,
        limit: int = _RECALL_DEFAULT,
    ) -> list[Lesson]:
        """Return the most-recent-first lessons matching the filters.

        Calling shape mirrors the prompt-prepend pattern: the runner
        does ``brain.recall(codename, repo)`` and dumps the bodies
        into the firing's system prompt.
        """
        clamped = max(1, min(int(limit), _RECALL_MAX))
        return self.store.recall_lessons(
            codename=codename,
            repo=repo,
            query=query,
            limit=clamped,
        )

    def get_repo_note(self, repo: str) -> RepoNote | None:
        return self.store.get_repo_note(repo)

    def list_lessons(self, limit: int | None = None) -> list[Lesson]:
        return self.store.list_lessons(limit=limit)

    def list_firings(
        self,
        codename: str | None = None,
        status: FiringStatus | None = None,
        limit: int = 50,
    ) -> list[FiringLog]:
        return self.store.list_firing_logs(codename=codename, status=status, limit=limit)

    def list_file_touches(
        self,
        repo: str | None = None,
        codename: str | None = None,
        path: str | None = None,
        limit: int = 50,
    ) -> list[FileTouch]:
        clamped = max(1, min(int(limit), 500))
        return self.store.list_file_touches(
            repo=repo,
            codename=codename,
            path=path,
            limit=clamped,
        )

    def count_file_touches(
        self,
        repo: str | None = None,
        codename: str | None = None,
        path: str | None = None,
        touched_since: datetime | None = None,
    ) -> int:
        """Exact COUNT(*) of file_touches, unbounded by the list 500-row cap.

        ``list_file_touches`` clamps ``limit`` to 500, so callers that need a
        true total (e.g. proof-telemetry's lifetime counts) must use this rather
        than ``len(list_...())``, which silently freezes at 500 on a busy brain.
        """
        return self.store.count_file_touches(
            repo=repo,
            codename=codename,
            path=path,
            touched_since=touched_since,
        )

    def list_memory_candidates(
        self,
        status: MemoryCandidateStatus | None = "candidate",
        repo: str | None = None,
        codename: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MemoryCandidate]:
        clamped = max(1, min(int(limit), 500))
        return self.store.list_memory_candidates(
            status=status,
            repo=repo,
            codename=codename,
            limit=clamped,
            offset=max(0, int(offset)),
        )

    def list_failures(
        self,
        repo: str | None = None,
        codename: str | None = None,
        subtype: str | None = None,
        limit: int = 50,
    ) -> list[FailureEvent]:
        clamped = max(1, min(int(limit), 500))
        return self.store.list_failure_events(
            repo=repo,
            codename=codename,
            subtype=subtype,
            limit=clamped,
        )

    def list_github_items(
        self,
        repo: str | None = None,
        kind: GitHubItemKind | None = None,
        state: GitHubItemState | None = None,
        bundle_slug: str | None = None,
        limit: int = 50,
    ) -> list[GitHubItem]:
        clamped = max(1, min(int(limit), 500))
        return self.store.list_github_items(
            repo=repo,
            kind=kind,
            state=state,
            bundle_slug=bundle_slug,
            limit=clamped,
        )

    def count_github_items(
        self,
        repo: str | None = None,
        kind: GitHubItemKind | None = None,
        state: GitHubItemState | None = None,
        bundle_slug: str | None = None,
        authored_only: bool = False,
        agent_labeled_only: bool = False,
        created_since: datetime | None = None,
        closed_since: datetime | None = None,
        merged_since: datetime | None = None,
        updated_since: datetime | None = None,
    ) -> int:
        """Exact COUNT(*) of github_items, unbounded by the list 500-row cap.

        ``list_github_items`` clamps ``limit`` to 500, so any caller needing a
        true total (proof-telemetry's lifetime PR counts) must use this. Counting
        by paginating ``list_github_items`` can never exceed 500 because the list
        method re-clamps every request.

        ``authored_only=True`` restricts the count to agent-authored PRs/issues:
        rows carrying the ``agent:authored`` provenance label or pushed from an
        agent branch prefix. The poller stores EVERY PR from ``gh pr list`` (not
        just Alfred's), so proof-telemetry passes this to avoid claiming PRs the
        fleet did not open. The filter is a SQL predicate on already-stored
        columns, so it stays an exact COUNT(*).

        ``agent_labeled_only=True`` restricts the count to rows with any
        ``agent:*`` label. Proof telemetry uses this for issue counts, where the
        public signal is the issue label rather than a branch name.
        """
        return self.store.count_github_items(
            repo=repo,
            kind=kind,
            state=state,
            bundle_slug=bundle_slug,
            authored_only=authored_only,
            agent_labeled_only=agent_labeled_only,
            created_since=created_since,
            closed_since=closed_since,
            merged_since=merged_since,
            updated_since=updated_since,
        )

    def sum_github_changed_lines(
        self,
        repo: str | None = None,
        kind: GitHubItemKind | None = None,
        state: GitHubItemState | None = None,
        bundle_slug: str | None = None,
        authored_only: bool = False,
        agent_labeled_only: bool = False,
        created_since: datetime | None = None,
        closed_since: datetime | None = None,
        merged_since: datetime | None = None,
        updated_since: datetime | None = None,
    ) -> int:
        """Sum additions + deletions from cached GitHub PR rows.

        Proof telemetry uses this with ``kind="pr"`` and
        ``authored_only=True`` so the line-count metric is anchored to the same
        Alfred-authored PR subset as the PR counters.
        """
        return self.store.sum_github_changed_lines(
            repo=repo,
            kind=kind,
            state=state,
            bundle_slug=bundle_slug,
            authored_only=authored_only,
            agent_labeled_only=agent_labeled_only,
            created_since=created_since,
            closed_since=closed_since,
            merged_since=merged_since,
            updated_since=updated_since,
        )

    def sum_github_changed_files(
        self,
        repo: str | None = None,
        kind: GitHubItemKind | None = None,
        state: GitHubItemState | None = None,
        bundle_slug: str | None = None,
        authored_only: bool = False,
        agent_labeled_only: bool = False,
        created_since: datetime | None = None,
        closed_since: datetime | None = None,
        merged_since: datetime | None = None,
        updated_since: datetime | None = None,
    ) -> int:
        """Sum changed-file counts from cached GitHub PR rows."""
        return self.store.sum_github_changed_files(
            repo=repo,
            kind=kind,
            state=state,
            bundle_slug=bundle_slug,
            authored_only=authored_only,
            agent_labeled_only=agent_labeled_only,
            created_since=created_since,
            closed_since=closed_since,
            merged_since=merged_since,
            updated_since=updated_since,
        )

    def list_bundle_items(
        self,
        bundle_slug: str | None = None,
        state: GitHubItemState | None = None,
        limit: int = 50,
    ) -> list[BundleItem]:
        clamped = max(1, min(int(limit), 500))
        return self.store.list_bundle_items(bundle_slug=bundle_slug, state=state, limit=clamped)

    def list_worker_heartbeats(
        self,
        codename: str | None = None,
        status: WorkerStatus | None = None,
        limit: int = 50,
    ) -> list[WorkerHeartbeat]:
        clamped = max(1, min(int(limit), 500))
        return self.store.list_worker_heartbeats(
            codename=codename,
            status=status,
            limit=clamped,
        )

    def list_stale_workers(self, *, max_age_minutes: int = 60) -> list[WorkerHeartbeat]:
        """Return running worker heartbeats older than ``max_age_minutes``."""
        cutoff = datetime.now(UTC) - timedelta(minutes=max(1, int(max_age_minutes)))
        return [
            hb
            for hb in self.list_worker_heartbeats(status="running", limit=500)
            if hb.heartbeat_at < cutoff
        ]

    def stats(self) -> dict[str, int]:
        return self.store.stats()

    # ----- delete paths -------------------------------------------------

    def forget(self, lesson_id: str) -> bool:
        """Delete a single lesson by id. Returns True if it existed."""
        return self.store.delete_lesson(lesson_id)

    def forget_before(self, *, days: int | None = None, before: datetime | None = None) -> int:
        """GC lessons older than ``days`` (or older than ``before``).

        Pass exactly one of ``days`` or ``before``.
        """
        if (days is None) == (before is None):
            raise ValueError("forget_before: pass exactly one of days= or before=")
        cutoff = before
        if cutoff is None:
            assert days is not None  # for mypy
            cutoff = datetime.now(UTC) - timedelta(days=int(days))
        return self.store.delete_lessons_before(cutoff)
