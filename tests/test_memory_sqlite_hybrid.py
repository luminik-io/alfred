"""Tests for the embedded SQLite hybrid memory provider.

Covers:

* lexical (FTS5/BM25) recall correctness and scope filtering;
* the write -> recall round-trip and idempotent upsert on ``memory_id``;
* ``forget_lesson`` removing a lesson from recall;
* Reciprocal Rank Fusion ordering (pure function, deterministic);
* clean degradation to lexical-only when the embedder or sqlite-vec is
  unavailable;
* the dense arm end to end when ``sqlite-vec`` is installed (skipped otherwise);
* default-provider resolution and backward-compatible lesson-writer routing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "lib"))

from memory import MemoryProvider  # noqa: E402
from memory.config import load_lesson_writer  # noqa: E402
from memory.redis_agent_memory import RedisAgentMemoryProvider  # noqa: E402
from memory.sqlite_hybrid import (  # noqa: E402
    SqliteHybridProvider,
    _reciprocal_rank_fusion,
    default_hybrid_db_path,
)


@pytest.fixture
def provider() -> SqliteHybridProvider:
    """Lexical-only in-memory provider. No on-disk side effects."""
    return SqliteHybridProvider(db_path=Path(":memory:"))


# ---------------------------------------------------------------------------
# Protocol + basic contract
# ---------------------------------------------------------------------------


def test_is_a_memory_provider(provider: SqliteHybridProvider) -> None:
    assert isinstance(provider, MemoryProvider)
    assert provider.name == "sqlite"


def test_recall_empty_store_returns_empty(provider: SqliteHybridProvider) -> None:
    assert provider.recall(query="anything") == []


# ---------------------------------------------------------------------------
# Write -> recall round-trip
# ---------------------------------------------------------------------------


def test_write_then_recall_round_trip(provider: SqliteHybridProvider) -> None:
    lesson = provider.reflect(
        codename="lucius",
        repo="acme/api",
        body="GraphQL schema lives in src/schema.graphql; tests live next to it.",
        tags=["graphql", "layout"],
    )
    out = provider.recall(query="graphql", codename="lucius", repo="acme/api")
    assert [L.id for L in out] == [lesson.id]
    assert out[0].body.startswith("GraphQL schema")
    assert out[0].tags == ["graphql", "layout"]


def test_lexical_recall_ranks_matching_lesson_first(provider: SqliteHybridProvider) -> None:
    provider.reflect(codename="c", repo="r", body="the deploy pipeline uses terraform")
    match = provider.reflect(
        codename="c", repo="r", body="rate limiting lives in the gateway module"
    )
    provider.reflect(codename="c", repo="r", body="unrelated note about logging")

    out = provider.recall(query="gateway rate limiting", codename="c", repo="r")
    assert out, "expected at least one lexical hit"
    assert out[0].id == match.id


def test_recall_scopes_by_codename_and_repo(provider: SqliteHybridProvider) -> None:
    provider.reflect(codename="lucius", repo="acme/api", body="shared token about caching")
    other = provider.reflect(codename="drake", repo="acme/web", body="shared token about caching")

    out = provider.recall(query="caching", codename="lucius", repo="acme/api")
    assert [L.codename for L in out] == ["lucius"]
    assert other.id not in {L.id for L in out}


def test_recall_no_query_returns_recency_baseline(provider: SqliteHybridProvider) -> None:
    provider.reflect(codename="c", repo="r", body="older lesson")
    newer = provider.reflect(codename="c", repo="r", body="newer lesson")
    out = provider.recall(codename="c", repo="r", limit=5)
    # No query text -> recency baseline, most-recent first, never blank.
    assert out[0].id == newer.id
    assert len(out) == 2


def test_recall_honors_limit(provider: SqliteHybridProvider) -> None:
    for i in range(6):
        provider.reflect(codename="c", repo="r", body=f"token shared lesson number {i}")
    out = provider.recall(query="shared", codename="c", repo="r", limit=3)
    assert len(out) == 3


def test_reflect_is_idempotent_on_memory_id(provider: SqliteHybridProvider) -> None:
    mid = "lesson:memory_candidate:abc123"
    provider.reflect(codename="c", repo="r", body="first version", memory_id=mid)
    provider.reflect(codename="c", repo="r", body="second version", memory_id=mid)
    stored = provider.list_lessons(limit=100)
    assert len(stored) == 1
    assert stored[0].id == mid
    assert stored[0].body == "second version"


def test_fts_query_with_special_characters_does_not_crash(provider: SqliteHybridProvider) -> None:
    provider.reflect(codename="c", repo="r", body="handle quotes and parens safely")
    # A raw issue-body-style query with FTS operator characters must not raise.
    out = provider.recall(query='"(NOT quotes) AND parens*"', codename="c", repo="r")
    assert isinstance(out, list)


# ---------------------------------------------------------------------------
# forget
# ---------------------------------------------------------------------------


def test_forget_removes_from_recall(provider: SqliteHybridProvider) -> None:
    lesson = provider.reflect(codename="c", repo="r", body="ephemeral gateway note")
    assert provider.forget_lesson(lesson.id) is True
    assert provider.recall(query="gateway", codename="c", repo="r") == []


def test_forget_blank_id_is_false(provider: SqliteHybridProvider) -> None:
    assert provider.forget_lesson("") is False
    assert provider.forget_lesson("   ") is False


def test_forget_unknown_id_is_false(provider: SqliteHybridProvider) -> None:
    assert provider.forget_lesson("does-not-exist") is False


def test_sync_lesson_round_trips(provider: SqliteHybridProvider) -> None:
    lesson = provider.reflect(codename="c", repo="r", body="a durable lesson")
    other = SqliteHybridProvider(db_path=Path(":memory:"))
    assert other.sync_lesson(lesson) is True
    assert [L.id for L in other.list_lessons(limit=10)] == [lesson.id]


# ---------------------------------------------------------------------------
# RRF fusion (pure function)
# ---------------------------------------------------------------------------


def test_rrf_lexical_only_preserves_bm25_order() -> None:
    fused = _reciprocal_rank_fusion(["a", "b", "c"], [], k=60)
    assert [lid for lid, _ in fused] == ["a", "b", "c"]


def test_rrf_promotes_ids_agreed_by_both_arms() -> None:
    # "b" is ranked low by lexical but high by dense; agreement should lift it
    # above ids that appear in only one arm.
    lexical = ["a", "x", "b"]
    dense = ["b", "y", "a"]
    fused = _reciprocal_rank_fusion(lexical, dense, k=60)
    ranked = [lid for lid, _ in fused]
    # a (ranks 1 + 3) and b (ranks 3 + 1) both appear in both arms and tie; the
    # single-arm ids x and y must come after them.
    assert set(ranked[:2]) == {"a", "b"}
    assert ranked[2:] == ["x", "y"]


def test_rrf_score_uses_k_constant() -> None:
    fused = dict(_reciprocal_rank_fusion(["a"], [], k=60))
    assert fused["a"] == pytest.approx(1.0 / 61)


# ---------------------------------------------------------------------------
# Dense-arm degradation (no daemon, no sqlite-vec)
# ---------------------------------------------------------------------------


def test_dense_requested_but_no_embedder_falls_back_to_lexical() -> None:
    # dense=True but embedder is None: the provider must still answer from the
    # lexical arm rather than failing.
    prov = SqliteHybridProvider(db_path=Path(":memory:"), dense=True, embedder=None)
    prov.reflect(codename="c", repo="r", body="lexical still works without a vector arm")
    out = prov.recall(query="lexical vector", codename="c", repo="r")
    assert out and out[0].body.startswith("lexical still works")


def test_dense_requested_but_sqlite_vec_missing_falls_back_to_lexical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import memory.sqlite_hybrid as mod

    # Simulate sqlite-vec not being importable / loadable.
    monkeypatch.setattr(mod, "_load_sqlite_vec", lambda _conn: False)

    def _embed(_text: str) -> list[float]:
        return [0.1] * 1024

    prov = SqliteHybridProvider(db_path=Path(":memory:"), dense=True, embedder=_embed)
    prov.reflect(codename="c", repo="r", body="gateway degrades cleanly to lexical")
    out = prov.recall(query="gateway", codename="c", repo="r")
    assert out and out[0].body.startswith("gateway degrades")
    assert prov.health()["dense"] is False


# ---------------------------------------------------------------------------
# Dense-arm end to end (requires the optional sqlite-vec extension)
# ---------------------------------------------------------------------------


def test_dense_arm_end_to_end_with_sqlite_vec() -> None:
    pytest.importorskip("sqlite_vec")

    # A tiny deterministic 4-d embedding space. The query embeds closest to the
    # "gateway" lesson, so the dense arm ranks it first even though its lexical
    # tokens do not overlap the query.
    space = {
        "gateway": [1.0, 0.0, 0.0, 0.0],
        "billing": [0.0, 1.0, 0.0, 0.0],
        "query": [0.98, 0.02, 0.0, 0.0],
    }

    def _embed(text: str) -> list[float]:
        low = text.lower()
        if "gateway" in low or "throttle" in low:
            return space["gateway"]
        if "billing" in low or "invoice" in low:
            return space["billing"]
        return space["query"]

    prov = SqliteHybridProvider(db_path=Path(":memory:"), dense=True, dimensions=4, embedder=_embed)
    prov.reflect(codename="c", repo="r", body="throttle limits per tenant")  # -> gateway vec
    prov.reflect(codename="c", repo="r", body="invoice generation runs nightly")  # -> billing vec

    out = prov.recall(query="how does the gateway work", codename="c", repo="r")
    assert out, "dense arm should return candidates"
    assert out[0].body.startswith("throttle limits")
    assert prov.health()["dense"] is True


# ---------------------------------------------------------------------------
# Config: default resolution + backward-compatible lesson writer
# ---------------------------------------------------------------------------


def test_lesson_writer_default_is_sqlite_hybrid() -> None:
    writer = load_lesson_writer(env={})
    assert isinstance(writer, SqliteHybridProvider)
    assert writer.name == "sqlite"


def test_lesson_writer_redis_chain_still_routes_to_redis() -> None:
    writer = load_lesson_writer(env={"ALFRED_MEMORY_PROVIDERS": "redis,fleet"})
    assert isinstance(writer, RedisAgentMemoryProvider)


def test_lesson_writer_falls_back_to_sqlite_when_no_recall_store() -> None:
    # A fleet-only (or null) chain names no dedicated recall store, so a promoted
    # lesson still lands in the zero-daemon SQLite floor rather than nowhere.
    writer = load_lesson_writer(env={"ALFRED_MEMORY_PROVIDERS": "fleet"})
    assert isinstance(writer, SqliteHybridProvider)


def test_lesson_writer_picks_first_recall_store_in_chain() -> None:
    writer = load_lesson_writer(env={"ALFRED_MEMORY_PROVIDERS": "sqlite,redis,fleet"})
    assert isinstance(writer, SqliteHybridProvider)


def test_default_hybrid_db_path_prefers_explicit_then_home(tmp_path: Path) -> None:
    explicit = tmp_path / "custom.db"
    assert default_hybrid_db_path({"ALFRED_MEMORY_SQLITE_DB": str(explicit)}) == explicit
    home = tmp_path / "alfred-home"
    assert default_hybrid_db_path({"ALFRED_HOME": str(home)}) == home / "memory-hybrid.db"


def test_from_env_reads_knobs(tmp_path: Path) -> None:
    prov = SqliteHybridProvider.from_env(
        env={
            "ALFRED_MEMORY_SQLITE_DB": str(tmp_path / "m.db"),
            "ALFRED_MEMORY_SQLITE_RRF_K": "10",
            "ALFRED_MEMORY_SQLITE_POOL": "7",
            "ALFRED_MEMORY_SQLITE_DENSE": "0",
        }
    )
    assert prov.rrf_k == 10
    assert prov.pool == 7
    assert prov.dense is False
    assert prov.embedder is None
