"""Optional run-priming that injects code skeletons for orientation files.

The skeleton MCP tool lets an agent PULL an outline on demand. This module is
the complementary PUSH: at prompt-assembly time it can prepend a compact
skeleton block for a caller-supplied set of orientation paths (dependencies,
neighbors, or a large reference file), so the agent starts with structure
instead of spending a full read to orient.

Two invariants keep it correct:

- It is OFF by default (``ALFRED_SKELETON_PRIMING``). A host that does not opt
  in gets a byte-for-byte unchanged prompt.
- It only ever renders the paths the CALLER passes as *orientation* paths. The
  firing's edit-target is never skeletonized here; that file is read and edited
  in full. The block also says, in words, that bodies are elided and the real
  file is one read away, so a skeleton can never be mistaken for edit content.

The block reuses ``code_graph``'s existing index for symbol anchors and reads
source under the firing's ``workdir``; it introduces no new store.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

__all__ = [
    "SKELETON_PRIMING_ENV",
    "skeleton_priming_block",
    "skeleton_priming_enabled",
]

SKELETON_PRIMING_ENV = "ALFRED_SKELETON_PRIMING"

_DEFAULT_MAX_FILES = 6
_FALSEY = {"0", "false", "no", "off", ""}

CONTEXT_HEADER = "## Orientation skeletons (structure only, bodies elided)"


def skeleton_priming_enabled(env: Mapping[str, str] | None = None) -> bool:
    """True only when the operator armed priming via ``ALFRED_SKELETON_PRIMING``.

    Default OFF: this is the outer guard so a host that never sets the knob is
    unaffected.
    """
    resolved = os.environ if env is None else env
    raw = resolved.get(SKELETON_PRIMING_ENV)
    return bool(raw and raw.strip().lower() not in _FALSEY)


def _env_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        return default
    return value if value > 0 else default


def skeleton_priming_block(
    repo: str,
    paths: Sequence[str],
    *,
    workdir: Path | str,
    code_map: dict[str, Any] | None = None,
    code_map_path: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """Render an orientation-skeleton block for ``paths``, or ``""``.

    Returns the empty string (the no-op signal) when priming is disabled, when
    ``repo``/``paths`` are empty, or when no path resolves to a skeleton. The
    caller must pass only orientation paths, never the firing's edit-target.
    """
    resolved_env = os.environ if env is None else env
    if not skeleton_priming_enabled(resolved_env):
        return ""
    if not repo or not paths:
        return ""

    # Imported lazily so a host without the code map on sys.path degrades to a
    # no-op rather than failing prompt assembly.
    try:
        from code_graph import skeleton_for_path
    except Exception:
        return ""

    max_files = _env_int(resolved_env, "ALFRED_SKELETON_MAX_FILES", _DEFAULT_MAX_FILES)
    max_signature_lines = _env_int(
        resolved_env,
        "ALFRED_SKELETON_MAX_SIGNATURE_LINES",
        6,
    )

    rendered: list[str] = []
    seen: set[str] = set()
    for path in paths:
        clean = str(path or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        payload: dict[str, Any] | None = None
        with contextlib.suppress(Exception):
            payload = skeleton_for_path(
                code_map,
                repo=repo,
                path=clean,
                repo_root=Path(workdir),
                code_map_path=code_map_path,
                max_signature_lines=max_signature_lines,
            )
        if not payload:
            continue
        skeleton = str(payload.get("skeleton") or "").strip()
        if not skeleton:
            continue
        rendered.append(skeleton)
        if len(rendered) >= max_files:
            break

    if not rendered:
        return ""

    lines = [
        CONTEXT_HEADER,
        "",
        "These are structural outlines of files near your task, not their full "
        "source. Each `[body: N line(s) elided]` marker hides an implementation "
        "you can read in full at any time. Use them to orient; read and edit the "
        "real file whenever you need its body or intend to change it.",
        "",
    ]
    lines.append("\n\n".join(rendered))
    return "\n".join(lines).rstrip() + "\n"
