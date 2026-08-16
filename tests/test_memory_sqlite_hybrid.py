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

import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "lib"))

import memory.sqlite_hybrid as mod  # noqa: E402
from fleet_brain import FleetBrain, Lesson, MemoryPromotionError  # noqa: E402
from memory import MemoryProvider  # noqa: E402
from memory.config import load_lesson_writer, load_provider  # noqa: E402
from memory.providers import ChainedMemoryProvider, FleetBrainProvider  # noqa: E402
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


def test_lexical_recall_keeps_compound_single_character_terms(
    provider: SqliteHybridProvider,
) -> None:
    match = provider.reflect(
        codename="c",
        repo="r",
        body="The reporting endpoint had an N+1 query. Use one batch query.",
    )
    provider.reflect(codename="c", repo="r", body="Unrelated release checklist")

    out = provider.recall(query="N+1", codename="c", repo="r")

    assert out[0].id == match.id


@pytest.mark.parametrize(
    "query",
    ["C", "R", "C++", "C#", "F#", "N+12", "O(n)", "O(log n)", "O(42)", "HTTP/2.1", "I/O", "A/B"],
)
def test_default_chain_round_trips_symbolic_technical_terms(
    tmp_path: Path,
    query: str,
) -> None:
    env = {
        "ALFRED_HOME": str(tmp_path / "alfred-home"),
        "ALFRED_MEMORY_SQLITE_DB": str(tmp_path / "memory.db"),
    }
    writer = load_lesson_writer(env)
    assert writer is not None
    lesson = writer.reflect(
        codename="c",
        repo="r",
        body=f"Use {query} carefully in the request path.",
    )

    out = load_provider(env).recall(query=query, codename="c", repo="r")

    assert [item.id for item in out] == [lesson.id]


def test_default_chain_requires_symbolic_query_identity(tmp_path: Path) -> None:
    env = {
        "ALFRED_HOME": str(tmp_path / "alfred-home"),
        "ALFRED_MEMORY_SQLITE_DB": str(tmp_path / "memory.db"),
    }
    writer = load_lesson_writer(env)
    assert writer is not None
    writer.reflect(codename="c", repo="r", body="Fix C# compiler warnings")
    relevant = writer.reflect(codename="c", repo="r", body="Fix C++ compiler warnings")

    out = load_provider(env).recall(
        query="Fix C++ compiler warnings",
        codename="c",
        repo="r",
    )

    assert [item.id for item in out] == [relevant.id]


def test_default_chain_round_trips_japanese_issue_title(tmp_path: Path) -> None:
    env = {
        "ALFRED_HOME": str(tmp_path / "alfred-home"),
        "ALFRED_MEMORY_SQLITE_DB": str(tmp_path / "memory.db"),
    }
    query = "認証エラーを修正"
    writer = load_lesson_writer(env)
    assert writer is not None
    lesson = writer.reflect(codename="c", repo="r", body=f"手順: {query}してください")

    out = load_provider(env).recall(query=query, codename="c", repo="r")

    assert [item.id for item in out] == [lesson.id]


def test_default_chain_bounds_token_empty_literal_lookup_and_keeps_scope(tmp_path: Path) -> None:
    env = {
        "ALFRED_HOME": str(tmp_path / "alfred-home"),
        "ALFRED_MEMORY_SQLITE_DB": str(tmp_path / "memory.db"),
        "ALFRED_MEMORY_SQLITE_POOL": "1000",
    }
    writer = load_lesson_writer(env)
    assert writer is not None
    for index in range(401):
        writer.reflect(codename="c", repo="r", body=f"修 {index}")
    writer.reflect(codename="c", repo="other", body="修 out of scope")

    out = load_provider(env).recall(query="修", codename="c", repo="r", limit=1000)

    assert len(out) == 400
    assert all(item.repo == "r" and "修" in item.body for item in out)


def test_default_chain_keeps_generic_low_signal_queries_as_hard_misses(tmp_path: Path) -> None:
    env = {
        "ALFRED_HOME": str(tmp_path / "alfred-home"),
        "ALFRED_MEMORY_SQLITE_DB": str(tmp_path / "memory.db"),
    }
    writer = load_lesson_writer(env)
    assert writer is not None
    writer.reflect(codename="c", repo="r", body="Fix the release process.")

    assert load_provider(env).recall(query="fix", codename="c", repo="r") == []


@pytest.mark.parametrize(
    ("query", "lesson_body"),
    [
        ("Fix cold-start handling", "Use cold start handling in the request path."),
        ("Fix cold start handling", "Use cold-start handling in the request path."),
    ],
)
def test_default_chain_matches_hyphenated_spelling_variants(
    tmp_path: Path,
    query: str,
    lesson_body: str,
) -> None:
    env = {
        "ALFRED_HOME": str(tmp_path / "alfred-home"),
        "ALFRED_MEMORY_SQLITE_DB": str(tmp_path / "memory.db"),
    }
    writer = load_lesson_writer(env)
    assert writer is not None
    lesson = writer.reflect(codename="c", repo="r", body=lesson_body)

    out = load_provider(env).recall(query=query, codename="c", repo="r")

    assert [item.id for item in out] == [lesson.id]


