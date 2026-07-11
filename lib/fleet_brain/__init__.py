"""Alfred's fleet-brain: a local procedural-learning memory layer.

``fleet_brain`` records what each agent firing learned about a repo
or codename. It keeps reviewable candidates, firing history, file
touches, GitHub cache rows, and local evidence under ``$ALFRED_HOME``.
Embedded SQLite hybrid memory is the default recalled-lesson layer for new
installs; FleetBrain is the local ledger behind that review loop.

Quick start::

    from fleet_brain import FleetBrain

    brain = FleetBrain()
    brain.reflect(
        codename="lucius",
        repo="your-org/api",
        body="GraphQL schema lives in src/schema.graphql; tests live next to it.",
        tags=["graphql", "layout"],
    )
    lessons = brain.recall(codename="lucius", repo="your-org/api")
    for L in lessons:
        print(L.body)

Public surface:

* :class:`FleetBrain`: the main API: ``recall``, ``reflect``,
  ``firing_log``, ``record_file_touch``, ``note_repo``, ``forget``,
  ``export``.
* :class:`fleet_brain.store.Lesson`, :class:`FiringLog`,
  :class:`FileTouch`, :class:`RepoNote`: entity dataclasses,
  re-exported here.
* :class:`fleet_brain.store.Store`: the Protocol the public API
  depends on. The default local ledger implementation is
  :class:`SQLiteStore`.

Internals are decomposed into cohesive modules that ``FleetBrain`` composes:

* :mod:`fleet_brain.base`: the thin, store-backed CRUD ledger (``LedgerBase``).
* :mod:`fleet_brain.store` / :mod:`fleet_brain.schema`: SQLite storage + schema.
* :mod:`fleet_brain.config`: environment parsing + policy predicates.
* :mod:`fleet_brain.promotion`: the capture -> judge -> promote pipeline.
* :mod:`fleet_brain.consolidate`: consolidation/decay + semantic dedup + merge.
* :mod:`fleet_brain.classify`: failure classification.
* :mod:`fleet_brain.reliability`: failure patterns, suggestions, doctor/health.
* :mod:`fleet_brain.serialize`: export/import snapshots.

``FleetBrain`` itself is the thin facade that binds the base CRUD to the
promotion, consolidation, reliability, and serialization mixins, keeping its
public method names stable so existing call sites and the ``MemoryProvider``
seam keep working.

Privacy: the FleetBrain ledger is a SQLite file in your
``$ALFRED_HOME``. It never leaves your machine. The only outbound
surface is prompt context sent to Claude Code or Codex on your
existing CLI auth, plus anonymous usage totals if telemetry is left
on. No raw prompts, transcripts, or candidate text are sent by
telemetry.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

from .base import LedgerBase
from .config import (
    consolidate_enabled,
    consolidate_semantic_enabled,
    consolidate_sim_threshold,
    direct_auto_promote_env,
    max_lessons_cap,
)
from .consolidate import ConsolidationMixin
from .consolidate import _cosine_similarity as _cosine_similarity
from .consolidate import _semantic_dup_groups as _semantic_dup_groups
from .graph import (
    CodeOwnerRule,
    GraphEdge,
    densify_enabled,
    edges_for_file_touch,
    owners_for_path,
    parse_codeowners,
)
from .promotion import (
    MemoryPromotionError,
    PromotionMixin,
    candidate_id_from_lesson_id,
)
from .reliability import ReliabilityMixin
from .serialize import SerializationMixin
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
    SQLiteStore,
    Store,
    WorkerHeartbeat,
    WorkerStatus,
    default_db_path,
    new_id,
)
from .taxonomy import (
    ANCHOR_RELATIONS,
    ANCHOR_TYPES,
    DEFAULT_LESSON_KIND,
    LESSON_KINDS,
    normalize_anchor_relation,
    normalize_anchor_type,
    normalize_kind,
)

__all__ = [
    "ANCHOR_RELATIONS",
    "ANCHOR_TYPES",
    "DEFAULT_LESSON_KIND",
    "LESSON_KINDS",
    "BundleItem",
    "CodeOwnerRow",
    "CodeOwnerRule",
    "FailureEvent",
    "FileChangeType",
    "FileTouch",
    "FiringLog",
    "FiringStatus",
    "FleetBrain",
    "GitHubItem",
    "GitHubItemKind",
    "GitHubItemState",
    "GraphEdge",
    "GraphEdgeRow",
    "Lesson",
    "LessonAnchor",
    "MemoryCandidate",
    "MemoryCandidateStatus",
    "MemoryPromotionError",
    "RepoNote",
    "SQLiteStore",
    "Severity",
    "Store",
    "WorkerHeartbeat",
    "WorkerStatus",
    "candidate_id_from_lesson_id",
    "consolidate_enabled",
    "consolidate_semantic_enabled",
    "consolidate_sim_threshold",
    "default_db_path",
    "densify_enabled",
    "direct_auto_promote_env",
    "edges_for_file_touch",
    "max_lessons_cap",
    "new_id",
    "normalize_anchor_relation",
    "normalize_anchor_type",
    "normalize_kind",
    "owners_for_path",
    "parse_codeowners",
]


_LOG = logging.getLogger(__name__)


class FleetBrain(
    PromotionMixin,
    ConsolidationMixin,
    ReliabilityMixin,
    SerializationMixin,
    LedgerBase,
):
    """Local procedural-memory layer for the Alfred fleet.

    Operates on a SQLite file by default; tests can inject a custom
    :class:`Store` through the constructor.

    ``FleetBrain`` is a thin facade: it binds the store to the cohesive
    concern mixins (:class:`~fleet_brain.promotion.PromotionMixin`,
    :class:`~fleet_brain.consolidate.ConsolidationMixin`,
    :class:`~fleet_brain.reliability.ReliabilityMixin`,
    :class:`~fleet_brain.serialize.SerializationMixin`) on top of the
    store-backed CRUD in :class:`~fleet_brain.base.LedgerBase`.

    Method names map to the operator-facing verbs:

    * :meth:`reflect`: file a lesson the firing learned.
    * :meth:`recall`: pull lessons relevant to the next firing.
    * :meth:`firing_log`: record one firing's audit row.
    * :meth:`record_file_touch`: record a file changed by an agent.
    * :meth:`propose_memory`: stage a lesson candidate for review.
    * :meth:`record_failure`: normalize non-success outcomes for later diagnosis.
    * :meth:`upsert_github_item`: cache GitHub issue/PR state from a poller.
    * :meth:`upsert_worker_heartbeat`: record worker liveness.
    * :meth:`note_repo`: upsert a free-text repo summary.
    * :meth:`health`: confirm the local ledger is reachable.
    * :meth:`forget`: remove a lesson by id.
    * :meth:`export`: JSON-serializable snapshot for backup or
      cross-host export (the operator must do the transfer; the
      brain never phones home).
    """

    def __init__(
        self,
        store: Store | None = None,
        *,
        db_path: Path | str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        if store is not None:
            self.store = store
        else:
            resolved = Path(db_path) if db_path is not None else default_db_path()
            self.store = SQLiteStore(db_path=resolved)
        # Optional env override for config-driven toggles (e.g. graph
        # densification). ``None`` means read the live process environment.
        self._env = env
        self.store.ensure_schema()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> FleetBrain:
        """Build a brain from the public environment contract."""
        if env is None:
            return cls()
        explicit = env.get("ALFRED_FLEET_BRAIN_DB", "").strip()
        if explicit:
            return cls(db_path=Path(explicit).expanduser(), env=env)
        alfred_home = env.get("ALFRED_HOME", "").strip()
        if alfred_home:
            return cls(db_path=Path(alfred_home).expanduser() / "fleet-brain.db", env=env)
        return cls(db_path=Path.home() / ".alfred" / "fleet-brain.db", env=env)
