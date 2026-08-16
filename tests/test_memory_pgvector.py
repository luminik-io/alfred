"""Tests for the Postgres + pgvector scale-tier memory provider.

The provider is an opt-in scale tier behind the same seam as the SQLite hybrid
store, so it is exercised on two levels:

* **Without a live Postgres (always runs).** The SQL builders are pure
  ``(sql, params)`` functions, so scope-filtering-in-the-query, the dense
  cosine-distance order, and the lexical arms are asserted directly. RRF fusion
  is the SAME function object the SQLite store uses (parity by identity). The
  consolidation surface (``merge_lesson`` / ``union_reuse_counts`` /
  ``bump_reuse_counts`` / ``get_reuse_count``) is driven end to end against a
  small fake connection that models the three tables, so the provenance +
  anchor + reuse UNION logic is covered with no daemon. Config registration,
  ``from_env`` arming, and DSN redaction round it out.

* **With a live Postgres (skipped unless configured).** Set
  ``ALFRED_MEMORY_PG_DSN`` to a database with the ``vector`` extension available
  and the integration test does a real write -> recall -> merge -> forget round
  trip. Skipped otherwise, exactly as the sqlite-vec dense-arm test skips when
  the extension is absent.
"""

from __future__ import annotations

import os
import re
import sys
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "lib"))

import memory.pgvector_provider as mod  # noqa: E402
from agent_runner import memory_ranking  # noqa: E402
from agent_runner.memory_runtime import load_runtime_memory  # noqa: E402
from fleet_brain import Lesson  # noqa: E402
from memory import MemoryProvider  # noqa: E402
from memory.config import (  # noqa: E402
    DEFAULT_PROVIDER_NAMES,
    LESSON_STORE_NAMES,
    PROVIDER_REGISTRY,
    load_lesson_writer,
    load_provider,
)
from memory.pgvector_provider import (  # noqa: E402
    _MAX_PG_IDENTIFIER_BYTES,
    _MAX_TABLE_PREFIX_LEN,
    MemoryProviderMisconfigured,
    MemoryProviderUnavailable,
    PgvectorProvider,
    _dense_query,
    _lexical_like_query,
    _lexical_query,
    _recency_query,
    _reciprocal_rank_fusion,
    _redact_dsn,
    _scope_clause,
    _vector_literal,
)

