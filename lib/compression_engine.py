#!/usr/bin/env python3
"""Compression-engine selector for the PostToolUse tool-output compactor.

Alfred ships two compression engines for verbose tool output:

* **builtin** - the pure-Python, stdlib-only #453 compactor in
  ``lib/tool_compactor.py``. It is the ZERO-INSTALL DEFAULT and the fallback:
  a fresh solo install needs nothing extra.
* **headroom** - the optional, more capable ``headroom-ai`` engine
  (Apache-2.0, wired in ``lib/headroom_engine.py``). Used only when it is both
  selected and actually available; otherwise the selector silently falls back
  to the built-in compactor.

The engine is chosen by ``ALFRED_COMPRESSION_ENGINE``:

* ``builtin`` (default) - always the #453 compactor.
* ``headroom`` - route through headroom when available, else the #453 compactor.
* ``off`` - no compaction at all (raw output passes through).

Crucially, the #453 **safety invariants are preserved through every engine**.
The headroom path runs behind the exact same confirmed-success valve
(:func:`tool_compactor.compaction_gate`): an errored, unknown-status, disabled,
untargeted, or too-small output is passed through untouched and is NEVER handed
to headroom, so an error can never be hidden regardless of engine.

Kept stdlib-only (headroom is imported dynamically inside ``headroom_engine``)
so it stays on the Claude Code hook path under any ``python3`` without the venv.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

import headroom_engine
import tool_compactor
from tool_compactor import CompactionResult

__all__ = [
    "compact_output_via_engine",
    "selected_engine",
]

_VALID_ENGINES = ("builtin", "headroom", "off")


def _resolve(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def selected_engine(env: Mapping[str, str] | None = None) -> str:
    """The configured engine name, defaulting to ``builtin``.

    An unset or unrecognized value falls back to ``builtin`` so a typo can never
    silently disable compaction or route through an engine that is not there.
    """
    raw = (_resolve(env).get("ALFRED_COMPRESSION_ENGINE") or "").strip().lower()
    return raw if raw in _VALID_ENGINES else "builtin"


def _builtin(
    text: str,
    tool_name: str,
    exit_code: int | None,
    env: Mapping[str, str],
) -> CompactionResult:
    return tool_compactor.compact_output(text, tool_name=tool_name, exit_code=exit_code, env=env)


def compact_output_via_engine(
    text: str,
    *,
    tool_name: str = "Bash",
    exit_code: int | None = None,
    env: Mapping[str, str] | None = None,
) -> CompactionResult:
    """Compact one tool-result body through the configured engine.

    * ``off`` -> a passthrough (compaction disabled).
    * ``builtin`` (default) -> the #453 compactor, unchanged.
    * ``headroom`` -> the headroom engine when available AND the confirmed-success
      valve passes; otherwise the #453 compactor.

    The safety valve is enforced *before* headroom ever sees the text, so an
    errored or unknown-status output is teed through in full by every engine.
    """
    resolved = _resolve(env)
    text = text or ""
    engine = selected_engine(resolved)

    if engine == "off":
        original_bytes = len(text.encode("utf-8"))
        return tool_compactor.passthrough_result(text, original_bytes, "engine_off")

    if engine == "builtin":
        return _builtin(text, tool_name, exit_code, resolved)

    # engine == "headroom": optionally autofetch (opt-in, once), then require
    # real availability. Anything short of a working headroom falls back to the
    # zero-install built-in compactor.
    if not headroom_engine.headroom_available(resolved):
        headroom_engine.maybe_autofetch(resolved)
        if not headroom_engine.headroom_available(resolved):
            return _builtin(text, tool_name, exit_code, resolved)

    # Same safety valve as the built-in path: only a confirmed-success, targeted,
    # over-budget output is eligible. Everything else passes through untouched
    # and headroom is never invoked.
    reason, (_min, max_bytes, _head, _tail), original_bytes = tool_compactor.compaction_gate(
        text, tool_name=tool_name, exit_code=exit_code, env=resolved
    )
    if reason is not None:
        return tool_compactor.passthrough_result(text, original_bytes, reason)

    compressed = headroom_engine.compress(text, env=resolved, max_bytes=max_bytes)
    if compressed is None:
        # headroom declined (absent, errored, empty): fall back to the built-in
        # compactor so the token win is not lost. The valve already passed, so
        # this is a confirmed-success output that is safe to compact.
        return _builtin(text, tool_name, exit_code, resolved)

    final_bytes = len(compressed.encode("utf-8"))
    if final_bytes >= original_bytes:
        # headroom produced no saving on this payload; the deterministic
        # built-in compactor may still shrink it (ANSI, dupes, head+tail).
        return _builtin(text, tool_name, exit_code, resolved)

    omitted = max(0, len(text.split("\n")) - len(compressed.split("\n")))
    return CompactionResult(
        applied=True,
        text=compressed,
        original_bytes=original_bytes,
        final_bytes=final_bytes,
        omitted_lines=omitted,
        reason="compacted_headroom",
    )
