"""Battery manifest, the single source of truth for Alfred's opt-in enhancements.

Alfred runs fully with ZERO batteries. The built-ins below (the embedded SQLite
memory store, the tool-output compactor, skeleton/delta reads, and blast-radius)
are always on and need no setup. The other entries are opt-in "batteries" a solo
builder can switch on when they want more: better recall, more token savings, or
a live code graph the agent can query. Each battery is honest about what it is,
what it needs, and whether it depends on a service you have to run yourself
(Redis, Postgres). Nothing here is installed or started without an explicit
choice, and no daemon is ever started for you.

This module is pure stdlib on purpose. It is the one manifest read by:

  - ``bin/alfred-init.py``  (the install wizard's battery-selection step)
  - ``bin/alfred``          (the ``alfred batteries`` subcommand)
  - ``lib/server/setup.py`` (the ``/api/setup/batteries`` endpoints the GUI reads)

so the CLI and the desktop app agree on one list and never drift. The env flags,
pip extras, and autofetch binaries named below are the real ones documented in
``docs/MEMORY_PROVIDERS.md``, ``docs/COMPRESSION.md``, and ``docs/CODE_MEMORY.md``.
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import re
import shutil
import socket
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #
CATEGORY_MEMORY = "memory"
CATEGORY_COMPRESSION = "compression"
CATEGORY_CODE_GRAPH = "code-graph"

# How a battery is obtained. "included" batteries ship on; the rest are opt-in.
INSTALL_INCLUDED = "included"  # built-in, nothing to install
INSTALL_PIP_EXTRA = "pip-extra"  # pip install "alfred-os[<extra>]"
INSTALL_AUTOFETCH = "autofetch"  # a helper fetches a pinned binary on first use
INSTALL_DAEMON = "daemon"  # you run an external service (Redis / Postgres)

# Status of a battery on this host, from the picker's point of view.
STATUS_INCLUDED = "included"  # built-in, always on
STATUS_ENABLED = "enabled"  # opt-in, its env flag is set
STATUS_AVAILABLE = "available"  # installed/detected but not enabled yet
STATUS_NOT_INSTALLED = "not_installed"  # needs a pip extra / autofetch / daemon

# Sentinel used by ``enable_flag`` for "any truthy value counts as on".
_ANY_TRUTHY = "__truthy__"

_TRUTHY = {"1", "true", "yes", "on"}
_ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

# The memory provider chain is a comma-separated CONSULT ORDER, not a single
# value: enabling a memory battery composes its provider into the existing chain
# rather than replacing it. These anchor the composition logic below.
MEMORY_PROVIDERS_KEY = "ALFRED_MEMORY_PROVIDERS"
DEFAULT_PROVIDER_CHAIN: tuple[str, ...] = ("sqlite", "fleet")
# Providers that actually store and recall lessons. The chain must always keep at
# least one of these, so disabling a store never leaves a chain that cannot
# recall (e.g. a bare "fleet" ledger).
STORE_PROVIDERS = frozenset({"sqlite", "sqlite_hybrid", "redis", "pgvector"})


def _truthy(value: str) -> bool:
    return value.strip().lower() in _TRUTHY


# --------------------------------------------------------------------------- #
# The battery record
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Battery:
    """One battery: a built-in that is always on, or an opt-in enhancement."""

    id: str
    name: str
    category: str
    # Plain, truthful one-liner. What the battery actually is.
    what: str
    # Plain, non-technical. What a solo builder gets by turning it on.
    how_it_helps: str
    # Always-on built-in vs opt-in. Built-ins are shown as "included, no setup".
    builtin: bool
    default_on: bool
    # Env flags written to $ALFRED_HOME/.env to enable / disable the battery.
    enable_env: Mapping[str, str] = field(default_factory=dict)
    disable_env: Mapping[str, str] = field(default_factory=dict)
    # How Alfred decides the battery is currently enabled.
    #   provider != ""   -> a member of the ALFRED_MEMORY_PROVIDERS chain
    #   enable_flag[0]   -> a single env key that must match enable_flag[1]
    provider: str = ""
    enable_flag: tuple[str, str] = ("", "")
    # Whether the battery needs a service you must run yourself, and which one.
    requires_daemon: bool = False
    service: str = ""  # "" | "Redis" | "Postgres" | "Ollama"
    # How to obtain it, plus the exact command / guidance to show the user.
    install_kind: str = INSTALL_INCLUDED
    install_hint: str = ""
    pip_extra: str = ""  # "" | "vector" | "pgvector"
    # A pinned autofetch helper command, when install_kind is INSTALL_AUTOFETCH.
    autofetch_cmd: tuple[str, ...] = ()
    # How to detect availability (see ``is_installed``); dispatched by id.
    detect: str = ""
    docs: str = ""


# --------------------------------------------------------------------------- #
# The manifest
# --------------------------------------------------------------------------- #
# Built-ins first (included, no setup), then the opt-in batteries. Order here is
# the order the CLI and GUI present them.
BATTERIES: tuple[Battery, ...] = (
    # ----- Always-on built-ins ------------------------------------------- #
    Battery(
        id="sqlite-memory",
        name="Built-in memory",
        category=CATEGORY_MEMORY,
        what="An embedded SQLite lesson store with keyword (BM25) recall, kept in a single file.",
        how_it_helps=(
            "Alfred remembers what it learned on past runs and pulls the relevant lessons "
            "back in, with zero setup and no server to run."
        ),
        builtin=True,
        default_on=True,
        install_kind=INSTALL_INCLUDED,
        docs="docs/MEMORY_PROVIDERS.md",
    ),
    Battery(
        id="tool-compactor",
        name="Tool-output compactor",
        category=CATEGORY_COMPRESSION,
        what="A built-in compactor that trims verbose command, test, and log output before it is stored.",
        how_it_helps=(
            "Keeps noisy tool output from filling the context window, so more of each run's budget "
            "goes to real work. Nothing to install."
        ),
        builtin=True,
        default_on=True,
        install_kind=INSTALL_INCLUDED,
        docs="docs/COMPRESSION.md",
    ),
    Battery(
        id="skeleton-reads",
        name="Skeleton and delta reads",
        category=CATEGORY_CODE_GRAPH,
        what="A local code index that lets the agent read a file's outline, and only what changed since last time.",
        how_it_helps=(
            "The agent gets its bearings in a file from a compact outline instead of re-reading the "
            "whole thing, which saves tokens and time. Built in, no external index."
        ),
        builtin=True,
        default_on=True,
        install_kind=INSTALL_INCLUDED,
        docs="docs/CODE_MEMORY.md",
    ),
    Battery(
        id="blast-radius",
        name="Blast radius",
        category=CATEGORY_CODE_GRAPH,
        what="A local impact check that flags what else a change might touch, from Alfred's own code map.",
        how_it_helps=(
            "Before an edit, the agent can see roughly what depends on the code it is about to change, "
            "so it is less likely to break something out of sight. Advisory, and built in."
        ),
        builtin=True,
        default_on=True,
        install_kind=INSTALL_INCLUDED,
        docs="docs/CODE_MEMORY.md",
    ),
    # ----- Opt-in batteries ---------------------------------------------- #
    Battery(
        id="dense-embeddings",
        name="Dense embeddings",
        category=CATEGORY_MEMORY,
        what=(
            "A vector (semantic) recall arm on the built-in SQLite store, fused with the keyword arm."
        ),
        how_it_helps=(
            "Finds relevant past lessons even when you word things differently, because it matches on "
            "meaning as well as keywords. Stays a single file; needs a local Ollama for the embeddings."
        ),
        builtin=False,
        default_on=False,
        enable_env={"ALFRED_MEMORY_SQLITE_DENSE": "1"},
        disable_env={"ALFRED_MEMORY_SQLITE_DENSE": "0"},
        enable_flag=("ALFRED_MEMORY_SQLITE_DENSE", _ANY_TRUTHY),
        requires_daemon=False,
        service="Ollama",
        install_kind=INSTALL_PIP_EXTRA,
        pip_extra="vector",
        install_hint=(
            'pip install "alfred-os[vector]" (adds sqlite-vec), then run a local Ollama with the '
            "mxbai-embed-large model. Without the embedder it falls back to keyword-only."
        ),
        detect="dense_embeddings",
        docs="docs/MEMORY_PROVIDERS.md",
    ),
    Battery(
        id="headroom-compression",
        name="Headroom compression",
        category=CATEGORY_COMPRESSION,
        what="An optional external compressor (headroom-ai) wired in behind the same tool-output seam.",
        how_it_helps=(
            "Squeezes more out of verbose logs, JSON, and test output than the built-in compactor, "
            "lowering the token cost of each run. Optional; if it is missing Alfred just uses the built-in."
        ),
        builtin=False,
        default_on=False,
        enable_env={"ALFRED_COMPRESSION_ENGINE": "headroom", "ALFRED_HEADROOM_AUTOFETCH": "1"},
        disable_env={"ALFRED_COMPRESSION_ENGINE": "builtin"},
        enable_flag=("ALFRED_COMPRESSION_ENGINE", "headroom"),
        requires_daemon=False,
        install_kind=INSTALL_PIP_EXTRA,
        install_hint=(
            "pip install headroom-ai into Alfred's interpreter (the library path). Alfred can also run "
            "pipx install headroom-ai for the CLI when ALFRED_HEADROOM_AUTOFETCH=1."
        ),
        autofetch_cmd=("pipx", "install", "headroom-ai"),
        detect="headroom",
        docs="docs/COMPRESSION.md",
    ),
    Battery(
        id="code-memory-mcp",
        name="Codebase memory (MCP)",
        category=CATEGORY_CODE_GRAPH,
        what=(
            "A standalone MIT binary (codebase-memory-mcp) that indexes your repos into a code graph "
            "the agent queries over MCP."
        ),
        how_it_helps=(
            "Lets the agent ask where a symbol is, who calls it, and what a change would affect, instead "
            "of grepping and re-reading. Alfred fetches a pinned, checksum-verified binary on first use."
        ),
        builtin=False,
        default_on=False,
        enable_env={"ALFRED_CODE_MEMORY_MCP": "1", "ALFRED_CODE_MEMORY_AUTOFETCH": "1"},
        # Disabling must close the REAL runtime gate (ALFRED_CODE_MEMORY_MCP),
        # not just stop autofetch: the MCP defaults on and would still attach a
        # previously fetched binary, so "off" in the picker must write MCP=0.
        disable_env={"ALFRED_CODE_MEMORY_MCP": "0", "ALFRED_CODE_MEMORY_AUTOFETCH": "0"},
        enable_flag=("ALFRED_CODE_MEMORY_AUTOFETCH", _ANY_TRUTHY),
        requires_daemon=False,
        install_kind=INSTALL_AUTOFETCH,
        install_hint=(
            "Run `alfred code-memory doctor` then `alfred code-memory index`. The pinned binary is "
            "fetched from the codebase-memory-mcp releases and checksum-verified; no daemon runs."
        ),
        autofetch_cmd=("code-memory-mcp", "doctor"),
        detect="code_memory",
        docs="docs/CODE_MEMORY.md",
    ),
    Battery(
        id="redis-ams",
        name="Redis Agent Memory Server",
        category=CATEGORY_MEMORY,
        what=(
            "A daemon-backed semantic memory store (Redis Agent Memory Server), used instead of the "
            "embedded SQLite store."
        ),
        how_it_helps=(
            "Shares one semantic memory across many machines, for when a single file on one host is not "
            "enough. It needs Redis, the memory server, and Ollama running; the SQLite default needs none "
            "of that, so most solo setups do not need this."
        ),
        builtin=False,
        default_on=False,
        enable_env={"ALFRED_MEMORY_PROVIDERS": "redis,fleet"},
        disable_env={"ALFRED_MEMORY_PROVIDERS": "sqlite,fleet"},
        provider="redis",
        requires_daemon=True,
        service="Redis",
        install_kind=INSTALL_DAEMON,
        install_hint=(
            "Run the Redis Agent Memory Server (plus Redis and Ollama) yourself, then set the connection "
            "with ALFRED_REDIS_MEMORY_URL. Alfred never starts a daemon for you. See docs/MEMORY_PROVIDERS.md."
        ),
        detect="redis_ams",
        docs="docs/MEMORY_PROVIDERS.md",
    ),
    Battery(
        id="pgvector",
        name="Postgres + pgvector",
        category=CATEGORY_MEMORY,
        what="The scale-tier memory backend: Postgres with pgvector, behind the same memory contract.",
        how_it_helps=(
            "Handles the case where the single-file SQLite store becomes the bottleneck (many machines "
            "writing at once, or very large lesson counts). Needs a Postgres you run. Stay on SQLite until "
            "you actually hit that wall."
        ),
        builtin=False,
        default_on=False,
        enable_env={"ALFRED_MEMORY_PROVIDERS": "pgvector,fleet"},
        disable_env={"ALFRED_MEMORY_PROVIDERS": "sqlite,fleet"},
        provider="pgvector",
        requires_daemon=True,
        service="Postgres",
        install_kind=INSTALL_DAEMON,
        pip_extra="pgvector",
        install_hint=(
            'pip install "alfred-os[pgvector]", run Postgres with the vector extension, then set '
            "ALFRED_MEMORY_PG_DSN to your database. Alfred never starts a database for you."
        ),
        detect="pgvector",
        docs="docs/MEMORY_PROVIDERS.md",
    ),
)


# --------------------------------------------------------------------------- #
# Lookups
# --------------------------------------------------------------------------- #
def battery_by_id(battery_id: str) -> Battery | None:
    for battery in BATTERIES:
        if battery.id == battery_id:
            return battery
    return None


def builtin_batteries() -> tuple[Battery, ...]:
    return tuple(b for b in BATTERIES if b.builtin)


def opt_in_batteries() -> tuple[Battery, ...]:
    return tuple(b for b in BATTERIES if not b.builtin)


def managed_env_keys() -> frozenset[str]:
    """Every env key any battery may write, so the init wizard can manage them."""
    keys: set[str] = set()
    for battery in BATTERIES:
        keys.update(battery.enable_env)
        keys.update(battery.disable_env)
    return frozenset(keys)


# --------------------------------------------------------------------------- #
# Environment
# --------------------------------------------------------------------------- #
def _alfred_home(env: Mapping[str, str]) -> Path:
    raw = str(env.get("ALFRED_HOME", "")).strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".alfred"


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return out
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        name = name.removeprefix("export ").strip()
        if not _ENV_KEY_RE.match(name):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        out[name] = value
    return out


def load_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the effective env: ``$ALFRED_HOME/.env`` overlaid by the process env.

    The picker reads persisted config from the .env file, but a value already set
    in the live process wins, matching how the fleet actually boots.
    """
    base = dict(os.environ) if env is None else dict(env)
    merged = _parse_env_file(_alfred_home(base) / ".env")
    merged.update(base)
    return merged


