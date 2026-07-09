"""Env-driven configuration for the memory-provider chain.

The operator tunes runtime memory via two env vars:

* ``ALFRED_MEMORY_PROVIDERS`` -- comma-separated provider names, in
  consult order. Example: ``sqlite,fleet``. Unset means the embedded
  SQLite hybrid store first (zero-daemon semantic recall), with the local
  FleetBrain ledger behind it; set it to ``null`` or an empty string to
  disable runtime memory. Redis Agent Memory (``redis``) stays a supported
  opt-in: ``ALFRED_MEMORY_PROVIDERS=redis,fleet`` restores the daemon-backed
  chain unchanged.
* Per-provider env (e.g. ``ALFRED_GBRAIN_BIN``) -- see the provider's
  docstring.

The registry pattern keeps this Open-Closed: a new provider drops a
factory into :data:`PROVIDER_REGISTRY` and is immediately addressable
by name. Nothing else changes.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from .gbrain_stub import GBrainProvider
from .providers import (
    ChainedMemoryProvider,
    FleetBrainProvider,
    NullMemoryProvider,
)
from .redis_agent_memory import RedisAgentMemoryProvider
from .sqlite_hybrid import SqliteHybridProvider

if TYPE_CHECKING:
    from . import MemoryProvider

__all__ = [
    "DEFAULT_PROVIDER_NAMES",
    "LESSON_STORE_NAMES",
    "PROVIDER_REGISTRY",
    "build_chain",
    "load_lesson_writer",
    "load_provider",
    "parse_provider_names",
    "recall_lessons",
]

_LOG = logging.getLogger(__name__)

ProviderFactory = Callable[[Mapping[str, str]], "MemoryProvider"]

# Zero-daemon default: the embedded SQLite hybrid store leads (semantic-quality
# recall with no Redis/Ollama), with the local FleetBrain ledger behind it. An
# explicit ``ALFRED_MEMORY_PROVIDERS=redis,fleet`` restores the daemon-backed
# chain, so this is a backward-compatible default change, not a removal.
DEFAULT_PROVIDER_NAMES = ["sqlite", "fleet"]

# Providers that are dedicated, writable recall stores: they implement the AMS
# write contract (``reflect`` with a deterministic ``memory_id``, plus
# ``forget_lesson``) and are therefore valid targets for the promoted-lesson
# write path. ``fleet``/``gbrain``/``null`` are NOT: fleet is the candidate
# ledger, gbrain is a read-only shim, null is a no-op. Order-independent set;
# ``load_lesson_writer`` picks the first such name in the configured chain.
LESSON_STORE_NAMES = frozenset({"sqlite", "sqlite_hybrid", "redis"})

# Registry: each entry is a small factory that constructs the provider
# from the process environment. Keep the factories trivial; the
# providers themselves own their config schema.
PROVIDER_REGISTRY: dict[str, ProviderFactory] = {
    "fleet": lambda env: FleetBrainProvider.from_env(env),
    "gbrain": lambda env: GBrainProvider.from_env(env=dict(env)),
    "redis": lambda env: RedisAgentMemoryProvider.from_env(env=env),
    "sqlite": lambda env: SqliteHybridProvider.from_env(env=env),
    "sqlite_hybrid": lambda env: SqliteHybridProvider.from_env(env=env),
    "null": lambda _env: NullMemoryProvider(),
}


def parse_provider_names(raw: str | None) -> list[str]:
    """Split a comma-separated provider list into normalized names.

    Whitespace and empty entries are dropped. Order is preserved (it
    determines the chain consult order).
    """
    if not raw:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for chunk in raw.split(","):
        name = chunk.strip().lower()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def build_chain(
    names: list[str],
    *,
    env: Mapping[str, str] | None = None,
    registry: Mapping[str, ProviderFactory] | None = None,
) -> MemoryProvider:
    """Build a :class:`MemoryProvider` from a list of provider names.

    * Empty list -- returns :class:`NullMemoryProvider`.
    * One name -- returns that provider directly (no chain wrapper).
    * Multiple names -- returns a :class:`ChainedMemoryProvider`.

    Unknown names are logged and skipped (a typo in env must not
    break the runner).
    """
    envmap = env if env is not None else os.environ
    reg = registry if registry is not None else PROVIDER_REGISTRY
    built: list[MemoryProvider] = []
    for name in names:
        factory = reg.get(name)
        if factory is None:
            _LOG.warning("memory.config: unknown provider %r; skipping", name)
            continue
        try:
            built.append(factory(envmap))
        except Exception:
            _LOG.exception(
                "memory.config: provider %r failed to initialize; skipping",
                name,
            )
    if not built:
        return NullMemoryProvider()
    if len(built) == 1:
        return built[0]
    return ChainedMemoryProvider(providers=built)


def load_provider(env: Mapping[str, str] | None = None) -> MemoryProvider:
    """Top-level entry point: build the chain from
    ``ALFRED_MEMORY_PROVIDERS``.

    The default (env unset) is the embedded SQLite hybrid store first, then
    FleetBrain. The SQLite store is the zero-daemon semantic-recall layer
    (FTS5 lexical + optional sqlite-vec dense, fused with RRF). FleetBrain stays
    in the chain as the local operational ledger for candidates, firings, GitHub
    cache, worker heartbeats, and telemetry inputs. Redis Agent Memory remains a
    supported opt-in (``ALFRED_MEMORY_PROVIDERS=redis,fleet``).
    Operators who want only a no-op layer can set
    ``ALFRED_MEMORY_PROVIDERS=null``.
    """
    envmap = env if env is not None else os.environ
    raw = envmap.get("ALFRED_MEMORY_PROVIDERS")
    if raw is None:
        return build_chain(DEFAULT_PROVIDER_NAMES, env=envmap)
    names = parse_provider_names(raw)
    if not names:
        # Explicitly empty -- the operator turned memory off.
        return NullMemoryProvider()
    return build_chain(names, env=envmap)


def load_lesson_writer(env: Mapping[str, str] | None = None) -> MemoryProvider | None:
    """Build the promoted-lesson WRITE backend from the configured chain.

    The capture->judge->promote pipeline (in :mod:`fleet_brain`) writes a
    promoted lesson to the recall store, and the revert/retire/decay levers
    forget it from that same store. The writer MUST target a store the active
    recall chain actually reads, otherwise a promotion is written somewhere
    recall never looks and is silently lost.

    Resolution honours ``ALFRED_MEMORY_PROVIDERS``:

    * memory DISABLED (``ALFRED_MEMORY_PROVIDERS`` empty, or only ``null``) ->
      returns ``None``. Runtime memory is off, so nothing is written: the
      promote path is a no-op and does not silently persist lessons to disk;
    * a dedicated recall store is named (``sqlite`` / ``redis``) -> write to the
      FIRST one, since that is exactly where recall reads (default ``sqlite``,
      ``redis,fleet`` -> Redis, unchanged from earlier releases);
    * no dedicated recall store but ``fleet`` is in the chain (e.g. ``fleet``
      only) -> write to FleetBrain's own lessons table, the store fleet recall
      reads. Never a disconnected SQLite file fleet recall would ignore;
    * nothing writable in the recall chain (e.g. a read-only ``gbrain`` shim
      only) -> returns ``None``: the promote path is a no-op rather than writing
      to a store outside the active recall chain.

    Construction errors propagate to the caller, which treats them as a
    retryable promotion failure (the candidate stays pending).
    """
    envmap = env if env is not None else os.environ
    raw = envmap.get("ALFRED_MEMORY_PROVIDERS")
    if raw is None:
        names = list(DEFAULT_PROVIDER_NAMES)
    else:
        names = parse_provider_names(raw)
        # Memory explicitly disabled (empty list, or only the ``null`` no-op):
        # no writer, so promotion writes nothing. Mirrors ``load_provider``
        # returning ``NullMemoryProvider`` for the same input.
        if not names or all(name == "null" for name in names):
            return None
    for name in names:
        if name in LESSON_STORE_NAMES:
            factory = PROVIDER_REGISTRY.get(name)
            if factory is not None:
                return factory(envmap)
    # No dedicated recall store named. If fleet is active, the promoted lesson
    # belongs in FleetBrain's own lessons table (what fleet recall reads), never
    # a disconnected SQLite file recall would never consult. If nothing writable
    # is in the chain, there is no in-chain store to write to: no-op (None).
    if "fleet" in names:
        return FleetBrainProvider.from_env(envmap)
    return None


def recall_lessons(
    *,
    codename: str | None = None,
    repo: str | None = None,
    query: str | None = None,
    limit: int = 50,
    env: Mapping[str, str] | None = None,
    provider: MemoryProvider | None = None,
) -> list:
    """Recall the lessons Alfred is actually using, across the whole chain.

    This is the read surface behind ``alfred brain lessons`` and
    ``/api/memory/lessons``. It routes through the configured provider chain
    (Redis AMS + local FleetBrain, merged and deduped) rather than the local
    SQLite ledger alone, so an AMS-primary install shows the lessons it has
    actually promoted instead of an empty list.

    Best-effort recall, NOT a complete namespace enumeration: for the AMS
    backend an unfiltered call is a semantic search (ranked, capped at
    ``limit``), so on a large namespace it returns the top matches a firing
    would recall, not every stored lesson. That is the right semantic for a
    "what does Alfred recall" surface. A caller that needs to enumerate every
    stored lesson (e.g. a destructive reset) must use the provider's
    ``list_lessons`` page-and-loop primitive instead.

    ``provider`` is an injectable seam for tests; when omitted the chain is
    built from env via :func:`load_provider`. Any provider error is swallowed to
    an empty list by the chain itself, so this never raises on a down backend.
    """
    chain = provider if provider is not None else load_provider(env)
    return chain.recall(
        codename=codename,
        repo=repo,
        query=query,
        limit=max(1, int(limit)),
    )