def test_default_chain_falls_through_to_fleet_for_identifier_concepts(tmp_path: Path) -> None:
    env = {
        "ALFRED_HOME": str(tmp_path / "alfred-home"),
        "ALFRED_MEMORY_SQLITE_DB": str(tmp_path / "memory.db"),
    }
    lesson_body = "The task id schema is validated by the API serializer"
    brain = FleetBrain.from_env(env)
    lesson = brain.reflect(codename="c", repo="r", body=lesson_body)

    out = load_provider(env).recall(
        query="Fix the task_id schema in the API serializer",
        codename="c",
        repo="r",
    )

    assert [item.id for item in out] == [lesson.id]


def test_default_chain_requires_mixed_query_unicode_subject(tmp_path: Path) -> None:
    env = {
        "ALFRED_HOME": str(tmp_path / "alfred-home"),
        "ALFRED_MEMORY_SQLITE_DB": str(tmp_path / "memory.db"),
    }
    writer = load_lesson_writer(env)
    assert writer is not None
    writer.reflect(codename="c", repo="r", body="The API client retries requests")
    relevant = FleetBrain.from_env(env).reflect(
        codename="c",
        repo="r",
        body="API の課金エラーを修正する手順",
    )

    out = load_provider(env).recall(
        query="API の課金エラーを修正",
        codename="c",
        repo="r",
    )

    assert [item.id for item in out] == [relevant.id]


def test_default_chain_requires_single_character_unicode_subject(tmp_path: Path) -> None:
    env = {
        "ALFRED_HOME": str(tmp_path / "alfred-home"),
        "ALFRED_MEMORY_SQLITE_DB": str(tmp_path / "memory.db"),
    }
    writer = load_lesson_writer(env)
    assert writer is not None
    writer.reflect(codename="c", repo="r", body="API billing guidance")
    relevant = writer.reflect(codename="c", repo="r", body="API 税 guidance")

    out = load_provider(env).recall(query="API 税", codename="c", repo="r")

    assert [item.id for item in out] == [relevant.id]


def test_default_chain_matches_singular_lesson_for_plural_query(tmp_path: Path) -> None:
    env = {
        "ALFRED_HOME": str(tmp_path / "alfred-home"),
        "ALFRED_MEMORY_SQLITE_DB": str(tmp_path / "memory.db"),
    }
    writer = load_lesson_writer(env)
    assert writer is not None
    writer.reflect(codename="c", repo="r", body="GraphQL resolver guidance")
    relevant = writer.reflect(codename="c", repo="r", body="GraphQL schema guidance")

    out = load_provider(env).recall(query="Fix GraphQL schemas", codename="c", repo="r")

    assert [item.id for item in out] == [relevant.id]


def test_default_chain_does_not_require_ordinary_slash_path(tmp_path: Path) -> None:
    env = {
        "ALFRED_HOME": str(tmp_path / "alfred-home"),
        "ALFRED_MEMORY_SQLITE_DB": str(tmp_path / "memory.db"),
    }
    writer = load_lesson_writer(env)
    assert writer is not None
    writer.reflect(codename="c", repo="r", body="GraphQL resolver guidance")
    relevant = writer.reflect(codename="c", repo="r", body="GraphQL schema guidance")

    out = load_provider(env).recall(
        query="Fix src/api GraphQL schema",
        codename="c",
        repo="r",
    )

    assert [item.id for item in out] == [relevant.id]


@pytest.mark.parametrize(
    "query",
    ["C", "R", "C++", "C#", "F#", "N+12", "O(n)", "O(log n)", "O(42)", "HTTP/2.1", "I/O", "A/B"],
)
def test_like_fallback_recalls_symbolic_technical_terms(
    monkeypatch: pytest.MonkeyPatch,
    query: str,
) -> None:
    monkeypatch.setattr(mod.SqliteHybridProvider, "_try_create_fts", lambda self, conn: False)
    provider = SqliteHybridProvider(db_path=Path(":memory:"), pool=2)
    lesson = provider.reflect(codename="c", repo="r", body=f"Prefer {query} for this case.")

    out = provider.recall(query=query, codename="c", repo="r")

    assert [item.id for item in out] == [lesson.id]


def test_symbolic_query_distinguishes_punctuation_collision(
    provider: SqliteHybridProvider,
) -> None:
    provider.reflect(codename="c", repo="r", body="Use C# for the client")
    matching = provider.reflect(codename="c", repo="r", body="Use C++ for the client")

    out = provider.recall(query="C++", codename="c", repo="r")

    assert [item.id for item in out] == [matching.id]


def test_like_fallback_recalls_unicode_query(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod.SqliteHybridProvider, "_try_create_fts", lambda self, conn: False)
    provider = SqliteHybridProvider(db_path=Path(":memory:"), pool=2)
    query = "認証エラーを修正"
    lesson = provider.reflect(codename="c", repo="r", body=f"手順: {query}してください")

    out = provider.recall(query=query, codename="c", repo="r")

    assert [item.id for item in out] == [lesson.id]