def _provider_chain(env: Mapping[str, str]) -> list[str]:
    raw = env.get(MEMORY_PROVIDERS_KEY, "")
    if not raw.strip():
        return list(DEFAULT_PROVIDER_CHAIN)  # the documented zero-daemon default
    return [tok.strip() for tok in raw.split(",") if tok.strip()]


def compose_provider_chain(chain: list[str], provider: str, *, enable: bool) -> list[str]:
    """Merge one memory provider into an existing chain without clobbering it.

    Enabling makes ``provider`` the primary recall store (front of the chain) and
    keeps every other provider (including the local ``fleet`` ledger) in order.
    Disabling drops just ``provider`` and preserves the rest; if that would leave
    no recall store, ``sqlite`` is restored so the chain can still recall, and an
    emptied chain falls back to the documented ``sqlite,fleet`` default. This is
    why toggling Redis or pgvector never replaces a user's custom chain.
    """
    rest = [name for name in chain if name != provider]
    if enable:
        return [provider, *rest]
    if not any(name in STORE_PROVIDERS for name in rest):
        rest = ["sqlite", *rest]
    if not rest:
        return list(DEFAULT_PROVIDER_CHAIN)
    return rest


def provider_batteries() -> tuple[Battery, ...]:
    """Opt-in batteries that swap the primary memory store (Redis, pgvector)."""
    return tuple(b for b in BATTERIES if b.provider)


