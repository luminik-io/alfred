"""Env-driven configuration for the memory-provider chain.

The operator tunes runtime memory via two env vars:

* ``ALFRED_MEMORY_PROVIDERS`` -- comma-separated provider names, in
  consult order. Example: ``redis,fleet``. Unset means Redis Agent
  Memory first, with the local FleetBrain ledger behind it; set it to
  ``null`` or an empty string to disable runtime memory.
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

if TYPE_CHECKING:
    from . import MemoryProvider

__all__ = [
    "DEFAULT_PROVIDER_NAMES",
    "PROVIDER_REGISTRY",
    "build_chain",
    "load_provider",
    "parse_provider_names",
    "recall_lessons",
]

_LOG = logging.getLogger(__name__)

ProviderFactory = Callable[[Mapping[str, str]], "MemoryProvider"]
DEFAULT_PROVIDER_NAMES = ["redis", "fleet"]

# Registry: each entry is a small factory that constructs the provider
# from the process environment. Keep the factories trivial; the
# providers themselves own their config schema.
PROVIDER_REGISTRY: dict[str, ProviderFactory] = {
    "fleet": lambda env: FleetBrainProvider.from_env(env),
    "gbrain": lambda env: GBrainProvider.from_env(env=dict(env)),
    "redis": lambda env: RedisAgentMemoryProvider.from_env(env=env),
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

    The default (env unset) is Redis Agent Memory first, then FleetBrain.
    Redis is the semantic memory layer. FleetBrain stays in the chain as the
    local operational ledger for candidates, firings, GitHub cache, worker
    heartbeats, and telemetry inputs.
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
