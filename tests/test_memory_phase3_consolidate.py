"""Phase 3 memory tests: consolidation policy + persisted reuse.

Covers the additive, gated Phase 3 layer built on top of the Phase 1 hybrid
store and the Phase 2 typed/validity/provenance lessons:

* semantic near-duplicate detection at consolidation (on top of the existing
  lexical ``_auto_dedup_key`` pass), degrading to lexical-only without an
  embedder;
* provenance-union merge in the SQLite hybrid store (``merge_lesson``): the
  survivor keeps both provenance and both anchor sets, the loser is invalidated
  (``superseded_by``), never deleted;
* pressure/budget eviction by the #452 value score (``evict_to_cap``),
  invalidate-not-delete and reversible;
* the durable reuse counter round-tripping across a simulated process boundary,
  in both the FleetBrain store and the hybrid store, and wired through
  ``memory_ranking``.

Every test runs offline: no model, no network, no operator disk (``:memory:`` or
``tmp_path``). Where the behaviour must hold with and without SQLite FTS5, the
FTS arm is forced off explicitly.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "lib"))

import fleet_brain as fb_mod  # noqa: E402
from agent_runner import memory_ranking  # noqa: E402
from fleet_brain import (  # noqa: E402
    FleetBrain,
    Lesson,
    consolidate_semantic_enabled,
    consolidate_sim_threshold,
    max_lessons_cap,
    new_id,
)
from memory.providers import ChainedMemoryProvider, FleetBrainProvider  # noqa: E402
from memory.sqlite_hybrid import SqliteHybridProvider, _union_provenance  # noqa: E402

ARM = {"ALFRED_MEMORY_CONSOLIDATE": "1"}
ARM_SEMANTIC = {"ALFRED_MEMORY_CONSOLIDATE": "1", "ALFRED_MEMORY_CONSOLIDATE_SEMANTIC": "1"}


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def test_semantic_switch_defaults_off_and_fails_closed() -> None:
    assert consolidate_semantic_enabled({}) is False
    assert consolidate_semantic_enabled({"ALFRED_MEMORY_CONSOLIDATE_SEMANTIC": "1"}) is True
    # A typo must not arm a destructive-ish merge (fail closed).
    assert consolidate_semantic_enabled({"ALFRED_MEMORY_CONSOLIDATE_SEMANTIC": "maybe"}) is False


def test_sim_threshold_clamps_bad_values_to_default() -> None:
    assert consolidate_sim_threshold({}) == pytest.approx(0.92)
    assert consolidate_sim_threshold({"ALFRED_MEMORY_CONSOLIDATE_SIM_THRESHOLD": "0.8"}) == 0.8
    for bad in ("0", "-1", "1.5", "nope", ""):
        assert consolidate_sim_threshold({"ALFRED_MEMORY_CONSOLIDATE_SIM_THRESHOLD": bad}) == (
            pytest.approx(0.92)
        )


def test_max_lessons_cap_defaults_disabled() -> None:
    assert max_lessons_cap({}) == 0
    assert max_lessons_cap({"ALFRED_MEMORY_MAX_LESSONS": "50"}) == 50
    for bad in ("0", "-5", "x", ""):
        assert max_lessons_cap({"ALFRED_MEMORY_MAX_LESSONS": bad}) == 0


# ---------------------------------------------------------------------------
# Pure helpers: cosine + semantic grouping + provenance union
# ---------------------------------------------------------------------------


def test_cosine_identical_and_orthogonal() -> None:
    assert fb_mod._cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert fb_mod._cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    # A zero vector never scores as similar (never merges by accident).
    assert fb_mod._cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0
    # Length mismatch is 0.0, not a crash.
    assert fb_mod._cosine_similarity([1.0], [1.0, 0.0]) == 0.0


def test_semantic_dup_groups_clusters_near_and_keeps_distinct_apart() -> None:
    vecs = {"a": [1.0, 0.0], "b": [0.99, 0.14], "c": [0.0, 1.0]}
    groups = fb_mod._semantic_dup_groups(
        [("a", "a"), ("b", "b"), ("c", "c")], lambda body: vecs[body], threshold=0.9
    )
    assert groups == [["a", "b"]]  # c stays a singleton, not returned


def test_semantic_dup_groups_never_merges_unembeddable_bodies() -> None:
    # An embedder that returns None for one body leaves it a singleton (degrade).
    def embedder(body: str) -> list[float] | None:
        return [1.0, 0.0] if body != "x" else None

    groups = fb_mod._semantic_dup_groups(
        [("a", "a"), ("b", "b"), ("x", "x")], embedder, threshold=0.9
    )
    assert groups == [["a", "b"]]


def test_union_provenance_dedupes_survivor_first() -> None:
    assert _union_provenance("firing-1", "firing-2") == "firing-1, firing-2"
    assert _union_provenance("firing-1, firing-2", "firing-2") == "firing-1, firing-2"
    assert _union_provenance(None, None) is None
    assert _union_provenance("", "firing-9") == "firing-9"


# ---------------------------------------------------------------------------
# SQLite hybrid: provenance-union merge (item 2)
# ---------------------------------------------------------------------------


def _fts_variants() -> list[bool]:
    return [True, False]


@pytest.mark.parametrize("force_fts_off", _fts_variants())
def test_merge_lesson_unions_provenance_and_anchors_and_invalidates_loser(
    force_fts_off: bool,
) -> None:
    provider = SqliteHybridProvider(db_path=Path(":memory:"))
    if force_fts_off:
        provider._fts_ok = False  # exercise the LIKE fallback path
    survivor = provider.reflect(
        codename="lucius",
        repo="acme/api",
        body="Use timezone-aware datetimes.",
        memory_id="keep",
        provenance="firing-keep",
        anchors=[("file", "src/a.py")],
    )
    loser = provider.reflect(
        codename="lucius",
        repo="acme/api",
        body="Store datetimes in UTC with tzinfo.",
        memory_id="dup",
        provenance="firing-dup",
        anchors=[("file", "src/b.py")],
    )

    assert provider.merge_lesson(loser.id, survivor.id) is True

    # Survivor keeps BOTH provenances and BOTH anchors.
    rows = provider.list_lessons(limit=50)
    kept = next(x for x in rows if x.id == "keep")
    assert kept.provenance == "firing-keep, firing-dup"
    survivor_anchor_refs = {a.anchor_ref for a in _anchors(provider, "keep")}
    assert {"src/a.py", "src/b.py"}.issubset(survivor_anchor_refs)
    assert "dup" in survivor_anchor_refs  # supersedes link recorded

    # Loser is invalidated, NOT deleted: recall never surfaces it, but the row
    # survives with superseded_by set (reversible audit trail).
    recalled = {L.id for L in provider.recall(query="datetimes utc timezone", codename="lucius")}
    assert "dup" not in recalled
    loser_row = _raw_lesson(provider, "dup")
    assert loser_row["superseded_by"] == "keep"
    assert loser_row["valid_until"] is not None


def test_merge_lesson_noops_on_bad_ids() -> None:
    provider = SqliteHybridProvider(db_path=Path(":memory:"))
    provider.reflect(codename="c", repo="r", body="only one", memory_id="solo")
    assert provider.merge_lesson("", "solo") is False
    assert provider.merge_lesson("solo", "solo") is False
    assert provider.merge_lesson("missing", "solo") is False


@pytest.mark.parametrize("force_fts_off", _fts_variants())
def test_merge_lesson_unions_reuse_and_clears_loser(force_fts_off: bool) -> None:
    provider = SqliteHybridProvider(db_path=Path(":memory:"))
    if force_fts_off:
        provider._fts_ok = False
    survivor = provider.reflect(codename="c", repo="r", body="keep body", memory_id="keep")
    loser = provider.reflect(codename="c", repo="r", body="dup body", memory_id="dup")
    survivor_key = memory_ranking.lesson_key(survivor, codename="c", repo="r")
    loser_key = memory_ranking.lesson_key(loser, codename="c", repo="r")
    # Survivor reused 2x, loser reused 3x.
    provider.bump_reuse_counts([survivor_key, survivor_key])
    provider.bump_reuse_counts([loser_key, loser_key, loser_key])

    assert provider.merge_lesson(loser.id, survivor.id) is True

    # Survivor now carries M + N (2 + 3); the loser's orphaned row is gone.
    assert provider.get_reuse_count(survivor_key) == 5
    assert provider.get_reuse_count(loser_key) == 0
    with provider._connect() as conn:
        (rows,) = conn.execute(
            "SELECT COUNT(*) FROM lesson_reuse WHERE scope_key = ?", (loser_key,)
        ).fetchone()
    assert rows == 0


def test_merge_lesson_reuse_union_when_survivor_has_none() -> None:
    provider = SqliteHybridProvider(db_path=Path(":memory:"))
    survivor = provider.reflect(codename="c", repo="r", body="keep", memory_id="keep")
    loser = provider.reflect(codename="c", repo="r", body="dup", memory_id="dup")
    survivor_key = memory_ranking.lesson_key(survivor, codename="c", repo="r")
    loser_key = memory_ranking.lesson_key(loser, codename="c", repo="r")
    provider.bump_reuse_counts([loser_key, loser_key])  # only the loser was reused

    assert provider.merge_lesson(loser.id, survivor.id) is True

    assert provider.get_reuse_count(survivor_key) == 2
    assert provider.get_reuse_count(loser_key) == 0


def test_fleet_store_union_reuse_counts_moves_and_clears() -> None:
    brain = FleetBrain(db_path=Path(":memory:"))
    brain.bump_reuse_counts(["survivor", "survivor"])
    brain.bump_reuse_counts(["loser", "loser", "loser"])

    brain.union_reuse_counts("survivor", "loser")

    assert brain.get_reuse_count("survivor") == 5
    assert brain.get_reuse_count("loser") == 0


def test_union_reuse_counts_noop_on_bad_keys() -> None:
    brain = FleetBrain(db_path=Path(":memory:"))
    brain.bump_reuse_counts(["a"])
    brain.union_reuse_counts("a", "a")  # identical -> no-op
    brain.union_reuse_counts("a", "")  # blank loser -> no-op
    assert brain.get_reuse_count("a") == 1


def test_scope_key_matches_lesson_key_by_id() -> None:
    lesson = Lesson(
        id="L9",
        codename="c",
        repo="r",
        body="b",
        tags=[],
        created_at=datetime.now(UTC),
        firing_id=None,
    )
    assert memory_ranking.scope_key(lesson_id="L9", codename="c", repo="r") == (
        memory_ranking.lesson_key(lesson, codename="c", repo="r")
    )


# ---------------------------------------------------------------------------
# SQLite hybrid: pressure/budget eviction by value (item 3)
# ---------------------------------------------------------------------------


def test_evict_to_cap_removes_lowest_value_lessons() -> None:
    provider = SqliteHybridProvider(db_path=Path(":memory:"))
    now = datetime(2026, 6, 1, tzinfo=UTC)
    # A high-severity fresh lesson (high value) and two low-value info lessons,
    # one older than the other. The cap of 1 must keep the blocker and evict the
    # two info lessons, oldest-lowest first.
    provider.reflect(
        codename="c",
        repo="r",
        body="blocker fresh",
        memory_id="hi",
        severity="blocker",
        created_at=now - timedelta(days=1),
    )
    provider.reflect(
        codename="c",
        repo="r",
        body="info old",
        memory_id="lo-old",
        severity="info",
        created_at=now - timedelta(days=120),
    )
    provider.reflect(
        codename="c",
        repo="r",
        body="info newer",
        memory_id="lo-new",
        severity="info",
        created_at=now - timedelta(days=60),
    )

    evicted = provider.evict_to_cap(max_lessons=1, now=now)

    assert set(evicted) == {"lo-old", "lo-new"}
    # The blocker survives; the two info lessons are invalidated-not-deleted.
    live = {
        L.id
        for L in provider.list_lessons(limit=50)
        if _raw_lesson(provider, L.id)["valid_until"] is None
    }
    assert live == {"hi"}
    # Rows survive (reversible): clearing valid_until would restore them.
    assert _raw_lesson(provider, "lo-old")["valid_until"] is not None
    assert _raw_lesson(provider, "lo-old")["superseded_by"] is None  # eviction, not supersede


def test_evict_to_cap_respects_persisted_reuse() -> None:
    provider = SqliteHybridProvider(db_path=Path(":memory:"))
    now = datetime(2026, 6, 1, tzinfo=UTC)
    a = provider.reflect(codename="c", repo="r", body="lesson a", memory_id="a", created_at=now)
    b = provider.reflect(codename="c", repo="r", body="lesson b", memory_id="b", created_at=now)
    # Reinforce "a" heavily so its value beats the otherwise-identical "b".
    key = memory_ranking.lesson_key(a, codename="c", repo="r")
    provider.bump_reuse_counts([key] * 5)

    evicted = provider.evict_to_cap(max_lessons=1, now=now)

    assert evicted == ["b"]  # the un-reused lesson is the lowest value
    assert b.id  # (silence unused)


def test_evict_to_cap_disabled_and_dry_run() -> None:
    provider = SqliteHybridProvider(db_path=Path(":memory:"))
    now = datetime(2026, 6, 1, tzinfo=UTC)
    for i in range(3):
        provider.reflect(codename="c", repo="r", body=f"l{i}", memory_id=f"l{i}", created_at=now)
    # Cap of 0 (disabled) never evicts.
    assert provider.evict_to_cap(max_lessons=0, now=now) == []
    # Dry-run reports what WOULD go without writing.
    would = provider.evict_to_cap(max_lessons=1, now=now, dry_run=True)
    assert len(would) == 2
    assert all(_raw_lesson(provider, f"l{i}")["valid_until"] is None for i in range(3))


# ---------------------------------------------------------------------------
# Consolidate: semantic near-duplicate merge wiring (item 1 + 2)
# ---------------------------------------------------------------------------


class _FakeMergeStore:
    """Recall store stub that supports the Phase 3 provenance-union merge.

    Records ``merge_lesson`` / ``forget_lesson`` calls so a consolidate test can
    assert the survivor/loser pairing without a full hybrid DB."""

    name = "redis"

    def __init__(self) -> None:
        self.merged: list[tuple[str, str]] = []
        self.forgotten: list[str] = []

    def reflect(self, *, codename, repo, body, memory_id=None, **_kw) -> Lesson:  # type: ignore[no-untyped-def]
        return Lesson(
            id=memory_id or new_id(),
            codename=codename,
            repo=repo,
            body=body.strip(),
            tags=[],
            created_at=datetime.now(UTC),
            firing_id=None,
            severity="info",
        )

    def forget_lesson(self, lesson_id: str) -> bool:
        self.forgotten.append(lesson_id)
        return True

    def merge_lesson(self, loser_id: str, survivor_id: str) -> bool:
        self.merged.append((loser_id, survivor_id))
        return True


def _brain_with(store: object, tmp_path: Path) -> FleetBrain:
    fb = FleetBrain(db_path=tmp_path / "brain.db")
    fb._lesson_provider = lambda env=None: store  # type: ignore[method-assign]
    return fb


def _promote_auto(brain: FleetBrain, body: str, *, created_at: datetime) -> str:
    cand = brain.propose_memory(
        codename="lucius", repo="acme/api", body=body, evidence="saw it", created_at=created_at
    )
    brain.promote_memory_candidate(cand.id, reviewer="auto", reviewed_at=created_at)
    return cand.id


def _lesson_id(cid: str) -> str:
    return f"lesson:memory_candidate:{cid}"


def test_consolidate_semantic_merges_near_duplicates(tmp_path: Path) -> None:
    store = _FakeMergeStore()
    brain = _brain_with(store, tmp_path)
    older = datetime.now(UTC) - timedelta(days=3)
    newer = datetime.now(UTC) - timedelta(days=1)
    # Two bodies that are NOT lexically identical (lexical dedup keeps them apart)
    # but embed to the same vector -> the semantic pass merges them.
    keep = _promote_auto(brain, "Use timezone-aware datetimes.", created_at=older)
    dup = _promote_auto(brain, "Store datetimes in UTC with tzinfo.", created_at=newer)
    vecs = {
        "Use timezone-aware datetimes.": [1.0, 0.0],
        "Store datetimes in UTC with tzinfo.": [1.0, 0.0],
    }

    summary = brain.consolidate_lessons(
        env=ARM_SEMANTIC, lesson_forgetter=store, embedder=lambda b: vecs.get(b)
    )

    assert summary["merged"] == 1
    assert summary["provenance_unioned"] == 1
    # The newer copy is merged INTO the older survivor via the union path.
    assert store.merged == [(_lesson_id(dup), _lesson_id(keep))]
    assert store.forgotten == []  # union path, not a plain forget
    assert brain.store.get_memory_candidate(dup).status == "retired"  # type: ignore[union-attr]
    assert brain.store.get_memory_candidate(keep).status == "validated"  # type: ignore[union-attr]


def test_consolidate_degrades_to_lexical_only_without_embedder(tmp_path: Path) -> None:
    store = _FakeMergeStore()
    brain = _brain_with(store, tmp_path)
    older = datetime.now(UTC) - timedelta(days=3)
    newer = datetime.now(UTC) - timedelta(days=1)
    # Near-dup but lexically DIFFERENT: with no embedder the semantic pass cannot
    # run, so nothing merges (byte-identical to the pre-Phase-3 behaviour).
    _promote_auto(brain, "Use timezone-aware datetimes.", created_at=older)
    _promote_auto(brain, "Store datetimes in UTC with tzinfo.", created_at=newer)

    summary = brain.consolidate_lessons(env=ARM_SEMANTIC, lesson_forgetter=store, embedder=None)

    assert summary["merged"] == 0
    assert store.merged == []


def test_consolidate_semantic_disarmed_leaves_near_dups(tmp_path: Path) -> None:
    store = _FakeMergeStore()
    brain = _brain_with(store, tmp_path)
    older = datetime.now(UTC) - timedelta(days=3)
    newer = datetime.now(UTC) - timedelta(days=1)
    _promote_auto(brain, "Use timezone-aware datetimes.", created_at=older)
    _promote_auto(brain, "Store datetimes in UTC with tzinfo.", created_at=newer)
    vecs = {
        "Use timezone-aware datetimes.": [1.0, 0.0],
        "Store datetimes in UTC with tzinfo.": [1.0, 0.0],
    }

    # Semantic switch OFF (only the base consolidate arm): embedder present but
    # unused, so lexical-only -> no merge.
    summary = brain.consolidate_lessons(
        env=ARM, lesson_forgetter=store, embedder=lambda b: vecs.get(b)
    )
    assert summary["merged"] == 0


def test_consolidate_lexical_merge_falls_back_to_forget(tmp_path: Path) -> None:
    """A store without merge_lesson (Redis AMS) keeps the pre-Phase-3 forget."""

    class _ForgetOnly:
        name = "redis"

        def __init__(self) -> None:
            self.forgotten: list[str] = []

        def reflect(self, *, codename, repo, body, memory_id=None, **_kw) -> Lesson:  # type: ignore[no-untyped-def]
            return Lesson(
                id=memory_id or new_id(),
                codename=codename,
                repo=repo,
                body=body.strip(),
                tags=[],
                created_at=datetime.now(UTC),
                firing_id=None,
                severity="info",
            )

        def forget_lesson(self, lesson_id: str) -> bool:
            self.forgotten.append(lesson_id)
            return True

    store = _ForgetOnly()
    brain = _brain_with(store, tmp_path)
    older = datetime.now(UTC) - timedelta(days=3)
    newer = datetime.now(UTC) - timedelta(days=1)
    keep = _promote_auto(brain, "Same body.", created_at=older)
    dup = _promote_auto(brain, "same body.", created_at=newer)  # lexical dup

    summary = brain.consolidate_lessons(env=ARM, lesson_forgetter=store)

    assert summary["merged"] == 1
    assert summary["provenance_unioned"] == 0  # no union capability -> forget path
    assert _lesson_id(dup) in store.forgotten
    assert _lesson_id(keep) not in store.forgotten


def test_consolidate_eviction_wired_to_provider(tmp_path: Path) -> None:
    provider = SqliteHybridProvider(db_path=Path(":memory:"))
    now = datetime.now(UTC)
    for i in range(4):
        provider.reflect(codename="c", repo="r", body=f"l{i}", memory_id=f"l{i}", created_at=now)
    brain = FleetBrain(db_path=tmp_path / "brain.db")

    summary = brain.consolidate_lessons(
        env={**ARM, "ALFRED_MEMORY_MAX_LESSONS": "2"}, lesson_forgetter=provider
    )

    assert summary["evicted"] == 2
    live = [
        L
        for L in provider.list_lessons(limit=50)
        if _raw_lesson(provider, L.id)["valid_until"] is None
    ]
    assert len(live) == 2


# ---------------------------------------------------------------------------
# Persisted reuse counter (item 4)
# ---------------------------------------------------------------------------


def test_fleet_store_reuse_roundtrip_across_process_boundary(tmp_path: Path) -> None:
    db = tmp_path / "brain.db"
    first = FleetBrain(db_path=db)
    first.bump_reuse_counts(["scope-a", "scope-a", "scope-b"])
    # Simulate a process restart: a brand-new brain over the same file.
    second = FleetBrain(db_path=db)
    assert second.get_reuse_count("scope-a") == 2
    assert second.get_reuse_count("scope-b") == 1
    assert second.get_reuse_count("never") == 0


def test_hybrid_reuse_roundtrip_across_process_boundary(tmp_path: Path) -> None:
    db = tmp_path / "hybrid.db"
    first = SqliteHybridProvider(db_path=db)
    first.bump_reuse_counts(["k1", "k1", "k1"])
    second = SqliteHybridProvider(db_path=db)
    assert second.get_reuse_count("k1") == 3
    assert second.get_reuse_count("missing") == 0


def test_memory_ranking_persists_reuse_across_simulated_restart(tmp_path: Path) -> None:
    lesson = Lesson(
        id="L1",
        codename="lucius",
        repo="acme/api",
        body="a lesson",
        tags=[],
        created_at=datetime.now(UTC),
        firing_id=None,
    )
    store = SqliteHybridProvider(db_path=tmp_path / "hybrid.db")
    try:
        memory_ranking.set_reuse_store(store)
        memory_ranking.record_reuse([lesson], codename="lucius", repo="acme/api")
        memory_ranking.record_reuse([lesson], codename="lucius", repo="acme/api")
        # Simulate a new process: drop the in-process cache, re-open the store.
        memory_ranking.reset_reuse_state()  # clears cache AND unbinds the store
        reopened = SqliteHybridProvider(db_path=tmp_path / "hybrid.db")
        memory_ranking.set_reuse_store(reopened)
        assert memory_ranking.reuse_count(lesson, codename="lucius", repo="acme/api") == 2
    finally:
        memory_ranking.reset_reuse_state()


def test_memory_ranking_without_store_stays_in_process(tmp_path: Path) -> None:
    lesson = Lesson(
        id="L2",
        codename="c",
        repo="r",
        body="b",
        tags=[],
        created_at=datetime.now(UTC),
        firing_id=None,
    )
    try:
        memory_ranking.reset_reuse_state()  # no store bound -> legacy path
        memory_ranking.record_reuse([lesson], codename="c", repo="r")
        assert memory_ranking.reuse_count(lesson, codename="c", repo="r") == 1
        # A fresh "process" with no store and a cleared cache forgets it.
        memory_ranking.reset_reuse_state()
        assert memory_ranking.reuse_count(lesson, codename="c", repo="r") == 0
    finally:
        memory_ranking.reset_reuse_state()


# ---------------------------------------------------------------------------
# reuse_store_for: discovery across provider shapes
# ---------------------------------------------------------------------------


def test_reuse_store_for_finds_hybrid_directly(tmp_path: Path) -> None:
    provider = SqliteHybridProvider(db_path=tmp_path / "h.db")
    assert memory_ranking.reuse_store_for(provider) is provider


def test_reuse_store_for_finds_fleet_brain(tmp_path: Path) -> None:
    provider = FleetBrainProvider(brain=FleetBrain(db_path=tmp_path / "b.db"))
    found = memory_ranking.reuse_store_for(provider)
    assert found is provider.brain


def test_reuse_store_for_walks_a_chain(tmp_path: Path) -> None:
    hybrid = SqliteHybridProvider(db_path=tmp_path / "h.db")
    fleet = FleetBrainProvider(brain=FleetBrain(db_path=tmp_path / "b.db"))
    chain = ChainedMemoryProvider(providers=[fleet, hybrid])
    # The first reuse-capable member wins (FleetBrain here).
    assert memory_ranking.reuse_store_for(chain) is fleet.brain


def test_reuse_store_for_none_when_no_capable_member() -> None:
    class _Dumb:
        name = "dumb"

    assert memory_ranking.reuse_store_for(_Dumb()) is None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _anchors(provider: SqliteHybridProvider, lesson_id: str) -> list:
    with provider._connect() as conn:
        rows = conn.execute(
            "SELECT anchor_ref FROM lesson_anchors WHERE lesson_id = ?", (lesson_id,)
        ).fetchall()

    class _A:
        def __init__(self, ref: str) -> None:
            self.anchor_ref = ref

    return [_A(r[0]) for r in rows]


def _raw_lesson(provider: SqliteHybridProvider, lesson_id: str) -> dict:
    with provider._connect() as conn:
        row = conn.execute(
            "SELECT superseded_by, valid_until FROM lessons WHERE id = ?", (lesson_id,)
        ).fetchone()
    return {"superseded_by": row[0], "valid_until": row[1]} if row else {}