def enabled_provider_ids(env: Mapping[str, str]) -> list[str]:
    """Ids of provider batteries whose provider is currently in the chain."""
    chain = _provider_chain(env)
    return [b.id for b in provider_batteries() if b.provider in chain]


def selection_conflict(battery_ids: Iterable[str]) -> str:
    """Return a plain error if a set of batteries cannot be enabled together.

    Redis and pgvector each want to be the single primary recall store
    (``ALFRED_MEMORY_PROVIDERS``), so enabling both is a conflict rather than a
    silent last-write-wins. Returns an empty string when the selection is fine.
    """
    providers = [bid for bid in battery_ids if (b := battery_by_id(bid)) and b.provider]
    unique = list(dict.fromkeys(providers))
    if len(unique) > 1:
        names = " and ".join(unique)
        return (
            f"{names} each replace the primary memory store "
            f"({MEMORY_PROVIDERS_KEY}); enable only one of them."
        )
    return ""


# --------------------------------------------------------------------------- #
# Enabled / installed / status
# --------------------------------------------------------------------------- #
def is_enabled(battery: Battery, env: Mapping[str, str]) -> bool:
    if battery.builtin:
        return True
    if battery.provider:
        return battery.provider in _provider_chain(env)
    key, expected = battery.enable_flag
    if not key:
        return False
    current = env.get(key, "")
    if expected == _ANY_TRUTHY:
        return _truthy(current)
    return current == expected


