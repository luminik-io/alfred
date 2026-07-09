"""Embedded SQLite hybrid memory provider (zero-daemon default).

This provider gives Alfred semantic-quality recall of promoted lessons
without any running service. It is the zero-dependency default recall
backend: a single SQLite file under the state root, no Redis, no Ollama,
no cloud vector database.

Retrieval is hybrid and degrades in clean tiers:

* **Lexical (always on, zero-dependency).** An FTS5 virtual table ranks
  lessons by BM25 over the lesson body and tags. If the bundled SQLite
  build lacks FTS5 the provider falls back to substring (``LIKE``)
  matching, so recall never hard-fails.
* **Dense (optional, opt-in).** When ``ALFRED_MEMORY_SQLITE_DENSE`` is
  armed AND the optional ``sqlite-vec`` extension imports AND the Ollama
  embedder is reachable, a ``vec0`` vector table adds a k-nearest-neighbour
  arm over ``mxbai-embed-large`` embeddings (Alfred's existing embedding
  config). Any of those being unavailable transparently drops back to the
  lexical arm.
* **Fusion.** When both arms run, their ranked lists are fused with
  Reciprocal Rank Fusion (RRF, ``Σ 1/(k + rank)``, ``k`` default 60). With
  only the lexical arm the fused order is exactly the BM25 order.

The provider matches the Redis AMS recall CONTRACT (``recall`` returns
``list[Lesson]`` scoped by ``codename`` / ``repo``) and the AMS write
contract used by the promotion path (``reflect`` accepting a deterministic
``memory_id`` for idempotent upserts, plus ``forget_lesson`` /
``sync_lesson`` / ``list_lessons``), so it is a first-class read AND write
target behind the existing provider seam.

Config knobs (env, conservative defaults):

* ``ALFRED_MEMORY_SQLITE_DB`` -- database path (default
  ``$ALFRED_HOME/memory-hybrid.db``).
* ``ALFRED_MEMORY_SQLITE_DENSE`` -- arm the dense arm (default off).
* ``ALFRED_MEMORY_SQLITE_RRF_K`` -- RRF constant ``k`` (default 60).
* ``ALFRED_MEMORY_SQLITE_POOL`` -- per-arm candidate pool size before
  fusion (default 50).
* Dense embeddings reuse ``ALFRED_AMS_EMBEDDING_MODEL`` /
  ``ALFRED_AMS_EMBEDDING_DIM`` / ``ALFRED_AMS_OLLAMA_BASE_URL``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import sqlite3
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from fleet_brain import (
    Lesson,
    Severity,
    new_id,
    normalize_anchor_relation,
    normalize_anchor_type,
    normalize_kind,
)
from fleet_brain.taxonomy import DEFAULT_LESSON_KIND

__all__ = ["SqliteHybridProvider", "default_hybrid_db_path"]

_LOG = logging.getLogger(__name__)

# Conservative defaults. Every one is env-overridable via from_env.
_DEFAULT_RRF_K = 60
_DEFAULT_POOL = 50
_DEFAULT_EMBEDDING_MODEL = "ollama/mxbai-embed-large"
_DEFAULT_EMBEDDING_DIM = 1024
_DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
_EMBED_TIMEOUT_S = 5.0

# Token extraction for the FTS/LIKE lexical arm. One-character tokens are
# dropped as noise; the list is capped so a giant issue-body query cannot build
# a pathological MATCH expression.
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_MAX_QUERY_TOKENS = 24

_TRUTHY = {"1", "true", "yes", "on", "enabled"}


def default_hybrid_db_path(env: Mapping[str, str] | None = None) -> Path:
    """Resolve the hybrid store's SQLite path from the environment.

    Order of precedence:

    1. ``ALFRED_MEMORY_SQLITE_DB`` -- explicit override.
    2. ``$ALFRED_HOME/memory-hybrid.db``.
    3. ``~/.alfred/memory-hybrid.db``.

    Deliberately a SEPARATE file from ``fleet-brain.db``: the FleetBrain ledger
    owns candidates/firings/graph state, while this file owns only the promoted,
    recall-able lessons. Keeping them apart means the recall store can be reset
    or rebuilt without touching the operational ledger.
    """
    src = env if env is not None else os.environ
    explicit = (src.get("ALFRED_MEMORY_SQLITE_DB") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    alfred_home = (src.get("ALFRED_HOME") or "").strip()
    if alfred_home:
        return Path(alfred_home).expanduser() / "memory-hybrid.db"
    return Path.home() / ".alfred" / "memory-hybrid.db"


def _env_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = (env.get(key) or "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _env_flag(env: Mapping[str, str], key: str, *, default: bool = False) -> bool:
    raw = (env.get(key) or "").strip().lower()
    if not raw:
        return default
    return raw in _TRUTHY


def _clean_tags(tags: Iterable[str] | None) -> list[str]:
    return sorted({str(t).strip() for t in (tags or []) if str(t).strip()})


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def _from_iso(value: str) -> datetime:
    raw = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return datetime.now(UTC)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


Embedder = Callable[[str], list[float] | None]


@dataclass
class _OllamaEmbedder:
    """Best-effort dense embedder over a local Ollama endpoint.

    Reuses Alfred's AMS embedding config so the dense arm speaks the same
    ``mxbai-embed-large`` space as Redis AMS. Any failure (endpoint down, model
    missing, malformed response) returns ``None`` so the caller falls back to
    the lexical arm. Never raises.
    """

    base_url: str = _DEFAULT_OLLAMA_BASE_URL
    model: str = "mxbai-embed-large"
    dimensions: int = _DEFAULT_EMBEDDING_DIM
    timeout_s: float = _EMBED_TIMEOUT_S
    transport: Callable[[str, dict[str, Any], float], Any] | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> _OllamaEmbedder:
        model = (env.get("ALFRED_AMS_EMBEDDING_MODEL") or _DEFAULT_EMBEDDING_MODEL).strip()
        # AMS stores litellm-style ``ollama/<model>``; the raw Ollama HTTP API
        # wants the bare model name.
        if "/" in model:
            model = model.split("/", 1)[1]
        base = (
            (env.get("ALFRED_AMS_OLLAMA_BASE_URL") or _DEFAULT_OLLAMA_BASE_URL).strip().rstrip("/")
        )
        return cls(
            base_url=base or _DEFAULT_OLLAMA_BASE_URL,
            model=model or "mxbai-embed-large",
            dimensions=_env_int(env, "ALFRED_AMS_EMBEDDING_DIM", _DEFAULT_EMBEDDING_DIM),
        )

    def __call__(self, text: str) -> list[float] | None:
        payload = {"model": self.model, "prompt": text}
        try:
            if self.transport is not None:
                response = self.transport(
                    f"{self.base_url}/api/embeddings", payload, self.timeout_s
                )
            else:
                response = self._http(payload)
        except Exception as exc:  # never let embedding break recall/write
            _LOG.debug("memory.sqlite: embed failed: %s", exc)
            return None
        if not isinstance(response, dict):
            return None
        vec = response.get("embedding")
        if not isinstance(vec, list) or not vec:
            return None
        try:
            return [float(x) for x in vec]
        except (TypeError, ValueError):
            return None

    def _http(self, payload: dict[str, Any]) -> Any:
        data = json.dumps(payload).encode("utf-8")
        request = Request(
            f"{self.base_url}/api/embeddings",
            data=data,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=self.timeout_s) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw) if raw.strip() else {}


def _load_sqlite_vec(conn: sqlite3.Connection) -> bool:
    """Load the optional ``sqlite-vec`` extension onto ``conn``.

    Import-guarded: returns ``False`` (lexical-only) when the package is not
    installed or the runtime SQLite build forbids loadable extensions.
    """
    try:
        import sqlite_vec
    except Exception:
        return False
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except Exception as exc:
        _LOG.debug("memory.sqlite: sqlite-vec load failed: %s", exc)
        return False
    return True


def _serialize_vector(vec: list[float]) -> Any:
    import sqlite_vec

    return sqlite_vec.serialize_float32(vec)


@dataclass
class SqliteHybridProvider:
    """Embedded SQLite hybrid :class:`~memory.MemoryProvider`.

    See the module docstring for the retrieval tiers. Construct via
    :meth:`from_env` in normal operation; tests pass ``db_path=":memory:"`` and
    an injected ``embedder`` to exercise the dense arm without a server.
    """

    db_path: Path = field(default_factory=default_hybrid_db_path)
    dense: bool = False
    rrf_k: int = _DEFAULT_RRF_K
    pool: int = _DEFAULT_POOL
    dimensions: int = _DEFAULT_EMBEDDING_DIM
    embedder: Embedder | None = None
    name: str = "sqlite"

    _memory_conn: sqlite3.Connection | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _fts_ok: bool | None = field(default=None, init=False, repr=False)
    _vec_ok: bool | None = field(default=None, init=False, repr=False)
    _schema_ready: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.db_path, Path):
            self.db_path = Path(self.db_path)
        self.rrf_k = max(1, int(self.rrf_k))
        self.pool = max(1, int(self.pool))

    @classmethod
    def from_env(cls, *, env: Mapping[str, str] | None = None) -> SqliteHybridProvider:
        envmap = env if env is not None else os.environ
        dense = _env_flag(envmap, "ALFRED_MEMORY_SQLITE_DENSE", default=False)
        embedder: Embedder | None = None
        if dense:
            embedder = _OllamaEmbedder.from_env(envmap)
        return cls(
            db_path=default_hybrid_db_path(envmap),
            dense=dense,
            rrf_k=_env_int(envmap, "ALFRED_MEMORY_SQLITE_RRF_K", _DEFAULT_RRF_K),
            pool=_env_int(envmap, "ALFRED_MEMORY_SQLITE_POOL", _DEFAULT_POOL),
            dimensions=_env_int(envmap, "ALFRED_AMS_EMBEDDING_DIM", _DEFAULT_EMBEDDING_DIM),
            embedder=embedder,
        )

    # ----- connection + schema ------------------------------------------

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection, ensuring the schema (and optional vec extension)
        is in place. In-memory stores reuse one handle so data survives calls;
        on-disk stores open a fresh short-lived handle per call."""
        with self._lock:
            if str(self.db_path) == ":memory:":
                if self._memory_conn is None:
                    self._memory_conn = self._open(":memory:")
                    self._ensure_schema(self._memory_conn)
                yield self._memory_conn
                return
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = self._open(str(self.db_path))
            try:
                self._ensure_schema(conn)
                yield conn
            finally:
                conn.close()

    def _open(self, target: str) -> sqlite3.Connection:
        conn = sqlite3.connect(target)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def _dense_active(self, conn: sqlite3.Connection) -> bool:
        """Whether the dense arm can run on this connection."""
        if not self.dense or self.embedder is None:
            return False
        if self._vec_ok is None:
            self._vec_ok = _load_sqlite_vec(conn)
        elif self._vec_ok:
            # Re-load per fresh connection (extensions are per-connection).
            _load_sqlite_vec(conn)
        return bool(self._vec_ok)

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        with conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS lessons (
                    id            TEXT NOT NULL PRIMARY KEY,
                    codename      TEXT NOT NULL,
                    repo          TEXT NOT NULL,
                    body          TEXT NOT NULL,
                    tags_json     TEXT NOT NULL DEFAULT '[]',
                    severity      TEXT NOT NULL DEFAULT 'info',
                    firing_id     TEXT,
                    created_at    TEXT NOT NULL,
                    updated_at    TEXT NOT NULL,
                    kind          TEXT NOT NULL DEFAULT '{DEFAULT_LESSON_KIND}',
                    valid_until   TEXT,
                    superseded_by TEXT,
                    provenance    TEXT,
                    CHECK (severity IN ('info', 'warning', 'blocker'))
                )
                """
            )
            # Phase 2 additive migration for a pre-Phase-2 hybrid DB: add the
            # typed/validity/provenance columns in place. Existing rows read back
            # as the pre-Phase-2 default (``note`` kind, still-valid, no
            # provenance), so recall is unchanged until the columns are used.
            _add_column_if_missing(
                conn, "lessons", "kind", f"TEXT NOT NULL DEFAULT '{DEFAULT_LESSON_KIND}'"
            )
            _add_column_if_missing(conn, "lessons", "valid_until", "TEXT")
            _add_column_if_missing(conn, "lessons", "superseded_by", "TEXT")
            _add_column_if_missing(conn, "lessons", "provenance", "TEXT")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS lesson_anchors (
                    id          TEXT NOT NULL PRIMARY KEY,
                    lesson_id   TEXT NOT NULL,
                    anchor_type TEXT NOT NULL,
                    anchor_ref  TEXT NOT NULL,
                    relation    TEXT NOT NULL DEFAULT 'about',
                    repo        TEXT,
                    created_at  TEXT NOT NULL,
                    UNIQUE (lesson_id, anchor_type, anchor_ref, relation)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS lessons_scope_created_idx "
                "ON lessons (codename, repo, created_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS lessons_repo_created_idx "
                "ON lessons (repo, created_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS lesson_anchors_ref_idx "
                "ON lesson_anchors (anchor_type, anchor_ref)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS lesson_anchors_lesson_idx ON lesson_anchors (lesson_id)"
            )
            if self._fts_ok is None:
                self._fts_ok = self._try_create_fts(conn)
            if self._dense_active(conn):
                self._try_create_vec(conn)
        self._schema_ready = True

    def _try_create_fts(self, conn: sqlite3.Connection) -> bool:
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS lessons_fts "
                "USING fts5(text, lesson_id UNINDEXED, tokenize = 'unicode61')"
            )
        except sqlite3.OperationalError as exc:
            _LOG.debug("memory.sqlite: FTS5 unavailable, using LIKE fallback: %s", exc)
            return False
        return True

    def _try_create_vec(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS lessons_vec "
                f"USING vec0(lesson_id TEXT PRIMARY KEY, embedding float[{int(self.dimensions)}])"
            )
        except Exception as exc:
            _LOG.debug("memory.sqlite: could not create vec0 table: %s", exc)
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
        created_at: datetime | None = None,
        memory_id: str | None = None,
        kind: str | None = None,
        provenance: str | None = None,
        anchors: Iterable[tuple[str, str]] | None = None,
    ) -> Lesson:
        """Persist a promoted lesson. Idempotent on ``memory_id``.

        The promotion path passes a deterministic ``memory_id`` so a re-promote
        upserts the same row (and the revert/retire levers can forget exactly the
        lesson they wrote). Matches the Redis AMS ``reflect`` write contract.

        Phase 2 optional args, all backward-compatible: ``kind`` types the lesson
        (unknown folds to ``note``); ``provenance`` records the firing/PR that
        created it (defaults to ``firing_id``); ``anchors`` is an iterable of
        ``(anchor_type, anchor_ref)`` pairs linking the lesson to code entities.
        """
        created = created_at or datetime.now(UTC)
        lesson = Lesson(
            id=memory_id or new_id(),
            codename=codename.strip(),
            repo=repo.strip(),
            body=body.strip(),
            tags=_clean_tags(tags),
            created_at=created,
            firing_id=firing_id,
            severity=severity,
            kind=normalize_kind(kind),
            provenance=(provenance or firing_id or None),
        )
        with self._connect() as conn, conn:
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
        """Mirror one trusted lesson into the hybrid store (parity with AMS)."""
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
            )
        except Exception:
            return False
        return True

    def forget_lesson(self, lesson_id: str) -> bool:
        """Remove one lesson by id from every arm. Blank id is a no-op ``False``.

        Callers gate a destructive follow-up (retiring the candidate row) on a
        ``True`` return, so a blank id must not claim success.
        """
        clean = (lesson_id or "").strip()
        if not clean:
            return False
        with self._connect() as conn, conn:
            cur = conn.execute("DELETE FROM lessons WHERE id = ?", (clean,))
            deleted = cur.rowcount > 0
            conn.execute("DELETE FROM lesson_anchors WHERE lesson_id = ?", (clean,))
            if self._fts_ok:
                conn.execute("DELETE FROM lessons_fts WHERE lesson_id = ?", (clean,))
            if self._vec_ok:
                with contextlib.suppress(Exception):
                    conn.execute("DELETE FROM lessons_vec WHERE lesson_id = ?", (clean,))
        return deleted

    def _write_lesson(self, conn: sqlite3.Connection, lesson: Lesson) -> None:
        now = _iso(datetime.now(UTC))
        conn.execute(
            "INSERT INTO lessons "
            "(id, codename, repo, body, tags_json, severity, firing_id, created_at, "
            " updated_at, kind, valid_until, superseded_by, provenance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET "
            "  codename = excluded.codename, repo = excluded.repo, body = excluded.body, "
            "  tags_json = excluded.tags_json, severity = excluded.severity, "
            "  firing_id = excluded.firing_id, created_at = excluded.created_at, "
            "  updated_at = excluded.updated_at, kind = excluded.kind, "
            "  valid_until = excluded.valid_until, superseded_by = excluded.superseded_by, "
            "  provenance = excluded.provenance",
            (
                lesson.id,
                lesson.codename,
                lesson.repo,
                lesson.body,
                json.dumps(lesson.tags),
                lesson.severity,
                lesson.firing_id,
                _iso(lesson.created_at),
                now,
                normalize_kind(lesson.kind),
                _iso(lesson.valid_until) if lesson.valid_until else None,
                lesson.superseded_by,
                lesson.provenance,
            ),
        )
        if self._fts_ok:
            conn.execute("DELETE FROM lessons_fts WHERE lesson_id = ?", (lesson.id,))
            conn.execute(
                "INSERT INTO lessons_fts (text, lesson_id) VALUES (?, ?)",
                (self._fts_text(lesson), lesson.id),
            )
        if self._dense_active(conn):
            self._write_vector(conn, lesson)

    def _write_vector(self, conn: sqlite3.Connection, lesson: Lesson) -> None:
        assert self.embedder is not None
        vec = self.embedder(lesson.body)
        if not vec or len(vec) != int(self.dimensions):
            # Embedder unreachable or wrong shape: skip the dense arm for this
            # lesson. Lexical recall still finds it.
            return
        try:
            conn.execute("DELETE FROM lessons_vec WHERE lesson_id = ?", (lesson.id,))
            conn.execute(
                "INSERT INTO lessons_vec (lesson_id, embedding) VALUES (?, ?)",
                (lesson.id, _serialize_vector(vec)),
            )
        except Exception as exc:
            _LOG.debug("memory.sqlite: vector write failed for %s: %s", lesson.id, exc)

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

        Matches the Redis AMS recall contract: an empty list is the normal
        "nothing to say" answer the chained provider uses to fall through.

        Phase 2 code-grounding: when ``anchor_refs`` is supplied (e.g. the files
        being edited), lessons anchored to those refs are surfaced FIRST, so
        "editing ``auth.py``" pulls up the convention + the fix that worked +
        the mistake to avoid before the general lexical/dense hits. The default
        call passes no anchors and behaves exactly as Phase 1.
        """
        cap = max(1, int(limit))
        text = (query or " ".join(x for x in (codename, repo) if x) or "").strip()
        anchored_ids = self._anchor_ids(anchor_refs, repo=repo, limit=cap)
        with self._connect() as conn:
            lexical = self._lexical_ids(conn, text, codename=codename, repo=repo)
            dense: list[str] = []
            if self._dense_active(conn):
                dense = self._dense_ids(conn, text, codename=codename, repo=repo)
            if not lexical and not dense:
                # No query signal (or no lexical/dense hit): fall back to the
                # recency baseline so a scoped rail is never blank.
                fused_ids = self._recency_ids(conn, codename=codename, repo=repo, limit=cap)
            else:
                fused = _reciprocal_rank_fusion(lexical, dense, k=self.rrf_k)
                fused_ids = [lid for lid, _ in fused]
            # Anchored lessons lead, then the fused/recency order fills the rest.
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

    def _anchor_ids(
        self, anchor_refs: Iterable[str] | None, *, repo: str | None, limit: int
    ) -> list[str]:
        """Lesson ids anchored to any of ``anchor_refs`` (still-valid), scoped by repo."""
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

    def list_lessons(self, *, limit: int = 100) -> list[Lesson]:
        """Enumerate stored lessons, most-recent first (parity with AMS reset)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM lessons ORDER BY created_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
            return self._hydrate(conn, [r[0] for r in rows])

    def health(self) -> dict[str, Any]:
        """Operator-tooling health snapshot (not part of the Protocol)."""
        try:
            with self._connect() as conn:
                (count,) = conn.execute("SELECT COUNT(*) FROM lessons").fetchone()
            return {
                "ok": True,
                "db_path": str(self.db_path),
                "lessons": int(count),
                "lexical": "fts5" if self._fts_ok else "like",
                "dense": bool(self._vec_ok),
            }
        except Exception as exc:
            return {"ok": False, "db_path": str(self.db_path), "error": str(exc)}

    def _lexical_ids(
        self,
        conn: sqlite3.Connection,
        text: str,
        *,
        codename: str | None,
        repo: str | None,
    ) -> list[str]:
        tokens = _tokenize(text)
        if not tokens:
            return []
        scope_sql, scope_params = _scope_clause(codename, repo, alias="l")
        if self._fts_ok:
            match = " OR ".join(f'"{t}"' for t in tokens)
            sql = (
                "SELECT l.id FROM lessons_fts f JOIN lessons l ON l.id = f.lesson_id "
                "WHERE f.text MATCH ? " + scope_sql + " ORDER BY bm25(f) LIMIT ?"
            )
            params: list[Any] = [match, *scope_params, self.pool]
            try:
                rows = conn.execute(sql, params).fetchall()
                return [r[0] for r in rows]
            except sqlite3.OperationalError as exc:
                _LOG.debug("memory.sqlite: FTS query failed, falling back to LIKE: %s", exc)
        # LIKE fallback (SQLite build without FTS5): any-token substring match,
        # most-recent first. Match the SAME body+tags surface the FTS arm indexes
        # via _fts_text(), so a tag-only hit is still recalled here. Tags are
        # stored as a JSON array in tags_json, so a token like "graphql" matches
        # the serialized '["graphql", ...]'.
        like_params: list[Any] = []
        clauses: list[str] = []
        for tok in tokens:
            clauses.append("(l.body LIKE ? OR l.tags_json LIKE ?)")
            like_params.extend([f"%{tok}%", f"%{tok}%"])
        like_sql = " OR ".join(clauses)
        sql = (
            f"SELECT l.id FROM lessons l WHERE ({like_sql}) {scope_sql} "
            "ORDER BY l.created_at DESC LIMIT ?"
        )
        params = [*like_params, *scope_params, self.pool]
        rows = conn.execute(sql, params).fetchall()
        return [r[0] for r in rows]

    def _dense_ids(
        self,
        conn: sqlite3.Connection,
        text: str,
        *,
        codename: str | None,
        repo: str | None,
    ) -> list[str]:
        if self.embedder is None or not text:
            return []
        vec = self.embedder(text)
        if not vec or len(vec) != int(self.dimensions):
            return []
        serialized = _serialize_vector(vec)
        want = self.pool
        # The vec0 KNN limit is GLOBAL and cannot filter on scope or validity, so
        # taking the top `want` nearest vectors first and filtering afterwards
        # would drop in-scope/valid vectors whenever enough out-of-scope or
        # invalidated vectors rank closer. Grow the KNN window until we have
        # `want` surviving hits or we have pulled every stored vector (an upper
        # bound from the lessons count), so the filter can never truncate away a
        # relevant vector. This runs even unscoped so an invalidated (superseded/
        # expired) lesson is never recalled through the dense arm.
        (total,) = conn.execute("SELECT COUNT(*) FROM lessons").fetchone()
        total = max(1, int(total))
        k = min(total, max(want * 4, want))
        while True:
            candidate_ids = self._knn(conn, serialized, limit=k)
            if not candidate_ids:
                return []
            in_scope = self._filter_scope(conn, candidate_ids, codename=codename, repo=repo)
            if len(in_scope) >= want or k >= total:
                return in_scope[:want]
            k = min(total, k * 2)

    def _knn(self, conn: sqlite3.Connection, serialized: Any, *, limit: int) -> list[str]:
        try:
            rows = conn.execute(
                "SELECT lesson_id FROM lessons_vec "
                "WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
                (serialized, max(1, int(limit))),
            ).fetchall()
        except Exception as exc:
            _LOG.debug("memory.sqlite: dense KNN failed: %s", exc)
            return []
        return [r[0] for r in rows]

    def _filter_scope(
        self,
        conn: sqlite3.Connection,
        candidate_ids: list[str],
        *,
        codename: str | None,
        repo: str | None,
    ) -> list[str]:
        # vec0 KNN cannot filter on scope columns, so narrow in Python while
        # preserving the KNN (distance) order.
        scope_sql, scope_params = _scope_clause(codename, repo, alias="l")
        placeholders = ",".join("?" for _ in candidate_ids)
        allowed = {
            r[0]
            for r in conn.execute(
                f"SELECT l.id FROM lessons l WHERE l.id IN ({placeholders}) {scope_sql}",
                [*candidate_ids, *scope_params],
            ).fetchall()
        }
        return [cid for cid in candidate_ids if cid in allowed]

    def _recency_ids(
        self,
        conn: sqlite3.Connection,
        *,
        codename: str | None,
        repo: str | None,
        limit: int,
    ) -> list[str]:
        scope_sql, scope_params = _scope_clause(codename, repo, alias="l")
        sql = f"SELECT l.id FROM lessons l WHERE 1=1 {scope_sql} ORDER BY l.created_at DESC LIMIT ?"
        rows = conn.execute(sql, [*scope_params, limit]).fetchall()
        return [r[0] for r in rows]

    def _hydrate(self, conn: sqlite3.Connection, ids: list[str]) -> list[Lesson]:
        out: list[Lesson] = []
        for lesson_id in ids:
            row = conn.execute(
                "SELECT id, codename, repo, body, tags_json, severity, firing_id, "
                "created_at, kind, valid_until, superseded_by, provenance "
                "FROM lessons WHERE id = ?",
                (lesson_id,),
            ).fetchone()
            if row is not None:
                out.append(_row_to_lesson(row))
        return out

    # ----- anchors + validity (Phase 2) ---------------------------------

    def _write_anchor(
        self,
        conn: sqlite3.Connection,
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
            "INSERT OR IGNORE INTO lesson_anchors "
            "(id, lesson_id, anchor_type, anchor_ref, relation, repo, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                new_id(),
                lesson_id,
                normalize_anchor_type(anchor_type),
                ref,
                normalize_anchor_relation(relation),
                repo,
                _iso(datetime.now(UTC)),
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
        """Link an existing lesson to a code entity or another lesson.

        Idempotent on ``(lesson_id, anchor_type, anchor_ref, relation)``. Returns
        ``True`` when a link exists after the call (blank input is a no-op
        ``False``).
        """
        if not (lesson_id or "").strip() or not (anchor_ref or "").strip():
            return False
        with self._connect() as conn, conn:
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
        """Return still-valid lessons anchored to ``anchor_ref`` (e.g. a file).

        The code-grounding read: "what does the fleet know about this file." A
        superseded or expired lesson is filtered out; most recent first.
        """
        ref = (anchor_ref or "").strip()
        if not ref:
            return []
        scope_sql, scope_params = _scope_clause(None, repo, alias="l")
        wheres = ["a.anchor_ref = ?"]
        params: list[Any] = [ref]
        if anchor_type:
            wheres.append("a.anchor_type = ?")
            params.append(normalize_anchor_type(anchor_type))
        sql = (
            "SELECT DISTINCT l.id FROM lesson_anchors a JOIN lessons l ON l.id = a.lesson_id "
            f"WHERE {' AND '.join(wheres)} {scope_sql} "
            "ORDER BY l.created_at DESC LIMIT ?"
        )
        params.extend(scope_params)
        params.append(max(1, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return self._hydrate(conn, [r[0] for r in rows])

    def supersede_lesson(self, old_id: str, new_id_: str, *, at: datetime | None = None) -> bool:
        """Invalidate ``old_id`` in favour of ``new_id_`` (invalidate, not delete).

        Stamps ``superseded_by``/``valid_until`` on the old row and records a
        ``supersedes`` lesson-to-lesson anchor. Recall stops surfacing the old
        lesson; the row survives for audit. No-op ``False`` on blank/missing ids.
        """
        old = (old_id or "").strip()
        new = (new_id_ or "").strip()
        if not old or not new or old == new:
            return False
        ts = _iso(at or datetime.now(UTC))
        with self._connect() as conn, conn:
            cur = conn.execute(
                "UPDATE lessons SET superseded_by = ?, valid_until = ? WHERE id = ?",
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


def _tokenize(text: str) -> list[str]:
    tokens = [t.lower() for t in _TOKEN_RE.findall(text) if len(t) > 1]
    # De-dupe preserving order, then cap.
    seen: set[str] = set()
    out: list[str] = []
    for tok in tokens:
        if tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
        if len(out) >= _MAX_QUERY_TOKENS:
            break
    return out


def _scope_clause(codename: str | None, repo: str | None, *, alias: str) -> tuple[str, list[Any]]:
    """Build the shared ``AND ...`` filter every recall arm appends after WHERE.

    Always excludes invalidated lessons (Phase 2 bi-temporal validity): a row
    with ``superseded_by`` set or ``valid_until`` in the past is never recalled.
    The validity filter is inert until the supersede path is used, so default
    recall is unchanged. Scope (codename/repo) clauses follow when supplied.
    """
    now_iso = _iso(datetime.now(UTC))
    clauses: list[str] = [
        f"{alias}.superseded_by IS NULL",
        f"({alias}.valid_until IS NULL OR {alias}.valid_until > ?)",
    ]
    params: list[Any] = [now_iso]
    if codename:
        clauses.append(f"{alias}.codename = ?")
        params.append(codename)
    if repo:
        clauses.append(f"{alias}.repo = ?")
        params.append(repo)
    return "AND " + " AND ".join(clauses), params


def _reciprocal_rank_fusion(
    lexical: list[str], dense: list[str], *, k: int
) -> list[tuple[str, float]]:
    """Fuse two ranked id lists with Reciprocal Rank Fusion.

    ``score(id) = Σ 1 / (k + rank)`` over every list the id appears in, rank
    1-based. Ties break toward the lexical arm's order (it is enumerated first),
    which keeps a lexical-only chain's output in exact BM25 order.
    """
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    for ranked in (lexical, dense):
        for rank, lesson_id in enumerate(ranked, start=1):
            if lesson_id not in scores:
                scores[lesson_id] = 0.0
                first_seen[lesson_id] = len(first_seen)
            scores[lesson_id] += 1.0 / (k + rank)
    # Sort by descending fused score; ties keep insertion order (lexical arm
    # first), so a lexical-only chain returns exact BM25 order. ``first_seen`` is
    # a stable position map captured before sorting, avoiding an index() lookup
    # against a list being mutated in place.
    order = sorted(scores, key=lambda lid: (-scores[lid], first_seen[lid]))
    return [(lid, scores[lid]) for lid in order]


def _row_to_lesson(row: tuple[Any, ...]) -> Lesson:
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
    return Lesson(
        id=lesson_id,
        codename=codename,
        repo=repo,
        body=body,
        tags=sorted(tags),
        created_at=_from_iso(created_at),
        firing_id=firing_id,
        severity=sev,
        kind=normalize_kind(kind),
        valid_until=_from_iso(valid_until) if valid_until else None,
        superseded_by=superseded_by,
        provenance=provenance,
    )


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """Additively migrate an existing table: ``ALTER TABLE ... ADD COLUMN``.

    Idempotent: inspects ``PRAGMA table_info`` and only alters when the column
    is absent. A concurrent Alfred process adding the same column races to a
    ``duplicate column name`` error, which is safe to swallow. Mirrors the
    FleetBrain schema's migration helper so the two stores use one pattern.
    """
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column in cols:
        return
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    except sqlite3.OperationalError as exc:
        if "duplicate column name" in str(exc).lower():
            return
        raise
