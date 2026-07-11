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

import model_context

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
# The preview must never keep fewer edge lines than this while byte-trimming.
_MIN_PREVIEW_EDGE_LINES = 1

# Bounded retries for the O_EXCL index-claim loop: two concurrent hook processes
# can race for the same next index, so each claim is an atomic exclusive create
# and a loser simply tries the next index. The cap only exists so a pathological
# filesystem can never spin the hook forever.
_MAX_CLAIM_ATTEMPTS = 1000

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
    if cleaned in {"", ".", ".."}:
        return _UNKNOWN_FIRING
    return cleaned


def firing_offload_dir(firing_id: str | None, env: Mapping[str, str] | None = None) -> Path:
    """The ``tool-output`` directory for one firing (not created here)."""
    resolved = _resolve(env)
    root = _firings_root(resolved).resolve()
    directory = (root / _safe_firing_id(firing_id) / "tool-output").resolve()
    if root not in directory.parents:
        directory = (root / _UNKNOWN_FIRING / "tool-output").resolve()
    if root not in directory.parents:
        raise ValueError("offload directory escapes firings root")
    return directory


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


def _preview_byte_cap(env: Mapping[str, str]) -> int:
    """The byte budget the inline preview must honour.

    The offload preview REPLACES the compactor's budgeted head+tail output, so it
    must never exceed the same compaction byte budget - otherwise a few multi-MB
    lines would ride back into the model's context nearly in full. This mirrors
    ``tool_compactor._compact_config``: the explicit
    ``ALFRED_OUTPUT_COMPACTOR_MAX_BYTES`` override wins, else the model-derived
    default.
    """
    _, derived_max = model_context.derived_compaction_bytes(env)
    return max(256, _env_int(env, "ALFRED_OUTPUT_COMPACTOR_MAX_BYTES", derived_max))


def _render(head: list[str], tail: list[str], marker: str) -> str:
    body = "\n".join(head)
    if tail:
        return body + marker + "\n".join(tail)
    return body + marker


def _pointer_text(
    full_text: str,
    path: Path,
    head_lines: int,
    tail_lines: int,
    max_bytes: int,
) -> str:
    """Head/tail preview around a saved-path pointer, within a byte budget.

    When the output is short enough that head+tail would cover it, the pointer is
    still appended (so the agent knows the full copy exists) but no content is
    omitted. The preview is then trimmed to ``max_bytes``: tail lines are dropped
    first, then head lines, and as a last resort a single oversized line is
    hard-truncated - so a log made of a few very long lines can never blow the
    compaction byte budget the preview replaces.
    """
    lines = full_text.split("\n")
    total = len(lines)
    if total <= head_lines + tail_lines:
        head, tail = lines, []
    else:
        head = lines[:head_lines]
        tail = lines[-tail_lines:]

    def marker(omitted: int) -> str:
        return (
            f"\n[ALFRED_TOOL_OFFLOAD omitted_lines={omitted} "
            f"bytes={len(full_text.encode('utf-8'))}]\n"
            f"Full output saved to {path}\n"
            "Re-read that file (or a line range of it) to recover the omitted content.\n"
            "[/ALFRED_TOOL_OFFLOAD]\n"
        )

    text = _render(head, tail, marker(total - len(head) - len(tail)))
    # Byte budget: drop tail lines (then head lines) until the preview fits.
    while (
        len(text.encode("utf-8")) > max_bytes and (len(head) + len(tail)) > _MIN_PREVIEW_EDGE_LINES
    ):
        if len(tail) > 0:
            tail = tail[1:]
        else:
            head = head[:-1]
        text = _render(head, tail, marker(total - len(head) - len(tail)))
    if len(text.encode("utf-8")) > max_bytes and head:
        # A single pathological line still exceeds the budget: hard-truncate it.
        keep = max(0, max_bytes - len(marker(total - 1).encode("utf-8")))
        head = [head[0].encode("utf-8")[:keep].decode("utf-8", errors="ignore")]
        text = _render(head, [], marker(total - 1))
    return text


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
    try:
        directory = firing_offload_dir(firing_id, resolved)
    except ValueError:
        return OffloadResult(False, full_text, None, 0, 0, "unsafe_path")
    max_bytes = max(
        0, _env_int(resolved, "ALFRED_TOOL_OFFLOAD_MAX_BYTES", DEFAULT_MAX_BYTES_PER_FIRING)
    )

    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return OffloadResult(False, full_text, None, 0, 0, "mkdir_failed")

    # Enforce the per-firing disk bound BEFORE writing. Two concurrent hook
    # processes can both pass this check (bounded-approximation TOCTOU), so the
    # bound can overshoot by at most one output; it can never run away.
    if _dir_total_bytes(directory) + len(payload) > max_bytes:
        return OffloadResult(False, full_text, None, 0, 0, "disk_bound_exceeded")

    # Claim an index ATOMICALLY (O_CREAT | O_EXCL) so two concurrent hook
    # processes offloading for the same firing can never write the same <n>.txt:
    # the loser of a claim race simply advances to the next index.
    index = _next_index(directory)
    path: Path | None = None
    for _ in range(_MAX_CLAIM_ATTEMPTS):
        candidate = directory / f"{index}.txt"
        try:
            fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            index += 1
            continue
        except OSError:
            return OffloadResult(False, full_text, None, index, 0, "write_failed")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
        except OSError:
            return OffloadResult(False, full_text, None, index, 0, "write_failed")
        path = candidate.resolve()
        break
    if path is None:
        return OffloadResult(False, full_text, None, index, 0, "claim_exhausted")

    head_lines, tail_lines = _preview_lines(resolved)
    text = _pointer_text(full_text, path, head_lines, tail_lines, _preview_byte_cap(resolved))
    return OffloadResult(True, text, str(path), index, len(payload), "offloaded")


def sweep_expired(
    max_age_seconds: float,
    *,
    now: float,
    env: Mapping[str, str] | None = None,
) -> tuple[int, float]:
    """Remove expired ``tool-output`` offload directories under state/firings.

    Returns ``(dirs_removed, mb_freed)``. Offload only OWNS the ``tool-output``
    subdirectory of each firing dir, so only that subtree is removed; the parent
    ``state/firings/<id>`` directory is rmdir'd afterwards ONLY when it is empty,
    so any sibling firing-scoped state another feature stores there survives this
    sweep untouched. Best-effort: any per-entry IO error is swallowed so the
    cleanup pass never crashes on a partially-written tree. This is the hook the
    existing firing cleanup (``bin/agent-cleanup.py``) calls.
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
        offload_dir = firing_dir / "tool-output"
        if not offload_dir.is_dir():
            continue
        try:
            age = now - offload_dir.stat().st_mtime
        except OSError:
            continue
        if age <= max_age_seconds:
            continue
        freed_bytes += _dir_total_bytes(offload_dir)
        shutil.rmtree(offload_dir, ignore_errors=True)
        removed += 1
        # Reap the firing dir only when nothing else lives in it.
        with contextlib.suppress(OSError):
            firing_dir.rmdir()
    return removed, freed_bytes / (1024 * 1024)
