"""Environment-variable and engine-selection configuration.

This module owns the 12-factor env-var contract:

* ``env_int`` / ``optional_env_int`` for clamped integer knobs.
* ``truthy``, ``_truthy_env``, ``_env_value_enabled``, ``_env_present`` for the
  three flavours of boolean env-var test.
* Engine and model selection helpers (``normalize_engine``, ``agent_engine``,
  ``normalize_model_name``, ``agent_model``, ``engine_preflight_bins``) and the engine-mode constants
  (``ENGINE_CHOICES``, ``PROVIDER_LIMIT_SUBTYPES``).
* Codex sandbox resolution per agent (``codex_sandbox_for_agent``).
* Doctor + dry-run mode flags (``doctor_mode``, ``is_dry_run``,
  ``set_dry_run``, ``dry_run_log``).

What this module does NOT own:

* The Slack webhook URL resolution (env + cache + AWS Secrets) -> ``notify.py``.
* The dotenv loader: alfred-os reads config from process env and
  ``$ALFRED_HOME/.env``. There is no global shell rc parse path here.
* Constructing ``ClaudeResult`` objects -> ``result.py``.

All values are computed at call time (no module-level caches), so tests can
``monkeypatch.setenv`` then call any function and see the new value.
"""

from __future__ import annotations

import contextlib
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from envflags import FALSY_VALUES
from envflags import truthy as _truthy

from .engine_registry import DEFAULT_ENGINE_REGISTRY, EngineCapability
from .paths import CLAUDE_BIN, CODEX_BIN, STATE_ROOT

truthy = _truthy

# --------------------------------------------------------------------------
# Engine vocabulary
# --------------------------------------------------------------------------
ENGINE_CHOICES: frozenset[str] = DEFAULT_ENGINE_REGISTRY.dispatchable_ids | {"hybrid"}
MODEL_ENGINES: frozenset[str] = frozenset(
    descriptor.id
    for descriptor in DEFAULT_ENGINE_REGISTRY.supporting({EngineCapability.MODEL_SELECTION})
    if descriptor.dispatchable
)
_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_AGENT_CODENAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

PROVIDER_LIMIT_SUBTYPES: frozenset[str] = frozenset({"error_budget", "error_rate_limit"})
"""Subtypes that mean we hit a provider's quota / rate-limit wall."""


def reported_subtype(result: object) -> str:
    """Return the raw subtype an agent should report.

    Hybrid fallback no longer rewrites auth or quota failures through a second
    provider. The result subtype is therefore the honest headline; any
    ``fallback_from_subtype`` is audit context, not a replacement.
    """
    return getattr(result, "subtype", "") or ""


# --------------------------------------------------------------------------
# Env-var primitives
# --------------------------------------------------------------------------


def _truthy_env(name: str) -> bool:
    """True when env var is set to a canonical true token."""
    return truthy(os.environ.get(name))


def _env_value_enabled(name: str) -> bool:
    """True when env var is set to a non-falsy value (broader than _truthy_env)."""
    value = os.environ.get(name)
    return bool(value and value.strip().lower() not in {"", *FALSY_VALUES})


def _env_present(name: str) -> bool:
    """True when env var is set to any non-empty string."""
    return bool(os.environ.get(name))


def _agent_env_slug(agent: str) -> str:
    """Translate a codename to the env-var convention (UPPER, hyphens -> underscores)."""
    return agent.strip().upper().replace("-", "_")


