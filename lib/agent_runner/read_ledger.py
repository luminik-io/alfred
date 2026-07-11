"""Per-worktree delta cache for files already surfaced to a firing.

When an agent reads a file, then reads it again a few turns later, the second
read usually costs a full copy of the file for a handful of changed lines. This
module remembers what content was last surfaced for each key within one firing
and, on a re-read, returns only a unified diff against that prior copy. The
first read of any file is always full; a change too large to diff usefully, or
a file that is not usefully text, falls back to full content. Nothing is ever
hidden: a delta plus the previously surfaced copy reconstructs the file exactly.

Design rules (mirror the rest of ``agent_runner``):

- Stdlib only, so this imports cleanly under any ``python3`` (launchd, the bash
  CLI, the MCP subprocess, the test suite) without the venv.
- State is per-worktree and on disk under a firing-scoped root, so it matches
  the firing model and survives across the several tool invocations of one run
  without leaking between firings.
- Deterministic: ``difflib.unified_diff`` over the same two strings always
  yields the same patch, so a given re-read is reproducible.
- Fail-soft and conservative: any config parse error falls back to the safe
  default, and any ambiguity resolves toward returning full content.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from envflags import FALSY_VALUES

__all__ = [
    "DEFAULT_CONTEXT_LINES",
    "DEFAULT_MAX_DIFF_CHARS",
    "DEFAULT_MAX_RATIO",
    "READ_DELTA_ENV",
    "ReadLedger",
    "ReadResult",
    "delta_context_lines",
    "delta_max_chars",
    "delta_max_ratio",
    "ledger_root_for",
    "read_delta_available",
    "read_delta_enabled",
]

# Default-ON gate for the delta behavior. The delta read is loss-free (first
# read is full; a re-read returns a diff the agent can apply), so it is safe to
# default on; an operator can still pin it off.
READ_DELTA_ENV = "ALFRED_READ_DELTA"

# When the unified diff is larger than ``ratio`` times the new content, the
# saving is not worth the reasoning cost of applying a patch, so we send full
# content instead. Conservative default: only emit a delta when it is at most
# half the size of the whole file.
DEFAULT_MAX_RATIO = 0.5

# Lines of unchanged context each side of a change in the emitted diff.
DEFAULT_CONTEXT_LINES = 3

# Above this many characters on either side, skip diffing entirely and return
# full content. Bounds the cost of ``difflib`` on pathologically large files.
DEFAULT_MAX_DIFF_CHARS = 400_000

_FALSEY = FALSY_VALUES | {""}


def read_delta_enabled(env: Mapping[str, str] | None = None) -> bool:
    """True unless ``ALFRED_READ_DELTA`` is explicitly falsy (default on)."""
    resolved = os.environ if env is None else env
    raw = resolved.get(READ_DELTA_ENV)
    if raw is None:
        return True
    return raw.strip().lower() not in _FALSEY


def read_delta_available(env: Mapping[str, str] | None = None) -> bool:
    """True only when a real firing id scopes the ledger.

    Delta re-read strictly requires ``ALFRED_FIRING_ID``. Without it the ledger
    would be shared across firings, so a re-read could diff against another
    firing's content and leak or corrupt it. In that case delta is disabled and
    callers fall back to full reads, which are always correct.
    """
    resolved = os.environ if env is None else env
    return bool((resolved.get("ALFRED_FIRING_ID") or "").strip())


def _env_float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(str(raw).strip())
    except ValueError:
        return default
    return value if value >= 0 else default


def _env_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip().replace("_", ""))
    except ValueError:
        return default
    return value if value >= 0 else default


def delta_max_ratio(env: Mapping[str, str] | None = None) -> float:
    resolved = os.environ if env is None else env
    return _env_float(resolved, "ALFRED_READ_DELTA_MAX_RATIO", DEFAULT_MAX_RATIO)


def delta_context_lines(env: Mapping[str, str] | None = None) -> int:
    resolved = os.environ if env is None else env
    return _env_int(resolved, "ALFRED_READ_DELTA_CONTEXT", DEFAULT_CONTEXT_LINES)


def delta_max_chars(env: Mapping[str, str] | None = None) -> int:
    resolved = os.environ if env is None else env
    value = _env_int(resolved, "ALFRED_READ_DELTA_MAX_CHARS", DEFAULT_MAX_DIFF_CHARS)
    return value or DEFAULT_MAX_DIFF_CHARS


def ledger_root_for(
    workdir: Path | str | None,
    *,
    env: Mapping[str, str] | None = None,
    state_root: Path | str | None = None,
) -> Path:
    """Return the per-firing ledger directory at its default location.

    The ledger always lives under ``<state_root>/read-ledger/<digest>`` (state
    root defaults to ``$ALFRED_HOME/state``), keyed by the firing id plus the
    worktree path. A firing id is mandatory: delta re-read is only available
    when one is set (see :func:`read_delta_available`), so this raises
    ``ValueError`` when it is absent rather than inventing a scope that two
    firings could collide on. There is deliberately no directory override: the
    single, firing-scoped default location is what keeps two firings from ever
    sharing a ledger.
    """
    resolved = os.environ if env is None else env
    firing = (resolved.get("ALFRED_FIRING_ID") or "").strip()
    if not firing:
        raise ValueError("read-delta ledger requires ALFRED_FIRING_ID")

    base = Path(state_root) if state_root is not None else _default_state_root(resolved)
    workdir_token = str(Path(workdir).resolve()) if workdir else ""
    digest = hashlib.sha256(f"{firing}\n{workdir_token}".encode()).hexdigest()[:16]
    return base / "read-ledger" / digest


def _default_state_root(env: Mapping[str, str]) -> Path:
    home = env.get("ALFRED_HOME") or os.path.expanduser("~/.alfred")
    return Path(home).expanduser() / "state"


@dataclass(frozen=True)
class ReadResult:
    """Outcome of surfacing one file's content through the ledger."""

    mode: str  # "full" | "delta" | "unchanged"
    path: str
    content: str
    diff: str
    reason: str
    full_chars: int
    delta_chars: int
    prior_chars: int

    def as_raw(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "path": self.path,
            "reason": self.reason,
            "full_chars": self.full_chars,
            "delta_chars": self.delta_chars,
            "prior_chars": self.prior_chars,
        }