_NOW = datetime(2026, 7, 9, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Pure SQL builders: scope filtering baked into every arm's query
# ---------------------------------------------------------------------------


def test_scope_clause_always_filters_validity_then_scope() -> None:
    sql, params = _scope_clause("lucius", "acme/api", alias="l", now=_NOW)
    # Validity (superseded/expired) is always excluded, before scope narrowing.
    assert "l.superseded_by IS NULL" in sql
    assert "l.valid_until IS NULL OR l.valid_until > %s" in sql
    assert "l.codename = %s" in sql
    assert "l.repo = %s" in sql
    # Param order matches placeholder order: now, codename, repo.
    assert params == [_NOW, "lucius", "acme/api"]


def test_scope_clause_omits_absent_scope() -> None:
    sql, params = _scope_clause(None, None, alias="l", now=_NOW)
    assert "codename" not in sql
    assert "repo = %s" not in sql
    assert params == [_NOW]


def test_dense_query_filters_scope_in_the_same_where_as_vector_order() -> None:
    sql, params = _dense_query(
        "[0.1,0.2]", table="lessons", codename="lucius", repo="acme/api", pool=7, now=_NOW
    )
    # The scope + validity filter is in the WHERE that feeds ORDER BY, so an
    # in-scope vector can never be truncated away by closer out-of-scope ones.
    where, _, order = sql.partition("ORDER BY")
    assert "l.codename = %s" in where
    assert "l.repo = %s" in where
    assert "l.superseded_by IS NULL" in where
    assert "embedding <=> %s::vector" in order
    # cosine distance operator + the pool limit.
    assert params == [_NOW, "lucius", "acme/api", "[0.1,0.2]", 7]


def test_lexical_query_or_joins_tokens_and_scopes() -> None:
    sql, params = _lexical_query(
        [("gateway", "gateways"), ("rate", "rates")],
        table="lessons",
        codename="c",
        repo="r",
        pool=5,
        now=_NOW,
    )
    assert "to_tsquery('english', %s)" in sql
    assert "ts_rank(l.body_tsv" in sql
    assert "l.codename = %s" in sql
    # OR-of-tokens tsquery, like the SQLite FTS arm.
    assert params[:4] == [
        [0, 0, 1, 1],
        ["gateway", "gateway", "rate", "rate"],
        ["gateway", "gateways", "rate", "rates"],
        "gateway | gateways | rate | rates",
    ]
    assert params[-1] == 5


def test_lexical_query_deduplicates_english_lexemes_before_candidate_limit() -> None:
    sql, params = _lexical_query(
        [("caching", "cachings"), ("cached", "cacheds"), ("response", "responses")],
        table="lessons",
        codename="c",
        repo="r",
        pool=2,
        now=_NOW,
    )

    where, _, order = sql.partition("ORDER BY")
    assert "WITH query_lexemes AS" in where
    assert "SELECT DISTINCT concept_index" in where
    assert "plainto_tsquery('english', canonical) AS canonical_query" in where
    assert "plainto_tsquery('english', token) AS lexeme_query" in where
    assert "FROM unnest(%s::int[], %s::text[], %s::text[])" in where
    assert "numnode(plainto_tsquery('english', token)) > 0" in where
    assert "l.body_tsv @@ to_tsquery('english', %s)" in where
    assert "SELECT COUNT(DISTINCT q.canonical_query) FROM query_lexemes q" in where
    assert ">= LEAST(2, (SELECT COUNT(DISTINCT canonical_query) FROM query_lexemes))" in where
    assert "LIMIT %s" in order
    assert params == [
        [0, 0, 1, 1, 2, 2],
        ["caching", "caching", "cached", "cached", "response", "response"],
        ["caching", "cachings", "cached", "cacheds", "response", "responses"],
        "caching | cachings | cached | cacheds | response | responses",
        _NOW,
        "c",
        "r",
        "caching | cachings | cached | cacheds | response | responses",
        2,
    ]


def test_lexical_query_counts_variants_as_one_required_concept() -> None:
    sql, params = _lexical_query(
        [("graphql", "graphqls"), ("policy", "policies")],
        table="lessons",
        codename=None,
        repo=None,
        pool=2,
        now=_NOW,
    )

    assert "COUNT(DISTINCT q.canonical_query)" in sql
    assert "COUNT(DISTINCT canonical_query)" in sql
    assert params[:3] == [
        [0, 0, 1, 1],
        ["graphql", "graphql", "policy", "policy"],
        ["graphql", "graphqls", "policy", "policies"],
    ]


def test_fts_lexical_ids_delegate_duplicate_stems_to_postgres() -> None:
    class Cursor:
        def __init__(self, rows: list[tuple[Any, ...]]) -> None:
            self.rows = rows

        def fetchall(self) -> list[tuple[Any, ...]]:
            return self.rows

    class Connection:
        def __init__(self) -> None:
            self.calls: list[tuple[str, Any]] = []

        def execute(self, sql: str, _params: Any) -> Cursor:
            normalized = " ".join(sql.split())
            self.calls.append((normalized, _params))
            if normalized.startswith("SELECT COALESCE(bool_and"):
                assert _params == [["caching", "cached", "response"]]
                return Cursor([(True,)])
            if "SELECT l.id FROM lessons l" in normalized:
                return Cursor([("stemmed",)])
            if normalized.startswith("SELECT id, body, tags_json FROM lessons"):
                return Cursor([("stemmed", "cache invalidation", "[]")])
            raise AssertionError(f"unexpected SQL: {normalized}")

    provider = PgvectorProvider(dsn="postgresql://u:p@h/db")
    provider._fts_ok = True
    conn = Connection()

    ids = provider._lexical_ids(
        conn,
        "caching cached responses",
        codename="c",
        repo="r",
        now=_NOW,
    )

    assert ids == ["stemmed"]
    assert len(conn.calls) == 2
    assert conn.calls[1][1][:3] == [
        [0, 0, 1, 1, 2, 2],
        ["caching", "caching", "cached", "cached", "response", "response"],
        ["caching", "cachings", "cached", "cacheds", "response", "responses"],
    ]


@pytest.mark.parametrize("stop_concept", ["not", "very"])
def test_pg_fts_unrepresentable_concept_uses_exact_bounded_fallback(
    stop_concept: str,
) -> None:
    class Cursor:
        def __init__(self, rows: list[tuple[Any, ...]]) -> None:
            self.rows = rows

        def fetchall(self) -> list[tuple[Any, ...]]:
            return self.rows

    class Connection:
        def __init__(self) -> None:
            self.calls: list[tuple[str, list[Any]]] = []

        def execute(self, sql: str, params: list[Any]) -> Cursor:
            normalized = " ".join(sql.split())
            self.calls.append((normalized, params))
            if normalized.startswith("SELECT COALESCE(bool_and"):
                assert params == [["api", stop_concept]]
                return Cursor([(False,)])
            if "FROM lessons l WHERE" in normalized:
                assert "body_tsv" not in normalized
                assert params[-1] == 50
                return Cursor(
                    [
                        ("api-only", "api guidance", _NOW),
                        (
                            "relevant",
                            f"api {stop_concept} guidance",
                            _NOW - timedelta(seconds=1),
                        ),
                    ]
                )
            raise AssertionError(f"unexpected SQL: {normalized}")

    provider = PgvectorProvider(dsn="postgresql://u:p@h/db", pool=2)
    provider._fts_ok = True
    conn = Connection()

    ids = provider._lexical_ids(
        conn,
        f"API {stop_concept}",
        codename="c",
        repo="r",
        now=_NOW,
    )

    assert ids == ["relevant"]
    assert len(conn.calls) == 2
    assert sum("body_tsv" in sql for sql, _params in conn.calls) == 0


def test_pg_fts_concept_preflight_and_fallback_keep_query_bounds() -> None:
    class Cursor:
        def __init__(self, rows: list[tuple[Any, ...]]) -> None:
            self.rows = rows

        def fetchall(self) -> list[tuple[Any, ...]]:
            return self.rows

    class Connection:
        def __init__(self) -> None:
            self.preflight_calls = 0
            self.fallback_calls = 0

        def execute(self, sql: str, params: list[Any]) -> Cursor:
            normalized = " ".join(sql.split())
            if normalized.startswith("SELECT COALESCE(bool_and"):
                self.preflight_calls += 1
                assert len(params) == 1
                assert len(params[0]) == 24
                return Cursor([(False,)])
            if "FROM lessons l WHERE" in normalized:
                self.fallback_calls += 1
                assert params[-1] == 50
                return Cursor([])
            raise AssertionError(f"unexpected SQL: {normalized}")

    provider = PgvectorProvider(dsn="postgresql://u:p@h/db", pool=2)
    provider._fts_ok = True
    conn = Connection()

    ids = provider._lexical_ids(
        conn,
        " ".join(f"concept{index}" for index in range(100)),
        codename="c",
        repo="r",
        now=_NOW,
    )

    assert ids == []
    assert conn.preflight_calls == 1
    assert conn.fallback_calls == 1


@pytest.mark.parametrize(
    ("query", "collision_body", "matching_body"),
    [
        ("C++", "Use C# for the client", "Use C++ for the client"),
        ("C#", "Use C++ for the client", "Use C# for the client"),
        ("F#", "Use C# for the client", "Use F# for the client"),
        ("N+12", "Avoid N+13 queries", "Avoid N+12 queries"),
        ("O(42)", "The lookup is O(1)", "The lookup is O(42)"),
        ("I/O", "Use IO batching", "Use I/O batching"),
        ("A/B", "Use A/C testing", "Use A/B testing"),
        ("HTTP/2.1", "Require HTTP/3", "Require HTTP/2.1"),
    ],
)
def test_fts_enabled_symbolic_queries_use_exact_bounded_fallback(
    query: str,
    collision_body: str,
    matching_body: str,
) -> None:
    class Cursor:
        def __init__(self, rows: list[tuple[Any, ...]]) -> None:
            self.rows = rows

        def fetchall(self) -> list[tuple[Any, ...]]:
            return self.rows

    class Connection:
        def __init__(self) -> None:
            self.fts_calls = 0
            self.fallback_calls = 0

        def execute(self, sql: str, params: list[Any]) -> Cursor:
            normalized = " ".join(sql.split())
            if "body_tsv" in normalized:
                self.fts_calls += 1
                return Cursor([("collision",)])
            if "FROM lessons l WHERE" in normalized:
                self.fallback_calls += 1
                assert params[-1] == 50
                return Cursor(
                    [
                        ("collision", collision_body.casefold(), _NOW),
                        (
                            "matching",
                            matching_body.casefold(),
                            _NOW - timedelta(seconds=1),
                        ),
                    ]
                )
            raise AssertionError(f"unexpected SQL: {normalized}")

    provider = PgvectorProvider(dsn="postgresql://u:p@h/db", pool=2)
    provider._fts_ok = True
    conn = Connection()

    ids = provider._lexical_ids(conn, query, codename="c", repo="r", now=_NOW)

    assert ids == ["matching"]
    assert conn.fts_calls == 0
    assert conn.fallback_calls == 1


def test_lexical_like_fallback_matches_body_and_tags() -> None:
    sql, params = _lexical_like_query(
        [("graphql", "graphqls")],
        table="lessons",
        codename=None,
        repo="r",
        pool=3,
        now=_NOW,
    )
    assert "l.lexical_text ILIKE %s" in sql
    assert "%graphql%" in params
    assert "OFFSET" not in sql
    assert params[-1] == 3


def test_lexical_like_fallback_applies_overlap_before_candidate_limit() -> None:
    sql, params = _lexical_like_query(
        [("graphql", "graphqls"), ("schema", "schemas")],
        table="lessons",
        codename="c",
        repo="r",
        pool=2,
        now=_NOW,
    )

    where, _, order = sql.partition("ORDER BY")
    assert where.count("CAST((l.lexical_text ILIKE %s OR l.lexical_text ILIKE %s) AS INTEGER)") == 2
    assert ") >= %s" in where
    assert "LIMIT %s" in order
    assert "OFFSET" not in order
    assert params == [
        "%graphql%",
        "%graphqls%",
        "%schema%",
        "%schemas%",
        2,
        _NOW,
        "c",
        "r",
        2,
    ]


def test_lexical_like_fallback_uses_created_at_id_keyset() -> None:
    sql, params = _lexical_like_query(
        [("graphql", "graphqls"), ("schema", "schemas")],
        table="lessons",
        codename="c",
        repo="r",
        pool=2,
        now=_NOW,
        after=(_NOW, "lesson-9"),
    )

    where, _, order = sql.partition("ORDER BY")
    assert "l.created_at < %s OR (l.created_at = %s AND l.id > %s)" in where
    assert "l.created_at DESC, l.id" in order
    assert "OFFSET" not in sql
    assert params[-4:] == [_NOW, _NOW, "lesson-9", 2]


def test_lexical_like_fallback_pages_past_substring_only_matches() -> None:
    class Cursor:
        def __init__(self, rows: list[tuple[Any, ...]]) -> None:
            self.rows = rows

        def fetchall(self) -> list[tuple[Any, ...]]:
            return self.rows

    class Connection:
        def __init__(self) -> None:
            self.candidate_sql: list[str] = []

        def execute(self, sql: str, params: Any) -> Cursor:
            normalized = " ".join(sql.split())
            if "FROM lessons l WHERE" in normalized:
                page = len(self.candidate_sql)
                self.candidate_sql.append(normalized)
                pages = {
                    0: [
                        (
                            f"weak-{index:02}",
                            f"rapid rollout {index}",
                            _NOW - timedelta(seconds=index),
                        )
                        for index in range(50)
                    ],
                    1: [
                        (
                            "valid-older",
                            "api rapid response policy",
                            datetime(2026, 7, 8, tzinfo=UTC),
                        )
                    ],
                }
                return Cursor(pages.get(page, []))
            raise AssertionError(f"unexpected SQL: {normalized}")

    provider = PgvectorProvider(dsn="postgresql://u:p@h/db", pool=2)
    provider._fts_ok = False
    conn = Connection()

    ids = provider._lexical_ids(
        conn,
        "api rapid",
        codename="c",
        repo="r",
        now=_NOW,
    )

    assert ids == ["valid-older"]
    assert len(conn.candidate_sql) == 2
    assert "OFFSET" not in conn.candidate_sql[0]
    assert "l.created_at < %s OR (l.created_at = %s AND l.id > %s)" in conn.candidate_sql[1]


@pytest.mark.parametrize("pool", [1, 2])
def test_lexical_like_fallback_candidate_page_is_independent_of_result_pool(pool: int) -> None:
    rows = [
        (
            f"weak-{index:02}",
            f"rapid rollout {index}",
            _NOW - timedelta(seconds=index),
        )
        for index in range(pool * 8)
    ]
    rows.append(
        (
            "valid-older",
            "api rapid response policy",
            _NOW - timedelta(minutes=1),
        )
    )

    class Cursor:
        def __init__(self, page: list[tuple[Any, ...]]) -> None:
            self.page = page

        def fetchall(self) -> list[tuple[Any, ...]]:
            return self.page

    class Connection:
        def __init__(self) -> None:
            self.offset = 0
            self.page_sizes: list[int] = []

        def execute(self, sql: str, params: list[Any]) -> Cursor:
            normalized = " ".join(sql.split())
            if "FROM lessons l WHERE" not in normalized:
                raise AssertionError(f"unexpected SQL: {normalized}")
            page_size = int(params[-1])
            self.page_sizes.append(page_size)
            page = rows[self.offset : self.offset + page_size]
            self.offset += len(page)
            return Cursor(page)

    provider = PgvectorProvider(dsn="postgresql://u:p@h/db", pool=pool)
    provider._fts_ok = False
    conn = Connection()

    ids = provider._lexical_ids(conn, "api rapid", codename="c", repo="r", now=_NOW)

    assert ids == ["valid-older"]
    assert conn.page_sizes == [50]


def test_lexical_like_fallback_stops_after_eight_candidate_pages() -> None:
    class Cursor:
        def __init__(self, rows: list[tuple[Any, ...]]) -> None:
            self.rows = rows

        def fetchall(self) -> list[tuple[Any, ...]]:
            return self.rows

    class Connection:
        def __init__(self) -> None:
            self.candidate_calls = 0

        def execute(self, sql: str, params: Any) -> Cursor:
            normalized = " ".join(sql.split())
            if "FROM lessons l WHERE" in normalized:
                page = self.candidate_calls
                self.candidate_calls += 1
                if page >= 20:
                    return Cursor([])
                rows = [
                    (
                        f"weak-{page:02}-{index}",
                        f"rapid rollout {page}-{index}",
                        datetime(2026, 7, 9 - (page // 8), tzinfo=UTC),
                    )
                    for index in range(50)
                ]
                return Cursor(rows)
            raise AssertionError(f"unexpected SQL: {normalized}")

    provider = PgvectorProvider(dsn="postgresql://u:p@h/db", pool=2)
    provider._fts_ok = False
    conn = Connection()

    ids = provider._lexical_ids(
        conn,
        "api rapid",
        codename="c",
        repo="r",
        now=_NOW,
    )

    assert ids == []
    assert conn.candidate_calls == 8


@pytest.mark.parametrize(
    "query",
    [
        "C compiler",
        "R package",
        "C++",
        "C#",
        "F#",
        "N+12",
        "O(n)",
        "O(log n)",
        "O(42)",
        "HTTP/2.1",
        "I/O",
        "A/B",
    ],
)
def test_lexical_like_fallback_recalls_symbolic_technical_terms(query: str) -> None:
    class Cursor:
        def __init__(self, rows: list[tuple[Any, ...]]) -> None:
            self.rows = rows

        def fetchall(self) -> list[tuple[Any, ...]]:
            return self.rows

    class Connection:
        def execute(self, sql: str, params: list[Any]) -> Cursor:
            normalized = " ".join(sql.split())
            if "FROM lessons l WHERE" in normalized:
                assert f"%{query.casefold()}%" in params
                return Cursor([("match", f"prefer {query.casefold()} for this case.", _NOW)])
            raise AssertionError(f"unexpected SQL: {normalized}")

    provider = PgvectorProvider(dsn="postgresql://u:p@h/db", pool=2)
    provider._fts_ok = False

    ids = provider._lexical_ids(
        Connection(),
        query,
        codename="c",
        repo="r",
        now=_NOW,
    )

    assert ids == ["match"]


def test_lexical_like_fallback_requires_language_compound_identity() -> None:
    class Cursor:
        def fetchall(self) -> list[tuple[Any, ...]]:
            return [
                ("r", "fix r compiler warnings", _NOW),
                ("c", "fix c compiler warnings", _NOW - timedelta(seconds=1)),
            ]

    class Connection:
        def execute(self, sql: str, _params: Any) -> Cursor:
            normalized = " ".join(sql.split())
            assert "body_tsv" not in normalized
            assert "FROM lessons l WHERE" in normalized
            return Cursor()

    provider = PgvectorProvider(dsn="postgresql://u:p@h/db", pool=2)
    provider._fts_ok = True

    ids = provider._lexical_ids(
        Connection(),
        "Fix C compiler warnings",
        codename="c",
        repo="r",
        now=_NOW,
    )

    assert ids == ["c"]


@pytest.mark.parametrize(
    ("query_context", "lesson_context", "wrong_context"),
    [
        ("C compiler", "C language", "R language"),
        ("C language", "C compiler", "R compiler"),
        ("R package", "R language", "C language"),
        ("R language", "R package", "C compiler"),
        ("R script", "R language", "C language"),
        ("R language", "R script", "C compiler"),
    ],
)
def test_lexical_like_fallback_canonicalizes_language_identity_contexts(
    query_context: str,
    lesson_context: str,
    wrong_context: str,
) -> None:
    lesson_text = f"{lesson_context.casefold()} warnings guidance"
    wrong_text = f"{wrong_context.casefold()} warnings guidance"

    class Cursor:
        def __init__(self, rows: list[tuple[Any, ...]]) -> None:
            self.rows = rows

        def fetchall(self) -> list[tuple[Any, ...]]:
            return self.rows

    class Connection:
        def execute(self, sql: str, params: list[Any]) -> Cursor:
            normalized = " ".join(sql.split())
            assert "body_tsv" not in normalized
            assert "FROM lessons l WHERE" in normalized
            patterns = [
                value.strip("%")
                for value in params
                if isinstance(value, str) and value.startswith("%")
            ]
            rows = [
                (lesson_id, text, _NOW - timedelta(seconds=index))
                for index, (lesson_id, text) in enumerate(
                    [("wrong", wrong_text), ("matching", lesson_text)]
                )
                if any(pattern in text for pattern in patterns)
            ]
            return Cursor(rows)

    provider = PgvectorProvider(dsn="postgresql://u:p@h/db", pool=2)
    provider._fts_ok = True

    ids = provider._lexical_ids(
        Connection(),
        f"Fix {query_context} warnings",
        codename="c",
        repo="r",
        now=_NOW,
    )

    assert ids == ["matching"]


@pytest.mark.parametrize(
    "query",
    ["Rename column C in GraphQL schema", "Rename variable R in GraphQL schema"],
)
def test_lexical_like_fallback_does_not_require_one_letter_labels(query: str) -> None:
    class Cursor:
        def fetchall(self) -> list[tuple[Any, ...]]:
            return [("relevant", "graphql schema renaming guidance", _NOW)]

    class Connection:
        def execute(self, sql: str, _params: Any) -> Cursor:
            normalized = " ".join(sql.split())
            assert "FROM lessons l WHERE" in normalized
            return Cursor()

    provider = PgvectorProvider(dsn="postgresql://u:p@h/db", pool=2)
    provider._fts_ok = False

    ids = provider._lexical_ids(
        Connection(),
        query,
        codename="c",
        repo="r",
        now=_NOW,
    )

    assert ids == ["relevant"]


def test_lexical_like_fallback_requires_symbolic_identity() -> None:
    class Cursor:
        def fetchall(self) -> list[tuple[Any, ...]]:
            return [
                ("c-sharp", "fix c# compiler warnings", _NOW),
                ("c-plus-plus", "fix c++ compiler warnings", _NOW - timedelta(seconds=1)),
            ]

    class Connection:
        def execute(self, sql: str, _params: Any) -> Cursor:
            normalized = " ".join(sql.split())
            assert "body_tsv" not in normalized
            assert "FROM lessons l WHERE" in normalized
            return Cursor()

    provider = PgvectorProvider(dsn="postgresql://u:p@h/db", pool=2)
    provider._fts_ok = True

    ids = provider._lexical_ids(
        Connection(),
        "Fix C++ compiler warnings",
        codename="c",
        repo="r",
        now=_NOW,
    )

    assert ids == ["c-plus-plus"]


@pytest.mark.parametrize(
    ("query", "version", "wrong_text", "matching_text"),
    [
        (
            "Fix TLS 1.3 configuration",
            "1.3",
            "tls 1.2 configuration guidance",
            "tls 1.3 configuration guidance",
        ),
        (
            "Fix Python 3.13 runtime",
            "3.13",
            "python 3.12 runtime guidance",
            "python 3.13 runtime guidance",
        ),
    ],
)
def test_lexical_like_fallback_requires_dotted_version_identity(
    query: str,
    version: str,
    wrong_text: str,
    matching_text: str,
) -> None:
    class Cursor:
        def fetchall(self) -> list[tuple[Any, ...]]:
            return [
                ("wrong", wrong_text, _NOW),
                ("matching", matching_text, _NOW - timedelta(seconds=1)),
            ]

    class Connection:
        def execute(self, sql: str, params: list[Any]) -> Cursor:
            normalized = " ".join(sql.split())
            assert "body_tsv" not in normalized
            assert "FROM lessons l WHERE" in normalized
            assert f"%{version}%" in params
            return Cursor()

    provider = PgvectorProvider(dsn="postgresql://u:p@h/db", pool=2)
    provider._fts_ok = True

    ids = provider._lexical_ids(
        Connection(),
        query,
        codename="c",
        repo="r",
        now=_NOW,
    )

    assert ids == ["matching"]


@pytest.mark.parametrize(
    ("query", "identity", "wrong_texts", "matching_text"),
    [
        (
            "Fix C++17 compiler warnings",
            "c++17",
            ["fix c#17 compiler warnings", "fix c++20 compiler warnings"],
            "fix c++17 compiler warnings",
        ),
        (
            "Fix C#17 compiler warnings",
            "c#17",
            ["fix c++17 compiler warnings", "fix c#12 compiler warnings"],
            "fix c#17 compiler warnings",
        ),
    ],
)
def test_lexical_like_fallback_requires_atomic_language_standard_identity(
    query: str,
    identity: str,
    wrong_texts: list[str],
    matching_text: str,
) -> None:
    class Cursor:
        def fetchall(self) -> list[tuple[Any, ...]]:
            rows = [
                (f"wrong-{index}", text, _NOW - timedelta(seconds=index))
                for index, text in enumerate(wrong_texts)
            ]
            rows.append(("matching", matching_text, _NOW - timedelta(seconds=10)))
            return rows

    class Connection:
        def execute(self, sql: str, params: list[Any]) -> Cursor:
            normalized = " ".join(sql.split())
            assert "body_tsv" not in normalized
            assert "FROM lessons l WHERE" in normalized
            assert f"%{identity}%" in params
            return Cursor()

    provider = PgvectorProvider(dsn="postgresql://u:p@h/db", pool=3)
    provider._fts_ok = True

    ids = provider._lexical_ids(
        Connection(),
        query,
        codename="c",
        repo="r",
        now=_NOW,
    )

    assert ids == ["matching"]


@pytest.mark.parametrize(
    ("query", "identity", "wrong_text", "matching_text"),
    [
        (
            "Fix Python 3 migration",
            "python 3",
            "python 2 migration guidance",
            "python 3 migration guidance",
        ),
        (
            "Fix Node 22 runtime",
            "node 22",
            "node 20 runtime guidance",
            "node 22 runtime guidance",
        ),
        (
            "Fix Node.js 22 runtime",
            "node.js 22",
            "node.js 20 runtime guidance",
            "node.js 22 runtime guidance",
        ),
        (
            "Fix NodeJS 22 runtime",
            "nodejs 22",
            "nodejs 20 runtime guidance",
            "nodejs 22 runtime guidance",
        ),
    ],
)
def test_lexical_like_fallback_requires_contextual_major_version_identity(
    query: str,
    identity: str,
    wrong_text: str,
    matching_text: str,
) -> None:
    class Cursor:
        def fetchall(self) -> list[tuple[Any, ...]]:
            return [
                ("wrong", wrong_text, _NOW),
                ("matching", matching_text, _NOW - timedelta(seconds=1)),
            ]

    class Connection:
        def execute(self, sql: str, params: list[Any]) -> Cursor:
            normalized = " ".join(sql.split())
            assert "body_tsv" not in normalized
            assert "FROM lessons l WHERE" in normalized
            assert f"%{identity}%" in params
            return Cursor()

    provider = PgvectorProvider(dsn="postgresql://u:p@h/db", pool=2)
    provider._fts_ok = True

    ids = provider._lexical_ids(
        Connection(),
        query,
        codename="c",
        repo="r",
        now=_NOW,
    )

    assert ids == ["matching"]


@pytest.mark.parametrize(
    ("query_alias", "lesson_alias"),
    [
        ("Node", "Node.js"),
        ("Node", "NodeJS"),
        ("Node.js", "Node"),
        ("Node.js", "NodeJS"),
        ("NodeJS", "Node"),
        ("NodeJS", "Node.js"),
    ],
)
def test_lexical_like_fallback_retrieves_cross_alias_node_major(
    query_alias: str,
    lesson_alias: str,
) -> None:
    matching_text = f"{lesson_alias.casefold()} 22 runtime guidance"

    class Cursor:
        def __init__(self, rows: list[tuple[Any, ...]]) -> None:
            self.rows = rows

        def fetchall(self) -> list[tuple[Any, ...]]:
            return self.rows

    class Connection:
        def execute(self, sql: str, params: list[Any]) -> Cursor:
            normalized = " ".join(sql.split())
            assert "body_tsv" not in normalized
            assert "FROM lessons l WHERE" in normalized
            patterns = [
                value.strip("%")
                for value in params
                if isinstance(value, str) and value.startswith("%")
            ]
            rows = (
                [("matching", matching_text, _NOW)]
                if any(pattern in matching_text for pattern in patterns)
                else []
            )
            return Cursor(rows)

    provider = PgvectorProvider(dsn="postgresql://u:p@h/db", pool=2)
    provider._fts_ok = True

    ids = provider._lexical_ids(
        Connection(),
        f"Fix {query_alias} 22 runtime",
        codename="c",
        repo="r",
        now=_NOW,
    )

    assert ids == ["matching"]


def test_lexical_like_fallback_recalls_unicode_query() -> None:
    query = "認証エラーを修正"
    lesson_body = f"手順: {query}してください"

    class Cursor:
        def fetchall(self) -> list[tuple[Any, ...]]:
            return [("match", lesson_body.casefold(), _NOW)]

    class Connection:
        def execute(self, sql: str, _params: Any) -> Cursor:
            normalized = " ".join(sql.split())
            if "FROM lessons l WHERE" in normalized:
                return Cursor()
            raise AssertionError(f"unexpected SQL: {normalized}")

    provider = PgvectorProvider(dsn="postgresql://u:p@h/db", pool=2)
    provider._fts_ok = False

    ids = provider._lexical_ids(Connection(), query, codename="c", repo="r", now=_NOW)

    assert ids == ["match"]


def test_lexical_like_fallback_requires_unicode_subject_in_mixed_query() -> None:
    class Cursor:
        def fetchall(self) -> list[tuple[Any, ...]]:
            return [
                ("api-only", "the api client retries requests", _NOW),
                (
                    "relevant",
                    "api の課金エラーを修正する手順",
                    _NOW - timedelta(seconds=1),
                ),
            ]

    class Connection:
        def execute(self, sql: str, _params: Any) -> Cursor:
            normalized = " ".join(sql.split())
            if "FROM lessons l WHERE" in normalized:
                return Cursor()
            raise AssertionError(f"unexpected SQL: {normalized}")

    provider = PgvectorProvider(dsn="postgresql://u:p@h/db", pool=2)
    provider._fts_ok = False

    ids = provider._lexical_ids(
        Connection(),
        "API の課金エラーを修正",
        codename="c",
        repo="r",
        now=_NOW,
    )

    assert ids == ["relevant"]


def test_lexical_like_fallback_requires_devanagari_subject_in_mixed_query() -> None:
    class Cursor:
        def fetchall(self) -> list[tuple[Any, ...]]:
            return [
                ("api-only", "the api client retries requests", _NOW),
                (
                    "relevant",
                    "api डेटा मिटाएँ प्रक्रिया",
                    _NOW - timedelta(seconds=1),
                ),
            ]

    class Connection:
        def execute(self, sql: str, _params: Any) -> Cursor:
            normalized = " ".join(sql.split())
            if "FROM lessons l WHERE" in normalized:
                return Cursor()
            raise AssertionError(f"unexpected SQL: {normalized}")

    provider = PgvectorProvider(dsn="postgresql://u:p@h/db", pool=2)
    provider._fts_ok = False

    ids = provider._lexical_ids(
        Connection(),
        "API डेटा मिटाएँ",
        codename="c",
        repo="r",
        now=_NOW,
    )

    assert ids == ["relevant"]


def test_lexical_like_fallback_requires_single_character_unicode_subject() -> None:
    class Cursor:
        def fetchall(self) -> list[tuple[Any, ...]]:
            return [
                ("api-only", "api billing guidance", _NOW),
                ("relevant", "api 税 guidance", _NOW - timedelta(seconds=1)),
            ]

    class Connection:
        def execute(self, sql: str, _params: Any) -> Cursor:
            normalized = " ".join(sql.split())
            if "FROM lessons l WHERE" in normalized:
                return Cursor()
            raise AssertionError(f"unexpected SQL: {normalized}")

    provider = PgvectorProvider(dsn="postgresql://u:p@h/db", pool=2)
    provider._fts_ok = False

    ids = provider._lexical_ids(
        Connection(),
        "API 税",
        codename="c",
        repo="r",
        now=_NOW,
    )

    assert ids == ["relevant"]


def test_lexical_like_fallback_matches_singular_lesson_for_plural_query() -> None:
    class Cursor:
        def fetchall(self) -> list[tuple[Any, ...]]:
            return [
                ("graphql-only", "graphql resolver guidance", _NOW),
                ("relevant", "graphql schema guidance", _NOW - timedelta(seconds=1)),
            ]

    class Connection:
        def execute(self, sql: str, _params: Any) -> Cursor:
            normalized = " ".join(sql.split())
            if "FROM lessons l WHERE" in normalized:
                return Cursor()
            raise AssertionError(f"unexpected SQL: {normalized}")

    provider = PgvectorProvider(dsn="postgresql://u:p@h/db", pool=2)
    provider._fts_ok = False

    ids = provider._lexical_ids(
        Connection(),
        "Fix GraphQL schemas",
        codename="c",
        repo="r",
        now=_NOW,
    )

    assert ids == ["relevant"]


def test_lexical_like_fallback_does_not_require_ordinary_slash_path() -> None:
    class Cursor:
        def fetchall(self) -> list[tuple[Any, ...]]:
            return [
                ("graphql-only", "graphql resolver guidance", _NOW),
                ("relevant", "graphql schema guidance", _NOW - timedelta(seconds=1)),
            ]

    class Connection:
        def execute(self, sql: str, _params: Any) -> Cursor:
            normalized = " ".join(sql.split())
            if "FROM lessons l WHERE" in normalized:
                return Cursor()
            raise AssertionError(f"unexpected SQL: {normalized}")

    provider = PgvectorProvider(dsn="postgresql://u:p@h/db", pool=2)
    provider._fts_ok = False

    ids = provider._lexical_ids(
        Connection(),
        "Fix src/api GraphQL schema",
        codename="c",
        repo="r",
        now=_NOW,
    )

    assert ids == ["relevant"]


@pytest.mark.parametrize(
    ("query_term", "lesson_term"),
    [
        ("policies", "policies"),
        ("policy", "policies"),
        ("policies", "policy"),
        ("analyses", "analyses"),
        ("analysis", "analyses"),
        ("analyses", "analysis"),
        ("process", "processes"),
        ("processes", "process"),
        ("watch", "watches"),
        ("watches", "watch"),
        ("box", "boxes"),
        ("boxes", "box"),
        ("patch", "patches"),
        ("patches", "patch"),
        ("branch", "branches"),
        ("branches", "branch"),
        ("alias", "aliases"),
        ("aliases", "alias"),
        ("bias", "biases"),
        ("biases", "bias"),
        ("focus", "focuses"),
        ("focuses", "focus"),
        ("class", "classes"),
        ("classes", "class"),
        ("bus", "buses"),
        ("buses", "bus"),
    ],
)
def test_lexical_like_fallback_preserves_inflection_retrieval_variants(
    query_term: str,
    lesson_term: str,
) -> None:
    lexical_text = f"graphql {lesson_term} must be reviewed"

    class Cursor:
        def __init__(self, rows: list[tuple[Any, ...]]) -> None:
            self.rows = rows

        def fetchall(self) -> list[tuple[Any, ...]]:
            return self.rows

    class Connection:
        def execute(self, sql: str, params: list[Any]) -> Cursor:
            normalized = " ".join(sql.split())
            if "FROM lessons l WHERE" not in normalized:
                raise AssertionError(f"unexpected SQL: {normalized}")
            patterns = [
                value.strip("%")
                for value in params
                if isinstance(value, str) and value.startswith("%")
            ]
            threshold = next(value for value in params if isinstance(value, int))
            matches = sum(pattern in lexical_text for pattern in patterns)
            rows = [("relevant", lexical_text, _NOW)] if matches >= threshold else []
            return Cursor(rows)

    provider = PgvectorProvider(dsn="postgresql://u:p@h/db", pool=2)
    provider._fts_ok = False

    ids = provider._lexical_ids(
        Connection(),
        f"Fix GraphQL {query_term}",
        codename="c",
        repo="r",
        now=_NOW,
    )

    assert ids == ["relevant"]


def test_lexical_like_fallback_matches_unicode_subject_stored_only_in_tag() -> None:
    class Cursor:
        def __init__(self, rows: list[tuple[Any, ...]]) -> None:
            self.rows = rows

        def fetchall(self) -> list[tuple[Any, ...]]:
            return self.rows

    class Connection:
        def execute(self, sql: str, _params: Any) -> Cursor:
            normalized = " ".join(sql.split())
            assert normalized.startswith("SELECT l.id, l.lexical_text")
            return Cursor([("match", "api billing guidance 課金", _NOW)])

    provider = PgvectorProvider(dsn="postgresql://u:p@h/db", pool=2)
    provider._fts_ok = False

    ids = provider._lexical_ids(
        Connection(),
        "API 課金",
        codename="c",
        repo="r",
        now=_NOW,
    )

    assert ids == ["match"]


def test_token_empty_literal_lookup_is_bounded_escaped_and_scoped() -> None:
    calls: list[tuple[str, list[Any]]] = []

    class Cursor:
        def fetchall(self) -> list[tuple[Any, ...]]:
            return [("match",)]

    class Connection:
        def execute(self, sql: str, params: list[Any]) -> Cursor:
            calls.append((" ".join(sql.split()), params))
            return Cursor()

    provider = PgvectorProvider(dsn="postgresql://u:p@h/db", pool=1000)

    ids = provider._lexical_ids(Connection(), "修_%", codename="c", repo="r", now=_NOW)

    assert ids == ["match"]
    assert len(calls) == 1
    sql, params = calls[0]
    assert "l.lexical_text ILIKE %s ESCAPE '\\'" in sql
    assert "l.codename = %s" in sql
    assert "l.repo = %s" in sql
    assert params[0] == r"%修\_\%%"
    assert params[-1] == 400


def test_token_empty_literal_lookup_nfkc_normalizes_before_prefilter() -> None:
    calls: list[list[Any]] = []

    class Cursor:
        def fetchall(self) -> list[tuple[Any, ...]]:
            return [("match",)]

    class Connection:
        def execute(self, _sql: str, params: list[Any]) -> Cursor:
            calls.append(params)
            return Cursor()

    provider = PgvectorProvider(dsn="postgresql://u:p@h/db", pool=2)

    assert provider._lexical_ids(Connection(), "ｴﾗｰ", codename="c", repo="r", now=_NOW) == ["match"]
    assert calls[0][0] == "%エラー%"


@pytest.mark.parametrize(
    ("query", "lesson_body"),
    [
        ("Fix cold-start handling", "Use cold start handling in the request path."),
        ("Fix cold start handling", "Use cold-start handling in the request path."),
    ],
)
def test_lexical_like_fallback_matches_hyphenated_spelling_variants(
    query: str,
    lesson_body: str,
) -> None:
    class Cursor:
        def fetchall(self) -> list[tuple[Any, ...]]:
            return [("match", lesson_body.casefold(), _NOW)]

    class Connection:
        def execute(self, sql: str, _params: Any) -> Cursor:
            normalized = " ".join(sql.split())
            if "FROM lessons l WHERE" in normalized:
                return Cursor()
            raise AssertionError(f"unexpected SQL: {normalized}")

    provider = PgvectorProvider(dsn="postgresql://u:p@h/db", pool=2)
    provider._fts_ok = False

    ids = provider._lexical_ids(
        Connection(),
        query,
        codename="c",
        repo="r",
        now=_NOW,
    )

    assert ids == ["match"]


def test_recency_query_is_scoped_and_ordered() -> None:
    sql, params = _recency_query(table="lessons", codename="c", repo="r", limit=4, now=_NOW)
    assert "ORDER BY l.created_at DESC" in sql
    assert "l.codename = %s" in sql
    assert params[-1] == 4


def test_vector_literal_round_trips_floats() -> None:
    assert _vector_literal([0.0, 1.5, -2.0]) == "[0.0,1.5,-2.0]"


# ---------------------------------------------------------------------------
# RRF fusion parity: the SAME function object as the SQLite hybrid store
# ---------------------------------------------------------------------------


def test_rrf_is_the_same_function_as_sqlite_hybrid() -> None:
    from memory.sqlite_hybrid import _reciprocal_rank_fusion as sqlite_rrf

    assert _reciprocal_rank_fusion is sqlite_rrf


def test_rrf_fuses_two_ranked_lists() -> None:
    fused = _reciprocal_rank_fusion(["a", "b", "c"], ["b", "d"], k=60)
    order = [lid for lid, _ in fused]
    # "b" appears in both lists so it fuses to the top.
    assert order[0] == "b"
    assert set(order) == {"a", "b", "c", "d"}


# ---------------------------------------------------------------------------
# DSN redaction: health must never leak a password
# ---------------------------------------------------------------------------


def test_redact_dsn_scrubs_url_password() -> None:
    assert _redact_dsn("postgresql://user:s3cret@host:5432/db") == (
        "postgresql://user:***@host:5432/db"
    )


def test_redact_dsn_scrubs_keyword_password() -> None:
    out = _redact_dsn("host=db.local password=s3cret dbname=alfred")
    assert "s3cret" not in out
    assert "password=***" in out


def test_redact_dsn_handles_no_password() -> None:
    assert _redact_dsn("postgresql://host/db") == "postgresql://host/db"
    assert _redact_dsn("") == ""


def test_redact_dsn_scrubs_quoted_keyword_password_with_spaces() -> None:
    # A keyword DSN whose password is quoted and contains spaces must be redacted
    # whole; a naive whitespace split would leak the tail of the secret.
    out = _redact_dsn("host=db.local password='my secret pw' dbname=alfred")
    assert "my secret pw" not in out
    assert "secret" not in out
    assert "pw" not in out
    assert "password=***" in out
    # Non-secret fields survive.
    assert "host=db.local" in out
    assert "dbname=alfred" in out


def test_redact_dsn_scrubs_uri_query_password() -> None:
    # A password carried as a URI query parameter is scrubbed too.
    out = _redact_dsn("postgresql://user@host:5432/db?sslmode=require&password=s3cret")
    assert "s3cret" not in out
    assert "password=***" in out


@pytest.mark.parametrize(
    "dsn",
    [
        "host=db.local password = 'my secret pw' dbname=alfred",  # spaced '=', quoted
        "host=db.local password ='my secret pw' dbname=alfred",  # space before '='
        "host=db.local password= 'my secret pw' dbname=alfred",  # space after '='
        "password = 'my secret pw'",  # leading, spaced
    ],
)
def test_redact_dsn_handles_spaced_equals_libpq_password(dsn: str) -> None:
    # libpq allows whitespace around '='; a shlex/whitespace split would tear
    # `password = 'my secret pw'` into separate tokens and leak the value.
    out = _redact_dsn(dsn)
    assert "my secret pw" not in out
    assert "secret" not in out
    assert "password=***" in out


def test_redact_dsn_scrubs_sslpassword_keyword() -> None:
    # sslpassword (client-key passphrase) is a secret too.
    out = _redact_dsn("host=db.local sslpassword='key pass' sslmode=require")
    assert "key pass" not in out
    assert "sslpassword=***" in out
    assert "sslmode=require" in out


def test_health_output_fully_redacts_quoted_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The finding is about health() output: with psycopg absent, health() reports
    # ok=False and the REDACTED dsn, and the quoted secret must not appear anywhere.
    monkeypatch.setattr(mod, "_psycopg", None)
    provider = PgvectorProvider(dsn="host=db.local password='my secret pw' dbname=alfred")
    report = provider.health()
    assert report["ok"] is False
    assert "my secret pw" not in report["dsn"]
    assert "my secret pw" not in str(report)
    assert "secret" not in str(report)
    # URI-form password stays redacted in health() as well.
    uri_provider = PgvectorProvider(dsn="postgresql://u:topsecret@h:5432/db")
    assert "topsecret" not in uri_provider.health()["dsn"]


# ---------------------------------------------------------------------------
# Registration + arming: opt-in, never the default, never a hard dependency
# ---------------------------------------------------------------------------


def test_registered_as_opt_in_lesson_store() -> None:
    assert "pgvector" in PROVIDER_REGISTRY
    assert "pgvector" in LESSON_STORE_NAMES


def test_default_chain_is_unchanged() -> None:
    # The scale tier must never displace the zero-daemon SQLite default.
    assert DEFAULT_PROVIDER_NAMES == ["sqlite", "fleet"]


def test_from_env_requires_psycopg(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force the "psycopg not installed" branch deterministically, even on a box
    # that has it. This is UNAVAILABLE (not armed), so it is the type the chain
    # builders skip -- distinct from a misconfiguration.
    monkeypatch.setattr(mod, "_psycopg", None)
    with pytest.raises(MemoryProviderUnavailable, match="psycopg"):
        PgvectorProvider.from_env(env={"ALFRED_MEMORY_PG_DSN": "postgresql://h/db"})


def test_from_env_requires_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_psycopg", object())  # pretend psycopg is present
    with pytest.raises(MemoryProviderUnavailable, match="ALFRED_MEMORY_PG_DSN"):
        PgvectorProvider.from_env(env={})


def test_unarmed_pgvector_falls_through_the_chain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # ALFRED_MEMORY_PROVIDERS=pgvector,fleet with no DSN: pgvector is skipped and
    # the chain falls through to fleet. Never a hard dependency.
    monkeypatch.setattr(mod, "_psycopg", None)
    provider = load_provider(
        env={"ALFRED_MEMORY_PROVIDERS": "pgvector,fleet", "ALFRED_HOME": str(tmp_path)}
    )
    # Only fleet survived, so the single-provider unwrap returns it directly.
    assert getattr(provider, "name", None) == "fleet"


def test_from_env_builds_when_armed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_psycopg", object())
    provider = PgvectorProvider.from_env(
        env={
            "ALFRED_MEMORY_PG_DSN": "postgresql://u:p@h/db",
            "ALFRED_MEMORY_PG_INDEX": "ivfflat",
            "ALFRED_MEMORY_PG_RRF_K": "42",
        }
    )
    assert isinstance(provider, MemoryProvider)
    assert provider.name == "pgvector"
    assert provider.index_kind == "ivfflat"
    assert provider.rrf_k == 42


# ---------------------------------------------------------------------------
# Table prefix is a SQL identifier, not an injection surface
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_prefix",
    ["alfred-prod", "a b", "9lead", 'x"; DROP TABLE lessons; --', "tbl;", "a.b"],
)
def test_unsafe_table_prefix_is_rejected(bad_prefix: str) -> None:
    # A prefix that is not a valid SQL identifier must raise a clear config error
    # rather than being raw-interpolated into DDL/query identifiers. It is a
    # MemoryProviderMisconfigured (a ValueError) so the runtime loaders surface it.
    with pytest.raises(MemoryProviderMisconfigured, match="ALFRED_MEMORY_PG_TABLE_PREFIX"):
        PgvectorProvider(dsn="postgresql://u:p@h/db", table_prefix=bad_prefix)


def test_safe_table_prefix_composes_valid_identifiers() -> None:
    provider = PgvectorProvider(dsn="postgresql://u:p@h/db", table_prefix="alfred_prod_")
    # Composed table names are valid unquoted identifiers, no stray characters.
    assert provider._lessons == "alfred_prod_lessons"
    assert provider._anchors == "alfred_prod_lesson_anchors"
    assert provider._reuse == "alfred_prod_lesson_reuse"
    # And they flow through the SQL builders verbatim (no injection, no breakage).
    sql, _ = _recency_query(table=provider._lessons, codename="c", repo="r", limit=1, now=_NOW)
    assert "FROM alfred_prod_lessons l" in sql


def test_empty_table_prefix_is_the_default() -> None:
    provider = PgvectorProvider(dsn="postgresql://u:p@h/db")
    assert provider._lessons == "lessons"


def test_from_env_rejects_unsafe_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_psycopg", object())
    with pytest.raises(ValueError, match="ALFRED_MEMORY_PG_TABLE_PREFIX"):
        PgvectorProvider.from_env(
            env={
                "ALFRED_MEMORY_PG_DSN": "postgresql://u:p@h/db",
                "ALFRED_MEMORY_PG_TABLE_PREFIX": "alfred-prod",
            }
        )


def test_over_long_table_prefix_is_rejected() -> None:
    # A prefix that still matches the identifier allowlist but is long enough to
    # push a generated identifier past PostgreSQL's 63-byte limit (where it would
    # be silently truncated) must be rejected with a clear error.
    too_long = "a" * (_MAX_TABLE_PREFIX_LEN + 1)
    with pytest.raises(MemoryProviderMisconfigured, match="too long"):
        PgvectorProvider(dsn="postgresql://u:p@h/db", table_prefix=too_long)


def test_max_length_prefix_keeps_generated_identifiers_within_limit() -> None:
    # A prefix at exactly the max length composes valid identifiers, and EVERY
    # identifier the provider actually emits during schema creation stays within
    # 63 bytes. Driving the real schema against a recording connection re-derives
    # the identifiers, so a future longer index name would trip this test until
    # the prefix bound is updated.
    prefix = "p" * _MAX_TABLE_PREFIX_LEN
    provider = PgvectorProvider(
        dsn="postgresql://u:p@h/db",
        table_prefix=prefix,
        dense=True,
        embedder=lambda _text: [0.0] * 4,
        dimensions=4,
        index_kind="ivfflat",  # the longest of the two vector index names
    )
    rec = _RecordingConn()
    provider._conn = rec
    provider._ensure_schema(rec)

    identifiers = _generated_identifiers(rec.sql)
    assert identifiers, "expected the schema to create at least one identifier"
    oversized = {i: len(i.encode("utf-8")) for i in identifiers if len(i.encode("utf-8")) > 63}
    assert not oversized, f"identifiers exceed 63 bytes: {oversized}"
    # The bound is tight: the longest generated identifier lands exactly on 63.
    assert max(len(i.encode("utf-8")) for i in identifiers) == _MAX_PG_IDENTIFIER_BYTES


# ---------------------------------------------------------------------------
# Writer resolution degrades to the next store when pgvector is unarmed
# ---------------------------------------------------------------------------


def test_unarmed_pgvector_writer_falls_back_to_fleet(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # ALFRED_MEMORY_PROVIDERS=pgvector,fleet with psycopg absent: the promoted-
    # lesson writer must degrade to fleet, not error or drop the write.
    monkeypatch.setattr(mod, "_psycopg", None)
    env = {"ALFRED_MEMORY_PROVIDERS": "pgvector,fleet", "ALFRED_HOME": str(tmp_path)}
    writer = load_lesson_writer(env=env)
    assert writer is not None
    assert writer.name == "fleet"
    # And a promotion round-trips through the fallback writer: recall then finds
    # it via the same chain, so no write is silently lost.
    writer.reflect(
        codename="lucius",
        repo="acme/api",
        body="Fallback lesson stored via fleet when pgvector is unarmed",
        memory_id="fallback-1",
    )
    recalled = load_provider(env=env).recall(
        query="fallback lesson", codename="lucius", repo="acme/api"
    )
    assert "fallback-1" in {L.id for L in recalled}


def test_misconfigured_pgvector_writer_surfaces_not_silent_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # pgvector is ARMED (psycopg present + a DSN) but MISCONFIGURED (invalid table
    # prefix). This is an operator typo, not an unavailable backend, so the writer
    # must RAISE the config error rather than silently degrading to fleet and
    # writing lessons somewhere the operator did not intend.
    monkeypatch.setattr(mod, "_psycopg", object())
    env = {
        "ALFRED_MEMORY_PROVIDERS": "pgvector,fleet",
        "ALFRED_MEMORY_PG_DSN": "postgresql://u:p@h/db",
        "ALFRED_MEMORY_PG_TABLE_PREFIX": "alfred-prod",
    }
    with pytest.raises(ValueError, match="ALFRED_MEMORY_PG_TABLE_PREFIX"):
        load_lesson_writer(env=env)


def test_misconfigured_pgvector_recall_surfaces_not_silent_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Same on the recall path: build_chain skips an UNAVAILABLE provider but must
    # propagate a MISCONFIGURATION so the typo is not silently swallowed.
    monkeypatch.setattr(mod, "_psycopg", object())
    env = {
        "ALFRED_MEMORY_PROVIDERS": "pgvector,fleet",
        "ALFRED_MEMORY_PG_DSN": "postgresql://u:p@h/db",
        "ALFRED_MEMORY_PG_TABLE_PREFIX": "alfred-prod",
    }
    with pytest.raises(ValueError, match="ALFRED_MEMORY_PG_TABLE_PREFIX"):
        load_provider(env=env)


def test_runtime_loader_surfaces_misconfig_instead_of_disabling_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The runtime memory loader degrades to None on an unavailable backend, but a
    # MISCONFIGURATION must surface: otherwise a firing would start with recall
    # memory silently disabled instead of the operator's typo being reported.
    monkeypatch.setattr(mod, "_psycopg", object())
    env = {
        "ALFRED_MEMORY_PROVIDERS": "pgvector,fleet",
        "ALFRED_MEMORY_PG_DSN": "postgresql://u:p@h/db",
        "ALFRED_MEMORY_PG_TABLE_PREFIX": "alfred-prod",
    }
    with pytest.raises(MemoryProviderMisconfigured, match="ALFRED_MEMORY_PG_TABLE_PREFIX"):
        load_runtime_memory(env=env)


def test_runtime_loader_degrades_when_pgvector_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Unavailable (not armed) must still degrade gracefully: the loader returns
    # the rest of the chain (fleet), never None and never a raise.
    monkeypatch.setattr(mod, "_psycopg", None)
    env = {"ALFRED_MEMORY_PROVIDERS": "pgvector,fleet", "ALFRED_HOME": str(tmp_path)}
    provider = load_runtime_memory(env=env)
    assert provider is not None
    assert provider.name == "fleet"


# ---------------------------------------------------------------------------
# Consolidation surface driven against a fake connection (no daemon)
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None, rowcount: int = 0) -> None:
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class _ManyCursor:
    """Cursor handle for ``conn.cursor().executemany(...)`` (bump path)."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def executemany(self, sql: str, seq: list[tuple[Any, ...]]) -> None:
        for params in seq:
            self._conn.execute(sql, params)


class _RecordingConn:
    """psycopg-shaped fake that only records the SQL it is asked to execute.

    Used to drive ``_ensure_schema`` without a live Postgres so a test can assert
    every generated table/index identifier stays within the 63-byte limit.
    """

    def __init__(self) -> None:
        self.closed = False
        self.sql: list[str] = []

    @contextmanager
    def transaction(self) -> Any:
        yield self

    def cursor(self) -> _ManyCursor:
        return _ManyCursor(self)

    def execute(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> _FakeCursor:
        self.sql.append(" ".join(sql.split()))
        return _FakeCursor()


class _LexicalMigrationConn(_RecordingConn):
    def __init__(self) -> None:
        super().__init__()
        self.has_lexical_column = False
        self.rows = [
            ("legacy-a", "Use Ａ／Ｂ testing", '["Ｆ＃"]'),  # noqa: RUF001
            ("legacy-b", "Straße caching", "[]"),
        ]
        self.updates: list[tuple[str, str]] = []
        self.tsv_updates: list[tuple[str, str]] = []

    def execute(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> _FakeCursor:
        normalized = " ".join(sql.split())
        self.sql.append(normalized)
        if "FROM information_schema.columns" in normalized:
            return _FakeCursor([(self.has_lexical_column,)])
        if "ADD COLUMN IF NOT EXISTS lexical_text" in normalized:
            self.has_lexical_column = True
            return _FakeCursor()
        if normalized.startswith("SELECT id, body, tags_json FROM lessons"):
            after = str(params[0])
            rows = [row for row in self.rows if row[0] > after]
            return _FakeCursor(rows)
        if normalized.startswith("UPDATE lessons SET lexical_text"):
            surface, lesson_id = str(params[0]), str(params[-1])
            self.updates.append((surface, lesson_id))
            if "body_tsv = to_tsvector('english', %s)" in normalized:
                self.tsv_updates.append((str(params[1]), lesson_id))
            return _FakeCursor(rowcount=1)
        return _FakeCursor()


def test_schema_migration_backfills_canonical_lexical_surface_once() -> None:
    conn = _LexicalMigrationConn()

    PgvectorProvider(dsn="postgresql://u:p@h/db")._ensure_schema(conn)

    assert conn.updates == [
        ("use a/b testing f#", "legacy-a"),
        ("strasse caching", "legacy-b"),
    ]
    assert conn.tsv_updates == [
        ("use a/b testing f#", "legacy-a"),
        ("strasse caching", "legacy-b"),
    ]
    assert any("lexical_text = ''" in sql for sql in conn.sql)

    conn.updates.clear()
    conn.tsv_updates.clear()
    PgvectorProvider(dsn="postgresql://u:p@h/db")._ensure_schema(conn)

    assert conn.updates == []
    assert conn.tsv_updates == []


def _generated_identifiers(sql_log: list[str]) -> set[str]:
    """Extract the table/index identifiers a schema run created."""
    ids: set[str] = set()
    patterns = (
        r"CREATE TABLE IF NOT EXISTS (\w+)",
        r"CREATE INDEX IF NOT EXISTS (\w+)",
        r"ALTER TABLE (\w+)",
    )
    for statement in sql_log:
        for pat in patterns:
            m = re.search(pat, statement)
            if m:
                ids.add(m.group(1))
    return ids


class _VectorFailConn:
    """psycopg-shaped fake whose vector UPDATE raises, tracking transaction depth.

    Models the failure mode the savepoint isolation guards against: a rejected
    vector write inside the reflect() transaction. ``transaction()`` tracks
    nesting so a test can assert the vector write ran inside its own savepoint
    (a nested transaction) and that the outer transaction kept going after the
    isolated failure.
    """

    def __init__(self) -> None:
        self.closed = False
        self.depth = 0
        self.max_depth = 0
        self.executed: list[str] = []

    @contextmanager
    def transaction(self) -> Any:
        self.depth += 1
        self.max_depth = max(self.max_depth, self.depth)
        try:
            yield self
        finally:
            self.depth -= 1

    def execute(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> _FakeCursor:
        s = " ".join(sql.split())
        self.executed.append(s)
        if "SET embedding = %s::vector" in s:
            raise RuntimeError("vector rejected by postgres")
        return _FakeCursor()


def _make_lesson(body: str = "dense lesson", lesson_id: str = "L1") -> Lesson:
    return Lesson(
        id=lesson_id,
        codename="lucius",
        repo="acme/api",
        body=body,
        tags=[],
        created_at=_NOW,
        firing_id=None,
    )


def test_vector_write_failure_is_isolated_and_outer_transaction_survives() -> None:
    provider = PgvectorProvider(
        dsn="postgresql://u:p@h/db",
        dense=True,
        embedder=lambda _text: [0.1] * 4,
        dimensions=4,
    )
    provider._vec_ok = True
    conn = _VectorFailConn()
    # Simulate reflect()'s outer transaction: lesson insert, then the failing
    # vector write, then an anchor insert that must still run.
    with conn.transaction():
        conn.execute("INSERT INTO lessons ...")
        provider._write_vector(conn, _make_lesson())  # rejected vector, isolated
        conn.execute("INSERT INTO lesson_anchors ...")
    # The vector write opened its own savepoint (nested transaction)...
    assert conn.max_depth == 2
    # ...it did not propagate (the outer transaction was never aborted)...
    assert any("SET embedding = %s::vector" in s for s in conn.executed)
    # ...and the statement AFTER the failure still ran (lesson not lost).
    assert any("INSERT INTO lesson_anchors" in s for s in conn.executed)


class _FakePgConn:
    """Minimal psycopg-shaped fake modelling the three provider tables.

    Interprets exactly the statements ``merge_lesson`` / ``union_reuse_counts`` /
    ``bump_reuse_counts`` / ``get_reuse_count`` issue, so the UNION logic is
    exercised without a live Postgres. Unrecognized SQL raises, so the test
    fails loudly if the provider's SQL drifts.
    """

    def __init__(self) -> None:
        self.closed = False
        self.lessons: dict[str, dict[str, Any]] = {}
        self.anchors: list[dict[str, Any]] = []
        self.reuse: dict[str, int] = {}

    def seed_lesson(self, lid: str, *, codename: str, repo: str, provenance: str | None) -> None:
        self.lessons[lid] = {
            "codename": codename,
            "repo": repo,
            "provenance": provenance,
            "superseded_by": None,
            "valid_until": None,
        }

    @contextmanager
    def transaction(self) -> Any:
        yield self

    def cursor(self) -> _ManyCursor:
        return _ManyCursor(self)

    def execute(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> _FakeCursor:
        s = " ".join(sql.split())
        p = list(params)
        if s.startswith("SELECT provenance, codename, repo FROM lessons WHERE id ="):
            row = self.lessons.get(p[0])
            rows = [(row["provenance"], row["codename"], row["repo"])] if row else []
            return _FakeCursor(rows)
        if s.startswith("UPDATE lessons SET provenance = %s WHERE id ="):
            prov, lid = p
            if lid in self.lessons:
                self.lessons[lid]["provenance"] = prov
                return _FakeCursor(rowcount=1)
            return _FakeCursor(rowcount=0)
        if s.startswith("UPDATE lessons SET superseded_by = %s, valid_until = %s WHERE id ="):
            sby, vu, lid = p
            if lid in self.lessons:
                self.lessons[lid]["superseded_by"] = sby
                self.lessons[lid]["valid_until"] = vu
                return _FakeCursor(rowcount=1)
            return _FakeCursor(rowcount=0)
        if s.startswith("SELECT reuse_count FROM lesson_reuse WHERE scope_key ="):
            key = p[0]
            rows = [(self.reuse[key],)] if key in self.reuse else []
            return _FakeCursor(rows)
        if s.startswith("INSERT INTO lesson_reuse"):
            if "VALUES (%s, 1, %s)" in s:  # bump path: literal +1
                key, _ts = p
                self.reuse[key] = self.reuse.get(key, 0) + 1
            else:  # union path: += EXCLUDED.reuse_count
                key, cnt, _ts = p
                self.reuse[key] = self.reuse.get(key, 0) + cnt
            return _FakeCursor(rowcount=1)
        if s.startswith("DELETE FROM lesson_reuse WHERE scope_key ="):
            self.reuse.pop(p[0], None)
            return _FakeCursor(rowcount=1)
        if s.startswith("SELECT anchor_type, anchor_ref, relation, repo FROM lesson_anchors"):
            lid = p[0]
            rows = [
                (a["anchor_type"], a["anchor_ref"], a["relation"], a["repo"])
                for a in self.anchors
                if a["lesson_id"] == lid
            ]
            return _FakeCursor(rows)
        if s.startswith("INSERT INTO lesson_anchors"):
            _id, lesson_id, atype, aref, rel, repo, _created = p
            key = (lesson_id, atype, aref, rel)
            if not any(
                (a["lesson_id"], a["anchor_type"], a["anchor_ref"], a["relation"]) == key
                for a in self.anchors
            ):
                self.anchors.append(
                    {
                        "lesson_id": lesson_id,
                        "anchor_type": atype,
                        "anchor_ref": aref,
                        "relation": rel,
                        "repo": repo,
                    }
                )
            return _FakeCursor(rowcount=1)
        raise AssertionError(f"unhandled SQL in fake: {s}")


def _fake_provider() -> tuple[PgvectorProvider, _FakePgConn]:
    provider = PgvectorProvider(dsn="postgresql://u:p@h/db")
    fake = _FakePgConn()
    # Inject the fake as the live connection and skip real connect + schema.
    provider._conn = fake
    provider._schema_ready = True
    return provider, fake


def test_query_miss_does_not_backfill_recent_lessons(monkeypatch: pytest.MonkeyPatch) -> None:
    provider, _ = _fake_provider()
    provider._vec_ok = False
    monkeypatch.setattr(provider, "_anchor_ids", lambda *args, **kwargs: [])
    monkeypatch.setattr(provider, "_lexical_ids", lambda *args, **kwargs: [])

    recency_calls: list[bool] = []

    def track_recency(*args: Any, **kwargs: Any) -> list[str]:
        recency_calls.append(True)
        return ["recent"]

    monkeypatch.setattr(provider, "_recency_ids", track_recency)
    monkeypatch.setattr(provider, "_hydrate", lambda _conn, ids: list(ids))

    assert provider.recall(query="missing topic", repo="acme/api") == []
    assert recency_calls == []


def test_unfiltered_recall_uses_recent_lessons(monkeypatch: pytest.MonkeyPatch) -> None:
    provider, _ = _fake_provider()
    provider._vec_ok = False
    monkeypatch.setattr(provider, "_anchor_ids", lambda *args, **kwargs: [])
    lexical_calls: list[bool] = []

    def track_lexical(*args: Any, **kwargs: Any) -> list[str]:
        lexical_calls.append(True)
        return []

    monkeypatch.setattr(provider, "_lexical_ids", track_lexical)
    monkeypatch.setattr(provider, "_recency_ids", lambda *args, **kwargs: ["recent"])
    monkeypatch.setattr(provider, "_hydrate", lambda _conn, ids: list(ids))

    assert provider.recall(repo="acme/api") == ["recent"]
    assert lexical_calls == []


def test_union_reuse_counts_moves_and_clears() -> None:
    provider, fake = _fake_provider()
    provider.bump_reuse_counts(["survivor", "survivor"])
    provider.bump_reuse_counts(["loser", "loser", "loser"])

    provider.union_reuse_counts("survivor", "loser")

    assert provider.get_reuse_count("survivor") == 5
    assert provider.get_reuse_count("loser") == 0
    assert "loser" not in fake.reuse


def test_union_reuse_counts_noop_on_bad_keys() -> None:
    provider, _ = _fake_provider()
    provider.bump_reuse_counts(["a"])
    provider.union_reuse_counts("a", "a")  # identical -> no-op
    provider.union_reuse_counts("a", "")  # blank loser -> no-op
    assert provider.get_reuse_count("a") == 1


def test_merge_lesson_unions_provenance_reuse_and_anchors() -> None:
    provider, fake = _fake_provider()
    fake.seed_lesson("keep", codename="lucius", repo="acme/api", provenance="firing-keep")
    fake.seed_lesson("dup", codename="lucius", repo="acme/api", provenance="firing-dup")
    fake.anchors.append(
        {
            "lesson_id": "dup",
            "anchor_type": "file",
            "anchor_ref": "src/b.py",
            "relation": "about",
            "repo": "acme/api",
        }
    )
    survivor_key = memory_ranking.scope_key(lesson_id="keep", codename="lucius", repo="acme/api")
    loser_key = memory_ranking.scope_key(lesson_id="dup", codename="lucius", repo="acme/api")
    provider.bump_reuse_counts([survivor_key, survivor_key])  # 2
    provider.bump_reuse_counts([loser_key, loser_key, loser_key])  # 3

    assert provider.merge_lesson("dup", "keep") is True

    # Provenance union, survivor first.
    assert fake.lessons["keep"]["provenance"] == "firing-keep, firing-dup"
    # Reuse union: survivor gets 2 + 3, loser row cleared.
    assert provider.get_reuse_count(survivor_key) == 5
    assert provider.get_reuse_count(loser_key) == 0
    # Anchor union: the loser's file anchor + a supersedes link now hang on keep.
    survivor_anchor_refs = {a["anchor_ref"] for a in fake.anchors if a["lesson_id"] == "keep"}
    assert {"src/b.py", "dup"}.issubset(survivor_anchor_refs)
    # Loser invalidated, not deleted.
    assert fake.lessons["dup"]["superseded_by"] == "keep"
    assert fake.lessons["dup"]["valid_until"] is not None


def test_merge_lesson_noops_on_bad_ids() -> None:
    provider, fake = _fake_provider()
    fake.seed_lesson("solo", codename="c", repo="r", provenance=None)
    assert provider.merge_lesson("", "solo") is False
    assert provider.merge_lesson("solo", "solo") is False
    assert provider.merge_lesson("missing", "solo") is False


# ---------------------------------------------------------------------------
# Live-DB integration (skipped unless a Postgres DSN + psycopg are available)
# ---------------------------------------------------------------------------

_LIVE_DSN = os.environ.get("ALFRED_MEMORY_PG_DSN", "").strip()


@pytest.mark.skipif(
    not _LIVE_DSN or not mod.psycopg_available(),
    reason="set ALFRED_MEMORY_PG_DSN and install psycopg to run the live pgvector test",
)
def test_live_write_recall_merge_forget_round_trip() -> None:
    provider = PgvectorProvider.from_env(env={**os.environ, "ALFRED_MEMORY_PG_DSN": _LIVE_DSN})
    a = provider.reflect(
        codename="lucius",
        repo="acme/api",
        body="GraphQL schema lives in src/schema.graphql",
        tags=["graphql"],
        memory_id="pgtest-a",
    )
    provider.reflect(
        codename="lucius",
        repo="acme/api",
        body="Rate limiting lives in the gateway module",
        memory_id="pgtest-b",
    )
    out = provider.recall(query="graphql schema", codename="lucius", repo="acme/api")
    assert a.id in {L.id for L in out}
    assert provider.health()["ok"] is True
    assert provider.forget_lesson("pgtest-a") is True
    assert provider.forget_lesson("pgtest-b") is True


@pytest.mark.skipif(
    not _LIVE_DSN or not mod.psycopg_available(),
    reason="set ALFRED_MEMORY_PG_DSN and install psycopg to run the live pgvector test",
)
def test_live_fts_deduplicates_stems_before_overlap_and_candidate_limit() -> None:
    provider = PgvectorProvider.from_env(
        env={
            **os.environ,
            "ALFRED_MEMORY_PG_DSN": _LIVE_DSN,
            "ALFRED_MEMORY_PG_DENSE": "0",
            "ALFRED_MEMORY_PG_POOL": "2",
        }
    )
    lesson_ids = ("pgtest-stem-relevant", "pgtest-stem-cache", "pgtest-stem-response")
    try:
        relevant = provider.reflect(
            codename="lucius",
            repo="acme/api",
            body="cache response",
            memory_id=lesson_ids[0],
        )
        provider.reflect(
            codename="lucius",
            repo="acme/api",
            body=" ".join(["cache"] * 50),
            memory_id=lesson_ids[1],
        )
        provider.reflect(
            codename="lucius",
            repo="acme/api",
            body=" ".join(["response"] * 50),
            memory_id=lesson_ids[2],
        )

        out = provider.recall(
            query="caching cached responses",
            codename="lucius",
            repo="acme/api",
        )

        assert [lesson.id for lesson in out] == [relevant.id]
    finally:
        for lesson_id in lesson_ids:
            provider.forget_lesson(lesson_id)