def test_token_empty_literal_lookup_escapes_like_metacharacters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mod.SqliteHybridProvider, "_try_create_fts", lambda self, conn: False)
    provider = SqliteHybridProvider(db_path=Path(":memory:"), pool=2)
    exact = provider.reflect(codename="c", repo="r", body="修_%")
    provider.reflect(codename="c", repo="r", body="修AX")

    out = provider.recall(query="修_%", codename="c", repo="r")

    assert [item.id for item in out] == [exact.id]


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


def test_unfiltered_recall_does_not_search_with_scope_text(
    provider: SqliteHybridProvider,
) -> None:
    older = provider.reflect(codename="c", repo="acme/api", body="acme api legacy lesson")
    newer = provider.reflect(codename="c", repo="acme/api", body="newest scoped lesson")

    out = provider.recall(codename="c", repo="acme/api", limit=5)

    assert [lesson.id for lesson in out] == [newer.id, older.id]


def test_recall_query_miss_does_not_inject_unrelated_recent_lessons(
    provider: SqliteHybridProvider,
) -> None:
    provider.reflect(codename="c", repo="r", body="Always use UTC for stored timestamps")

    out = provider.recall(query="GraphQL batching policy", codename="c", repo="r")

    assert out == []


def test_recall_ignores_shared_low_signal_query_words(
    provider: SqliteHybridProvider,
) -> None:
    provider.reflect(codename="c", repo="r", body="Use the fixture factory")

    out = provider.recall(
        query="Fix the GraphQL schema loader so nested unions resolve on cold start",
        codename="c",
        repo="r",
    )

    assert out == []


def test_recall_requires_two_meaningful_terms_for_multiword_queries(
    provider: SqliteHybridProvider,
) -> None:
    provider.reflect(codename="c", repo="r", body="The schema uses the fixture factory")
    relevant = provider.reflect(
        codename="c",
        repo="r",
        body="The GraphQL schema loader caches nested unions",
    )

    out = provider.recall(
        query="Fix the GraphQL schema loader so nested unions resolve on cold start",
        codename="c",
        repo="r",
    )

    assert [lesson.id for lesson in out] == [relevant.id]


def test_recall_matches_singular_lesson_for_plural_query(
    provider: SqliteHybridProvider,
) -> None:
    provider.reflect(codename="c", repo="r", body="GraphQL resolver guidance")
    relevant = provider.reflect(codename="c", repo="r", body="GraphQL schema guidance")

    out = provider.recall(query="Fix GraphQL schemas", codename="c", repo="r")

    assert [lesson.id for lesson in out] == [relevant.id]


def test_recall_does_not_require_ordinary_slash_path(
    provider: SqliteHybridProvider,
) -> None:
    provider.reflect(codename="c", repo="r", body="GraphQL resolver guidance")
    relevant = provider.reflect(codename="c", repo="r", body="GraphQL schema guidance")

    out = provider.recall(query="Fix src/api GraphQL schema", codename="c", repo="r")

    assert [lesson.id for lesson in out] == [relevant.id]


def test_recall_requires_one_character_language_identity(
    provider: SqliteHybridProvider,
) -> None:
    provider.reflect(codename="c", repo="r", body="Fix R compiler warnings")
    relevant = provider.reflect(codename="c", repo="r", body="Fix C compiler warnings")

    out = provider.recall(query="Fix C compiler warnings", codename="c", repo="r")

    assert [lesson.id for lesson in out] == [relevant.id]


def test_recall_requires_symbolic_query_identity(
    provider: SqliteHybridProvider,
) -> None:
    provider.reflect(codename="c", repo="r", body="Fix C# compiler warnings")
    relevant = provider.reflect(codename="c", repo="r", body="Fix C++ compiler warnings")

    out = provider.recall(query="Fix C++ compiler warnings", codename="c", repo="r")

    assert [lesson.id for lesson in out] == [relevant.id]


def test_recall_requires_unicode_subject_in_mixed_query(provider: SqliteHybridProvider) -> None:
    provider.reflect(codename="c", repo="r", body="The API client retries requests")
    relevant = provider.reflect(
        codename="c",
        repo="r",
        body="API の課金エラーを修正する手順",
    )

    out = provider.recall(query="API の課金エラーを修正", codename="c", repo="r")

    assert [lesson.id for lesson in out] == [relevant.id]


def test_recall_requires_devanagari_subject_in_mixed_query(
    provider: SqliteHybridProvider,
) -> None:
    provider.reflect(codename="c", repo="r", body="The API client retries requests")
    relevant = provider.reflect(
        codename="c",
        repo="r",
        body="API डेटा मिटाएँ प्रक्रिया",
    )

    out = provider.recall(query="API डेटा मिटाएँ", codename="c", repo="r")

    assert [lesson.id for lesson in out] == [relevant.id]


def test_recall_requires_single_character_unicode_subject(
    provider: SqliteHybridProvider,
) -> None:
    provider.reflect(codename="c", repo="r", body="API billing guidance")
    relevant = provider.reflect(codename="c", repo="r", body="API 税 guidance")

    out = provider.recall(query="API 税", codename="c", repo="r")

    assert [lesson.id for lesson in out] == [relevant.id]