def env_int(name: str, default: int, *, minimum: int = 1, maximum: int | None = None) -> int:
    """Read a small integer knob from env, clamped to ``[minimum, maximum]``.

    Missing or non-integer values fall back to ``default``. The result is
    always clamped, including the fallback path, so a typo in the launchd
    plist can never kneecap or unbound a per-firing budget.

    Args:
        name: env var name.
        default: value used when the env var is unset or unparseable.
        minimum: floor (inclusive).
        maximum: optional ceiling (inclusive); ``None`` means uncapped.

    Returns:
        The clamped integer.
    """
    raw = os.environ.get(name, "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            value = default
    else:
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def optional_env_int(name: str, *, minimum: int = 1, maximum: int | None = None) -> int | None:
    """Read an optional integer knob; return ``None`` when unset or unparseable.

    Designed for "no default ceiling but allow temporary debugging via env"
    knobs, most prominently the per-firing ``max_turns`` budget on agents
    where a hard cap can produce no-output runs. The wall-clock ``timeout``
    on the invoke call remains the real bound.

    Args:
        name: env var name.
        minimum: floor when a value parses.
        maximum: optional ceiling when a value parses.

    Returns:
        The clamped integer, or ``None`` when no value is configured.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


# --------------------------------------------------------------------------
# Engine selection
# --------------------------------------------------------------------------


def normalize_engine(raw: str | None, *, default: str = "hybrid") -> str:
    """Return one canonical engine mode, rejecting unknown values."""

    value = (raw or "").strip().lower()
    fallback = (default or "hybrid").strip().lower()
    if fallback not in ENGINE_CHOICES:
        raise ValueError(f"unknown default engine: {default!r}")
    if not value:
        return fallback
    if value not in ENGINE_CHOICES:
        raise ValueError(
            f"unknown engine {raw!r}; choose one of: {', '.join(sorted(ENGINE_CHOICES))}"
        )
    return value


def agent_engine(
    agent: str,
    *,
    default: str = "hybrid",
    environ: dict[str, str] | None = None,
) -> str:
    """Resolve the configured engine for one agent.

    Precedence:

    1. ``ALFRED_<AGENT>_ENGINE``
    2. ``ALFRED_ENGINE`` for fleet-wide testing
    3. ``${ALFRED_HOME}/state/engines/<agent>``
    4. ``default``

    Args:
        agent: codename.
        default: fallback when nothing is configured.
        environ: env mapping override (defaults to ``os.environ``).

    Returns:
        A value in ``ENGINE_CHOICES``.
    """
    env = environ if environ is not None else os.environ
    safe_agent = agent.strip().lower().replace("_", "-")
    env_name = f"ALFRED_{_agent_env_slug(safe_agent)}_ENGINE"
    for name in (env_name, "ALFRED_ENGINE"):
        if name and env.get(name, "").strip():
            return normalize_engine(env.get(name), default=default)

    state_file = STATE_ROOT / "engines" / safe_agent
    try:
        raw = state_file.read_text(encoding="utf-8").strip()
    except OSError:
        raw = ""
    if raw:
        return normalize_engine(raw, default=default)
    return normalize_engine(None, default=default)


def normalize_model_name(raw: str | None) -> str | None:
    """Validate one CLI model name, returning ``None`` for unsafe input."""

    value = (raw or "").strip()
    return value if _MODEL_NAME.fullmatch(value) else None


@dataclass(frozen=True)
class AgentModelSelection:
    """Resolved model state for one agent and provider."""

    model: str | None
    source: str
    persisted: str | None


def _model_provider(engine: str) -> str:
    provider = engine.strip().lower()
    if provider not in MODEL_ENGINES:
        raise ValueError(f"model engine must be one of: {', '.join(sorted(MODEL_ENGINES))}")
    return provider


def _model_agent(agent: str) -> str:
    codename = agent.strip().lower().replace("_", "-")
    if not _AGENT_CODENAME.fullmatch(codename):
        raise ValueError("agent codename must use lowercase letters, digits, and hyphens")
    return codename


def agent_model_state_file(
    agent: str,
    engine: str,
    *,
    state_root: Path | None = None,
) -> Path:
    """Return the isolated state file for one agent/provider pair."""

    root = Path(state_root) if state_root is not None else STATE_ROOT
    return root / "models" / _model_agent(agent) / _model_provider(engine)


def agent_model_selection(
    agent: str,
    engine: str,
    *,
    environ: dict[str, str] | None = None,
    state_root: Path | None = None,
) -> AgentModelSelection:
    """Resolve a model and report both its winning source and saved value."""

    provider = _model_provider(engine)
    safe_agent = _model_agent(agent)
    env = environ if environ is not None else os.environ
    state_file = agent_model_state_file(safe_agent, provider, state_root=state_root)
    try:
        persisted = normalize_model_name(state_file.read_text(encoding="utf-8"))
    except OSError:
        persisted = None

    agent_key = f"ALFRED_{_agent_env_slug(safe_agent)}_{provider.upper()}_MODEL"
    global_key = f"ALFRED_{provider.upper()}_MODEL"
    for name, source in (
        (agent_key, "agent-environment"),
        (global_key, "fleet-environment"),
    ):
        raw = env.get(name, "")
        if raw.strip():
            return AgentModelSelection(
                model=normalize_model_name(raw),
                source=source,
                persisted=persisted,
            )
    if persisted:
        return AgentModelSelection(model=persisted, source="state", persisted=persisted)
    return AgentModelSelection(model=None, source="provider-default", persisted=None)


def persist_agent_model(
    agent: str,
    engine: str,
    model: str,
    *,
    state_root: Path | None = None,
) -> Path:
    """Atomically persist a validated model choice."""

    normalized = normalize_model_name(model)
    if normalized is None:
        raise ValueError(
            "model names must start with a letter or digit and use only letters, "
            "digits, '.', '_', ':', '/', or '-' (max 128 characters)"
        )
    target = agent_model_state_file(agent, engine, state_root=state_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(normalized + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)
        with contextlib.suppress(OSError):
            temp_path.unlink()
    return target


def clear_agent_model(
    agent: str,
    engine: str,
    *,
    state_root: Path | None = None,
) -> Path:
    """Remove one persisted model choice without changing environment overrides."""

    target = agent_model_state_file(agent, engine, state_root=state_root)
    target.unlink(missing_ok=True)
    with contextlib.suppress(OSError):
        target.parent.rmdir()
    return target


def agent_model(
    agent: str,
    engine: str,
    *,
    environ: dict[str, str] | None = None,
) -> str | None:
    """Resolve one agent's model override for Claude Code or Codex.

    Precedence:

    1. ``ALFRED_<AGENT>_<ENGINE>_MODEL``
    2. ``ALFRED_<ENGINE>_MODEL`` for a fleet-wide provider override
    3. ``${ALFRED_HOME}/state/models/<agent>/<engine>``
    4. ``None`` so the provider CLI keeps its own default

    Invalid explicit env values disable lower-precedence model state instead
    of forwarding untrusted text as a CLI argument.
    """

    return agent_model_selection(agent, engine, environ=environ).model


def agent_repos(
    agent: str,
    *,
    environ: dict[str, str] | None = None,
) -> list[str]:
    """Resolve the repo scope for one agent, keyed off its runtime role slug.

    ``alfred-init`` writes repo scope to ``ALFRED_<ROLE>_REPOS``. Themes and
    custom visible names never change this key; the role slug is the machine
    identity.

    Returns the parsed, de-blanked repo list for ``ALFRED_<AGENT>_REPOS``.
    """
    env = environ if environ is not None else os.environ
    slug = _agent_env_slug(agent)
    primary = f"ALFRED_{slug}_REPOS"
    raw = env.get(primary, "")
    return [r.strip() for r in raw.split(",") if r.strip()]


def engine_preflight_bins(engine: str, *, hybrid_requires_codex: bool = False) -> list[str]:
    """Return load-bearing binaries for an engine mode.

    Hybrid is Claude-first by default, so a missing optional Codex
    fallback does not stop ordinary scheduled work. Callers that require
    Codex even in hybrid mode pass ``hybrid_requires_codex=True``.
    """
    mode = normalize_engine(engine)
    if mode != "hybrid":
        descriptor = DEFAULT_ENGINE_REGISTRY.descriptor(mode)
        configured = os.environ.get(descriptor.binary_env, "").strip()
        return [configured or descriptor.default_binary]
    if mode == "hybrid" and hybrid_requires_codex:
        return [CLAUDE_BIN, CODEX_BIN]
    return [CLAUDE_BIN]


def codex_sandbox_for_agent(
    agent: str,
    *,
    default: str = "read-only",
    environ: dict[str, str] | None = None,
) -> str:
    """Resolve the Codex sandbox mode for an agent.

    Precedence:

    1. ``ALFRED_<AGENT>_CODEX_SANDBOX``
    2. ``ALFRED_<AGENT>_CODEX_WRITE=1`` -> ``workspace-write``
    3. ``default``
    """
    env = environ if environ is not None else os.environ
    slug = _agent_env_slug(agent)
    explicit = (env.get(f"ALFRED_{slug}_CODEX_SANDBOX") or "").strip()
    if explicit:
        return explicit
    if truthy(env.get(f"ALFRED_{slug}_CODEX_WRITE")):
        return "workspace-write"
    return default


# --------------------------------------------------------------------------
# Doctor + dry-run mode
# --------------------------------------------------------------------------


def doctor_requested() -> bool:
    """True when the operator requested ``alfred doctor`` mode."""
    return _env_value_enabled("ALFRED_DOCTOR")


def doctor_mode() -> bool:
    """True when running under ``alfred doctor`` (``ALFRED_DOCTOR=1``).

    Agents check this after preflight passes and exit ``0`` with a
    ``[<AGENT>-DOCTOR-OK]`` sentinel instead of doing real work. Lets
    the operator verify a fresh setup without burning Claude turns.
    """
    return doctor_requested()


# Dry-run step counter: process-local. Reset on import; bin scripts run in
# their own process so there is no shared-state risk.
_DRY_RUN_STEP = 0


def is_dry_run() -> bool:
    """True when the firing is a dry run (``ALFRED_DRY_RUN`` truthy).

    Checked at every side-effecting boundary as a single seam, not as
    scattered conditionals. Runners that accept a ``--dry-run`` CLI flag
    call ``set_dry_run()`` to flip this on.
    """
    return _env_value_enabled("ALFRED_DRY_RUN")


def set_dry_run(enabled: bool = True) -> None:
    """Enable (or disable) dry-run mode for the rest of this process.

    Writes ``ALFRED_DRY_RUN`` into ``os.environ`` so ``is_dry_run()`` and
    any subprocess-spawned children agree. Runners call this once after
    parsing a ``--dry-run`` CLI flag, before the lifecycle starts.
    """
    if enabled:
        os.environ["ALFRED_DRY_RUN"] = "1"
    else:
        os.environ.pop("ALFRED_DRY_RUN", None)


def dry_run_log(step: str, message: str) -> None:
    """Print one narrated ``[dry-run]`` trace line to stdout.

    ``step`` is a short lifecycle tag (``slack``, ``gh``, ``git``,
    ``llm``, ``spend``, ...). The output is deliberately legible and
    well-sequenced; a dry-run firing is meant to be recorded with
    asciinema.
    """
    global _DRY_RUN_STEP
    _DRY_RUN_STEP += 1
    print(f"[dry-run] {_DRY_RUN_STEP:>2}. ({step}) {message}", file=sys.stdout, flush=True)