def _find_spec(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _code_memory_binary(env: Mapping[str, str]) -> bool:
    override = str(env.get("ALFRED_CODE_MEMORY_BIN", "")).strip()
    if override and Path(override).expanduser().exists():
        return True
    if shutil.which("codebase-memory-mcp"):
        return True
    fetched = _alfred_home(env) / "bin" / "codebase-memory-mcp"
    return fetched.exists()


def _headroom_available(env: Mapping[str, str]) -> bool:
    if _find_spec("headroom"):
        return True
    override = str(env.get("ALFRED_HEADROOM_BIN", "")).strip()
    if override and Path(override).expanduser().exists():
        return True
    return bool(shutil.which("headroom"))


def _ams_reachable(env: Mapping[str, str]) -> bool:
    """Best-effort, non-blocking probe of the Agent Memory Server socket."""
    host = str(env.get("ALFRED_AMS_HOST", "127.0.0.1")).strip() or "127.0.0.1"
    try:
        port = int(str(env.get("ALFRED_AMS_PORT", "8088")).strip() or "8088")
    except ValueError:
        port = 8088
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


def is_installed(battery: Battery, env: Mapping[str, str]) -> bool:
    """Whether the battery's requirement is present on this host (read-only)."""
    if battery.builtin:
        return True
    if battery.detect == "dense_embeddings":
        return _find_spec("sqlite_vec")
    if battery.detect == "headroom":
        return _headroom_available(env)
    if battery.detect == "code_memory":
        return _code_memory_binary(env)
    if battery.detect == "redis_ams":
        return _ams_reachable(env)
    if battery.detect == "pgvector":
        return _find_spec("psycopg")
    return False


def battery_status(battery: Battery, env: Mapping[str, str]) -> str:
    if battery.builtin:
        return STATUS_INCLUDED
    if is_enabled(battery, env):
        return STATUS_ENABLED
    if is_installed(battery, env):
        return STATUS_AVAILABLE
    return STATUS_NOT_INSTALLED


def enable_values(battery: Battery, env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Env values to write to enable ``battery``.

    For a memory-provider battery (Redis, pgvector) the chain-valued
    ``ALFRED_MEMORY_PROVIDERS`` is COMPOSED onto the current chain (read from
    ``env``, or the effective env when omitted) so an existing or custom chain is
    preserved. Flag batteries return their static enable flag(s).
    """
    if battery.builtin:
        return {}
    if battery.provider:
        chain = _provider_chain(env if env is not None else load_env())
        composed = compose_provider_chain(chain, battery.provider, enable=True)
        return {MEMORY_PROVIDERS_KEY: ",".join(composed)}
    return dict(battery.enable_env)


def disable_values(battery: Battery, env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Env values to write to disable ``battery``.

    For a memory-provider battery this removes just that provider from the chain
    (preserving the rest, falling back to the default when nothing recall-worthy
    remains). Flag batteries return their static disable flag(s).
    """
    if battery.builtin:
        return {}
    if battery.provider:
        chain = _provider_chain(env if env is not None else load_env())
        composed = compose_provider_chain(chain, battery.provider, enable=False)
        return {MEMORY_PROVIDERS_KEY: ",".join(composed)}
    return dict(battery.disable_env)


# --------------------------------------------------------------------------- #
# Serialization (one shape for the CLI --json and the GUI endpoint)
# --------------------------------------------------------------------------- #
def to_dict(battery: Battery, env: Mapping[str, str]) -> dict[str, object]:
    return {
        "id": battery.id,
        "name": battery.name,
        "category": battery.category,
        "what": battery.what,
        "how_it_helps": battery.how_it_helps,
        "builtin": battery.builtin,
        "default_on": battery.default_on,
        "status": battery_status(battery, env),
        "enabled": is_enabled(battery, env),
        "installed": is_installed(battery, env),
        "requires_daemon": battery.requires_daemon,
        "service": battery.service,
        "install_kind": battery.install_kind,
        "install_hint": battery.install_hint,
        "pip_extra": battery.pip_extra,
        "env_keys": sorted(set(battery.enable_env) | set(battery.disable_env)),
        "docs": battery.docs,
    }


def manifest(env: Mapping[str, str] | None = None) -> dict[str, object]:
    """The full manifest with per-battery status. Read by the GUI and the CLI."""
    resolved = load_env(env)
    rows = [to_dict(battery, resolved) for battery in BATTERIES]
    summary = {
        "included": sum(1 for r in rows if r["status"] == STATUS_INCLUDED),
        "enabled": sum(1 for r in rows if r["status"] == STATUS_ENABLED),
        "available": sum(1 for r in rows if r["status"] == STATUS_AVAILABLE),
        "not_installed": sum(1 for r in rows if r["status"] == STATUS_NOT_INSTALLED),
        "total": len(rows),
    }
    return {"version": 1, "summary": summary, "batteries": rows}


# --------------------------------------------------------------------------- #
# .env writer (stdlib, atomic, 0600). Shared by the wizard and `alfred batteries`.
# --------------------------------------------------------------------------- #
def write_env(path: Path, values: Mapping[str, str]) -> None:
    """Upsert environment-variable lines into an .env file, preserving the rest.

    An existing line for a key is replaced in place (comments and ordering
    around it survive); a missing key is appended. Written atomically with 0600
    perms so a reader never sees a half-written, world-readable secrets file.
    Idempotent: writing the same values twice leaves the file unchanged.
    """
    for key in values:
        if not _ENV_KEY_RE.match(key):
            raise ValueError(f"unsafe env key: {key!r}")
    for value in values.values():
        if "\n" in value or "\r" in value:
            raise ValueError("env values may not contain newlines")

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        existing = []

    remaining = dict(values)
    out_lines: list[str] = []
    for line in existing:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            name = stripped.partition("=")[0].removeprefix("export ").strip()
            if name in remaining:
                out_lines.append(f"{name}={remaining.pop(name)}")
                continue
        out_lines.append(line)
    for name, value in remaining.items():
        out_lines.append(f"{name}={value}")

    body = "\n".join(out_lines).rstrip("\n") + "\n"
    tmp = path.with_name(f"{path.name}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, body.encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, path)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)