@pytest.mark.parametrize("force_like_fallback", [False, True])
def test_recall_matches_unicode_subject_stored_only_in_tag(
    monkeypatch: pytest.MonkeyPatch,
    force_like_fallback: bool,
) -> None:
    if force_like_fallback:
        monkeypatch.setattr(
            mod.SqliteHybridProvider,
            "_try_create_fts",
            lambda self, conn: False,
        )
    provider = SqliteHybridProvider(db_path=Path(":memory:"), pool=2)
    relevant = provider.reflect(
        codename="c",
        repo="r",
        body="API billing guidance",
        tags=["課金"],
    )

    out = provider.recall(query="API 課金", codename="c", repo="r")

    assert [lesson.id for lesson in out] == [relevant.id]


def test_like_fallback_applies_overlap_before_candidate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mod.SqliteHybridProvider, "_try_create_fts", lambda self, conn: False)
    provider = SqliteHybridProvider(db_path=Path(":memory:"), pool=2)
    relevant = provider.reflect(
        codename="c",
        repo="r",
        body="The GraphQL schema defines nested unions",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    provider.reflect(
        codename="c",
        repo="r",
        body="GraphQL resolvers use batching",
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    provider.reflect(
        codename="c",
        repo="r",
        body="Schema migrations run before deploy",
        created_at=datetime(2026, 1, 3, tzinfo=UTC),
    )

    out = provider.recall(query="GraphQL schema", codename="c", repo="r")

    assert [lesson.id for lesson in out] == [relevant.id]


def test_like_fallback_pages_past_substring_only_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mod.SqliteHybridProvider, "_try_create_fts", lambda self, conn: False)
    provider = SqliteHybridProvider(db_path=Path(":memory:"), pool=2)
    relevant = provider.reflect(
        codename="c",
        repo="r",
        body="The API rapid response policy is documented",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    for day in (2, 3):
        provider.reflect(
            codename="c",
            repo="r",
            body=f"Rapid rollout note {day}",
            created_at=datetime(2026, 1, day, tzinfo=UTC),
        )

    out = provider.recall(query="api rapid", codename="c", repo="r")

    assert [lesson.id for lesson in out] == [relevant.id]


@pytest.mark.parametrize("pool", [1, 2])
def test_like_fallback_candidate_page_is_independent_of_result_pool(
    monkeypatch: pytest.MonkeyPatch,
    pool: int,
) -> None:
    monkeypatch.setattr(mod.SqliteHybridProvider, "_try_create_fts", lambda self, conn: False)
    provider = SqliteHybridProvider(db_path=Path(":memory:"), pool=pool)
    relevant = provider.reflect(
        codename="c",
        repo="r",
        body="The API rapid response policy is documented",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    for index in range(pool * 8):
        provider.reflect(
            codename="c",
            repo="r",
            body=f"Rapid rollout note {index}",
            created_at=datetime(2026, 1, 2, tzinfo=UTC) + timedelta(seconds=index),
        )

    assert provider._memory_conn is not None
    statements: list[str] = []
    provider._memory_conn.set_trace_callback(statements.append)

    out = provider.recall(query="api rapid", codename="c", repo="r")

    candidate_queries = [
        statement
        for statement in statements
        if statement.startswith("SELECT l.id") and "FROM lessons l WHERE" in statement
    ]
    assert [lesson.id for lesson in out] == [relevant.id]
    assert len(candidate_queries) == 1
    assert "LIMIT 50" in candidate_queries[0]


def test_like_fallback_stops_after_eight_candidate_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mod.SqliteHybridProvider, "_try_create_fts", lambda self, conn: False)
    provider = SqliteHybridProvider(db_path=Path(":memory:"), pool=2)
    for index in range(450):
        provider.reflect(
            codename="c",
            repo="r",
            body=f"Rapid rollout note {index}",
            created_at=datetime(2026, 1, 2, tzinfo=UTC),
        )

    assert provider._memory_conn is not None
    statements: list[str] = []
    provider._memory_conn.set_trace_callback(statements.append)

    out = provider.recall(query="api rapid", codename="c", repo="r")

    candidate_queries = [
        statement
        for statement in statements
        if statement.startswith("SELECT l.id") and "FROM lessons l WHERE" in statement
    ]
    assert out == []
    assert len(candidate_queries) == 8
    assert all("LIMIT 50" in statement for statement in candidate_queries)
    assert all(" OFFSET " not in statement for statement in candidate_queries)


def test_tokenize_drops_low_signal_words_and_keeps_domain_terms() -> None:
    assert mod._tokenize("Fix the GraphQL schema with the loader") == [
        "graphql",
        "schema",
        "loader",
    ]


def test_tokenize_mixed_query_emits_unicode_subject_concepts() -> None:
    tokens = mod._tokenize("API の課金エラーを修正")

    assert "api" in tokens
    assert {"課金", "エラー", "修正"}.issubset(tokens)
    assert not mod._has_meaningful_lexical_overlap("The API client retries requests", tokens)
    assert mod._has_meaningful_lexical_overlap("API の課金エラーを修正する手順", tokens)


def test_tokenize_keeps_devanagari_combining_marks_in_subject_concepts() -> None:
    tokens = mod._tokenize("API डेटा मिटाएँ")

    assert {"api", "डेटा", "मिटाएँ"}.issubset(tokens)
    assert not mod._has_meaningful_lexical_overlap("API billing guidance", tokens)
    assert mod._has_meaningful_lexical_overlap("API डेटा मिटाएँ प्रक्रिया", tokens)


def test_tokenize_keeps_single_character_unicode_only_in_mixed_queries() -> None:
    mixed_tokens = mod._tokenize("API 税")

    assert mixed_tokens == ["api", "税"]
    assert not mod._has_meaningful_lexical_overlap("API billing guidance", mixed_tokens)
    assert mod._has_meaningful_lexical_overlap("API 税 guidance", mixed_tokens)
    assert mod._tokenize("税") == []
    assert mod._literal_fallback_query("税") == "税"
    assert mod._tokenize("A I X Q") == []


def test_overlap_normalizes_bounded_english_inflections() -> None:
    query_tokens = mod._tokenize("Fix GraphQL schemas")

    assert query_tokens == ["graphql", "schema"]
    assert mod._has_meaningful_lexical_overlap("GraphQL schema guidance", query_tokens)
    assert not mod._has_meaningful_lexical_overlap("GraphQL resolver guidance", query_tokens)


@pytest.mark.parametrize(
    ("plural", "singular"),
    [
        ("schemas", "schema"),
        ("policies", "policy"),
        ("classes", "class"),
        ("statuses", "status"),
        ("analyses", "analysis"),
    ],
)
def test_tokenize_normalizes_bounded_regular_and_irregular_inflections(
    plural: str,
    singular: str,
) -> None:
    query_tokens = mod._tokenize(plural)

    assert query_tokens == [singular]
    assert mod._has_meaningful_lexical_overlap(singular, query_tokens)


@pytest.mark.parametrize(
    ("query", "expected", "damaged_form"),
    [
        ("status", "status", "statu"),
        ("analysis", "analysis", "analysi"),
        ("class", "class", "clas"),
        ("CSS", "css", "cs"),
        ("Redis", "redis", "redi"),
    ],
)
def test_overlap_does_not_strip_technical_s_endings(
    query: str,
    expected: str,
    damaged_form: str,
) -> None:
    query_tokens = mod._tokenize(query)

    assert query_tokens == [expected]
    assert not mod._has_meaningful_lexical_overlap(damaged_form, query_tokens)


def test_tokenize_bounds_inflection_normalization_by_token_length() -> None:
    long_token = f"{'a' * 64}s"

    assert mod._tokenize(long_token) == [long_token]


@pytest.mark.parametrize(
    ("unicode_word", "overlapping_ascii_fragment"),
    [("café", "caf"), ("API課金", "api")],
)
def test_unicode_word_is_one_overlap_concept(
    unicode_word: str,
    overlapping_ascii_fragment: str,
) -> None:
    tokens = mod._tokenize(f"GraphQL schema {unicode_word}")

    assert overlapping_ascii_fragment not in tokens
    assert not mod._has_meaningful_lexical_overlap(unicode_word, tokens)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("C++", "c++"),
        ("C#", "c#"),
        ("F#", "f#"),
        ("N+12", "n+12"),
        ("O(42)", "o(42)"),
        ("HTTP/2.1", "http/2.1"),
        ("I/O", "i/o"),
        ("A/B", "a/b"),
    ],
)
def test_tokenize_recognizes_symbolic_technical_terms(query: str, expected: str) -> None:
    assert mod._tokenize(query) == [expected]


