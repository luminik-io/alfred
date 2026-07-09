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
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "lib"))

import memory.pgvector_provider as mod  # noqa: E402
from agent_runner import memory_ranking  # noqa: E402
from memory import MemoryProvider  # noqa: E402
from memory.config import (  # noqa: E402
    DEFAULT_PROVIDER_NAMES,
    LESSON_STORE_NAMES,
    PROVIDER_REGISTRY,
    load_provider,
)
from memory.pgvector_provider import (  # noqa: E402
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
        ["gateway", "rate"], table="lessons", codename="c", repo="r", pool=5, now=_NOW
    )
    assert "to_tsquery('english', %s)" in sql
    assert "ts_rank(l.body_tsv" in sql
    assert "l.codename = %s" in sql
    # OR-of-tokens tsquery, like the SQLite FTS arm.
    assert params[0] == "gateway | rate"
    assert params[-1] == 5


def test_lexical_like_fallback_matches_body_and_tags() -> None:
    sql, params = _lexical_like_query(
        ["graphql"], table="lessons", codename=None, repo="r", pool=3, now=_NOW
    )
    assert "l.body ILIKE %s OR l.tags_json ILIKE %s" in sql
    assert "%graphql%" in params
    assert params[-1] == 3


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
    # that has it: from_env must raise (which build_chain turns into a skip).
    monkeypatch.setattr(mod, "_psycopg", None)
    with pytest.raises(RuntimeError, match="psycopg"):
        PgvectorProvider.from_env(env={"ALFRED_MEMORY_PG_DSN": "postgresql://h/db"})


def test_from_env_requires_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_psycopg", object())  # pretend psycopg is present
    with pytest.raises(RuntimeError, match="ALFRED_MEMORY_PG_DSN"):
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

    def __init__(self, conn: _FakePgConn) -> None:
        self._conn = conn

    def executemany(self, sql: str, seq: list[tuple[Any, ...]]) -> None:
        for params in seq:
            self._conn.execute(sql, params)


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