class ReadLedger:
    """On-disk record of the last content surfaced for each key in a firing."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self._index = self.root / "entries.json"

    # -- storage ---------------------------------------------------------
    def _load(self) -> dict[str, str]:
        try:
            payload = json.loads(self._index.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {str(k): str(v) for k, v in payload.items() if isinstance(v, str)}

    def _store(self, entries: dict[str, str]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self._index.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self._index)

    def get(self, key: str) -> str | None:
        return self._load().get(key)

    def record(self, key: str, content: str) -> None:
        entries = self._load()
        entries[key] = content
        self._store(entries)

    # -- surfacing -------------------------------------------------------
    def surface(
        self,
        key: str,
        content: str,
        *,
        max_ratio: float = DEFAULT_MAX_RATIO,
        context_lines: int = DEFAULT_CONTEXT_LINES,
        max_diff_chars: int = DEFAULT_MAX_DIFF_CHARS,
    ) -> ReadResult:
        """Surface ``content`` for ``key`` as full, delta, or unchanged.

        - No prior copy -> ``full`` (and record it).
        - Identical to the prior copy -> ``unchanged`` (empty content).
        - Changed and usefully diffable -> ``delta`` (unified diff; record new).
        - Otherwise (too large, binary-ish, or diff not smaller enough) ->
          ``full`` fallback (and record new).
        """
        full_chars = len(content)
        prior = self.get(key)
        if prior is None:
            self.record(key, content)
            return ReadResult(
                mode="full",
                path=key,
                content=content,
                diff="",
                reason="first_read",
                full_chars=full_chars,
                delta_chars=0,
                prior_chars=0,
            )

        prior_chars = len(prior)
        if prior == content:
            return ReadResult(
                mode="unchanged",
                path=key,
                content="",
                diff="",
                reason="identical_to_prior_read",
                full_chars=full_chars,
                delta_chars=0,
                prior_chars=prior_chars,
            )

        # Update the stored copy up front: after this call the agent has seen
        # the new content (as delta or full), so the next re-read diffs against
        # it either way.
        self.record(key, content)

        if _looks_binary(prior) or _looks_binary(content):
            return self._full_fallback(key, content, prior_chars, "not_text")
        if max(full_chars, prior_chars) > max_diff_chars:
            return self._full_fallback(key, content, prior_chars, "too_large_to_diff")

        diff = _unified_diff(prior, content, key, context_lines)
        if not diff:
            # Difference is whitespace at EOF only or otherwise produced no
            # hunks; a full copy is clearer than an empty patch.
            return self._full_fallback(key, content, prior_chars, "empty_diff")
        if len(diff) > max(1, int(full_chars * max_ratio)):
            return self._full_fallback(key, content, prior_chars, "diff_not_smaller")

        return ReadResult(
            mode="delta",
            path=key,
            content="",
            diff=diff,
            reason="changed_since_prior_read",
            full_chars=full_chars,
            delta_chars=len(diff),
            prior_chars=prior_chars,
        )

    def _full_fallback(self, key: str, content: str, prior_chars: int, reason: str) -> ReadResult:
        return ReadResult(
            mode="full",
            path=key,
            content=content,
            diff="",
            reason=reason,
            full_chars=len(content),
            delta_chars=0,
            prior_chars=prior_chars,
        )


def _looks_binary(text: str) -> bool:
    return "\x00" in text


def _unified_diff(prior: str, current: str, label: str, context_lines: int) -> str:
    prior_lines = prior.splitlines(keepends=True)
    current_lines = current.splitlines(keepends=True)
    diff = difflib.unified_diff(
        prior_lines,
        current_lines,
        fromfile=f"a/{label}",
        tofile=f"b/{label}",
        n=max(0, context_lines),
    )
    return "".join(diff)