@pytest.mark.parametrize(
    "ordinary_slash",
    ["src/api", "owner/repo", "2026/08", "path/A/B", "HTTP/2.1/client"],
)
def test_ordinary_slash_terms_are_not_mandatory_identities(ordinary_slash: str) -> None:
    query_tokens = mod._tokenize(f"Fix {ordinary_slash} GraphQL schema")

    assert ordinary_slash not in query_tokens
    assert mod._has_meaningful_lexical_overlap("GraphQL schema guidance", query_tokens)


@pytest.mark.parametrize("identity", ["HTTP/2.1", "I/O", "A/B"])
def test_explicit_slash_technical_terms_remain_mandatory_identities(identity: str) -> None:
    query_tokens = mod._tokenize(f"Fix {identity} GraphQL schema")

    assert not mod._has_meaningful_lexical_overlap("GraphQL schema guidance", query_tokens)
    assert mod._has_meaningful_lexical_overlap(
        f"{identity} GraphQL schema guidance",
        query_tokens,
    )


@pytest.mark.parametrize(
    ("query", "expected"),
    [("O(n)", "o(n)"), ("O(log n)", "o(log n)"), ("O(42)", "o(42)")],
)
def test_tokenize_recognizes_bounded_big_o_terms(query: str, expected: str) -> None:
    assert mod._tokenize(query) == [expected]


def test_tokenize_does_not_treat_arbitrary_big_o_prose_as_symbolic() -> None:
    assert "o(ready)" not in mod._tokenize("O(ready)")


def test_tokenize_preserves_only_explicit_one_character_languages() -> None:
    assert mod._tokenize("Fix C compiler warnings") == ["c", "compiler", "warning"]
    assert mod._tokenize("Use R") == ["r"]
    assert mod._tokenize("A I X Q") == []


