#!/usr/bin/env python3
"""Deterministic tool-output compaction and PreToolUse command normalization.

The single biggest recurring token sink in an autonomous firing is verbose tool
output: thousand-line build logs, ``npm install`` chatter, all-green test runs,
progress spinners, ANSI colour codes. Every byte of it re-enters the model's
context and is paid for on every turn. This module intercepts that output at the
Claude Code tool-I/O boundary and compacts it *before* it reaches the model, and
normalizes a small allowlist of verbose commands into their quiet equivalents
*before* they run.

Two seams, both wired through ``lib/alfred_hooks.py`` (see that file's
``main()``):

* **PostToolUse output compactor** (:func:`compact_output`): collapses
  low-signal Bash output into ANSI-stripped, de-duplicated, budget-bounded
  head+tail form with an explicit omitted-N marker. Emitted back to Claude Code
  as ``hookSpecificOutput.updatedToolOutput``.
* **PreToolUse command normalizer** (:func:`normalize_command`): an allowlisted,
  conservative rewrite table that swaps a verbose command for a quiet/porcelain
  equivalent only when the two are semantically identical. Emitted as
  ``hookSpecificOutput.updatedInput``.

Critical safety valve (borrowed from rtk's tee-full-output-on-failure): if a
command **failed** (non-zero exit) or its output matches an error signature, the
compactor passes the full output through untouched. Compaction must never hide a
traceback, a build error, or a test failure from the model. See
:func:`looks_like_failure`.

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
import shlex
from collections.abc import Mapping
from dataclasses import dataclass

__all__ = [
    "CompactionResult",
    "command_normalizer_enabled",
    "compact_output",
    "compact_text",
    "looks_like_failure",
    "normalize_command",
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
# Failure-count fragment inside a pytest tail ("3 failed", "1 error").
_TEST_FAIL_COUNT_RE = re.compile(r"\b([1-9]\d*)\s+(failed|error)s?\b", re.IGNORECASE)

# Error signatures that force a full-output tee even when no exit code is known.
# Intentionally conservative: these only DISABLE compaction (the safe
# direction), and they target genuine failures, not the word "error" appearing
# incidentally in a healthy log ("0 errors", "error handling").
_ERROR_SIGNATURE_RE = re.compile(
    r"Traceback \(most recent call last\)"
    r"|^\s*File \"[^\"]+\", line \d+"
    r"|\bpanic:"
    r"|Segmentation fault"
    r"|\bfatal:\s"
    r"|\bnpm ERR!"
    r"|command not found"
    r"|No such file or directory"
    r"|\bunhandled exception\b"
    r"|\bcore dumped\b",
    re.IGNORECASE | re.MULTILINE,
)

# Shell metacharacters that make a command a pipeline / compound / redirect.
# The normalizer refuses to rewrite any command containing one of these: a
# porcelain swap could change what a downstream ``grep``/``awk`` parses.
_SHELL_META_RE = re.compile(r"[|&;<>`$()]|\|\||&&|\n")


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


def command_normalizer_enabled(env: Mapping[str, str] | None = None) -> bool:
    """True unless an operator opts out with ``ALFRED_CMD_NORMALIZER=0``."""
    return _flag_enabled(_resolve(env), "ALFRED_CMD_NORMALIZER")


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
# Safety valve
# --------------------------------------------------------------------------
def _test_run_failed(text: str) -> bool:
    for match in _TEST_TAIL_RE.finditer(text or ""):
        if _TEST_FAIL_COUNT_RE.search(match.group("body")):
            return True
    return False


def looks_like_failure(text: str, *, exit_code: int | None = None) -> bool:
    """True when the output must NOT be compacted (the tee-on-failure valve).

    A command failed if its exit code is non-zero, or (when the exit code is
    unknown) its output carries an error signature or a failing test tail. In any
    of those cases the full output is preserved so a traceback or build error is
    never hidden from the model.
    """
    if exit_code is not None and exit_code != 0:
        return True
    body = text or ""
    if _ERROR_SIGNATURE_RE.search(body):
        return True
    return _test_run_failed(body)


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


def _all_pass_test_summary(text: str) -> str | None:
    """One-line summary for an all-green test run, else ``None``.

    Failing runs are handled by the tee valve, so this only fires when the tail
    reports zero failures/errors. It shrinks a thousand ``PASSED`` lines to the
    single counts line the model actually needs.
    """
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
    """Compact one tool-result body, honoring the tee-on-failure safety valve.

    Order of checks (each returns the original bytes unchanged when it fires):

    1. Disabled via ``ALFRED_OUTPUT_COMPACTOR=0``.
    2. Tool not in the compaction allowlist (default: Bash only).
    3. **Safety valve**: the command failed (non-zero exit or an error
       signature) so the full output is teed through untouched.
    4. Output already under ``ALFRED_OUTPUT_COMPACTOR_MIN_BYTES``.
    5. Cleaning + budgeting produced no byte saving.
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
    # Safety valve BEFORE any size gate: an error is always preserved in full.
    if looks_like_failure(text, exit_code=exit_code):
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


