"""Embedded SQLite hybrid memory provider (zero-daemon default).

This provider gives Alfred ranked lexical recall of promoted lessons without
any running service. It is the zero-dependency default recall backend: a single
SQLite file under the state root, no Redis, no Ollama, no cloud vector database.
Dense semantic retrieval is optional.

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
import sqlite3
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from envflags import truthy
from fleet_brain import (
    Lesson,
    Severity,
    new_id,
    normalize_anchor_relation,
    normalize_anchor_type,
    normalize_kind,
)
from fleet_brain.taxonomy import DEFAULT_LESSON_KIND
from memory_tokens import MAX_DENSE_QUERY_CANDIDATES, MAX_LITERAL_QUERY_CANDIDATES
from memory_tokens import escape_like_literal as _escape_like_literal
from memory_tokens import (
    has_meaningful_lexical_overlap as _has_meaningful_lexical_overlap,
)
from memory_tokens import identity_variant_matches as _identity_variant_matches
from memory_tokens import is_identity_token as _is_identity_token
from memory_tokens import lexical_surface as _lexical_surface
from memory_tokens import literal_fallback_query as _literal_fallback_query
from memory_tokens import query_token_groups as _query_token_groups
from memory_tokens import required_identities_match as _required_identities_match
from memory_tokens import (
    required_lexical_overlap as _required_lexical_overlap,
)
from memory_tokens import (
    requires_exact_lexical_tokens as _requires_exact_lexical_tokens,
)
from memory_tokens import (
    tokenize as _tokenize,
)

__all__ = ["SqliteHybridProvider", "default_hybrid_db_path"]

_LOG = logging.getLogger(__name__)

# Conservative defaults. Every one is env-overridable via from_env.
_DEFAULT_RRF_K = 60
_DEFAULT_POOL = 50
_DEFAULT_EMBEDDING_MODEL = "ollama/mxbai-embed-large"
_DEFAULT_EMBEDDING_DIM = 1024
_DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
_EMBED_TIMEOUT_S = 5.0

# The substring fallback is a last-resort path for SQLite builds without FTS5.
# Keep it predictably bounded even when every recent lesson is a false
# substring match. Eight keyset pages of at most 50 rows means one recall
# inspects no more than 400 candidates and issues no more than eight fallback
# queries. Smaller configured pools keep their existing page size.
_LEXICAL_FALLBACK_PAGE_SIZE = 50
_MAX_LEXICAL_FALLBACK_PAGES = 8
# FTS retrieves a wider BM25-ranked window before exact overlap filtering so
# short partial matches cannot consume a small result pool. The scan shares
# the 400-candidate hard cap used by bounded literal recall.
_MIN_LEXICAL_FTS_CANDIDATES = 50
_LEXICAL_MIGRATION_BATCH_SIZE = 200


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
    raw = env.get(key)
    if raw is None or not raw.strip():
        return default
    return truthy(raw)


def _clean_tags(tags: Iterable[str] | None) -> list[str]:
    return sorted({str(t).strip() for t in (tags or []) if str(t).strip()})


def _iso(value: datetime | str) -> str:
    """Normalize a timestamp to a UTC ISO-8601 string.

    Accepts either a ``datetime`` or an already-serialized ISO string. A stored
    lesson round-tripped through the AMS write contract carries ``created_at`` as
    a string, so ``sync_lesson`` legitimately hands one straight through; treating
    it as a datetime (``.tzinfo``) would raise and silently drop the lesson.
    """
    dt = _from_iso(value) if isinstance(value, str) else value
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
        conn.create_function(
            "alfred_identity_variant_matches",
            2,
            _identity_variant_matches,
            deterministic=True,
        )
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
                    lexical_text  TEXT NOT NULL DEFAULT '',
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
            lexical_text_added = _add_column_if_missing(
                conn, "lessons", "lexical_text", "TEXT NOT NULL DEFAULT ''"
            )
            if lexical_text_added:
                _backfill_lexical_text(conn)
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
            # Phase 3: durable reinforce-on-reuse. One row per ranking scope key
            # (codename + repo + lesson identity) with the injection count. Absent
            # rows read back as zero, so ranking is unchanged until a lesson is
            # actually reused.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS lesson_reuse (
                    scope_key   TEXT NOT NULL PRIMARY KEY,
                    reuse_count INTEGER NOT NULL DEFAULT 0,
                    updated_at  TEXT NOT NULL
                )
                """
            )
            if self._fts_ok is None:
                self._fts_ok = self._try_create_fts(conn)
            if lexical_text_added and self._fts_ok:
                _rebuild_fts_lexical_text(conn)
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
        created_at: datetime | str | None = None,
        memory_id: str | None = None,
        kind: str | None = None,
        provenance: str | None = None,
        valid_until: datetime | str | None = None,
        superseded_by: str | None = None,
        anchors: Iterable[tuple[str, str]] | None = None,
    ) -> Lesson:
        """Persist a promoted lesson. Idempotent on ``memory_id``.

        The promotion path passes a deterministic ``memory_id`` so a re-promote
        upserts the same row (and the revert/retire levers can forget exactly the
        lesson they wrote). Matches the Redis AMS ``reflect`` write contract.

        ``created_at`` accepts a ``datetime``, an ISO-8601 string, or ``None``.
        ``sync_lesson`` mirrors an already-stored lesson whose ``created_at`` is a
        serialized string, so a string is a first-class input here; it is parsed
        back to a ``datetime`` so the returned :class:`Lesson` stays well-typed.

        Optional args are backward-compatible. ``kind`` types the lesson
        (unknown folds to ``note``). ``provenance`` records the firing or PR
        that created it and defaults to ``firing_id``. ``valid_until`` and
        ``superseded_by`` restore an audited validity state for a trusted
        fixture import. ``anchors`` links the lesson to code entities as
        ``(anchor_type, anchor_ref)`` pairs.
        """
        if isinstance(created_at, str):
            created = _from_iso(created_at)
        else:
            created = created_at or datetime.now(UTC)
        valid: datetime | None
        if isinstance(valid_until, str):
            valid = _from_iso(valid_until)
        else:
            valid = valid_until
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
            valid_until=valid,
            superseded_by=(superseded_by or "").strip() or None,
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
            "(id, codename, repo, body, tags_json, lexical_text, severity, firing_id, created_at, "
            " updated_at, kind, valid_until, superseded_by, provenance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET "
            "  codename = excluded.codename, repo = excluded.repo, body = excluded.body, "
            "  tags_json = excluded.tags_json, lexical_text = excluded.lexical_text, "
            "  severity = excluded.severity, "
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
                self._fts_text(lesson),
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
        return _lexical_surface(" ".join([lesson.body, " ".join(lesson.tags)]).strip())

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
        has_query = bool((query or "").strip())
        text = (query or "").strip()
        query_tokens = _tokenize(text) if has_query else []
        anchored_ids = self._anchor_ids(anchor_refs, repo=repo, limit=cap)
        with self._connect() as conn:
            lexical = (
                self._lexical_ids(conn, text, codename=codename, repo=repo) if has_query else []
            )
            dense: list[str] = []
            if has_query and query_tokens and self._dense_active(conn):
                dense = self._dense_ids(conn, text)
                dense = self._filter_dense_ids(
                    conn,
                    dense,
                    query_tokens,
                    codename=codename,
                    repo=repo,
                )[: self.pool]
            if not lexical and not dense:
                # An intentionally unfiltered view gets a recency baseline. A
                # real query miss stays empty so unrelated recent lessons do
                # not enter an agent prompt as if they matched the task.
                fused_ids = (
                    []
                    if has_query
                    else self._recency_ids(conn, codename=codename, repo=repo, limit=cap)
                )
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
        text = _lexical_surface(text)
        token_groups = _query_token_groups(text)
        tokens = [group[0] for group in token_groups]
        if not tokens:
            literal = _literal_fallback_query(text)
            if literal is None:
                return []
            scope_sql, scope_params = _scope_clause(codename, repo, alias="l")
            pattern = f"%{_escape_like_literal(literal)}%"
            limit = min(max(1, self.pool), MAX_LITERAL_QUERY_CANDIDATES)
            sql = (
                "SELECT l.id FROM lessons l "
                "WHERE l.lexical_text LIKE ? ESCAPE '\\' "
                f"{scope_sql} ORDER BY l.created_at DESC, l.id ASC LIMIT ?"
            )
            rows = conn.execute(sql, [pattern, *scope_params, limit]).fetchall()
            return [str(row[0]) for row in rows]
        scope_sql, scope_params = _scope_clause(codename, repo, alias="l")
        if self._fts_ok and not _requires_exact_lexical_tokens(tokens):
            retrieval_tokens = dict.fromkeys(variant for group in token_groups for variant in group)
            match = " OR ".join(f'"{token}"' for token in retrieval_tokens)
            candidate_limit = min(
                max(self.pool * 4, _MIN_LEXICAL_FTS_CANDIDATES),
                MAX_LITERAL_QUERY_CANDIDATES,
            )
            sql = (
                "SELECT l.id FROM lessons_fts f JOIN lessons l ON l.id = f.lesson_id "
                "WHERE f.text MATCH ? "
                + scope_sql
                + " ORDER BY bm25(lessons_fts), l.created_at DESC, l.id ASC LIMIT ?"
            )
            params: list[Any] = [match, *scope_params, candidate_limit]
            try:
                rows = conn.execute(sql, params).fetchall()
                return self._filter_lexical_ids(conn, [r[0] for r in rows], tokens)[: self.pool]
            except sqlite3.OperationalError as exc:
                _LOG.debug("memory.sqlite: FTS query failed, falling back to LIKE: %s", exc)
        # LIKE fallback (SQLite build without FTS5): require enough token
        # substrings before each bounded candidate page, then enforce exact
        # token overlap on the returned canonical lexical surface. Keyset
        # pagination avoids increasingly expensive OFFSET scans. The fixed page and page-count
        # caps above bound this fallback to 400 inspected candidates and eight
        # SQL queries even if the corpus contains only substring false matches.
        # Match the same canonical body+tags surface the FTS arm indexes via
        # _fts_text(), so compatibility/case variants and tag-only hits behave
        # identically before the exact Python overlap filter.
        like_params: list[Any] = []
        clauses: list[str] = []
        for group in token_groups:
            if _is_identity_token(group[0]):
                group_clauses = [
                    "alfred_identity_variant_matches(l.lexical_text, ?) = 1" for _variant in group
                ]
                like_params.extend(group)
            else:
                group_clauses = ["l.lexical_text LIKE ? ESCAPE '\\'" for _variant in group]
                like_params.extend(f"%{_escape_like_literal(variant)}%" for variant in group)
            clauses.append(f"({' OR '.join(group_clauses)})")
        like_score_sql = " + ".join(f"CAST({clause} AS INTEGER)" for clause in clauses)
        base_params = [*like_params, _required_lexical_overlap(tokens), *scope_params]
        out: list[str] = []
        page_size = _lexical_fallback_page_size()
        after: tuple[str, str] | None = None
        for _page in range(_MAX_LEXICAL_FALLBACK_PAGES):
            cursor_sql = ""
            cursor_params: list[Any] = []
            if after is not None:
                created_at, lesson_id = after
                cursor_sql = "AND (l.created_at < ? OR (l.created_at = ? AND l.id > ?)) "
                cursor_params = [created_at, created_at, lesson_id]
            sql = (
                "SELECT l.id, l.lexical_text, l.created_at FROM lessons l "
                f"WHERE ({like_score_sql}) >= ? {scope_sql} {cursor_sql}"
                "ORDER BY l.created_at DESC, l.id ASC LIMIT ?"
            )
            rows = conn.execute(sql, [*base_params, *cursor_params, page_size]).fetchall()
            if not rows:
                break
            for lesson_id, lexical_text, _created_at in rows:
                if _has_meaningful_lexical_overlap(str(lexical_text), tokens):
                    out.append(str(lesson_id))
                if len(out) >= self.pool:
                    break
            if len(out) >= self.pool:
                break
            next_after = (str(rows[-1][2]), str(rows[-1][0]))
            if next_after == after:
                break
            after = next_after
            if len(rows) < page_size:
                break
        return out

    def _filter_lexical_ids(
        self,
        conn: sqlite3.Connection,
        ids: list[str],
        query_tokens: list[str],
    ) -> list[str]:
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT id, lexical_text FROM lessons WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
        text_by_id = {row[0]: str(row[1]) for row in rows}
        return [
            lesson_id
            for lesson_id in ids
            if _has_meaningful_lexical_overlap(text_by_id.get(lesson_id, ""), query_tokens)
        ]

    def _dense_ids(
        self,
        conn: sqlite3.Connection,
        text: str,
    ) -> list[str]:
        if self.embedder is None or not text:
            return []
        vec = self.embedder(text)
        if not vec or len(vec) != int(self.dimensions):
            return []
        serialized = _serialize_vector(vec)
        # The vec0 KNN limit is GLOBAL and cannot filter on scope or validity, so
        # taking only the result pool before filtering
        # would drop in-scope, valid, identity-matching vectors whenever enough
        # other vectors rank closer. Inspect one result-pool-independent bounded
        # window, then apply every authoritative filter before the pool cap.
        # This runs even unscoped so an invalidated (superseded/expired) lesson
        # is never recalled through the dense arm.
        return self._knn(conn, serialized, limit=MAX_DENSE_QUERY_CANDIDATES)

    def _filter_dense_ids(
        self,
        conn: sqlite3.Connection,
        ids: list[str],
        query_tokens: list[str],
        *,
        codename: str | None,
        repo: str | None,
    ) -> list[str]:
        """Apply scope, validity, and mandatory identities in one bounded read."""

        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        scope_sql, scope_params = _scope_clause(codename, repo, alias="l")
        rows = conn.execute(
            f"SELECT l.id, l.lexical_text FROM lessons l "
            f"WHERE l.id IN ({placeholders}) {scope_sql}",
            [*ids, *scope_params],
        ).fetchall()
        text_by_id = {str(row[0]): str(row[1]) for row in rows}
        return [
            lesson_id
            for lesson_id in ids
            if lesson_id in text_by_id
            and _required_identities_match(text_by_id[lesson_id], query_tokens)
        ]

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
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            "SELECT id, codename, repo, body, tags_json, severity, firing_id, "
            "created_at, kind, valid_until, superseded_by, provenance "
            f"FROM lessons WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
        by_id = {str(row[0]): _row_to_lesson(row) for row in rows}
        return [by_id[lesson_id] for lesson_id in ids if lesson_id in by_id]

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

    def merge_lesson(self, loser_id: str, survivor_id: str) -> bool:
        """Merge ``loser_id`` into ``survivor_id`` without losing history.

        The Phase 3 provenance-union merge the consolidation pass calls instead of
        a plain forget when it collapses a duplicate: it UNIONS the loser's
        provenance and its anchors onto the survivor, then INVALIDATES the loser
        (``superseded_by`` + ``valid_until`` = now) so recall stops surfacing it
        while the row survives for audit (invalidate-not-delete). One transaction,
        so a survivor never keeps a half-merged history. No-op ``False`` on
        blank/identical ids or a missing survivor/loser row."""
        loser = (loser_id or "").strip()
        survivor = (survivor_id or "").strip()
        if not loser or not survivor or loser == survivor:
            return False
        now = _iso(datetime.now(UTC))
        with self._connect() as conn, conn:
            survivor_row = conn.execute(
                "SELECT provenance, codename, repo FROM lessons WHERE id = ?", (survivor,)
            ).fetchone()
            loser_row = conn.execute(
                "SELECT provenance, codename, repo FROM lessons WHERE id = ?", (loser,)
            ).fetchone()
            if survivor_row is None or loser_row is None:
                return False
            # Union provenance (survivor's history first), so the surviving lesson
            # carries every firing/PR that produced either copy.
            merged_provenance = _union_provenance(survivor_row[0], loser_row[0])
            conn.execute(
                "UPDATE lessons SET provenance = ? WHERE id = ?",
                (merged_provenance, survivor),
            )
            # Union durable reuse: the loser's accumulated reinforce-on-reuse count
            # moves onto the survivor and its now-orphaned row is deleted, so a
            # heavily-reused lesson keeps its weight through a merge (ranking and
            # eviction score the survivor, not the invalidated loser). Keys are
            # built the SAME way the reinforce path wrote them.
            from agent_runner import memory_ranking

            survivor_key = memory_ranking.scope_key(
                lesson_id=survivor, codename=survivor_row[1], repo=survivor_row[2]
            )
            loser_key = memory_ranking.scope_key(
                lesson_id=loser, codename=loser_row[1], repo=loser_row[2]
            )
            _union_reuse_on_conn(conn, survivor_key=survivor_key, loser_key=loser_key, now=now)
            # Union anchors: copy every one of the loser's links onto the survivor
            # (idempotent), so the survivor is grounded to all the code entities
            # both copies were about.
            anchor_rows = conn.execute(
                "SELECT anchor_type, anchor_ref, relation, repo "
                "FROM lesson_anchors WHERE lesson_id = ?",
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
            # Invalidate-not-delete the loser and record the supersedes link.
            cur = conn.execute(
                "UPDATE lessons SET superseded_by = ?, valid_until = ? WHERE id = ?",
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

        Pressure/budget eviction: when the count of still-valid lessons exceeds
        the cap, the lowest-value lessons are expired (``valid_until`` = now,
        ``superseded_by`` left NULL) so recall stops surfacing them, but the rows
        survive and eviction is reversible by clearing ``valid_until``. Value is
        the #452 score (:func:`agent_runner.memory_ranking.score_lesson`) with a
        neutral relevance (there is no query at GC time), so ROI/severity, recency
        and durable reuse decide what stays. A non-positive cap is a no-op.
        Returns the evicted (or, in dry-run, would-evict) ids, lowest value
        first."""
        cap = int(max_lessons)
        if cap <= 0:
            return []
        from agent_runner import memory_ranking

        moment = now or datetime.now(UTC)
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
        # Lowest value first; ties break to the older lesson, then id, so the
        # choice is fully deterministic.
        scored.sort(key=lambda row: (row[0], row[1], row[2]))
        evict_count = len(scored) - cap
        evicted = [row[2] for row in scored[:evict_count]]
        if dry_run or not evicted:
            return evicted
        stamp = _iso(moment)
        with self._connect() as conn, conn:
            conn.executemany(
                "UPDATE lessons SET valid_until = ? WHERE id = ? AND valid_until IS NULL",
                [(stamp, lid) for lid in evicted],
            )
        return evicted

    def _valid_lesson_ids(self, conn: sqlite3.Connection, now: datetime) -> list[str]:
        """Ids of every still-valid lesson (not superseded, not expired)."""
        now_iso = _iso(now)
        rows = conn.execute(
            "SELECT id FROM lessons "
            "WHERE superseded_by IS NULL AND (valid_until IS NULL OR valid_until > ?) "
            "ORDER BY created_at DESC",
            (now_iso,),
        ).fetchall()
        return [r[0] for r in rows]

    # ----- durable reuse counters (Phase 3) -----------------------------

    def get_reuse_count(self, scope_key: str) -> int:
        """Persisted reinforce-on-reuse count for a ranking scope key (0 if absent).

        Mirrors :meth:`fleet_brain.store.SQLiteStore.get_reuse_count` so the
        ranking layer can persist reuse against whichever store backs recall."""
        key = (scope_key or "").strip()
        if not key:
            return 0
        with self._connect() as conn:
            row = conn.execute(
                "SELECT reuse_count FROM lesson_reuse WHERE scope_key = ?", (key,)
            ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def bump_reuse_counts(self, scope_keys: Sequence[str]) -> None:
        """Increment the persisted reuse count for each scope key by one."""
        keys = [k.strip() for k in scope_keys if k and k.strip()]
        if not keys:
            return
        now = _iso(datetime.now(UTC))
        with self._connect() as conn, conn:
            conn.executemany(
                "INSERT INTO lesson_reuse (scope_key, reuse_count, updated_at) "
                "VALUES (?, 1, ?) "
                "ON CONFLICT (scope_key) DO UPDATE SET "
                "  reuse_count = reuse_count + 1, updated_at = excluded.updated_at",
                [(key, now) for key in keys],
            )

    def union_reuse_counts(self, survivor_key: str, loser_key: str) -> None:
        """Move the loser scope key's reuse count onto the survivor, then drop it.

        Keeps reinforce-on-reuse whole across a merge: ``survivor += loser`` and
        the loser's orphaned row is deleted so nothing dangles on the invalidated
        key. Idempotent and a no-op when the loser has no reuse row."""
        with self._connect() as conn, conn:
            _union_reuse_on_conn(
                conn,
                survivor_key=survivor_key,
                loser_key=loser_key,
                now=_iso(datetime.now(UTC)),
            )


def _union_reuse_on_conn(
    conn: sqlite3.Connection, *, survivor_key: str, loser_key: str, now: str
) -> None:
    """Add the loser key's reuse count onto the survivor and delete the loser row.

    Operates on an open connection so a merge can do it inside its own
    transaction. No-op when the keys are blank/identical or the loser has no
    persisted reuse to move."""
    s_key = (survivor_key or "").strip()
    l_key = (loser_key or "").strip()
    if not s_key or not l_key or s_key == l_key:
        return
    row = conn.execute(
        "SELECT reuse_count FROM lesson_reuse WHERE scope_key = ?", (l_key,)
    ).fetchone()
    loser_count = int(row[0]) if row and row[0] else 0
    if loser_count <= 0:
        # Still clean up a stray zero-count loser row if one somehow exists.
        conn.execute("DELETE FROM lesson_reuse WHERE scope_key = ?", (l_key,))
        return
    conn.execute(
        "INSERT INTO lesson_reuse (scope_key, reuse_count, updated_at) "
        "VALUES (?, ?, ?) "
        "ON CONFLICT (scope_key) DO UPDATE SET "
        "  reuse_count = reuse_count + excluded.reuse_count, updated_at = excluded.updated_at",
        (s_key, loser_count, now),
    )
    conn.execute("DELETE FROM lesson_reuse WHERE scope_key = ?", (l_key,))


def _union_provenance(survivor: str | None, loser: str | None) -> str | None:
    """Comma-join two provenance strings, survivor first, de-duped in order.

    Provenance is a free-text firing/PR reference (or a comma list of them after
    an earlier merge). The union keeps the survivor's references first, then adds
    any of the loser's that are new, so a merged lesson records the full history
    of both copies without ever dropping a link. ``None`` when both are empty."""
    out: list[str] = []
    seen: set[str] = set()
    for source in (survivor, loser):
        for part in (source or "").split(","):
            token = part.strip()
            if token and token not in seen:
                seen.add(token)
                out.append(token)
    return ", ".join(out) if out else None


def _lexical_fallback_page_size() -> int:
    """Return the fixed candidate page size, independent of result count."""

    return _LEXICAL_FALLBACK_PAGE_SIZE


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


def _stored_lexical_text(body: str, tags_json: str) -> str:
    """Build the canonical body+tags surface for a stored lesson row."""

    try:
        decoded = json.loads(tags_json) if tags_json else []
    except (json.JSONDecodeError, TypeError):
        decoded = []
    tags = [str(tag) for tag in decoded] if isinstance(decoded, list) else []
    return _lexical_surface(" ".join([body, *tags]).strip())


def _backfill_lexical_text(conn: sqlite3.Connection) -> None:
    """Populate a newly introduced lexical column in bounded keyset batches."""

    after_id = ""
    while True:
        rows = conn.execute(
            "SELECT id, body, tags_json FROM lessons "
            "WHERE lexical_text = '' AND id > ? ORDER BY id LIMIT ?",
            (after_id, _LEXICAL_MIGRATION_BATCH_SIZE),
        ).fetchall()
        if not rows:
            return
        conn.executemany(
            "UPDATE lessons SET lexical_text = ? WHERE id = ? AND lexical_text = ''",
            [
                (_stored_lexical_text(str(body), str(tags_json)), str(lesson_id))
                for lesson_id, body, tags_json in rows
            ],
        )
        after_id = str(rows[-1][0])


def _rebuild_fts_lexical_text(conn: sqlite3.Connection) -> None:
    """Replace legacy FTS rows with canonical text in bounded batches."""

    after_id = ""
    while True:
        rows = conn.execute(
            "SELECT id, lexical_text FROM lessons WHERE id > ? ORDER BY id LIMIT ?",
            (after_id, _LEXICAL_MIGRATION_BATCH_SIZE),
        ).fetchall()
        if not rows:
            return
        ids = [(str(lesson_id),) for lesson_id, _lexical_text in rows]
        conn.executemany("DELETE FROM lessons_fts WHERE lesson_id = ?", ids)
        conn.executemany(
            "INSERT INTO lessons_fts (text, lesson_id) VALUES (?, ?)",
            [(str(lexical_text), str(lesson_id)) for lesson_id, lexical_text in rows],
        )
        after_id = str(rows[-1][0])


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> bool:
    """Additively migrate an existing table: ``ALTER TABLE ... ADD COLUMN``.

    Idempotent: inspects ``PRAGMA table_info`` and only alters when the column
    is absent. A concurrent Alfred process adding the same column races to a
    ``duplicate column name`` error, which is safe to swallow. Mirrors the
    FleetBrain schema's migration helper so the two stores use one pattern.
    """
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column in cols:
        return False
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    except sqlite3.OperationalError as exc:
        if "duplicate column name" in str(exc).lower():
            return False
        raise
    return True