def test_compound_and_constituent_count_as_one_overlap_concept() -> None:
    query_tokens = mod._tokenize("Fix GraphQL schema transport over HTTP/2")

    assert "http/2" in query_tokens
    assert "http" not in query_tokens
    assert not mod._has_meaningful_lexical_overlap("Only HTTP/2 is supported", query_tokens)


def test_tokenize_splits_ordinary_hyphens_but_keeps_symbolic_compounds() -> None:
    tokens = mod._tokenize("cold-start HTTP/2 C++ C# N+1 O(1)")

    assert {"cold", "start"}.issubset(tokens)
    assert {"http/2", "c++", "c#", "n+1", "o(1)"}.issubset(tokens)
    assert {"http", "1"}.isdisjoint(tokens)
    assert len(tokens) == 7


def test_tokenize_nfkc_casefolds_before_detecting_symbolic_compounds() -> None:
    assert mod._tokenize("ＧｒａｐｈＱＬ Ｆ＃ Ａ／Ｂ") == [  # noqa: RUF001
        "f#",
        "a/b",
        "graphql",
    ]


@pytest.mark.parametrize("compound", ["F#", "A/B"])
def test_generic_symbolic_compound_does_not_double_count_constituents(compound: str) -> None:
    query_tokens = mod._tokenize(f"GraphQL {compound}")

    assert len(query_tokens) == 2
    assert not mod._has_meaningful_lexical_overlap(f"Only {compound} is supported", query_tokens)


@pytest.mark.parametrize(
    ("query", "wrong_lesson"),
    [
        ("Fix C++ compiler warnings", "Fix C# compiler warnings"),
        ("Optimize O(n) parser warnings", "Optimize O(42) parser warnings"),
        ("Use A/B release testing", "Use A/C release testing"),
    ],
)
def test_symbolic_query_identity_is_mandatory(query: str, wrong_lesson: str) -> None:
    assert not mod._has_meaningful_lexical_overlap(wrong_lesson, mod._tokenize(query))


def test_literal_only_query_uses_nfkc_surface_before_sqlite_prefilter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mod.SqliteHybridProvider, "_try_create_fts", lambda self, conn: False)
    provider = SqliteHybridProvider(db_path=Path(":memory:"), pool=2)
    lesson = provider.reflect(codename="c", repo="r", body="エラー処理の手順")

    out = provider.recall(query="ｴﾗｰ", codename="c", repo="r")

    assert [item.id for item in out] == [lesson.id]


def test_schema_migration_backfills_canonical_lexical_surface_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "pre-lexical.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE lessons (
                id TEXT PRIMARY KEY, codename TEXT NOT NULL, repo TEXT NOT NULL,
                body TEXT NOT NULL, tags_json TEXT NOT NULL DEFAULT '[]',
                severity TEXT NOT NULL DEFAULT 'info', firing_id TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'note', valid_until TEXT,
                superseded_by TEXT, provenance TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO lessons VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy",
                "c",
                "r",
                "Use Ａ／Ｂ testing",  # noqa: RUF001
                '["Ｆ＃"]',  # noqa: RUF001
                "info",
                None,
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
                "note",
                None,
                None,
                None,
            ),
        )
    monkeypatch.setattr(mod.SqliteHybridProvider, "_try_create_fts", lambda self, conn: False)

    provider = SqliteHybridProvider(db_path=db_path, pool=2)

    assert [item.id for item in provider.recall(query="A/B", codename="c", repo="r")] == ["legacy"]
    assert [item.id for item in provider.recall(query="F#", codename="c", repo="r")] == ["legacy"]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT lexical_text FROM lessons").fetchone() == (
            "use a/b testing f#",
        )
        conn.execute("UPDATE lessons SET lexical_text = 'already-populated'")
        conn.commit()

    SqliteHybridProvider(db_path=db_path).health()

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT lexical_text FROM lessons").fetchone() == ("already-populated",)


@pytest.mark.parametrize(
    ("query", "lesson"),
    [
        ("Fix cold-start handling", "Use cold start handling"),
        ("Fix cold start handling", "Use cold-start handling"),
    ],
)
def test_hyphenated_spelling_variants_share_constituent_overlap(
    query: str,
    lesson: str,
) -> None:
    assert mod._has_meaningful_lexical_overlap(lesson, mod._tokenize(query))


def test_lexical_overlap_scans_lesson_terms_beyond_query_token_cap() -> None:
    prefix = " ".join(f"noise{i}" for i in range(25))
    lesson = f"{prefix} GraphQL schema"
    query = " ".join([*(f"term{i}" for i in range(30)), "ignored-tail"])

    assert len(mod._tokenize(query)) == 24
    assert mod._has_meaningful_lexical_overlap(lesson, ["graphql", "schema"])


def test_default_chain_query_miss_does_not_fall_back_to_recent_fleet_lesson(
    tmp_path: Path,
) -> None:
    sqlite = SqliteHybridProvider(db_path=tmp_path / "memory.db")
    fleet = FleetBrainProvider(brain=FleetBrain(db_path=tmp_path / "brain.db"))
    sqlite.reflect(codename="c", repo="r", body="Always use UTC for stored timestamps")
    fleet.reflect(codename="c", repo="r", body="Run the release checklist before tagging")
    chain = ChainedMemoryProvider(providers=[sqlite, fleet])

    out = chain.recall(query="GraphQL batching policy", codename="c", repo="r")

    assert out == []


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