# --------------------------------------------------------------------------
# PreToolUse command normalization
# --------------------------------------------------------------------------
# Allowlist only. Each rule maps a verbose command to a quiet/porcelain
# equivalent that is SEMANTICALLY IDENTICAL - it changes only what is printed,
# never what the command does, and never drops signal the agent needs. A rule
# receives the shlex tokens and returns the rewritten token list or ``None``.
def _rule_git_status(toks: list[str]) -> tuple[list[str], str] | None:
    # `git status` -> `git status --short --branch`: the porcelain-stable short
    # format. `--branch` is chosen over a bare `--porcelain` so the branch and
    # ahead/behind line is preserved; only the verbose per-file prose is dropped.
    if toks == ["git", "status"]:
        return (["git", "status", "--short", "--branch"], "git status -> short+branch porcelain")
    return None


def _rule_git_quiet(toks: list[str]) -> tuple[list[str], str] | None:
    # `git pull|fetch|clone` -> add `--quiet`: suppresses progress chatter only.
    # Errors, conflicts and the summary all still print.
    if len(toks) >= 2 and toks[0] == "git" and toks[1] in {"pull", "fetch", "clone"}:
        rest = toks[2:]
        noisy = {"-v", "--verbose", "--progress", "-q", "--quiet"}
        if any(flag in rest for flag in noisy):
            return None
        return ([toks[0], toks[1], "--quiet", *rest], f"git {toks[1]} -> --quiet")
    return None


_RULES = (_rule_git_status, _rule_git_quiet)


def normalize_command(
    command: str, *, env: Mapping[str, str] | None = None
) -> tuple[str, bool, str]:
    """Rewrite an allowlisted verbose command to its quiet equivalent.

    Returns ``(command, changed, note)``. Conservative by construction:

    * Disabled via ``ALFRED_CMD_NORMALIZER=0`` -> unchanged.
    * Any shell metacharacter (pipe, redirect, ``&&``, ``$(...)``) -> unchanged:
      a porcelain swap could break a downstream parser.
    * Only the exact allowlisted base commands are ever touched; everything else
      passes through verbatim.
    """
    resolved = _resolve(env)
    if not command_normalizer_enabled(resolved):
        return command, False, "disabled"
    if not command or not command.strip():
        return command, False, "empty"
    if _SHELL_META_RE.search(command):
        return command, False, "compound"
    try:
        toks = shlex.split(command)
    except ValueError:
        return command, False, "unparseable"
    if not toks:
        return command, False, "empty"
    for rule in _RULES:
        result = rule(toks)
        if result is not None:
            new_toks, note = result
            return shlex.join(new_toks), True, note
    return command, False, "no_rule"
