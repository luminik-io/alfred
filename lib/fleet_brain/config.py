"""Environment parsing and policy predicates for the fleet brain.

Every knob the fleet brain reads from the environment lives here: the
consolidation/decay opt-ins, the semantic-merge threshold, the lesson cap, and
the auto-promotion switch logic. Splitting this out of the ``FleetBrain`` facade
keeps the class free of env plumbing and gives the CLI/scheduled runner a single
place to check an opt-in BEFORE opening the ledger.

The predicates fail closed on typos: a destructive opt-in (consolidation) arms
only on a recognized truthy token, while the learning switch treats a malformed
value as "off" so a bad config never silently changes behavior.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

_TRUTHY_ENV_TOKENS = {"1", "true", "yes", "on", "enabled"}
_FALSY_ENV_TOKENS = {"0", "false", "no", "off", "disabled"}
_RECOGNIZED_ENV_TOKENS = _TRUTHY_ENV_TOKENS | _FALSY_ENV_TOKENS
_AUTO_PROMOTE_STOP_KEYS = {
    "ALFRED_AUTO_PROMOTE",
    "ALFRED_AUTO_PROMOTE_KILL",
    "ALFRED_AUTO_PROMOTE_LLM_JUDGE",
}

# Cosine-similarity floor at/above which two lessons in the same repo+codename
# scope are treated as near-duplicates by the semantic merge. Conservative: only
# very close bodies collapse, so a genuinely distinct lesson is never merged
# away. Override with ``ALFRED_MEMORY_CONSOLIDATE_SIM_THRESHOLD``.
_DEFAULT_CONSOLIDATE_SIM_THRESHOLD = 0.92


def _env_kill_switch_on(name: str, env: Mapping[str, str] | None = None) -> bool:
    """Default off, but treat malformed nonblank values as enabled."""
    src = env if env is not None else os.environ
    raw = src.get(name)
    value = _env_token(raw)
    if raw is None or not value:
        return False
    return value not in _FALSY_ENV_TOKENS


def _env_opt_in_armed(name: str, env: Mapping[str, str] | None = None) -> bool:
    """Default OFF; arms ONLY on a recognized truthy token, fail closed otherwise.

    For destructive opt-in switches (e.g. ``ALFRED_MEMORY_CONSOLIDATE``) where a
    typo must NOT arm the feature. Unlike ``_env_kill_switch_on`` (which arms on
    any nonblank non-falsy value), an unrecognized token like ``maybe`` stays
    disabled here so a config typo cannot run a destructive pass."""
    src = env if env is not None else os.environ
    value = _env_token(src.get(name))
    return value in _TRUTHY_ENV_TOKENS


def consolidate_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether the consolidation/decay pass is armed (``ALFRED_MEMORY_CONSOLIDATE``).

    Off by default; arms only on a recognized truthy token (fail-closed on a
    typo). This is the SAME predicate ``consolidate_lessons`` gates on, exported
    so a caller (the CLI, the scheduled runner) can check the opt-in BEFORE
    opening the ledger and avoid touching the store on a disarmed no-op run."""
    return _env_opt_in_armed("ALFRED_MEMORY_CONSOLIDATE", env)