def test_sync_lesson_roundtrip_with_string_created_at(provider: SqliteHybridProvider) -> None:
    # A lesson mirrored from the AMS write contract carries ``created_at`` as a
    # serialized ISO string, not a datetime. sync_lesson must persist it (not
    # silently swallow an AttributeError from _iso and return False) so the
    # lesson is recallable. This is the exact E2E path that regressed.
    lesson = Lesson(
        id="lesson:memory_candidate:strfix",
        codename="lucius",
        repo="acme/api",
        body="gateway rate limiting lives in the edge module",
        tags=["gateway"],
        created_at="2026-07-09T00:00:00Z",  # ISO string, not a datetime
        firing_id="firing-123",
        severity="info",
    )
    assert provider.sync_lesson(lesson) is True
    assert provider.health()["lessons"] == 1
    out = provider.recall(query="gateway rate limiting", codename="lucius", repo="acme/api")
    assert [L.id for L in out] == [lesson.id]
    # created_at is normalized back to a UTC datetime on the recalled lesson.
    assert isinstance(out[0].created_at, datetime)


def test_reflect_accepts_string_datetime_and_none_created_at(
    provider: SqliteHybridProvider,
) -> None:
    # reflect's created_at is (datetime | str | None); all three persist and the
    # returned lesson always carries a well-typed datetime.
    as_str = provider.reflect(
        codename="c", repo="r", body="string created_at", created_at="2026-01-02T03:04:05Z"
    )
    as_dt = provider.reflect(
        codename="c",
        repo="r",
        body="datetime created_at",
        created_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
    )
    as_none = provider.reflect(codename="c", repo="r", body="default created_at")
    for lesson in (as_str, as_dt, as_none):
        assert isinstance(lesson.created_at, datetime)
    assert as_str.created_at == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert as_dt.created_at == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert len(provider.list_lessons(limit=10)) == 3


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


def test_lesson_writer_fleet_only_targets_fleet_not_sqlite() -> None:
    # A fleet-only chain names no dedicated recall store, so the promoted lesson
    # must go to FleetBrain's own lessons table (what fleet recall reads), NOT a
    # disconnected SQLite file recall would ignore.
    writer = load_lesson_writer(env={"ALFRED_MEMORY_PROVIDERS": "fleet"})
    assert isinstance(writer, FleetBrainProvider)


def test_lesson_writer_is_none_when_no_writable_recall_store() -> None:
    # gbrain is read-only and not a recall store; with no fleet either, there is
    # nothing in the recall chain to write to, so promotion is a no-op.
    assert load_lesson_writer(env={"ALFRED_MEMORY_PROVIDERS": "gbrain"}) is None


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


# ---------------------------------------------------------------------------
# Disabled memory writes nothing (Greptile P1: "Disabled Memory Still Writes")
# ---------------------------------------------------------------------------


def test_lesson_writer_is_none_when_memory_disabled() -> None:
    # ALFRED_MEMORY_PROVIDERS=null (or empty) disables runtime memory, so there
    # is no writer and the promote path must not fall back to a SQLite file.
    assert load_lesson_writer(env={"ALFRED_MEMORY_PROVIDERS": "null"}) is None
    assert load_lesson_writer(env={"ALFRED_MEMORY_PROVIDERS": ""}) is None
    assert load_lesson_writer(env={"ALFRED_MEMORY_PROVIDERS": " , "}) is None


def test_promotion_is_a_noop_when_memory_disabled(tmp_path: Path) -> None:
    brain = FleetBrain(db_path=tmp_path / "brain.db")
    cand = brain.propose_memory(
        codename="c", repo="r", body="a lesson", evidence="saw it", confidence=0.9
    )
    # With memory disabled the promote is a no-op: nothing is written and the
    # candidate stays pending (re-promotable) rather than flipping to validated.
    with pytest.raises(MemoryPromotionError):
        brain.promote_memory_candidate(cand.id, env={"ALFRED_MEMORY_PROVIDERS": "null"})
    still = brain.store.get_memory_candidate(cand.id)
    assert still is not None and still.status == "candidate"
    # No hybrid store file was created as a side effect of the disabled promote.
    assert not (tmp_path / "memory-hybrid.db").exists()


# ---------------------------------------------------------------------------
# Tag recall under the LIKE fallback (Greptile P1: "Tag Recall Drops Without FTS")
# ---------------------------------------------------------------------------


def test_tag_only_match_recalled_under_like_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Force the no-FTS5 build: the provider must still recall a lesson whose only
    # match is a tag, mirroring what the FTS arm indexes (body + tags).
    monkeypatch.setattr(mod.SqliteHybridProvider, "_try_create_fts", lambda self, conn: False)
    prov = SqliteHybridProvider(db_path=Path(":memory:"))
    prov.reflect(
        codename="c",
        repo="r",
        body="deploy pipeline runbook",  # body does NOT contain the tag token
        tags=["graphql"],
    )
    assert prov.health()["lexical"] == "like"
    out = prov.recall(query="graphql", codename="c", repo="r")
    assert out and out[0].tags == ["graphql"]


