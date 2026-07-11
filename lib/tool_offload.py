#!/usr/bin/env python3
"""Offload oversized tool output to a firing-scoped file, keep it re-readable.

When a tool output is large enough that the compactor would truncate it, pure
truncation throws away bytes the agent may still need. This module - borrowing
the shape of deepagents' ``_message_eviction`` and re-implementing it natively in
Alfred's compaction seam - instead writes the FULL output to a firing-scoped
scratch file and replaces the inline body with a head/tail preview plus the
absolute path, so the agent can re-read the exact slice it needs (a line range of
the saved file) rather than re-running the command.

Layout (under ``$ALFRED_HOME``, the runtime state dir the fleet already owns)::

    $ALFRED_HOME/state/firings/<firing_id>/tool-output/<n>.txt

Total offload disk per firing is bounded (``ALFRED_TOOL_OFFLOAD_MAX_BYTES``,
default ~50MB): once a firing's offload directory would exceed the bound, further
outputs skip offload and fall back to plain compaction, so a runaway firing can
never fill the disk. Old firing directories are swept by the existing firing
cleanup (see ``bin/agent-cleanup.py``; :func:`sweep_expired`).

Design rules (mirroring ``lib/tool_compactor.py``):

* **Stdlib only.** Runs on the Claude Code PostToolUse hook path under any
  ``python3`` without the project venv.
* **Config-driven** via env, read at call time (12-factor).
* **Fail conservative.** Any IO error, a missing firing id, or a breached disk
  bound yields a not-applied result, and the caller falls back to the compactor's
  ordinary head+tail output. Offload never raises on the hook path.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "DEFAULT_MAX_BYTES_PER_FIRING",
    "OffloadResult",
    "firing_offload_dir",
    "offload",
    "offload_enabled",
    "sweep_expired",
]

_FALSEY = {"0", "false", "no", "off"}

# Total bytes of offloaded output allowed per firing before offload stops (and
# the caller falls back to plain compaction). ~50MB by default.
DEFAULT_MAX_BYTES_PER_FIRING = 50_000_000

# Preview edges kept inline alongside the saved-path pointer.
_DEFAULT_PREVIEW_HEAD_LINES = 20
_DEFAULT_PREVIEW_TAIL_LINES = 20

# A firing id becomes a single path component; keep it filesystem-safe.
_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9._-]+")
_UNKNOWN_FIRING = "unknown"


def _resolve(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _flag_enabled(env: Mapping[str, str], key: str) -> bool:
    """A default-ON opt-out flag: True unless set to a falsey token."""
    raw = env.get(key)
    if raw is None:
        return True
    return raw.strip().lower() not in _FALSEY


def _env_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip().replace("_", ""))
    except ValueError:
        return default


def offload_enabled(env: Mapping[str, str] | None = None) -> bool:
    """True unless an operator opts out with ``ALFRED_TOOL_OFFLOAD=0``."""
    return _flag_enabled(_resolve(env), "ALFRED_TOOL_OFFLOAD")


def _alfred_home(env: Mapping[str, str]) -> Path:
    home = (env.get("ALFRED_HOME") or "").strip()
    base = Path(home) if home else Path(os.path.expanduser("~/.alfred"))
    return base.expanduser()


def _firings_root(env: Mapping[str, str]) -> Path:
    return _alfred_home(env) / "state" / "firings"


def _safe_firing_id(firing_id: str | None) -> str:
    cleaned = _SAFE_ID_RE.sub("-", (firing_id or "").strip()).strip("-")
    return cleaned or _UNKNOWN_FIRING


def firing_offload_dir(firing_id: str | None, env: Mapping[str, str] | None = None) -> Path:
    """The ``tool-output`` directory for one firing (not created here)."""
    resolved = _resolve(env)
    return _firings_root(resolved) / _safe_firing_id(firing_id) / "tool-output"


def _dir_total_bytes(directory: Path) -> int:
    total = 0
    try:
        for entry in directory.iterdir():
            if entry.is_file():
                with contextlib.suppress(OSError):
                    total += entry.stat().st_size
    except OSError:
        return 0
    return total


def _next_index(directory: Path) -> int:
    """One past the highest existing ``<n>.txt`` stem (starts at 1)."""
    highest = 0
    try:
        for entry in directory.iterdir():
            if entry.suffix == ".txt" and entry.stem.isdigit():
                highest = max(highest, int(entry.stem))
    except OSError:
        return 1
    return highest + 1


@dataclass(frozen=True)
class OffloadResult:
    """Outcome of one offload attempt."""

    applied: bool
    text: str
    path: str | None
    index: int
    bytes_written: int
    reason: str


def _preview_lines(env: Mapping[str, str]) -> tuple[int, int]:
    head = max(
        1, _env_int(env, "ALFRED_TOOL_OFFLOAD_PREVIEW_HEAD_LINES", _DEFAULT_PREVIEW_HEAD_LINES)
    )
    tail = max(
        1, _env_int(env, "ALFRED_TOOL_OFFLOAD_PREVIEW_TAIL_LINES", _DEFAULT_PREVIEW_TAIL_LINES)
    )
    return head, tail


def _pointer_text(full_text: str, path: Path, head_lines: int, tail_lines: int) -> str:
    """Head/tail preview around a saved-path pointer, or the whole text if small.

    When the output is short enough that head+tail would cover it, the pointer is
    still appended (so the agent knows the full copy exists) but no content is
    omitted.
    """
    lines = full_text.split("\n")
    total = len(lines)
    if total <= head_lines + tail_lines:
        head, tail, omitted = lines, [], 0
    else:
        head = lines[:head_lines]
        tail = lines[-tail_lines:]
        omitted = total - head_lines - tail_lines
    marker = (
        f"\n[ALFRED_TOOL_OFFLOAD omitted_lines={omitted} bytes={len(full_text.encode('utf-8'))}]\n"
        f"Full output saved to {path}\n"
        "Re-read that file (or a line range of it) to recover the omitted content.\n"
        "[/ALFRED_TOOL_OFFLOAD]\n"
    )
    body = "\n".join(head)
    if tail:
        body = body + marker + "\n".join(tail)
    else:
        body = body + marker
    return body


def offload(
    full_text: str,
    *,
    firing_id: str | None,
    env: Mapping[str, str] | None = None,
) -> OffloadResult:
    """Save ``full_text`` to a firing-scoped file; return a preview + pointer.

    Returns a not-applied result (``applied=False``) when offload is disabled, the
    per-firing disk bound would be breached, or any IO fails - in every such case
    the caller keeps the compactor's ordinary output. On success ``text`` is the
    head/tail preview with the absolute saved path inlined, and ``path`` is that
    absolute path.
    """
    resolved = _resolve(env)
    full_text = full_text or ""
    if not offload_enabled(resolved):
        return OffloadResult(False, full_text, None, 0, 0, "disabled")

    payload = full_text.encode("utf-8")
    directory = firing_offload_dir(firing_id, resolved)
    max_bytes = max(
        0, _env_int(resolved, "ALFRED_TOOL_OFFLOAD_MAX_BYTES", DEFAULT_MAX_BYTES_PER_FIRING)
    )

    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return OffloadResult(False, full_text, None, 0, 0, "mkdir_failed")

    # Enforce the per-firing disk bound BEFORE writing.
    if _dir_total_bytes(directory) + len(payload) > max_bytes:
        return OffloadResult(False, full_text, None, 0, 0, "disk_bound_exceeded")

    index = _next_index(directory)
    path = (directory / f"{index}.txt").resolve()
    try:
        path.write_bytes(payload)
    except OSError:
        return OffloadResult(False, full_text, None, index, 0, "write_failed")

    head_lines, tail_lines = _preview_lines(resolved)
    text = _pointer_text(full_text, path, head_lines, tail_lines)
    return OffloadResult(True, text, str(path), index, len(payload), "offloaded")


def sweep_expired(
    max_age_seconds: float,
    *,
    now: float,
    env: Mapping[str, str] | None = None,
) -> tuple[int, float]:
    """Remove firing offload directories whose mtime is older than the cutoff.

    Returns ``(dirs_removed, mb_freed)``. Best-effort: any per-entry IO error is
    swallowed so the cleanup pass never crashes on a partially-written tree. This
    is the hook the existing firing cleanup (``bin/agent-cleanup.py``) calls.
    """
    resolved = _resolve(env)
    root = _firings_root(resolved)
    removed = 0
    freed_bytes = 0
    try:
        entries = list(root.iterdir())
    except OSError:
        return 0, 0.0
    for firing_dir in entries:
        if not firing_dir.is_dir():
            continue
        try:
            age = now - firing_dir.stat().st_mtime
        except OSError:
            continue
        if age <= max_age_seconds:
            continue
        freed_bytes += _dir_total_bytes(firing_dir / "tool-output")
        shutil.rmtree(firing_dir, ignore_errors=True)
        removed += 1
    return removed, freed_bytes / (1024 * 1024)