def consolidate_semantic_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether the OPTIONAL semantic near-duplicate merge is armed (Phase 3).

    Gated by ``ALFRED_MEMORY_CONSOLIDATE_SEMANTIC`` and only meaningful when the
    parent ``ALFRED_MEMORY_CONSOLIDATE`` pass is also armed. Off by default: with
    it disarmed (or no embedder available) consolidation collapses only lessons
    whose bodies are LEXICALLY identical, exactly as before. When armed AND an
    embedder is available, near-duplicate (not just identical) lessons are merged
    on top of that lexical pass. Fails closed on a typo (the same opt-in contract
    as the parent switch)."""
    return _env_opt_in_armed("ALFRED_MEMORY_CONSOLIDATE_SEMANTIC", env)


def consolidate_sim_threshold(env: Mapping[str, str] | None = None) -> float:
    """Cosine near-duplicate threshold for the semantic merge, clamped to (0, 1].

    A non-numeric, non-positive, or >1 value falls back to the conservative
    default rather than merging on a bad config."""
    src = env if env is not None else os.environ
    raw = src.get("ALFRED_MEMORY_CONSOLIDATE_SIM_THRESHOLD")
    if raw is None or not str(raw).strip():
        return _DEFAULT_CONSOLIDATE_SIM_THRESHOLD
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_CONSOLIDATE_SIM_THRESHOLD
    if not (0.0 < value <= 1.0):
        return _DEFAULT_CONSOLIDATE_SIM_THRESHOLD
    return value


def max_lessons_cap(env: Mapping[str, str] | None = None) -> int:
    """Configured pressure/budget cap on live recall-able lessons (0 = disabled).

    Read from ``ALFRED_MEMORY_MAX_LESSONS``. When positive and the recall store
    supports it, the consolidation pass evicts the lowest-value lessons (by the
    #452 value score) down to this cap, invalidate-not-delete. Zero, negative, or
    unparseable disables eviction (the default), so growth is unbounded unless an
    operator opts in."""
    src = env if env is not None else os.environ
    raw = src.get("ALFRED_MEMORY_MAX_LESSONS")
    if raw is None or not str(raw).strip():
        return 0
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def _env_flag_default_on(name: str, env: Mapping[str, str] | None = None) -> bool:
    """Default to ON, but fail closed for any unrecognized nonblank value."""
    src = env if env is not None else os.environ
    raw = src.get(name)
    value = _env_token(raw)
    if raw is None or not value:
        return True
    if value in _TRUTHY_ENV_TOKENS:
        return True
    if value in _FALSY_ENV_TOKENS:
        return False
    return False


def _env_flag_recognized_or_blank(name: str, env: Mapping[str, str] | None = None) -> bool:
    """True when a flag is absent, blank, or a recognized truthy/falsy token."""
    src = env if env is not None else os.environ
    raw = src.get(name)
    value = _env_token(raw)
    if raw is None or not value:
        return True
    return value in _RECOGNIZED_ENV_TOKENS


def _llm_judge_flag_allows_auto_promote(env: Mapping[str, str] | None = None) -> bool:
    return _env_flag_recognized_or_blank("ALFRED_AUTO_PROMOTE_LLM_JUDGE", env)


def _auto_promote_switches_allow_learning(env: Mapping[str, str] | None = None) -> bool:
    if _env_kill_switch_on("ALFRED_AUTO_PROMOTE_KILL", env):
        return False
    if not _llm_judge_flag_allows_auto_promote(env):
        return False
    return _env_flag_default_on("ALFRED_AUTO_PROMOTE", env)


def _strip_shell_inline_comment(value: str) -> str:
    """Strip shell-style inline comments while preserving quoted hashes."""
    quote: str | None = None
    escaped = False
    for index, ch in enumerate(value):
        if escaped:
            escaped = False
            continue
        if ch == "\\" and quote != "'":
            escaped = True
            continue
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in {"'", '"'}:
            quote = ch
            continue
        if ch == "#" and index > 0 and value[index - 1].isspace():
            return value[:index].rstrip()
    return value


def _env_token(raw: object) -> str:
    """Normalize env flag values, accepting shell-style trailing comments."""
    value = _strip_shell_inline_comment(str(raw)).strip()
    return value.strip().lower()


def _auto_promote_stop_control_active(name: str, raw: object) -> bool:
    if name not in _AUTO_PROMOTE_STOP_KEYS:
        return False
    value = _env_token(raw)
    if not value:
        return False
    if name in {"ALFRED_AUTO_PROMOTE", "ALFRED_AUTO_PROMOTE_LLM_JUDGE"}:
        return value not in _TRUTHY_ENV_TOKENS
    if name == "ALFRED_AUTO_PROMOTE_KILL":
        return value not in _FALSY_ENV_TOKENS
    return False


def _decode_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        return value[1:-1].replace("'\"'\"'", "'")
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def _expand_home(value: str) -> str:
    return value.replace("${HOME}", str(Path.home())).replace("$HOME", str(Path.home()))


def _load_auto_promote_env_file(
    path: Path,
    env: dict[str, str],
    *,
    override_existing: bool = False,
    protected_keys: set[str] | None = None,
    protected_key_overrides: set[str] | None = None,
) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    protected = protected_keys or set()
    protected_overrides = protected_key_overrides or set()
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        key, _, raw_value = line.partition("=")
        key = key.strip()
        if not key or key[0].isdigit() or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        raw_value = _strip_shell_inline_comment(raw_value).strip()
        value = _decode_env_value(raw_value)
        if not (raw_value.startswith("'") and raw_value.endswith("'")):
            value = _expand_home(value)
        if key in env:
            if _auto_promote_stop_control_active(key, env[key]):
                continue
            if not _auto_promote_stop_control_active(key, value) and (
                not override_existing or (key in protected and key not in protected_overrides)
            ):
                continue
        env[key] = value


def direct_auto_promote_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("ALFREDRC", None)
    process_keys = set(os.environ)
    if not env.get("ALFRED_HOME", "").strip():
        env["ALFRED_HOME"] = str(Path("~/.alfred").expanduser())
    else:
        env["ALFRED_HOME"] = str(Path(env["ALFRED_HOME"]).expanduser())
    _load_auto_promote_env_file(
        Path(env["ALFRED_HOME"]).expanduser() / ".env",
        env,
        protected_keys=process_keys,
        protected_key_overrides=set(),
    )
    return env


def _env_float(name: str, default: float, env: Mapping[str, str] | None = None) -> float:
    """Read a float from the environment, falling back on missing/bad input."""
    src = env if env is not None else os.environ
    raw = src.get(name)
    if raw is None or not str(raw).strip():
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)