# ---------------------------------------------------------------------------
# Scoped dense results not truncated (Greptile P1: "Scoped Dense Results Are
# Truncated") -- requires the optional sqlite-vec extension.
# ---------------------------------------------------------------------------


def test_dense_in_scope_vector_not_truncated_by_closer_out_of_scope() -> None:
    pytest.importorskip("sqlite_vec")

    # The query embeds onto the filler vector, so the global nearest neighbours
    # are all out-of-scope fillers. With pool=2 the old "top-k then filter"
    # approach dropped the single in-scope vector entirely; the over-fetch loop
    # must still surface it.
    def _embed(text: str) -> list[float]:
        if "scope-target" in text.lower():
            return [0.9, 0.1, 0.0, 0.0]
        return [1.0, 0.0, 0.0, 0.0]

    prov = SqliteHybridProvider(
        db_path=Path(":memory:"), dense=True, dimensions=4, embedder=_embed, pool=2
    )
    for i in range(5):
        prov.reflect(codename="other", repo="r", body=f"filler note {i}")
    target = prov.reflect(codename="lucius", repo="r", body="scope-target note")

    # Query text has no lexical overlap with any body, so only the dense arm
    # contributes; it must return the in-scope target despite five closer
    # out-of-scope vectors.
    out = prov.recall(query="unrelated lookup phrase", codename="lucius", repo="r")
    assert target.id in {L.id for L in out}


# ---------------------------------------------------------------------------
# Fleet-only writes land where recall reads (Greptile P1: "Fleet-Only Writes
# Disappear")
# ---------------------------------------------------------------------------


def test_fleet_only_promotion_is_recalled_and_creates_no_orphan_store(tmp_path: Path) -> None:
    # With ALFRED_MEMORY_PROVIDERS=fleet, recall reads ONLY FleetBrain, so a
    # promotion must land in FleetBrain's lessons table (what recall reads), not
    # a disconnected SQLite file.
    env = {"ALFRED_MEMORY_PROVIDERS": "fleet", "ALFRED_HOME": str(tmp_path)}
    brain = FleetBrain.from_env(env)
    cand = brain.propose_memory(
        codename="lucius",
        repo="acme/api",
        body="GraphQL schema lives in src/schema.graphql",
        evidence="saw it at app.py:10",
        confidence=0.9,
    )
    lesson = brain.promote_memory_candidate(cand.id, reviewer="operator", env=env)

    # Recall through the same fleet chain finds the promoted lesson: the write
    # landed where recall reads.
    chain = load_provider(env)
    out = chain.recall(query="graphql", codename="lucius", repo="acme/api")
    assert lesson.id in {L.id for L in out}
    # No orphan hybrid SQLite store was created under the state root.
    assert not (tmp_path / "memory-hybrid.db").exists()


# ---------------------------------------------------------------------------
# Disabled forgetter is a controlled no-op (Greptile P1: "Disabled Forgetter
# Skips Cleanup")
# ---------------------------------------------------------------------------


class _FakeLessonWriter:
    """Minimal in-memory lesson writer for staging a validated candidate."""

    name = "sqlite"

    def reflect(
        self,
        *,
        codename: str,
        repo: str,
        body: str,
        tags: object = None,
        severity: str = "info",
        firing_id: str | None = None,
        created_at: datetime | None = None,
        memory_id: str | None = None,
    ) -> Lesson:
        return Lesson(
            id=memory_id or "fake-lesson-id",
            codename=codename,
            repo=repo,
            body=body,
            tags=[],
            created_at=created_at or datetime.now(UTC),
            firing_id=firing_id,
            severity="info",
        )

    def forget_lesson(self, _lesson_id: str) -> bool:
        return True


def test_revert_and_retire_are_controlled_noops_when_memory_disabled(tmp_path: Path) -> None:
    brain = FleetBrain(db_path=tmp_path / "brain.db")
    cand = brain.propose_memory(
        codename="c", repo="r", body="a lesson", evidence="saw it", confidence=0.9
    )
    # Stage a validated (promoted) candidate with a working writer.
    brain.promote_memory_candidate(cand.id, reviewer="auto", lesson_writer=_FakeLessonWriter())
    assert brain.store.get_memory_candidate(cand.id).status == "validated"  # type: ignore[union-attr]

    # Now memory is disabled: the resolved forgetter is None.
    brain._lesson_provider = lambda env=None: None  # type: ignore[method-assign]

    # revert is a controlled no-op: no crash, nothing reverted, candidate stays
    # validated (its lesson was never actually forgotten).
    assert brain.revert_auto_promotions() == []
    assert brain.store.get_memory_candidate(cand.id).status == "validated"  # type: ignore[union-attr]

    # retire raises the same controlled MemoryPromotionError as a forget failure,
    # rather than crashing on a None forgetter or silently retiring.
    with pytest.raises(MemoryPromotionError):
        brain.retire_memory_candidate(cand.id)
    assert brain.store.get_memory_candidate(cand.id).status == "validated"  # type: ignore[union-attr]
