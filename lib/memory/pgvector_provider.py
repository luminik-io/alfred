"""Postgres + pgvector memory provider (opt-in scale tier).

The embedded SQLite hybrid store (:mod:`memory.sqlite_hybrid`) is Alfred's
zero-daemon default: one file, no service, great up to a single busy host. This
module is the documented **scale tier** behind the same provider seam. When a
team outgrows the single-writer SQLite file -- many agents writing lessons
concurrently, a lesson corpus large enough that recall latency matters, or a
need for durable server-side storage -- an operator opts into Postgres with
``ALFRED_MEMORY_PROVIDERS=pgvector,fleet`` and gets:

* **Durable concurrent writes.** Postgres owns MVCC and row locking, so many
  agents on many hosts can ``reflect`` at once without the single-writer lock a
  SQLite file forces.
* **Proper vector indexes.** A pgvector ``vector`` column with an HNSW (or
  IVFFlat) index does approximate-nearest-neighbour over the same
  ``mxbai-embed-large`` space the SQLite dense arm uses.
* **Scope-filtered retrieval in the query.** Unlike the ``sqlite-vec`` KNN
  (which is a global top-k that cannot see the scope columns), Postgres filters
  ``codename`` / ``repo`` / validity in the SAME ``WHERE`` as the vector order,
  so an in-scope lesson can never be truncated away by closer out-of-scope
  vectors.

It implements the SAME contract as the SQLite hybrid provider -- ``recall`` /
``reflect`` / ``sync_lesson`` / ``forget_lesson`` / ``list_lessons`` /
``health`` / ``merge_lesson`` / ``union_reuse_counts`` plus the Phase 2 typed /
anchor / validity surface and the Phase 3 durable ``lesson_reuse`` counters --
so recall, promotion, and consolidation all work through it with no
special-casing anywhere in the runner.

**It is opt-in only and never a hard dependency.** ``psycopg`` (v3) and the
server-side pgvector extension are optional, exactly like ``sqlite-vec`` is for
the SQLite dense arm. If ``psycopg`` is not installed or no DSN is configured,
the provider is simply unavailable and the configured chain falls through to the
next backend. The default chain (``sqlite,fleet``) is unchanged.

Config knobs (env):

* ``ALFRED_MEMORY_PG_DSN`` -- libpq connection string
  (``postgresql://user:pass@host:5432/alfred``). Required to arm the provider.
* ``ALFRED_MEMORY_PG_TABLE_PREFIX`` -- optional table-name prefix (default none),
  so several Alfred installs can share one database.
* ``ALFRED_MEMORY_PG_RRF_K`` -- RRF constant ``k`` (default 60, matching SQLite).
* ``ALFRED_MEMORY_PG_POOL`` -- per-arm candidate pool before fusion (default 50).
* ``ALFRED_MEMORY_PG_INDEX`` -- vector index kind, ``hnsw`` (default) or
  ``ivfflat``.
* Dense embeddings reuse the AMS embedding config
  (``ALFRED_AMS_EMBEDDING_MODEL`` / ``ALFRED_AMS_EMBEDDING_DIM`` /
  ``ALFRED_AMS_OLLAMA_BASE_URL``) via the shared embedder, so the dense arm
  speaks the same space as SQLite and Redis AMS.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import threading
from collections.abc import Iterable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fleet_brain import (
    Lesson,
    Severity,
    new_id,
    normalize_anchor_relation,
    normalize_anchor_type,
    normalize_kind,
)
from fleet_brain.taxonomy import DEFAULT_LESSON_KIND

# Reuse the exact helpers the SQLite hybrid provider uses so the two stores stay
# in lockstep: the same embedder config, the same RRF fusion (k=60 convention),
# the same provenance union, the same tokenizer and env parsing. Sharing the
# functions -- rather than re-implementing them -- is what guarantees parity
# between the default tier and the scale tier.
from .sqlite_hybrid import (
    _DEFAULT_EMBEDDING_DIM,
    _DEFAULT_POOL,
    _DEFAULT_RRF_K,
    Embedder,
    _clean_tags,
    _env_flag,
    _env_int,
    _from_iso,
    _OllamaEmbedder,
    _reciprocal_rank_fusion,
    _tokenize,
    _union_provenance,
)

if TYPE_CHECKING:
    from . import MemoryProvider

__all__ = [
    "MemoryProviderMisconfigured",
    "MemoryProviderUnavailable",
    "PgvectorProvider",
]

_LOG = logging.getLogger(__name__)

# psycopg (v3) is the OPTIONAL client, import-guarded exactly like sqlite-vec:
# absent -> the provider is unavailable and the chain falls through. Never a hard
# dependency of Alfred.
# Bind through a separate name and store as ``Any`` so the None fallback type-
# checks identically whether or not psycopg (with stubs) is installed -- no
# conditional ``type: ignore`` that would be unused in one of the two cases.
_psycopg: Any = None
try:  # pragma: no cover - import guard exercised by the "no psycopg" path
    import psycopg as _psycopg_mod

    _psycopg = _psycopg_mod
except Exception:  # any import failure means "unavailable"
    _psycopg = None

# pgvector's psycopg adapter is a nice-to-have: when present it lets us bind a
# Python list straight to a ``vector`` parameter. Absent, we serialize to the
# pgvector text literal and cast with ``%s::vector`` -- so the pip ``pgvector``
# package is optional on top of the server-side extension, which is the real
# requirement. Either way the SQL is identical (``%s::vector``).
_register_vector: Any = None
try:  # pragma: no cover - import guard
    from pgvector.psycopg import register_vector as _register_vector_fn

    _register_vector = _register_vector_fn
except Exception:  # pgvector adapter absent -> text-literal ``%s::vector`` path
    _register_vector = None


# A table-name prefix is interpolated into DDL/query identifiers (it cannot be a
# bound ``%s`` parameter -- SQL identifiers are not parameterizable), so it MUST
# be a safe SQL identifier and nothing else. This allowlist both closes the
# injection-via-config surface and guarantees the composed name (prefix +
# ``lessons``) is a valid *unquoted* identifier: it must start with a letter or
# underscore (never a digit) and contain only letters, digits, and underscores.
# A common operator value like ``alfred-prod`` (dash) is rejected with a clear
# error rather than silently producing broken or injectable SQL.
_TABLE_PREFIX_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# PostgreSQL SILENTLY truncates any identifier past NAMEDATALEN-1 (63) bytes,
# which would collide two of our tables/indexes or mismatch a table against its
# index. Every table/index name is ``prefix + suffix``, so the longest suffix
# bounds how long the prefix may be. This tuple is the single source of truth
# for those suffixes: it must list every suffix ``_ensure_schema`` /
# ``_maybe_provision_vector`` append to the prefix (a drift test drives the
# schema against a recording connection and re-derives the identifiers, so a
# new, longer index name fails that test until this list is updated). The
# allowlist regex above permits only ASCII, so one character is one byte here.
_MAX_PG_IDENTIFIER_BYTES = 63
_GENERATED_IDENTIFIER_SUFFIXES = (
    "lessons",
    "lesson_anchors",
    "lesson_reuse",
    "lessons_scope_created_idx",
    "lessons_repo_created_idx",
    "lessons_tsv_idx",
    "lessons_embedding_hnsw_idx",
    "lessons_embedding_ivfflat_idx",
    "lesson_anchors_ref_idx",
    "lesson_anchors_lesson_idx",
)
_MAX_TABLE_PREFIX_LEN = _MAX_PG_IDENTIFIER_BYTES - max(
    len(s) for s in _GENERATED_IDENTIFIER_SUFFIXES
)


class MemoryProviderUnavailable(RuntimeError):
    """The provider cannot be armed because an OPTIONAL dependency or endpoint is
    absent -- psycopg not installed, or no DSN configured.

    This is the normal "not turned on" state, NOT a misconfiguration: a chain
    builder should SKIP the provider and fall through to the rest of the chain.
    A genuine bad config value is a :class:`MemoryProviderMisconfigured` instead,
    and MUST surface to the operator rather than be silently swallowed --
    otherwise a typo'd setting would quietly disable the backend with no error.
    The two failure modes are deliberately distinct types so ``build_chain`` /
    ``load_lesson_writer`` (and the runtime loaders above them) can swallow only
    the first.
    """


class MemoryProviderMisconfigured(ValueError):
    """A provider is armed but a config VALUE is invalid -- e.g. an
    ``ALFRED_MEMORY_PG_TABLE_PREFIX`` that is not a valid SQL identifier or is
    long enough to overflow PostgreSQL's identifier limit.

    Subclasses :class:`ValueError` so callers that already handle a bad value
    keep working, but it is a NAMED type so the runtime memory loaders can
    re-raise it (surface the operator's typo) while still swallowing a genuinely
    :class:`MemoryProviderUnavailable` (not-armed) backend. A misconfiguration
    must never silently disable recall/promotion the way an unavailable backend
    is allowed to.
    """


def psycopg_available() -> bool:
    """Whether the optional psycopg client is importable."""
    return _psycopg is not None


def _aware(value: datetime | None) -> datetime | None:
    """Coerce a datetime to UTC-aware, leaving ``None`` alone."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _vector_literal(vec: Sequence[float]) -> str:
    """pgvector text literal ``[v1,v2,...]`` for binding via ``%s::vector``."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


_URI_QUERY_PASSWORD_RE = re.compile(r"(?i)([?&]password=)[^&\s]*")


def _redact_dsn(dsn: str) -> str:
    """Best-effort password scrub for a DSN so ``health`` never leaks a secret.

    Handles the two common libpq shapes:

    * **URI** (``postgresql://u:pw@host/db``) -- the ``user:pw@`` credential is
      collapsed to ``user:***@``, and a password carried as a ``?password=`` query
      parameter is scrubbed too.
    * **Keyword/value** (``host=... password=... dbname=...``) -- parsed
      QUOTE-AWARE with :mod:`shlex`, so a quoted value with spaces
      (``password='my secret pw'``) is one token and is fully redacted rather
      than leaking its tail after a naive whitespace split.

    Anything it cannot parse is reported as ``"<dsn>"`` rather than echoed, so a
    malformed string can never leak a credential.
    """
    raw = (dsn or "").strip()
    if not raw:
        return ""
    try:
        if "://" in raw:
            scheme, rest = raw.split("://", 1)
            if "@" in rest:
                creds, host = rest.split("@", 1)
                user = creds.split(":", 1)[0]
                redacted = f"{scheme}://{user}:***@{host}"
            else:
                redacted = f"{scheme}://{rest}"
            # A libpq URI may also carry the password as a query parameter.
            return _URI_QUERY_PASSWORD_RE.sub(r"\1***", redacted)
        # Keyword/value DSN: tokenize honouring single/double quotes so a quoted
        # password containing spaces is a single token and is redacted whole.
        parts: list[str] = []
        for token in shlex.split(raw):
            key, sep, _value = token.partition("=")
            if sep and key.strip().lower() == "password":
                parts.append("password=***")
            else:
                parts.append(token)
        return " ".join(parts)
    except Exception:
        return "<dsn>"


# ---------------------------------------------------------------------------
# Pure SQL builders. These take no connection and return ``(sql, params)`` with
# psycopg ``%s`` placeholders, so they are unit-testable without a live Postgres
# -- and they are where scope filtering (codename / repo / validity) is baked
# INTO the query, including the dense arm.
# ---------------------------------------------------------------------------


def _scope_clause(
    codename: str | None, repo: str | None, *, alias: str, now: datetime
) -> tuple[str, list[Any]]:
    """The shared ``AND ...`` filter every recall arm appends after ``WHERE``.

    Always excludes invalidated lessons (a row with ``superseded_by`` set or
    ``valid_until`` in the past), then narrows by ``codename`` / ``repo`` when
    supplied. Identical semantics to the SQLite hybrid ``_scope_clause`` so the
    two tiers recall the same set for the same scope.
    """
    clauses = [
        f"{alias}.superseded_by IS NULL",
        f"({alias}.valid_until IS NULL OR {alias}.valid_until > %s)",
    ]
    params: list[Any] = [now]
    if codename:
        clauses.append(f"{alias}.codename = %s")
        params.append(codename)
    if repo:
        clauses.append(f"{alias}.repo = %s")
        params.append(repo)
    return "AND " + " AND ".join(clauses), params


def _lexical_query(
    tokens: list[str],
    *,
    table: str,
    codename: str | None,
    repo: str | None,
    pool: int,
    now: datetime,
) -> tuple[str, list[Any]]:
    """Full-text arm: ``to_tsquery`` OR-of-tokens ranked by ``ts_rank``.

    Mirrors the SQLite FTS arm, which OR-joins the query tokens and ranks by
    BM25. Postgres has no BM25 built in, so ``ts_rank`` over the maintained
    ``body_tsv`` column is the lexical relevance signal; ties fall back to
    recency. Scope + validity filtered in the same ``WHERE``.
    """
    scope_sql, scope_params = _scope_clause(codename, repo, alias="l", now=now)
    tsquery = " | ".join(tokens)
    sql = (
        f"SELECT l.id FROM {table} l "
        "WHERE l.body_tsv @@ to_tsquery('english', %s) "
        f"{scope_sql} "
        "ORDER BY ts_rank(l.body_tsv, to_tsquery('english', %s)) DESC, l.created_at DESC "
        "LIMIT %s"
    )
    params = [tsquery, *scope_params, tsquery, pool]
    return sql, params


def _lexical_like_query(
    tokens: list[str],
    *,
    table: str,
    codename: str | None,
    repo: str | None,
    pool: int,
    now: datetime,
) -> tuple[str, list[Any]]:
    """ILIKE fallback lexical arm (any-token substring, most-recent first).

    The counterpart to the SQLite ``LIKE`` fallback: used only if the tsvector
    column could not be provisioned. Matches the SAME body+tags surface the
    full-text arm indexes, so a tag-only hit is still recalled.
    """
    scope_sql, scope_params = _scope_clause(codename, repo, alias="l", now=now)
    like_params: list[Any] = []
    clauses: list[str] = []
    for tok in tokens:
        clauses.append("(l.body ILIKE %s OR l.tags_json ILIKE %s)")
        like_params.extend([f"%{tok}%", f"%{tok}%"])
    like_sql = " OR ".join(clauses)
    sql = (
        f"SELECT l.id FROM {table} l WHERE ({like_sql}) {scope_sql} "
        "ORDER BY l.created_at DESC LIMIT %s"
    )
    params = [*like_params, *scope_params, pool]
    return sql, params


def _dense_query(
    vector_literal: str,
    *,
    table: str,
    codename: str | None,
    repo: str | None,
    pool: int,
    now: datetime,
) -> tuple[str, list[Any]]:
    """Dense arm: cosine-distance KNN, scope-filtered IN the query.

    This is the key improvement over the SQLite dense arm. ``sqlite-vec``'s KNN
    is a global top-k that cannot see the scope columns, so it must over-fetch
    and filter afterwards. Postgres filters ``codename`` / ``repo`` / validity in
    the SAME ``WHERE`` that feeds ``ORDER BY embedding <=> query``, so an
    in-scope, still-valid lesson can never be pushed out of the window by closer
    out-of-scope or invalidated vectors. ``<=>`` is pgvector cosine distance,
    matching the SQLite dense arm's cosine space and the HNSW
    ``vector_cosine_ops`` index.
    """
    scope_sql, scope_params = _scope_clause(codename, repo, alias="l", now=now)
    sql = (
        f"SELECT l.id FROM {table} l "
        "WHERE l.embedding IS NOT NULL "
        f"{scope_sql} "
        "ORDER BY l.embedding <=> %s::vector "
        "LIMIT %s"
    )
    params = [*scope_params, vector_literal, pool]
    return sql, params


def _recency_query(
    *,
    table: str,
    codename: str | None,
    repo: str | None,
    limit: int,
    now: datetime,
) -> tuple[str, list[Any]]:
    """Scoped recency baseline so a scoped rail is never blank (parity)."""
    scope_sql, scope_params = _scope_clause(codename, repo, alias="l", now=now)
    sql = f"SELECT l.id FROM {table} l WHERE TRUE {scope_sql} ORDER BY l.created_at DESC LIMIT %s"
    return sql, [*scope_params, limit]


def _anchor_query(
    anchor_ref: str,
    *,
    table: str,
    anchor_table: str,
    anchor_type: str | None,
    repo: str | None,
    limit: int,
    now: datetime,
) -> tuple[str, list[Any]]:
    """Still-valid lessons anchored to ``anchor_ref`` (code-grounding read)."""
    scope_sql, scope_params = _scope_clause(None, repo, alias="l", now=now)
    wheres = ["a.anchor_ref = %s"]
    params: list[Any] = [anchor_ref]
    if anchor_type:
        wheres.append("a.anchor_type = %s")
        params.append(normalize_anchor_type(anchor_type))
    sql = (
        f"SELECT DISTINCT l.id, l.created_at FROM {anchor_table} a "
        f"JOIN {table} l ON l.id = a.lesson_id "
        f"WHERE {' AND '.join(wheres)} {scope_sql} "
        "ORDER BY l.created_at DESC LIMIT %s"
    )
    params.extend(scope_params)
    params.append(limit)
    return sql, params


def _row_to_lesson(row: tuple[Any, ...]) -> Lesson:
    """Hydrate a lessons row into a :class:`Lesson` (timestamps come back typed)."""
    (
        lesson_id,
        codename,
        repo,
        body,
        tags_json,
        severity,
        firing_id,
        created_at,
        kind,
        valid_until,
        superseded_by,
        provenance,
    ) = row
    try:
        tags = [str(t) for t in json.loads(tags_json)] if tags_json else []
    except (TypeError, ValueError):
        tags = []
    sev: Severity = severity if severity in ("info", "warning", "blocker") else "info"
    created = created_at if isinstance(created_at, datetime) else _from_iso(str(created_at))
    valid = (
        valid_until
        if isinstance(valid_until, datetime) or valid_until is None
        else _from_iso(str(valid_until))
    )
    return Lesson(
        id=lesson_id,
        codename=codename,
        repo=repo,
        body=body,
        tags=sorted(tags),
        created_at=_aware(created) or datetime.now(UTC),
        firing_id=firing_id,
        severity=sev,
        kind=normalize_kind(kind),
        valid_until=_aware(valid),
        superseded_by=superseded_by,
        provenance=provenance,
    )


@dataclass
class PgvectorProvider:
    """Postgres + pgvector :class:`~memory.MemoryProvider` (scale tier).

    Construct via :meth:`from_env`. The connection is opened lazily and reused
    behind a lock (Postgres owns cross-process concurrency; the per-process lock
    just serializes this one client handle). Recall never raises -- a down DB
    returns ``[]`` so the chain falls through -- while writes propagate errors so
    a failed promotion stays retryable, mirroring the other stores.
    """

    dsn: str
    table_prefix: str = ""
    dense: bool = False
    rrf_k: int = _DEFAULT_RRF_K
    pool: int = _DEFAULT_POOL
    dimensions: int = _DEFAULT_EMBEDDING_DIM
    index_kind: str = "hnsw"
    embedder: Embedder | None = None
    name: str = "pgvector"

    _conn: Any = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _schema_ready: bool = field(default=False, init=False, repr=False)
    _fts_ok: bool = field(default=True, init=False, repr=False)
    _vec_ok: bool = field(default=False, init=False, repr=False)
    _native_vectors: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self.rrf_k = max(1, int(self.rrf_k))
        self.pool = max(1, int(self.pool))
        self.index_kind = (self.index_kind or "hnsw").strip().lower()
        if self.index_kind not in ("hnsw", "ivfflat"):
            self.index_kind = "hnsw"
        prefix = (self.table_prefix or "").strip()
        if prefix and not _TABLE_PREFIX_RE.match(prefix):
            raise MemoryProviderMisconfigured(
                "ALFRED_MEMORY_PG_TABLE_PREFIX must be a valid SQL identifier prefix "
                "(letters, digits, and underscores; not starting with a digit), "
                f"e.g. 'alfred_prod'; got {prefix!r}."
            )
        if len(prefix) > _MAX_TABLE_PREFIX_LEN:
            raise MemoryProviderMisconfigured(
                f"ALFRED_MEMORY_PG_TABLE_PREFIX is too long ({len(prefix)} chars): the "
                "longest generated table/index identifier would exceed PostgreSQL's "
                f"{_MAX_PG_IDENTIFIER_BYTES}-byte limit and be silently truncated. Use at "
                f"most {_MAX_TABLE_PREFIX_LEN} characters."
            )
        self.table_prefix = prefix

    @classmethod
    def from_env(cls, *, env: Mapping[str, str] | None = None) -> PgvectorProvider:
        """Build from the environment. Raises when the provider cannot be armed.

        Two distinct failure modes:

        * **Unavailable** (:class:`MemoryProviderUnavailable`) -- psycopg is not
          installed, or no DSN is configured. This is the "not turned on" state:
          ``build_chain`` / ``load_lesson_writer`` catch it and skip the provider
          so the chain falls through, never a hard failure of the runner.
        * **Misconfigured** (:class:`MemoryProviderMisconfigured`, a
          :class:`ValueError`, raised from ``__post_init__``) -- a genuine bad
          config value such as an invalid or over-long table prefix. This MUST
          surface to the operator, so it is a different exception type that the
          chain builders and the runtime loaders deliberately do NOT swallow; a
          typo must not silently disable the backend.
        """
        if _psycopg is None:
            raise MemoryProviderUnavailable(
                "pgvector provider requires psycopg (v3); install with "
                '`pip install "alfred-os[pgvector]"` or drop `pgvector` from '
                "ALFRED_MEMORY_PROVIDERS."
            )
        envmap = env if env is not None else os.environ
        dsn = (envmap.get("ALFRED_MEMORY_PG_DSN") or "").strip()
        if not dsn:
            raise MemoryProviderUnavailable(
                "pgvector provider needs ALFRED_MEMORY_PG_DSN "
                "(e.g. postgresql://user:pass@host:5432/alfred)."
            )
        dense = _env_flag(envmap, "ALFRED_MEMORY_PG_DENSE", default=True)
        embedder: Embedder | None = _OllamaEmbedder.from_env(envmap) if dense else None
        return cls(
            dsn=dsn,
            table_prefix=(envmap.get("ALFRED_MEMORY_PG_TABLE_PREFIX") or "").strip(),
            dense=dense,
            rrf_k=_env_int(envmap, "ALFRED_MEMORY_PG_RRF_K", _DEFAULT_RRF_K),
            pool=_env_int(envmap, "ALFRED_MEMORY_PG_POOL", _DEFAULT_POOL),
            dimensions=_env_int(envmap, "ALFRED_AMS_EMBEDDING_DIM", _DEFAULT_EMBEDDING_DIM),
            index_kind=(envmap.get("ALFRED_MEMORY_PG_INDEX") or "hnsw").strip().lower(),
            embedder=embedder,
        )

    # ----- table names ---------------------------------------------------

    @property
    def _lessons(self) -> str:
        return f"{self.table_prefix}lessons"

    @property
    def _anchors(self) -> str:
        return f"{self.table_prefix}lesson_anchors"

    @property
    def _reuse(self) -> str:
        return f"{self.table_prefix}lesson_reuse"

    # ----- connection + schema ------------------------------------------

    @contextmanager
    def _connect(self) -> Any:
        """Yield a live, schema-ensured connection under the instance lock."""
        with self._lock:
            conn = self._ensure_conn()
            self._ensure_schema(conn)
            yield conn

    def _ensure_conn(self) -> Any:
        if self._conn is not None and not getattr(self._conn, "closed", False):
            return self._conn
        if _psycopg is None:  # pragma: no cover - guarded at construction
            raise RuntimeError("psycopg is not installed")
        conn = _psycopg.connect(self.dsn, autocommit=True)
        if _register_vector is not None:
            try:
                _register_vector(conn)
                self._native_vectors = True
            except Exception as exc:
                _LOG.debug("memory.pgvector: register_vector failed: %s", exc)
        self._conn = conn
        self._schema_ready = False
        return conn

    def _vector_param(self, vec: Sequence[float]) -> Any:
        """Bind a vector as a native list (when pgvector is registered) or text."""
        if self._native_vectors:
            return [float(x) for x in vec]
        return _vector_literal(vec)

    def _ensure_schema(self, conn: Any) -> None:
        if self._schema_ready:
            return
        lessons, anchors, reuse = self._lessons, self._anchors, self._reuse
        with conn.transaction():
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {lessons} (
                    id            TEXT NOT NULL PRIMARY KEY,
                    codename      TEXT NOT NULL,
                    repo          TEXT NOT NULL,
                    body          TEXT NOT NULL,
                    tags_json     TEXT NOT NULL DEFAULT '[]',
                    severity      TEXT NOT NULL DEFAULT 'info',
                    firing_id     TEXT,
                    created_at    TIMESTAMPTZ NOT NULL,
                    updated_at    TIMESTAMPTZ NOT NULL,
                    kind          TEXT NOT NULL DEFAULT '{DEFAULT_LESSON_KIND}',
                    valid_until   TIMESTAMPTZ,
                    superseded_by TEXT,
                    provenance    TEXT,
                    body_tsv      TSVECTOR,
                    CHECK (severity IN ('info', 'warning', 'blocker'))
                )
                """
            )
            # Additive migrations for a pre-existing table (idempotent).
            for column, ddl in (
                ("kind", f"TEXT NOT NULL DEFAULT '{DEFAULT_LESSON_KIND}'"),
                ("valid_until", "TIMESTAMPTZ"),
                ("superseded_by", "TEXT"),
                ("provenance", "TEXT"),
                ("body_tsv", "TSVECTOR"),
            ):
                conn.execute(f"ALTER TABLE {lessons} ADD COLUMN IF NOT EXISTS {column} {ddl}")
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {anchors} (
                    id          TEXT NOT NULL PRIMARY KEY,
                    lesson_id   TEXT NOT NULL,
                    anchor_type TEXT NOT NULL,
                    anchor_ref  TEXT NOT NULL,
                    relation    TEXT NOT NULL DEFAULT 'about',
                    repo        TEXT,
                    created_at  TIMESTAMPTZ NOT NULL,
                    UNIQUE (lesson_id, anchor_type, anchor_ref, relation)
                )
                """
            )
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {reuse} (
                    scope_key   TEXT NOT NULL PRIMARY KEY,
                    reuse_count INTEGER NOT NULL DEFAULT 0,
                    updated_at  TIMESTAMPTZ NOT NULL
                )
                """
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS {lessons}_scope_created_idx "
                f"ON {lessons} (codename, repo, created_at DESC)"
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS {lessons}_repo_created_idx "
                f"ON {lessons} (repo, created_at DESC)"
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS {lessons}_tsv_idx ON {lessons} USING gin (body_tsv)"
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS {anchors}_ref_idx "
                f"ON {anchors} (anchor_type, anchor_ref)"
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS {anchors}_lesson_idx ON {anchors} (lesson_id)"
            )
        self._maybe_provision_vector(conn)
        self._schema_ready = True

    def _maybe_provision_vector(self, conn: Any) -> None:
        """Provision the pgvector column + index when the dense arm is armed.

        Guarded like the sqlite-vec extension load: if the server-side ``vector``
        extension is not installed (or the role cannot create it), the dense arm
        is silently unavailable and recall stays lexical-only. Never raises.
        """
        if not self.dense or self.embedder is None:
            self._vec_ok = False
            return
        lessons = self._lessons
        dim = int(self.dimensions)
        try:
            with conn.transaction():
                conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
                conn.execute(
                    f"ALTER TABLE {lessons} ADD COLUMN IF NOT EXISTS embedding vector({dim})"
                )
                if self.index_kind == "ivfflat":
                    conn.execute(
                        f"CREATE INDEX IF NOT EXISTS {lessons}_embedding_ivfflat_idx "
                        f"ON {lessons} USING ivfflat (embedding vector_cosine_ops)"
                    )
                else:
                    conn.execute(
                        f"CREATE INDEX IF NOT EXISTS {lessons}_embedding_hnsw_idx "
                        f"ON {lessons} USING hnsw (embedding vector_cosine_ops)"
                    )
            self._vec_ok = True
        except Exception as exc:
            _LOG.debug("memory.pgvector: vector provisioning failed, lexical-only: %s", exc)
            self._vec_ok = False

    # ----- write path ----------------------------------------------------

    def reflect(
        self,
        *,
        codename: str,
        repo: str,
        body: str,
        tags: Iterable[str] | None = None,
        severity: Severity = "info",
        firing_id: str | None = None,
        created_at: datetime | str | None = None,
        memory_id: str | None = None,
        kind: str | None = None,
        provenance: str | None = None,
        anchors: Iterable[tuple[str, str]] | None = None,
    ) -> Lesson:
        """Persist a promoted lesson. Idempotent on ``memory_id``.

        Same write contract as the SQLite hybrid ``reflect``: a deterministic
        ``memory_id`` upserts the same row so a re-promote is idempotent and the
        revert / retire levers can forget exactly what they wrote. ``created_at``
        accepts a ``datetime``, an ISO string (as ``sync_lesson`` hands through),
        or ``None``.
        """
        if isinstance(created_at, str):
            created = _from_iso(created_at)
        else:
            created = created_at or datetime.now(UTC)
        lesson = Lesson(
            id=memory_id or new_id(),
            codename=codename.strip(),
            repo=repo.strip(),
            body=body.strip(),
            tags=_clean_tags(tags),
            created_at=_aware(created) or datetime.now(UTC),
            firing_id=firing_id,
            severity=severity,
            kind=normalize_kind(kind),
            provenance=(provenance or firing_id or None),
        )
        with self._connect() as conn, conn.transaction():
            self._write_lesson(conn, lesson)
            for anchor_type, anchor_ref in anchors or []:
                self._write_anchor(
                    conn,
                    lesson_id=lesson.id,
                    anchor_type=anchor_type,
                    anchor_ref=anchor_ref,
                    repo=lesson.repo,
                )
        return lesson

    def sync_lesson(self, lesson: Lesson) -> bool:
        """Mirror one trusted lesson into the store (parity with AMS/SQLite)."""
        try:
            self.reflect(
                codename=lesson.codename,
                repo=lesson.repo,
                body=lesson.body,
                tags=lesson.tags,
                severity=lesson.severity,
                firing_id=lesson.firing_id,
                created_at=lesson.created_at,
                memory_id=lesson.id,
                kind=lesson.kind,
                provenance=lesson.provenance,
            )
        except Exception:
            return False
        return True

    def forget_lesson(self, lesson_id: str) -> bool:
        """Remove one lesson by id from every table. Blank id -> ``False``."""
        clean = (lesson_id or "").strip()
        if not clean:
            return False
        with self._connect() as conn, conn.transaction():
            cur = conn.execute(f"DELETE FROM {self._lessons} WHERE id = %s", (clean,))
            deleted = cur.rowcount > 0
            conn.execute(f"DELETE FROM {self._anchors} WHERE lesson_id = %s", (clean,))
        return deleted

    def _write_lesson(self, conn: Any, lesson: Lesson) -> None:
        now = datetime.now(UTC)
        fts_text = self._fts_text(lesson)
        body_tsv_sql = "to_tsvector('english', %s)" if self._fts_ok else "NULL"
        params: list[Any] = [
            lesson.id,
            lesson.codename,
            lesson.repo,
            lesson.body,
            json.dumps(lesson.tags),
            lesson.severity,
            lesson.firing_id,
            _aware(lesson.created_at),
            now,
            normalize_kind(lesson.kind),
            _aware(lesson.valid_until),
            lesson.superseded_by,
            lesson.provenance,
        ]
        if self._fts_ok:
            params.append(fts_text)
        conn.execute(
            f"INSERT INTO {self._lessons} "
            "(id, codename, repo, body, tags_json, severity, firing_id, created_at, "
            " updated_at, kind, valid_until, superseded_by, provenance, body_tsv) "
            f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, {body_tsv_sql}) "
            "ON CONFLICT (id) DO UPDATE SET "
            "  codename = EXCLUDED.codename, repo = EXCLUDED.repo, body = EXCLUDED.body, "
            "  tags_json = EXCLUDED.tags_json, severity = EXCLUDED.severity, "
            "  firing_id = EXCLUDED.firing_id, created_at = EXCLUDED.created_at, "
            "  updated_at = EXCLUDED.updated_at, kind = EXCLUDED.kind, "
            "  valid_until = EXCLUDED.valid_until, superseded_by = EXCLUDED.superseded_by, "
            "  provenance = EXCLUDED.provenance, body_tsv = EXCLUDED.body_tsv",
            params,
        )
        if self._vec_ok:
            self._write_vector(conn, lesson)

    def _write_vector(self, conn: Any, lesson: Lesson) -> None:
        assert self.embedder is not None
        vec = self.embedder(lesson.body)
        # The dense arm is best-effort and MUST NOT be able to lose the lesson.
        # In Postgres, a statement error aborts the whole surrounding transaction
        # ("current transaction is aborted, commands ignored until end of
        # transaction block"), so a rejected vector UPDATE run in the caller's
        # reflect() transaction would roll the lesson INSERT back too. Isolate the
        # vector write in its own SAVEPOINT (a nested ``conn.transaction()``) and
        # swallow a failure there: the savepoint rolls back but the outer
        # transaction -- and the lesson row -- survives, and lexical recall still
        # finds the lesson.
        try:
            with conn.transaction():
                if not vec or len(vec) != int(self.dimensions):
                    # Embedder unreachable or wrong shape: skip the dense arm and
                    # clear any stale vector so a re-promote does not keep an old one.
                    conn.execute(
                        f"UPDATE {self._lessons} SET embedding = NULL WHERE id = %s",
                        (lesson.id,),
                    )
                else:
                    conn.execute(
                        f"UPDATE {self._lessons} SET embedding = %s::vector WHERE id = %s",
                        (self._vector_param(vec), lesson.id),
                    )
        except Exception as exc:
            _LOG.debug("memory.pgvector: vector write failed for %s: %s", lesson.id, exc)

    @staticmethod
    def _fts_text(lesson: Lesson) -> str:
        return " ".join([lesson.body, " ".join(lesson.tags)]).strip()

    # ----- read path -----------------------------------------------------

    def recall(
        self,
        *,
        query: str | None = None,
        codename: str | None = None,
        repo: str | None = None,
        limit: int = 5,
        anchor_refs: Iterable[str] | None = None,
    ) -> list[Lesson]:
        """Return up to ``limit`` lessons for the scope, hybrid-ranked.

        Same contract and shape as the SQLite hybrid ``recall``: anchored lessons
        lead, then lexical + dense arms fused with RRF, then a recency baseline so
        a scoped rail is never blank. Any DB error returns ``[]`` so the chained
        provider falls through -- recall never breaks a firing.
        """
        cap = max(1, int(limit))
        text = (query or " ".join(x for x in (codename, repo) if x) or "").strip()
        try:
            anchored_ids = self._anchor_ids(anchor_refs, repo=repo, limit=cap)
            with self._connect() as conn:
                now = datetime.now(UTC)
                lexical = self._lexical_ids(conn, text, codename=codename, repo=repo, now=now)
                dense: list[str] = []
                if self._vec_ok:
                    dense = self._dense_ids(conn, text, codename=codename, repo=repo, now=now)
                if not lexical and not dense:
                    fused_ids = self._recency_ids(
                        conn, codename=codename, repo=repo, limit=cap, now=now
                    )
                else:
                    fused = _reciprocal_rank_fusion(lexical, dense, k=self.rrf_k)
                    fused_ids = [lid for lid, _ in fused]
                ordered: list[str] = []
                seen: set[str] = set()
                for lesson_id in (*anchored_ids, *fused_ids):
                    if lesson_id in seen:
                        continue
                    seen.add(lesson_id)
                    ordered.append(lesson_id)
                    if len(ordered) >= cap:
                        break
                return self._hydrate(conn, ordered)
        except Exception:
            _LOG.exception("memory.pgvector: recall failed; returning empty")
            return []

    def _anchor_ids(
        self, anchor_refs: Iterable[str] | None, *, repo: str | None, limit: int
    ) -> list[str]:
        refs = [r.strip() for r in (anchor_refs or []) if r and r.strip()]
        if not refs:
            return []
        out: list[str] = []
        seen: set[str] = set()
        for ref in refs:
            for lesson in self.lessons_for_anchor(anchor_ref=ref, repo=repo, limit=limit):
                if lesson.id in seen:
                    continue
                seen.add(lesson.id)
                out.append(lesson.id)
        return out

    def _lexical_ids(
        self,
        conn: Any,
        text: str,
        *,
        codename: str | None,
        repo: str | None,
        now: datetime,
    ) -> list[str]:
        tokens = _tokenize(text)
        if not tokens:
            return []
        if self._fts_ok:
            sql, params = _lexical_query(
                tokens,
                table=self._lessons,
                codename=codename,
                repo=repo,
                pool=self.pool,
                now=now,
            )
            try:
                return [r[0] for r in conn.execute(sql, params).fetchall()]
            except Exception as exc:
                _LOG.debug("memory.pgvector: full-text query failed, using ILIKE: %s", exc)
        sql, params = _lexical_like_query(
            tokens,
            table=self._lessons,
            codename=codename,
            repo=repo,
            pool=self.pool,
            now=now,
        )
        return [r[0] for r in conn.execute(sql, params).fetchall()]

    def _dense_ids(
        self,
        conn: Any,
        text: str,
        *,
        codename: str | None,
        repo: str | None,
        now: datetime,
    ) -> list[str]:
        if self.embedder is None or not text:
            return []
        vec = self.embedder(text)
        if not vec or len(vec) != int(self.dimensions):
            return []
        sql, params = _dense_query(
            _vector_literal(vec),
            table=self._lessons,
            codename=codename,
            repo=repo,
            pool=self.pool,
            now=now,
        )
        try:
            return [r[0] for r in conn.execute(sql, params).fetchall()]
        except Exception as exc:
            _LOG.debug("memory.pgvector: dense KNN failed: %s", exc)
            return []

    def _recency_ids(
        self,
        conn: Any,
        *,
        codename: str | None,
        repo: str | None,
        limit: int,
        now: datetime,
    ) -> list[str]:
        sql, params = _recency_query(
            table=self._lessons, codename=codename, repo=repo, limit=limit, now=now
        )
        return [r[0] for r in conn.execute(sql, params).fetchall()]

    def _hydrate(self, conn: Any, ids: list[str]) -> list[Lesson]:
        if not ids:
            return []
        rows = conn.execute(
            f"SELECT id, codename, repo, body, tags_json, severity, firing_id, "
            f"created_at, kind, valid_until, superseded_by, provenance "
            f"FROM {self._lessons} WHERE id = ANY(%s)",
            (list(ids),),
        ).fetchall()
        by_id = {row[0]: _row_to_lesson(row) for row in rows}
        # Preserve the fused/anchored order the ids came in.
        return [by_id[i] for i in ids if i in by_id]

    def list_lessons(self, *, limit: int = 100) -> list[Lesson]:
        """Enumerate stored lessons, most-recent first (parity with reset)."""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT id FROM {self._lessons} ORDER BY created_at DESC LIMIT %s",
                (max(1, int(limit)),),
            ).fetchall()
            return self._hydrate(conn, [r[0] for r in rows])

    def health(self) -> dict[str, Any]:
        """Operator-tooling health snapshot (DSN password redacted)."""
        try:
            with self._connect() as conn:
                (count,) = conn.execute(f"SELECT COUNT(*) FROM {self._lessons}").fetchone()
            return {
                "ok": True,
                "dsn": _redact_dsn(self.dsn),
                "lessons": int(count),
                "lexical": "tsvector" if self._fts_ok else "ilike",
                "dense": bool(self._vec_ok),
                "index": self.index_kind if self._vec_ok else None,
            }
        except Exception as exc:
            return {"ok": False, "dsn": _redact_dsn(self.dsn), "error": str(exc)}

    # ----- anchors + validity (Phase 2) ---------------------------------

    def _write_anchor(
        self,
        conn: Any,
        *,
        lesson_id: str,
        anchor_type: str,
        anchor_ref: str,
        relation: str = "about",
        repo: str | None = None,
    ) -> None:
        ref = (anchor_ref or "").strip()
        if not lesson_id or not ref:
            return
        conn.execute(
            f"INSERT INTO {self._anchors} "
            "(id, lesson_id, anchor_type, anchor_ref, relation, repo, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (lesson_id, anchor_type, anchor_ref, relation) DO NOTHING",
            (
                new_id(),
                lesson_id,
                normalize_anchor_type(anchor_type),
                ref,
                normalize_anchor_relation(relation),
                repo,
                datetime.now(UTC),
            ),
        )

    def add_anchor(
        self,
        *,
        lesson_id: str,
        anchor_ref: str,
        anchor_type: str = "file",
        relation: str = "about",
        repo: str | None = None,
    ) -> bool:
        """Link an existing lesson to a code entity or another lesson (idempotent)."""
        if not (lesson_id or "").strip() or not (anchor_ref or "").strip():
            return False
        with self._connect() as conn, conn.transaction():
            self._write_anchor(
                conn,
                lesson_id=lesson_id.strip(),
                anchor_type=anchor_type,
                anchor_ref=anchor_ref,
                relation=relation,
                repo=repo,
            )
        return True

    def lessons_for_anchor(
        self,
        *,
        anchor_ref: str,
        anchor_type: str | None = None,
        repo: str | None = None,
        limit: int = 50,
    ) -> list[Lesson]:
        """Return still-valid lessons anchored to ``anchor_ref`` (most recent first)."""
        ref = (anchor_ref or "").strip()
        if not ref:
            return []
        sql, params = _anchor_query(
            ref,
            table=self._lessons,
            anchor_table=self._anchors,
            anchor_type=anchor_type,
            repo=repo,
            limit=max(1, int(limit)),
            now=datetime.now(UTC),
        )
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return self._hydrate(conn, [r[0] for r in rows])

    def supersede_lesson(self, old_id: str, new_id_: str, *, at: datetime | None = None) -> bool:
        """Invalidate ``old_id`` in favour of ``new_id_`` (invalidate, not delete)."""
        old = (old_id or "").strip()
        new = (new_id_ or "").strip()
        if not old or not new or old == new:
            return False
        ts = _aware(at) or datetime.now(UTC)
        with self._connect() as conn, conn.transaction():
            cur = conn.execute(
                f"UPDATE {self._lessons} SET superseded_by = %s, valid_until = %s WHERE id = %s",
                (new, ts, old),
            )
            if cur.rowcount <= 0:
                return False
            self._write_anchor(
                conn,
                lesson_id=new,
                anchor_type="lesson",
                anchor_ref=old,
                relation="supersedes",
            )
        return True

    def merge_lesson(self, loser_id: str, survivor_id: str) -> bool:
        """Merge ``loser_id`` into ``survivor_id`` without losing history.

        The Phase 3 provenance-union merge, identical in shape to the SQLite
        hybrid ``merge_lesson``: UNION the loser's provenance, durable reuse
        count, and anchors onto the survivor, then INVALIDATE the loser
        (``superseded_by`` + ``valid_until`` = now) so recall stops surfacing it
        while the row survives for audit. One transaction. No-op ``False`` on
        blank/identical ids or a missing survivor/loser row.
        """
        loser = (loser_id or "").strip()
        survivor = (survivor_id or "").strip()
        if not loser or not survivor or loser == survivor:
            return False
        now = datetime.now(UTC)
        with self._connect() as conn, conn.transaction():
            survivor_row = conn.execute(
                f"SELECT provenance, codename, repo FROM {self._lessons} WHERE id = %s",
                (survivor,),
            ).fetchone()
            loser_row = conn.execute(
                f"SELECT provenance, codename, repo FROM {self._lessons} WHERE id = %s",
                (loser,),
            ).fetchone()
            if survivor_row is None or loser_row is None:
                return False
            merged_provenance = _union_provenance(survivor_row[0], loser_row[0])
            conn.execute(
                f"UPDATE {self._lessons} SET provenance = %s WHERE id = %s",
                (merged_provenance, survivor),
            )
            from agent_runner import memory_ranking

            survivor_key = memory_ranking.scope_key(
                lesson_id=survivor, codename=survivor_row[1], repo=survivor_row[2]
            )
            loser_key = memory_ranking.scope_key(
                lesson_id=loser, codename=loser_row[1], repo=loser_row[2]
            )
            self._union_reuse_on_conn(conn, survivor_key=survivor_key, loser_key=loser_key, now=now)
            anchor_rows = conn.execute(
                f"SELECT anchor_type, anchor_ref, relation, repo "
                f"FROM {self._anchors} WHERE lesson_id = %s",
                (loser,),
            ).fetchall()
            for anchor_type, anchor_ref, relation, repo in anchor_rows:
                self._write_anchor(
                    conn,
                    lesson_id=survivor,
                    anchor_type=anchor_type,
                    anchor_ref=anchor_ref,
                    relation=relation,
                    repo=repo,
                )
            cur = conn.execute(
                f"UPDATE {self._lessons} SET superseded_by = %s, valid_until = %s WHERE id = %s",
                (survivor, now, loser),
            )
            if cur.rowcount <= 0:
                return False
            self._write_anchor(
                conn,
                lesson_id=survivor,
                anchor_type="lesson",
                anchor_ref=loser,
                relation="supersedes",
            )
        return True

    def evict_to_cap(
        self,
        *,
        max_lessons: int,
        env: Mapping[str, str] | None = None,
        now: datetime | None = None,
        dry_run: bool = False,
    ) -> list[str]:
        """Invalidate the lowest-value lessons down to ``max_lessons`` (Phase 3).

        Same policy as the SQLite hybrid store: value is the ranking score with a
        neutral relevance (no query at GC time), lowest first, ties to the older
        lesson then id. Evicted lessons get ``valid_until`` = now (reversible),
        ``superseded_by`` left NULL. Non-positive cap is a no-op.
        """
        cap = int(max_lessons)
        if cap <= 0:
            return []
        from agent_runner import memory_ranking

        moment = _aware(now) or datetime.now(UTC)
        weights = memory_ranking.rank_weights(env)
        half_life = memory_ranking.decay_half_life_days(env)
        with self._connect() as conn:
            valid_ids = self._valid_lesson_ids(conn, moment)
            if len(valid_ids) <= cap:
                return []
            lessons = self._hydrate(conn, valid_ids)
        scored: list[tuple[float, datetime, str, Lesson]] = []
        for lesson in lessons:
            scope_key = memory_ranking.lesson_key(
                lesson, codename=lesson.codename, repo=lesson.repo
            )
            reuse = self.get_reuse_count(scope_key)
            score = memory_ranking.score_lesson(
                lesson,
                None,
                weights=weights,
                half_life_days=half_life,
                reuse_count=reuse,
                now=moment,
            )
            scored.append((score.total, lesson.created_at, lesson.id, lesson))
        scored.sort(key=lambda row: (row[0], row[1], row[2]))
        evict_count = len(scored) - cap
        evicted = [row[2] for row in scored[:evict_count]]
        if dry_run or not evicted:
            return evicted
        with self._connect() as conn, conn.transaction():
            cur = conn.cursor()
            cur.executemany(
                f"UPDATE {self._lessons} SET valid_until = %s "
                "WHERE id = %s AND valid_until IS NULL",
                [(moment, lid) for lid in evicted],
            )
        return evicted

    def _valid_lesson_ids(self, conn: Any, now: datetime) -> list[str]:
        rows = conn.execute(
            f"SELECT id FROM {self._lessons} "
            "WHERE superseded_by IS NULL AND (valid_until IS NULL OR valid_until > %s) "
            "ORDER BY created_at DESC",
            (now,),
        ).fetchall()
        return [r[0] for r in rows]

    # ----- durable reuse counters (Phase 3) -----------------------------

    def get_reuse_count(self, scope_key: str) -> int:
        """Persisted reinforce-on-reuse count for a scope key (0 if absent)."""
        key = (scope_key or "").strip()
        if not key:
            return 0
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT reuse_count FROM {self._reuse} WHERE scope_key = %s", (key,)
            ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def bump_reuse_counts(self, scope_keys: Sequence[str]) -> None:
        """Increment the persisted reuse count for each scope key by one."""
        keys = [k.strip() for k in scope_keys if k and k.strip()]
        if not keys:
            return
        now = datetime.now(UTC)
        with self._connect() as conn, conn.transaction():
            cur = conn.cursor()
            cur.executemany(
                f"INSERT INTO {self._reuse} (scope_key, reuse_count, updated_at) "
                "VALUES (%s, 1, %s) "
                f"ON CONFLICT (scope_key) DO UPDATE SET "
                f"  reuse_count = {self._reuse}.reuse_count + 1, updated_at = EXCLUDED.updated_at",
                [(key, now) for key in keys],
            )

    def union_reuse_counts(self, survivor_key: str, loser_key: str) -> None:
        """Move the loser scope key's reuse count onto the survivor, then drop it."""
        with self._connect() as conn, conn.transaction():
            self._union_reuse_on_conn(
                conn,
                survivor_key=survivor_key,
                loser_key=loser_key,
                now=datetime.now(UTC),
            )

    def _union_reuse_on_conn(
        self, conn: Any, *, survivor_key: str, loser_key: str, now: datetime
    ) -> None:
        """Add the loser key's reuse count onto the survivor and delete the loser row.

        Operates on an open connection so a merge can do it inside its own
        transaction. No-op when the keys are blank/identical or the loser has no
        persisted reuse to move. Mirrors the SQLite hybrid ``_union_reuse_on_conn``.
        """
        s_key = (survivor_key or "").strip()
        l_key = (loser_key or "").strip()
        if not s_key or not l_key or s_key == l_key:
            return
        row = conn.execute(
            f"SELECT reuse_count FROM {self._reuse} WHERE scope_key = %s", (l_key,)
        ).fetchone()
        loser_count = int(row[0]) if row and row[0] else 0
        if loser_count <= 0:
            conn.execute(f"DELETE FROM {self._reuse} WHERE scope_key = %s", (l_key,))
            return
        conn.execute(
            f"INSERT INTO {self._reuse} (scope_key, reuse_count, updated_at) "
            "VALUES (%s, %s, %s) "
            f"ON CONFLICT (scope_key) DO UPDATE SET "
            f"  reuse_count = {self._reuse}.reuse_count + EXCLUDED.reuse_count, "
            "  updated_at = EXCLUDED.updated_at",
            (s_key, loser_count, now),
        )
        conn.execute(f"DELETE FROM {self._reuse} WHERE scope_key = %s", (l_key,))


# A tiny type-only assertion that the provider satisfies the Protocol, so a
# signature drift against MemoryProvider is caught by mypy without a runtime cost.
if TYPE_CHECKING:
    _proto_check: MemoryProvider = PgvectorProvider(dsn="")
