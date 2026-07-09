#!/usr/bin/env python3
"""Deterministic tool-output compaction before it enters the model's context.

The single biggest recurring token sink in an autonomous firing is verbose tool
output: thousand-line build logs, ``npm install`` chatter, all-green test runs,
progress spinners, ANSI colour codes. Every byte of it re-enters the model's
context and is paid for on every turn. This module intercepts that output at the
Claude Code tool-I/O boundary and compacts it *before* it reaches the model.

It is wired through ``lib/alfred_hooks.py`` (see that file's ``main()``) as a
**PostToolUse output compactor** (:func:`compact_output`): it collapses
low-signal Bash output into ANSI-stripped, de-duplicated, budget-bounded
head+tail form with an explicit omitted-N marker, and emits it back to Claude
Code as ``hookSpecificOutput.updatedToolOutput``.

A PreToolUse command normalizer (a rewrite table that swapped verbose commands
for quiet equivalents) was intentionally dropped: no ``git`` command rewrite
proved reliably output-equivalent (``git status --short`` drops the submodule
summary; ``git pull``/``git fetch --quiet`` drop the merge and ref-update
summaries), so the battery keeps only the safe half - compacting output that has
already been produced, guarded by the confirmed-success valve below.

Critical safety valve (compaction requires PROOF OF SUCCESS): the compactor only
touches output whose structured exit code is exactly ``0``. A non-zero exit is
teed through untouched, and - crucially - so is an *unknown* status (a plain
string response, or a structured response with no exit code). Compaction is
gated on positive proof of success, not on the absence of a known error
signature, so an unrecognized error format can never be hidden. This inversion
replaces the earlier, unwinnable game of enumerating every error signature (a
traceback, ``fatal:``, ``npm ERR!``, ``make: *** No rule...``, ...).

Design rules (mirroring ``lib/agent_hooks.py`` and
``agent_runner/context_governor.py``):

* **Stdlib only.** This module sits on the Claude Code hook path, which runs
  under any ``python3`` without the project venv. It imports nothing outside the
  standard library.
* **Deterministic and byte-budget driven.** No LLM call, no summarization that
  invents facts. Head + tail + markers, exactly like the context governor.
* **Config-driven tunables** via env (12-factor): enable flags, byte/line
  budgets, per-tool targeting. Read at call time so an operator can override in
  production without a redeploy and tests can monkeypatch freely.
* **Fail conservative.** When anything is ambiguous the compactor returns the
  original bytes unchanged. The worst case is fewer tokens saved, never a hidden
  error.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

__all__ = [
    "CompactionResult",
    "compact_output",
    "compact_text",
    "output_compactor_enabled",
    "strip_ansi",
]

_FALSEY = {"0", "false", "no", "off"}

# ---- defaults (all overridable via env, see _compact_config) --------------
_DEFAULT_MIN_BYTES = 2_000  # below this, output passes through un-compacted
_DEFAULT_MAX_BYTES = 8_000  # target byte budget for a compacted result
_DEFAULT_HEAD_LINES = 40
_DEFAULT_TAIL_LINES = 40
_MIN_EDGE_LINES = 3
_DEFAULT_COMPACT_TOOLS = ("Bash",)

# ANSI CSI colour / cursor sequences and OSC (title) sequences.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")

# pytest-style result tail, e.g. "===== 3 failed, 12 passed in 4.21s ====="
_TEST_TAIL_RE = re.compile(
    r"^=+\s*(?P<body>[\d].*?(?:passed|failed|error|skipped|xfailed|xpassed).*?)\s*=+\s*$",
    re.MULTILINE,
)
# Failure-count fragment inside a pytest tail ("3 failed", "1 error"). Used only
# as a defensive guard when summarizing an already-confirmed-success test run.
_TEST_FAIL_COUNT_RE = re.compile(r"\b([1-9]\d*)\s+(failed|error)s?\b", re.IGNORECASE)

# Lines that a test runner itself emits, used to decide whether output is PURELY
# a test run (see _is_pure_test_run). Deliberately narrow: it must not match
# arbitrary program output (e.g. a `git fetch` summary), because a false positive
# would let the test-only summary discard real non-test output. A false negative
# is safe - it just falls back to the normal head+tail compaction.
_TEST_LINE_RE = re.compile(
    r"^(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b"  # short-summary node lines
    r"|^\S+::\S+\s+(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b"  # verbose node lines
    r"|^=+.*=+\s*$"  # ===-decorated headers / footers
    r"|^_+ .* _+\s*$"  # ___ failure-section separators
    r"|^\S+\.py[ .FsxEXP]*(\[\s*\d+%\])?\s*$"  # progress line: file + result chars
    r"|^[.FsxEXP]+\s*(\[\s*\d+%\])?\s*$"  # bare progress dots / result chars
    r"|^(platform |rootdir:|plugins:|collected |collecting|cachedir:"
    r"|configfile:|testpaths:|hypothesis profile|worker |gw\d+ )"  # pytest boilerplate
)


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
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


def output_compactor_enabled(env: Mapping[str, str] | None = None) -> bool:
    """True unless an operator opts out with ``ALFRED_OUTPUT_COMPACTOR=0``."""
    return _flag_enabled(_resolve(env), "ALFRED_OUTPUT_COMPACTOR")


def _compact_tools(env: Mapping[str, str]) -> frozenset[str]:
    raw = env.get("ALFRED_OUTPUT_COMPACTOR_TOOLS")
    if raw is None or not raw.strip():
        return frozenset(_DEFAULT_COMPACT_TOOLS)
    tools = [t.strip() for t in raw.split(",") if t.strip()]
    return frozenset(tools) if tools else frozenset(_DEFAULT_COMPACT_TOOLS)


def _compact_config(env: Mapping[str, str]) -> tuple[int, int, int, int]:
    min_bytes = max(0, _env_int(env, "ALFRED_OUTPUT_COMPACTOR_MIN_BYTES", _DEFAULT_MIN_BYTES))
    max_bytes = max(256, _env_int(env, "ALFRED_OUTPUT_COMPACTOR_MAX_BYTES", _DEFAULT_MAX_BYTES))
    head_lines = max(
        _MIN_EDGE_LINES, _env_int(env, "ALFRED_OUTPUT_COMPACTOR_HEAD_LINES", _DEFAULT_HEAD_LINES)
    )
    tail_lines = max(
        _MIN_EDGE_LINES, _env_int(env, "ALFRED_OUTPUT_COMPACTOR_TAIL_LINES", _DEFAULT_TAIL_LINES)
    )
    return min_bytes, max_bytes, head_lines, tail_lines


# --------------------------------------------------------------------------
# Cleaning primitives
# --------------------------------------------------------------------------
def strip_ansi(text: str) -> str:
    """Remove ANSI colour / cursor / title escape sequences."""
    return _ANSI_RE.sub("", text or "")


def _collapse_carriage_returns(text: str) -> str:
    """Keep only the final state of each ``\\r``-overwritten progress line.

    A progress bar emits ``10%\\r20%\\r...\\r100%\\n`` on one physical line; only
    the last segment is meaningful once the line is done.
    """
    out: list[str] = []
    for line in (text or "").split("\n"):
        if "\r" in line:
            line = line.split("\r")[-1]
        out.append(line)
    return "\n".join(out)


def _dedupe_consecutive(lines: list[str]) -> list[str]:
    """Collapse runs of identical adjacent lines into ``line  (xN)``."""
    out: list[str] = []
    prev: str | None = None
    count = 0
    for line in lines:
        if line == prev:
            count += 1
            continue
        if prev is not None:
            out.append(prev if count == 1 else f"{prev}  (x{count})")
        prev = line
        count = 1
    if prev is not None:
        out.append(prev if count == 1 else f"{prev}  (x{count})")
    return out


# --------------------------------------------------------------------------
# Compaction
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class CompactionResult:
    """Outcome of one compaction pass."""

    applied: bool
    text: str
    original_bytes: int
    final_bytes: int
    omitted_lines: int
    reason: str

    def as_raw(self) -> dict[str, int | str | bool]:
        return {
            "applied": self.applied,
            "original_bytes": self.original_bytes,
            "final_bytes": self.final_bytes,
            "omitted_lines": self.omitted_lines,
            "reason": self.reason,
        }


def _marker(omitted_lines: int, max_bytes: int) -> str:
    return (
        f"\n[ALFRED_OUTPUT_COMPACTOR omitted_lines={omitted_lines} max_bytes={max_bytes}]\n"
        "Middle output omitted to keep this firing inside the local tool-output budget. "
        "Re-run the command for the full log if you need the omitted lines.\n"
        "[/ALFRED_OUTPUT_COMPACTOR]\n"
    )


def _is_pure_test_run(text: str) -> bool:
    """True when EVERY non-blank line is test-runner output ending in a tail.

    This gate keeps the aggressive test-only summary from firing on a *mixed*
    success, e.g. ``git fetch && pytest`` where useful non-test output precedes
    the all-green footer. If any non-blank line is not recognizable test-runner
    output, the caller falls back to the normal head+tail compaction, which
    preserves that non-test content instead of discarding it.
    """
    if not _TEST_TAIL_RE.search(text):
        return False
    return all(_TEST_LINE_RE.match(line) for line in text.split("\n") if line.strip())


def _all_pass_test_summary(text: str) -> str | None:
    """One-line summary for a PURE, all-green test run, else ``None``.

    Only fires when (a) the output is purely a test run (:func:`_is_pure_test_run`,
    so no non-test content is discarded) and (b) the tail reports zero
    failures/errors. Failing runs never reach here - they are teed by the valve.
    It shrinks a thousand ``PASSED`` lines to the single counts line the model
    actually needs.
    """
    if not _is_pure_test_run(text):
        return None
    tail: str | None = None
    for match in _TEST_TAIL_RE.finditer(text):
        tail = match.group("body").strip()
    if tail is None or _TEST_FAIL_COUNT_RE.search(tail):
        return None
    return f"Test run (all passed): {tail}"


def compact_text(text: str, *, max_bytes: int, head_lines: int, tail_lines: int) -> tuple[str, int]:
    """Head + tail excerpt within a line and byte budget.

    Returns ``(compacted, omitted_lines)``. Keeps the first ``head_lines`` and
    last ``tail_lines`` around an omitted-N marker, then trims further from the
    tail if the byte budget is still exceeded.
    """
    lines = text.split("\n")
    if len(lines) <= head_lines + tail_lines:
        return text, 0
    head = lines[:head_lines]
    tail = lines[-tail_lines:]
    omitted = len(lines) - head_lines - tail_lines
    compacted = "\n".join(head) + _marker(omitted, max_bytes) + "\n".join(tail)

    # Enforce the byte budget: drop tail lines (then head lines) until we fit.
    while (
        len(compacted.encode("utf-8")) > max_bytes and (len(head) + len(tail)) > _MIN_EDGE_LINES * 2
    ):
        if len(tail) > _MIN_EDGE_LINES:
            tail = tail[1:]
        elif len(head) > _MIN_EDGE_LINES:
            head = head[:-1]
        else:
            break
        omitted = len(lines) - len(head) - len(tail)
        compacted = "\n".join(head) + _marker(omitted, max_bytes) + "\n".join(tail)
    return compacted, omitted


def compact_output(
    text: str,
    *,
    tool_name: str = "Bash",
    exit_code: int | None = None,
    env: Mapping[str, str] | None = None,
) -> CompactionResult:
    """Compact one tool-result body, but ONLY on proof of success.

    ``exit_code`` is the structured exit status of the command (``None`` when the
    tool response carried no exit code, e.g. a plain-string response). The
    inverted safety valve compacts only when success is positively confirmed:

    1. Disabled via ``ALFRED_OUTPUT_COMPACTOR=0``.
    2. Tool not in the compaction allowlist (default: Bash only).
    3. **Unknown status** (``exit_code is None``): pass the full output through.
       Compaction requires proof of success, and there is none, so an
       unrecognized error format is never hidden.
    4. **Confirmed failure** (``exit_code != 0``): tee the full output through.
    5. Confirmed success (``exit_code == 0``) below: output already under
       ``ALFRED_OUTPUT_COMPACTOR_MIN_BYTES``, or cleaning + budgeting produced no
       byte saving, both pass through unchanged; otherwise the compacted form is
       returned.
    """
    resolved = _resolve(env)
    text = text or ""
    original_bytes = len(text.encode("utf-8"))

    def _passthrough(reason: str) -> CompactionResult:
        return CompactionResult(
            applied=False,
            text=text,
            original_bytes=original_bytes,
            final_bytes=original_bytes,
            omitted_lines=0,
            reason=reason,
        )

    if not output_compactor_enabled(resolved):
        return _passthrough("disabled")
    if tool_name not in _compact_tools(resolved):
        return _passthrough("tool_not_targeted")
    # Inverted safety valve BEFORE any size gate: compaction requires PROOF of
    # success. Unknown status and any non-zero exit both preserve full output.
    if exit_code is None:
        return _passthrough("unknown_status")
    if exit_code != 0:
        return _passthrough("teed_on_failure")

    min_bytes, max_bytes, head_lines, tail_lines = _compact_config(resolved)
    if original_bytes < min_bytes:
        return _passthrough("within_budget")

    # Clean noise, then compact the signal.
    cleaned = _collapse_carriage_returns(strip_ansi(text))
    cleaned = "\n".join(_dedupe_consecutive(cleaned.split("\n")))

    summary = _all_pass_test_summary(cleaned)
    if summary is not None:
        final = summary
        omitted = max(0, len(text.split("\n")) - 1)
    elif len(cleaned.encode("utf-8")) > max_bytes:
        final, omitted = compact_text(
            cleaned, max_bytes=max_bytes, head_lines=head_lines, tail_lines=tail_lines
        )
    else:
        final, omitted = cleaned, 0

    final_bytes = len(final.encode("utf-8"))
    if final_bytes >= original_bytes:
        # Cleaning did not help (no ANSI, no dupes, already tight) - keep raw.
        return _passthrough("no_gain")

    return CompactionResult(
        applied=True,
        text=final,
        original_bytes=original_bytes,
        final_bytes=final_bytes,
        omitted_lines=omitted,
        reason="compacted",
    )
