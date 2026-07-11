"""Subprocess wrappers and LLM CLI invocations.

This module owns the boundary between Python and the shell:

* :func:`run`: ``subprocess.run`` with sane defaults and no exceptions
  on timeout (returns a ``CompletedProcess`` with ``returncode=124``).
* :func:`gh_json`: call ``gh`` with ``--json`` and parse to ``dict``
  / ``list``; return a default on any failure.
* :func:`pid_start_key`: read ``ps -p ... lstart`` for lock-identity.
* :func:`short`: display-trim long output for logs.
* :func:`claude_invoke` and :func:`claude_invoke_streaming`:
  invoke the Claude Code CLI and parse its sentinel response.
* :func:`codex_invoke`: invoke the Codex CLI non-interactively and
  marshal its artefacts.
* :func:`invoke_agent_engine`: engine-aware dispatch for
  Claude / Codex / Claude-first hybrid with fallback.

What this module does NOT own:

* The ``ClaudeResult`` dataclass and envelope classification ->
  ``result.py``.
* Spend tracking or fleet ledgers -> ``state.py``.
* gh CLI helpers for PR / issue / label operations -> ``github.py``.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import shutil
import signal
import subprocess
import tempfile
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import memory_ranking
from .config import (
    _truthy_env,
    dry_run_log,
    env_int,
    is_dry_run,
    normalize_engine,
)
from .context_governor import govern_prompt_context
from .memory_runtime import (
    BEGIN_MARKER,
    load_runtime_memory,
    parse_memory_reflections,
    record_firing,
    record_reflections,
    strip_memory_reflections,
    with_memory_prompt,
)
from .paths import (
    CLAUDE_BIN,
    CODEX_APPROVAL_POLICY,
    CODEX_BIN,
    CODEX_DEFAULT_MODEL,
    CODEX_DEFAULT_SANDBOX,
)
from .reliability import (
    CircuitBreaker,
    FailureClass,
    LoopDetector,
    classify_result,
    retry_after_seconds,
    retry_with_backoff,
)
from .result import (
    _BUDGET_RESULT_RE,
    _RATE_LIMIT_RESULT_RE,
    ClaudeResult,
    _build_claude_result,
    _should_retry_claude_auth,
    dry_run_claude_result,
    looks_quota_exhausted,
    parse_quota_resume_at,
)
from .rubric import GraderVerdict
from .rubric import grade as grade_transcript
from .skills_context import skills_context_for_role
from .transcripts import (
    _extract_codex_session_id,
    _extract_codex_tokens,
    codex_artifact_paths,
    transcript_path,
)

# Claude Code's ``-p`` (non-interactive) mode applies a hidden 40-turn
# default when ``--max-turns`` is omitted. That default is far too tight
# for our agents (cross-file work routinely needs 60-150 turns), so
# ``claude_invoke`` always passes an explicit ``--max-turns``: the
# caller's value if given, otherwise this effectively-unlimited number.
# The per-firing wall-clock ``timeout`` becomes the real ceiling.
_CLAUDE_UNLIMITED_TURNS: int = 999

# Headless fleet agents run unattended under launchd, so a Claude Code
# desktop/push notification on every firing is pure noise (and on macOS it
# stacks up banners no one reads). We pass these settings via the CLI's
# ``--settings`` flag, which ADDS a settings source on top of the
# config-dir settings. It does NOT replace auth. Auth comes from the
# config-dir credentials (OAuth / keychain / CLAUDE_CODE_OAUTH_TOKEN), none
# of which live in a settings.json, so suppressing notifications here can
# never log the agent out. Opt back in (e.g. for interactive debugging)
# with ``ALFRED_AGENT_NOTIFICATIONS=1``.
_AGENT_NOTIF_SUPPRESS_SETTINGS = '{"agentPushNotifEnabled":false,"preferredNotifChannel":"none"}'


def _is_falsy_env(name: str) -> bool:
    """True when ``name`` is explicitly set to a falsy value (0/false/no/off).

    Used for default-ON features that an operator can opt OUT of: an unset
    env var returns ``False`` here so the feature stays enabled.
    """
    val = os.environ.get(name)
    return val is not None and val.strip().lower() in {"0", "false", "no", "off"}


def _agent_notifications_enabled() -> bool:
    """True only when the operator explicitly re-enables agent notifications.

    Default is suppressed (the flag is added). Setting
    ``ALFRED_AGENT_NOTIFICATIONS=1`` (or true/yes/on) keeps notifications
    on by omitting the ``--settings`` suppression source.
    """
    return _truthy_env("ALFRED_AGENT_NOTIFICATIONS")


# Headless firings also run under ``--permission-mode bypassPermissions`` (full
# trust), so a deterministic PreToolUse hook is the only backstop that survives
# prompt drift. ``lib/alfred_hooks.py`` denies pushes to protected branches,
# destructive ``rm -rf`` outside the worktree, secret-file reads, ``curl|bash``
# pipelines, and (when ``ALFRED_SCRUB_NAMES`` is configured) writes of banned
# names. It is merged into the same ``--settings`` payload as the notification
# suppression. On by default; disable for a manual debug run with
# ``ALFRED_AGENT_HOOKS=0``.
def _agent_hooks_enabled() -> bool:
    """PreToolUse guardrails are OPT-IN; unrestricted ("YOLO") is the default.

    Alfred's value is unattended autonomy, so we do NOT impose guardrails by
    default. The hook is an optional deterministic backstop for anyone who wants
    one on a bypassPermissions fleet (e.g. a cautious first run on an unfamiliar
    repo). Turn it on with ``ALFRED_AGENT_HOOKS=1`` (true/yes/on).
    """
    return _truthy_env("ALFRED_AGENT_HOOKS")


def _agent_hook_settings() -> dict:
    """PreToolUse guardrail + tool-compactor hook config, or ``{}`` when off.

    The same opt-in ``ALFRED_AGENT_HOOKS`` flag wires two hooks at
    ``lib/alfred_hooks.py``:

    * ``PreToolUse`` - the deterministic guardrails.
    * ``PostToolUse`` - the tool-output compactor that shrinks noisy Bash logs
      before they enter context, teeing full output through on failure
      (``ALFRED_OUTPUT_COMPACTOR``).
    """
    if not _agent_hooks_enabled():
        return {}
    # process.py lives at lib/agent_runner/process.py; the hook is lib/alfred_hooks.py.
    script = Path(__file__).resolve().parent.parent / "alfred_hooks.py"
    if not script.exists():
        return {}
    pre = f'python3 "{script}" pretooluse'
    post = f'python3 "{script}" posttooluse'
    return {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash|Read|Write|Edit|MultiEdit|NotebookEdit",
                    "hooks": [{"type": "command", "command": pre}],
                }
            ],
            # Only Bash produces the noisy, high-volume output worth compacting;
            # the compactor itself further filters by ALFRED_OUTPUT_COMPACTOR_TOOLS.
            "PostToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": post}],
                }
            ],
        }
    }


def _agent_settings_args() -> list[str]:
    """Single ``--settings`` payload: notification suppression + the hook.

    Returns ``[]`` only when both are opted out, keeping the command line clean.
    """
    settings: dict = {}
    if not _agent_notifications_enabled():
        settings.update({"agentPushNotifEnabled": False, "preferredNotifChannel": "none"})
    settings.update(_agent_hook_settings())
    if not settings:
        return []
    return ["--settings", json.dumps(settings, separators=(",", ":"))]


# ---------- Memory MCP attachment ----------
#
# bin/alfred-mcp.py is a stdio MCP server exposing read-only memory tools
# (recall, recent file touches, failure patterns, brain status) over the local
# brain. Attaching it to every firing lets agents recall prior lessons as a
# TOOL (the model decides when) instead of memory being a passive store the
# operator queries by hand. This is a capability, not a restriction, so it is
# on by default; disable with ALFRED_MEMORY_MCP=0.
MEMORY_MCP_SERVER = "alfred_memory"
_MEMORY_RECALL_TOOLS = (
    "alfred_memory_recall",
    "alfred_memory_candidates",
    "alfred_recent_file_touches",
    "alfred_failure_patterns",
    "alfred_brain_status",
    "alfred_who_owns",
    "alfred_recent_changes_near",
    "alfred_prs_touching",
    "alfred_code_graph_summary",
    "alfred_code_impact",
    "alfred_code_blast_radius",
    "alfred_code_skeleton",
    "alfred_read_delta",
)


def _memory_mcp_enabled() -> bool:
    val = os.environ.get("ALFRED_MEMORY_MCP")
    if val is None:
        return True
    return val.strip().lower() not in {"0", "false", "no", "off", ""}


def _memory_mcp_script() -> Path | None:
    # process.py is lib/agent_runner/process.py; the server is bin/alfred-mcp.py.
    script = Path(__file__).resolve().parents[2] / "bin" / "alfred-mcp.py"
    return script if script.exists() else None


class _Unresolved:
    """Sentinel: the caller did not pre-resolve the MCP script path."""


# Distinguishes "caller passed nothing" (resolve here) from "caller passed the
# already-resolved value, which may legitimately be None" (use it as-is). Without
# this, a caller that resolved the path to None would make each helper re-resolve
# independently, reopening the TOCTOU window the shared path is meant to close.
_UNRESOLVED = _Unresolved()


def _memory_mcp_server(script: Path | None | _Unresolved = _UNRESOLVED) -> dict[str, Any] | None:
    """Return the ``mcpServers`` entry for the memory server, or ``None``.

    Split out from the args builder so memory and code-memory can share one
    ``--mcp-config`` flag (a single ``mcpServers`` map). A resolved ``None`` is
    honored as-is; only the ``_UNRESOLVED`` sentinel triggers a fresh lookup.
    """
    if not _memory_mcp_enabled():
        return None
    resolved = _memory_mcp_script() if isinstance(script, _Unresolved) else script
    if resolved is None:
        return None
    return {MEMORY_MCP_SERVER: {"command": "python3", "args": [str(resolved), "serve"]}}


def _memory_mcp_args(
    script: Path | None | _Unresolved = _UNRESOLVED, workdir: Path | None = None
) -> list[str]:
    """``--mcp-config`` args attaching the read-only memory + code-memory
    servers, or ``[]``.

    The memory server exposes only read-only tools (no arbitrary-query escape
    hatch), so no per-tool restriction is needed even under bypassPermissions.
    The code-memory server (``codebase-memory-mcp``, an external MIT binary) is
    likewise read-only: it answers code-structure queries (search, call graph,
    blast radius, who-owns) and never mutates the repo.

    ``script`` lets the caller resolve ``_memory_mcp_script()`` once per invoke
    and share it with ``_with_memory_mcp_tools`` so the allowlist augmentation
    and the ``--mcp-config`` attachment can never disagree (no TOCTOU between two
    separate ``Path.exists()`` checks). A resolved ``None`` is honored as-is;
    only the ``_UNRESOLVED`` sentinel triggers a fresh lookup here.
    """
    servers: dict[str, Any] = {}
    memory = _memory_mcp_server(script)
    if memory:
        servers.update(memory)
    code = _active_code_graph_server(workdir)
    if code:
        servers.update(code)
    if not servers:
        return []
    return ["--mcp-config", json.dumps({"mcpServers": servers}, separators=(",", ":"))]


def _memory_tool_names() -> list[str]:
    return [f"mcp__{MEMORY_MCP_SERVER}__{t}" for t in _MEMORY_RECALL_TOOLS]


def _with_memory_mcp_tools(
    allowed_tools: str,
    script: Path | None | _Unresolved = _UNRESOLVED,
    workdir: Path | None = None,
) -> str:
    """Append the read-only memory recall tools to an allowlist when enabled.

    Preserves the caller's separator style (comma vs space). No-op when the MCP
    is disabled or the server script is missing. ``script`` shares one resolved
    ``_memory_mcp_script()`` with ``_memory_mcp_args`` (see its docstring); a
    resolved ``None`` is honored, only ``_UNRESOLVED`` triggers a fresh lookup.
    """
    base = (allowed_tools or "").strip()
    wanted: list[str] = []
    if _memory_mcp_enabled():
        resolved = _memory_mcp_script() if isinstance(script, _Unresolved) else script
        if resolved is not None:
            wanted.extend(_memory_tool_names())
    wanted.extend(_active_code_graph_tool_names(workdir))
    if not wanted:
        return base
    existing = set(base.replace(",", " ").split())
    additions = [n for n in wanted if n not in existing]
    if not additions:
        return base
    sep = "," if ("," in base or " " not in base) else " "
    return (base + sep if base else "") + sep.join(additions)


# ---------- Code-memory MCP attachment ----------
#
# codebase-memory-mcp (DeusData, MIT) is a STANDALONE external binary invoked
# over MCP -- it is never vendored into this tree, so the repo stays OSS-clean
# and passes scrub-check. It indexes the in-scope repos into a code graph and
# exposes read-only structure tools (search, call graph, impact / blast radius,
# who-owns) so fleet agents can reason about code structure instead of grepping
# blind. This is a capability, on by default when the binary is installed;
# disable with ALFRED_CODE_MEMORY_MCP=0. The bin/code-memory-mcp launcher
# resolves and (on first run) fetches the pinned upstream binary.
CODE_MEMORY_MCP_SERVER = "code_memory"
# Tools the upstream server exposes. Kept as an allowlist so a future upstream
# tool cannot silently widen agent capability without a code change here.
_CODE_MEMORY_TOOLS = (
    "search_code",
    "call_graph",
    "impact_analysis",
    "who_owns",
)


def _code_memory_mcp_enabled() -> bool:
    val = os.environ.get("ALFRED_CODE_MEMORY_MCP")
    if val is None:
        return True
    return val.strip().lower() not in {"0", "false", "no", "off", ""}


def _code_memory_launcher() -> Path | None:
    """Return the bin/code-memory-mcp launcher path, or ``None`` if absent."""
    script = Path(__file__).resolve().parents[2] / "bin" / "code-memory-mcp"
    return script if script.exists() else None


def _code_memory_mcp_server(*, explicit_fallback: bool = False) -> dict[str, Any] | None:
    """Return the ``mcpServers`` entry for the code-memory server, or ``None``.

    ``None`` when disabled by env or when the launcher is missing (e.g. a lib
    checkout without bin/, or an install that opted out of the binary). The
    launcher itself decides whether the underlying binary is present and exits
    cleanly if not, so attaching it is always safe.
    """
    if not explicit_fallback and not _code_memory_mcp_enabled():
        return None
    launcher = _code_memory_launcher()
    if launcher is None:
        return None
    return {CODE_MEMORY_MCP_SERVER: {"command": str(launcher), "args": ["serve"]}}


def _code_memory_tool_names() -> list[str]:
    return [f"mcp__{CODE_MEMORY_MCP_SERVER}__{t}" for t in _CODE_MEMORY_TOOLS]


# ---------- Graphify MCP attachment ----------
#
# graphify (graphifyy, MIT) is an OPT-IN alternative code-graph engine: a
# pinned Python package invoked over MCP via its ``graphify-mcp`` entrypoint. It
# serves a per-repo ``graphify-out/graph.json``
# read-only, exposing graph-query tools (query, neighbours, shortest path,
# stats, community). Off by default; turn on with ALFRED_GRAPHIFY_MCP=1. It is
# mutually exclusive with code-memory: when graphify is on it takes the
# code-graph slot and code-memory is not attached, so a firing never runs two
# code-graph servers at once.
GRAPHIFY_MCP_SERVER = "graphify"
# The read-only tools graphify's server exposes. Kept as an explicit allowlist
# so an upstream addition cannot silently widen agent capability.
_GRAPHIFY_TOOLS = (
    "query_graph",
    "get_node",
    "get_neighbors",
    "get_community",
    "god_nodes",
    "graph_stats",
    "shortest_path",
    "list_prs",
    "get_pr_impact",
    "triage_prs",
)


def _graphify_mcp_enabled() -> bool:
    """Off unless ALFRED_GRAPHIFY_MCP is explicitly truthy (opt-in)."""
    val = os.environ.get("ALFRED_GRAPHIFY_MCP")
    if val is None:
        return False
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _graphify_code_memory_fallback_enabled() -> bool:
    """Whether Graphify explicitly selected code-memory for unindexed repos."""
    return os.environ.get("ALFRED_GRAPHIFY_FALLBACK", "").strip().lower() == "code-memory"


def _graphify_command() -> tuple[str, list[str]] | None:
    """Resolve a supported graphify MCP entrypoint and its bootstrap arguments."""
    override = os.environ.get("ALFRED_GRAPHIFY_BIN", "").strip()
    if override:
        expanded = str(Path(override).expanduser())
        if shutil.which(override) or Path(expanded).is_file():
            return expanded, []
        return None
    installed = shutil.which("graphify-mcp")
    if installed and _graphify_entrypoint_works(installed):
        return installed, []
    return None


def _graphify_entrypoint_works(command: str) -> bool:
    """Start stdio against an empty graph to verify the optional MCP runtime."""
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8") as graph:
            graph.write('{"nodes": [], "links": []}')
            graph.flush()
            return (
                subprocess.run(
                    [command, graph.name, "--transport", "stdio"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    check=False,
                ).returncode
                == 0
            )
    except (OSError, subprocess.TimeoutExpired):
        return False


def _graphify_mcp_server(workdir: Path | None = None) -> dict[str, Any] | None:
    """Return the ``mcpServers`` entry for graphify, or ``None`` when disabled
    or the command is not on PATH.

    Uses the cwd-relative default graph (``graphify-out/graph.json``), so a
    firing running in a repo worktree serves that repo's own graph. If no graph
    has been built the server simply exposes no useful nodes; attaching is safe.
    """
    if not _graphify_mcp_enabled():
        return None
    invocation = _graphify_command()
    if invocation is None:
        return None
    cmd, prefix = invocation
    graph = os.environ.get("ALFRED_GRAPHIFY_GRAPH", "").strip() or "graphify-out/graph.json"
    graph_path = Path(graph).expanduser()
    resolved_graph = (
        graph_path if graph_path.is_absolute() else (workdir / graph_path if workdir else None)
    )
    if resolved_graph is not None and not resolved_graph.is_file():
        return None
    graph_arg = str(resolved_graph) if resolved_graph is not None else str(graph_path)
    return {
        GRAPHIFY_MCP_SERVER: {
            "command": cmd,
            "args": [*prefix, graph_arg, "--transport", "stdio"],
        }
    }


def _graphify_tool_names() -> list[str]:
    return [f"mcp__{GRAPHIFY_MCP_SERVER}__{t}" for t in _GRAPHIFY_TOOLS]


def _active_code_graph_server(workdir: Path | None = None) -> dict[str, Any] | None:
    """The single code-graph MCP server to attach, honouring exclusivity.

    graphify wins when enabled (explicit opt-in); otherwise code-memory (on by
    default when its binary is present). Never returns both.
    """
    if _graphify_mcp_enabled():
        graphify = _graphify_mcp_server(workdir)
        if graphify is not None:
            return graphify
        # Manual env users retain the normal code-memory gate. The Graphify
        # battery records an explicit fallback so unindexed repos keep one
        # structural engine without pretending both servers are active.
        return _code_memory_mcp_server(explicit_fallback=_graphify_code_memory_fallback_enabled())
    return _code_memory_mcp_server()


def _active_code_graph_tool_names(workdir: Path | None = None) -> list[str]:
    server = _active_code_graph_server(workdir)
    if server is None:
        return []
    if GRAPHIFY_MCP_SERVER in server:
        return _graphify_tool_names()
    if CODE_MEMORY_MCP_SERVER in server:
        return _code_memory_tool_names()
    return []


def _subprocess_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _terminate_process_group(proc: subprocess.Popen[str]) -> None:
    """Terminate ``proc`` and its child process group after a timeout."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            proc.terminate()
        else:
            os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired, OSError):
        with contextlib.suppress(ProcessLookupError, OSError):
            if os.name == "nt":
                proc.kill()
            else:
                os.killpg(proc.pid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired, OSError):
            proc.wait(timeout=5)


def _popen_run_text(
    cmd: list[str],
    *,
    cwd: str | None = None,
    timeout: int = 60,
    capture: bool = True,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess:
    """Run a subprocess in its own process group and reap it on timeout."""
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdin=subprocess.PIPE if input_text is not None else None,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
        env=env,
        start_new_session=(os.name != "nt"),
    )
    try:
        stdout, stderr = proc.communicate(input=input_text, timeout=timeout)
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout or "", stderr or "")
    except subprocess.TimeoutExpired as exc:
        stdout = _subprocess_text(getattr(exc, "stdout", None) or getattr(exc, "output", None))
        stderr = _subprocess_text(getattr(exc, "stderr", None))
        _terminate_process_group(proc)
        with contextlib.suppress(subprocess.TimeoutExpired, OSError, ValueError):
            more_out, more_err = proc.communicate(timeout=1)
            stdout += _subprocess_text(more_out)
            stderr += _subprocess_text(more_err)
        timeout_msg = f"TIMEOUT after {timeout}s"
        stderr = f"{stderr}\n{timeout_msg}".strip() if stderr else timeout_msg
        return subprocess.CompletedProcess(cmd, 124, stdout=stdout, stderr=stderr)


def run(
    cmd: list[str],
    *,
    cwd: str | None = None,
    timeout: int = 60,
    check: bool = False,
    capture: bool = True,
    env: dict | None = None,
) -> subprocess.CompletedProcess:
    """Wrapped ``subprocess.run`` with sane defaults and clear errors.

    Args:
        cmd: argv list.
        cwd: working directory.
        timeout: wall-clock seconds before ``CompletedProcess(returncode=124)``.
        check: forwarded to ``subprocess.run``.
        capture: capture stdout/stderr as text.
        env: extra env vars merged on top of ``os.environ``.

    Returns:
        Always a ``subprocess.CompletedProcess``; timeouts and unknown
        exceptions are caught and surfaced via the return code instead
        of propagating.
    """
    proc_env = dict(os.environ)
    if env:
        proc_env.update(env)
    try:
        result = _popen_run_text(
            cmd,
            cwd=cwd,
            timeout=timeout,
            capture=capture,
            env=proc_env,
        )
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode,
                cmd,
                output=result.stdout,
                stderr=result.stderr,
            )
        return result
    except Exception as e:
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=f"{type(e).__name__}: {e}")


def gh_json(cmd: list[str], default: Any = None) -> Any:
    """Run ``gh`` and parse JSON output; return ``default`` on any failure."""
    res = run(cmd, timeout=60)
    if res.returncode != 0:
        return default
    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError:
        return default


def pid_start_key(pid: int) -> str:
    """Read ``ps -p <pid> -o lstart`` as the per-PID identity key.

    Used by lock-holder verification: a PID alone can be recycled, but
    ``lstart`` (start time) plus PID is unique on the host. Returns the
    empty string when ``ps`` is unavailable or the PID is gone.
    """
    try:
        res = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return ""
    return res.stdout.strip() if res.returncode == 0 else ""


def short(text: str, n: int = 300) -> str:
    """Trim ``text`` to at most ``n`` characters with an ellipsis suffix."""
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + "..."


# --------------------------------------------------------------------------
# Claude CLI invocation
# --------------------------------------------------------------------------


def claude_invoke(
    prompt: str,
    *,
    workdir: Path,
    allowed_tools: str,
    max_turns: int | None = None,
    timeout: int = 1200,
    resume_session: str | None = None,
    model: str | None = None,
    _auth_retry: bool = False,
) -> ClaudeResult:
    """Invoke ``claude -p`` with the given prompt; return a parsed result.

    Uses ``--output-format json`` (single final event). On a one-time
    ``error_authentication`` classification we quarantine a stale
    ``~/.claude/.credentials.json`` (if any) and retry once, letting the
    CLI fall back to Keychain. ``_auth_retry`` is the re-entry guard,
    set internally on the retry call so we can never loop. Disabled
    entirely by ``ALFRED_DISABLE_CLAUDE_AUTH_REPAIR=1``.

    Args:
        prompt: full text passed via ``-p``.
        workdir: working directory for the subprocess.
        allowed_tools: comma-separated tool gate (forwarded to
            ``--allowedTools``).
        max_turns: explicit ceiling. ``None`` -> ``_CLAUDE_UNLIMITED_TURNS``
            so the CLI's hidden 40-turn default never bites.
        timeout: wall-clock seconds.
        resume_session: optional ``--resume`` session ID.
        model: optional ``--model`` alias forwarded to the CLI.

    Returns:
        A :class:`ClaudeResult` with both legacy (``success`` /
        ``subtype`` / ``num_turns`` / ``cost_usd`` / ``result_text``)
        and additive (``stop_reason`` / ``error_message``) fields.
    """
    if is_dry_run():
        dry_run_log(
            "llm",
            f"would invoke claude with prompt of {len(prompt)} chars, "
            f"model={model or '(cli-default)'}, "
            f"max_turns={max_turns if max_turns is not None else '(unlimited)'}",
        )
        return dry_run_claude_result(prompt, model=model, engine="claude")

    effective_max_turns = max_turns if max_turns is not None else _CLAUDE_UNLIMITED_TURNS
    # Resolve the memory-MCP server path ONCE so the allowlist augmentation and
    # the --mcp-config attachment below always agree (no TOCTOU between two
    # independent Path.exists() checks).
    memory_script = _memory_mcp_script()
    cmd = [
        CLAUDE_BIN,
        "-p",
        prompt,
        "--allowedTools",
        _with_memory_mcp_tools(allowed_tools, memory_script, workdir),
        "--max-turns",
        str(effective_max_turns),
        "--output-format",
        "json",
        "--permission-mode",
        "bypassPermissions",
    ]
    # One ``--settings`` source carrying notification suppression (default on,
    # opt out with ALFRED_AGENT_NOTIFICATIONS=1) AND the OPT-IN PreToolUse
    # guardrail hook (off by default; enable with ALFRED_AGENT_HOOKS=1).
    # ``--settings`` adds a source; it does not touch auth.
    cmd.extend(_agent_settings_args())
    # Attach the read-only memory MCP server so agents can recall lessons as a
    # tool (capability, on by default; ALFRED_MEMORY_MCP=0 to disable). Reuses
    # the single resolved memory_script from above.
    cmd.extend(_memory_mcp_args(memory_script, workdir))
    if model:
        cmd.extend(["--model", model])
    if resume_session:
        cmd.extend(["--resume", resume_session])

    res = run(cmd, cwd=str(workdir), timeout=timeout, capture=True)

    if res.returncode == 124:
        return ClaudeResult(
            success=False,
            subtype="error_timeout",
            num_turns=0,
            cost_usd=0.0,
            session_id=None,
            result_text=res.stdout or res.stderr or "",
            raw={"returncode": 124, "timeout": timeout},
            stop_reason="aborted",
            error_message=f"claude_invoke exceeded {timeout}s",
        )

    if not res.stdout:
        return ClaudeResult(
            success=False,
            subtype="parse-failed",
            num_turns=0,
            cost_usd=0.0,
            session_id=None,
            result_text=res.stderr or "",
            raw={},
            stop_reason="error",
            error_message="claude produced no stdout",
        )

    try:
        raw = json.loads(res.stdout)
    except json.JSONDecodeError:
        return ClaudeResult(
            success=False,
            subtype="parse-failed",
            num_turns=0,
            cost_usd=0.0,
            session_id=None,
            result_text=res.stdout or res.stderr or "",
            raw={},
            stop_reason="error",
            error_message="claude output unparseable",
        )

    result = _build_claude_result(raw, fallback_text=res.stderr or "")
    if _should_retry_claude_auth(result, already_retried=_auth_retry):
        return claude_invoke(
            prompt,
            workdir=workdir,
            allowed_tools=allowed_tools,
            max_turns=max_turns,
            timeout=timeout,
            resume_session=resume_session,
            model=model,
            _auth_retry=True,
        )
    return result


def claude_invoke_streaming(
    prompt: str,
    *,
    workdir: Path,
    allowed_tools: str,
    agent: str,
    firing_id: str,
    max_turns: int | None = None,
    timeout: int = 1200,
    resume_session: str | None = None,
    model: str | None = None,
    _auth_retry: bool = False,
) -> ClaudeResult:
    """Streaming counterpart of :func:`claude_invoke`. Same return shape.

    Historically this also routed through a local unix-socket daemon
    (``claude-proxy``) to work around a macOS Keychain ACL issue under
    launchd. Since the operator can instead expose
    ``CLAUDE_CODE_OAUTH_TOKEN`` (see ``docs/CLAUDE_CODE.md``) which makes
    ``claude`` skip Keychain entirely, the proxy was removed in v0.4.1.

    This path now invokes Claude with ``--output-format stream-json`` and writes
    every stdout event to
    ``$ALFRED_HOME/state/transcripts/<agent>/<YYYY-MM>/<firing_id>.jsonl`` as
    it arrives. The final ``result`` event is parsed into the same
    :class:`ClaudeResult` shape as :func:`claude_invoke`, so existing callers
    keep their return contract while live log/compose views can tail the JSONL.
    """
    if is_dry_run():
        dry_run_log(
            "llm",
            f"would invoke claude streaming with prompt of {len(prompt)} chars, "
            f"agent={agent}, firing_id={firing_id}, model={model or '(cli-default)'}",
        )
        return dry_run_claude_result(prompt, model=model, engine="claude")

    if max_turns is None:
        max_turns = _CLAUDE_UNLIMITED_TURNS

    memory_script = _memory_mcp_script()
    cmd = [
        CLAUDE_BIN,
        "-p",
        prompt,
        "--allowedTools",
        _with_memory_mcp_tools(allowed_tools, memory_script, workdir),
        "--max-turns",
        str(max_turns),
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "bypassPermissions",
    ]
    cmd.extend(_agent_settings_args())
    cmd.extend(_memory_mcp_args(memory_script, workdir))
    if model:
        cmd.extend(["--model", model])
    if resume_session:
        cmd.extend(["--resume", resume_session])

    transcript = transcript_path(agent, firing_id)
    transcript.parent.mkdir(parents=True, exist_ok=True)
    captured_lines: list[str] = []

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(workdir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        return ClaudeResult(
            success=False,
            subtype="parse-failed",
            num_turns=0,
            cost_usd=0.0,
            session_id=None,
            result_text=str(exc),
            raw={},
            stop_reason="error",
            error_message=f"claude CLI not found: {exc}",
        )
    except OSError as exc:
        return ClaudeResult(
            success=False,
            subtype="error_context_budget",
            num_turns=0,
            cost_usd=0.0,
            session_id=None,
            result_text=str(exc),
            raw={"transcript_path": str(transcript), "prompt_bytes": len(prompt.encode("utf-8"))},
            stop_reason="error",
            error_message=f"claude_invoke_streaming could not start: {exc}",
        )

    # Loop-fingerprint guard: watch the live stream for an agent stuck
    # repeating the same step (or blowing past the hard step ceiling) and
    # kill the subprocess instead of letting it spin to the wall-clock
    # timeout. Disabled with ``ALFRED_LOOP_DETECT=0``.
    loop_detector = None if _is_falsy_env("ALFRED_LOOP_DETECT") else LoopDetector()
    loop_stop: dict[str, str] = {}

    def _capture_stdout() -> None:
        assert proc.stdout is not None
        with transcript.open("w", encoding="utf-8") as handle:
            for raw_line in proc.stdout:
                captured_lines.append(raw_line)
                handle.write(raw_line)
                handle.flush()
                if loop_detector is not None and not loop_stop:
                    step = _stream_step_for_loopcheck(raw_line)
                    if step is not None and loop_detector.observe(*step):
                        loop_stop["reason"] = loop_detector.tripped_reason or "loop detected"
                        with contextlib.suppress(OSError):
                            proc.kill()
                        break

    reader = threading.Thread(target=_capture_stdout, name=f"claude-stream-{agent}", daemon=True)
    reader.start()

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        with contextlib.suppress(OSError):
            proc.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)
    reader.join(timeout=5)
    stderr = ""
    if proc.stderr is not None:
        with contextlib.suppress(OSError):
            stderr = proc.stderr.read()

    stdout_text = "".join(captured_lines)
    if loop_stop:
        # A stuck agent: surface honestly and escalate rather than spin.
        # Classified as a capability gap so hybrid mode can try the other
        # engine once, which may not get stuck on the same step.
        return ClaudeResult(
            success=False,
            subtype="error_loop_detected",
            num_turns=0,
            cost_usd=0.0,
            session_id=None,
            result_text=stdout_text or stderr,
            raw={
                "loop_detected": True,
                "reason": loop_stop["reason"],
                "transcript_path": str(transcript),
            },
            stop_reason="aborted",
            error_message=f"claude_invoke_streaming stopped: {loop_stop['reason']}",
        )
    if timed_out:
        return ClaudeResult(
            success=False,
            subtype="error_timeout",
            num_turns=0,
            cost_usd=0.0,
            session_id=None,
            result_text=stdout_text or stderr,
            raw={"returncode": 124, "timeout": timeout, "transcript_path": str(transcript)},
            stop_reason="aborted",
            error_message=f"claude_invoke_streaming exceeded {timeout}s",
        )

    final_event = _last_stream_result(captured_lines)
    if final_event is None:
        return ClaudeResult(
            success=False,
            subtype="parse-failed",
            num_turns=0,
            cost_usd=0.0,
            session_id=None,
            result_text=stdout_text or stderr,
            raw={"returncode": proc.returncode, "transcript_path": str(transcript)},
            stop_reason="error",
            error_message=(
                "claude stream-json produced no result event"
                + (f" (stderr: {stderr.strip()[-300:]})" if stderr and stderr.strip() else "")
            ),
        )

    result = _build_claude_result(final_event, fallback_text=stderr or stdout_text)
    result.raw.setdefault("transcript_path", str(transcript))
    if _should_retry_claude_auth(result, already_retried=_auth_retry):
        return claude_invoke_streaming(
            prompt,
            workdir=workdir,
            allowed_tools=allowed_tools,
            agent=agent,
            firing_id=firing_id,
            max_turns=max_turns,
            timeout=timeout,
            resume_session=resume_session,
            model=model,
            _auth_retry=True,
        )
    return result


def _last_stream_result(lines: list[str]) -> dict[str, Any] | None:
    """Return the final Claude stream-json result event from captured lines."""
    final: dict[str, Any] | None = None
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and ("result" in obj or obj.get("type") == "result"):
            final = obj
    return final


def _stream_step_for_loopcheck(line: str) -> tuple[str, str] | None:
    """Extract a ``(action, result_preview)`` pair from one stream-json line.

    Returns ``None`` for lines that are not a tool step (system init,
    assistant text, the final result). We fingerprint tool USE events
    (action = tool name, preview = a stable digest of the tool input) and
    tool RESULT events (action = ``"tool_result"``, preview = the result
    body), which together are what spins when an agent is stuck redoing
    the same failing action.
    """
    stripped = line.strip()
    if not stripped:
        return None
    try:
        obj = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    msg = obj.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if not isinstance(content, list):
        return None
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "tool_use":
            name = str(block.get("name") or "tool")
            payload = json.dumps(block.get("input", {}), sort_keys=True, default=str)
            return (name, payload)
        if btype == "tool_result":
            body = block.get("content")
            if isinstance(body, list):
                body = " ".join(str(b.get("text", "")) for b in body if isinstance(b, dict))
            # Fingerprint the RAW result body. This pair feeds only
            # ``loop_detector.observe`` (the subprocess runs with
            # ``stdin=DEVNULL``, so nothing here can reach the model's
            # context); the loop detector needs the raw bytes so that two
            # genuinely different outputs stay distinguishable in the
            # truncated fingerprint window. The tool_digest module is for
            # compressing output that actually re-enters the model turn,
            # which is not this path.
            return ("tool_result", str(body))
    return None


# --------------------------------------------------------------------------
# Codex CLI invocation
# --------------------------------------------------------------------------


def codex_invoke(
    prompt: str,
    *,
    workdir: Path,
    agent: str,
    firing_id: str | None = None,
    timeout: int = 1200,
    model: str | None = None,
    sandbox: str | None = None,
    approval_policy: str | None = None,
    bypass_approvals_and_sandbox: bool = False,
    add_dirs: list[Path] | None = None,
    allowed_tools: str | None = None,
    max_turns: int | None = None,
    resume_session: str | None = None,
) -> ClaudeResult:
    """Invoke ``codex exec`` non-interactively; return a ``ClaudeResult`` shape.

    Codex does not expose Claude's tool allow-list, max-turn, or
    resume-session semantics. The wrapper rejects those kwargs instead
    of implying they were enforced. Default posture is review-safe:
    read-only sandbox and no approval prompts.
    """
    if is_dry_run():
        dry_run_log(
            "llm",
            f"would invoke codex with prompt of {len(prompt)} chars, "
            f"model={model or CODEX_DEFAULT_MODEL or '(cli-default)'}, "
            f"sandbox={sandbox or CODEX_DEFAULT_SANDBOX}",
        )
        return dry_run_claude_result(prompt, model=model, engine="codex")

    unsupported = {
        "allowed_tools": allowed_tools,
        "max_turns": max_turns,
        "resume_session": resume_session,
    }
    rejected = [name for name, value in unsupported.items() if value is not None]
    if rejected:
        return ClaudeResult(
            success=False,
            subtype="error",
            num_turns=0,
            cost_usd=0.0,
            session_id=None,
            result_text="",
            raw={},
            stop_reason="error",
            error_message=(
                "codex engine does not support kwargs: "
                + ", ".join(rejected)
                + ". Use sandbox/approval controls, or route this prompt to Claude."
            ),
        )

    if firing_id is None:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        firing_id = f"{stamp}-{secrets.token_hex(2)}"

    paths = codex_artifact_paths(agent, firing_id)
    cmd = [
        CODEX_BIN,
        "exec",
        "--skip-git-repo-check",
        "--cd",
        str(workdir),
    ]
    resolved_sandbox = sandbox or CODEX_DEFAULT_SANDBOX
    if bypass_approvals_and_sandbox:
        cmd.append("--dangerously-bypass-approvals-and-sandbox")
        resolved_sandbox = "danger-full-access"
    else:
        cmd.extend(
            [
                "--sandbox",
                resolved_sandbox,
                "-c",
                f'approval_policy="{approval_policy or CODEX_APPROVAL_POLICY}"',
            ]
        )
    cmd.extend(["--output-last-message", str(paths["last_message"])])
    chosen_model = model or CODEX_DEFAULT_MODEL
    if chosen_model:
        cmd.extend(["--model", chosen_model])
    for directory in add_dirs or []:
        cmd.extend(["--add-dir", str(directory)])
    cmd.append("-")

    try:
        proc = _popen_run_text(
            cmd,
            cwd=str(workdir),
            timeout=timeout,
            capture=True,
            input_text=prompt,
        )
    except FileNotFoundError as e:
        return ClaudeResult(
            success=False,
            subtype="parse-failed",
            num_turns=0,
            cost_usd=0.0,
            session_id=None,
            result_text=str(e),
            raw={},
            stop_reason="error",
            error_message=f"codex CLI not found: {e}",
        )
    if proc.returncode == 124:
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        with contextlib.suppress(OSError):
            paths["stdout"].write_text(stdout)
            paths["stderr"].write_text(stderr)
        last_message = ""
        with contextlib.suppress(OSError):
            last_message = paths["last_message"].read_text().strip()
        combined = f"{stdout}\n{stderr}"
        return ClaudeResult(
            success=False,
            subtype="error_timeout",
            num_turns=0,
            cost_usd=0.0,
            session_id=_extract_codex_session_id(combined),
            result_text=last_message or stdout,
            raw={
                "engine": "codex",
                "returncode": 124,
                "stdout_path": str(paths["stdout"]),
                "stderr_path": str(paths["stderr"]),
                "last_message_path": str(paths["last_message"]),
                "tokens_used": _extract_codex_tokens(combined),
                "model": chosen_model,
                "sandbox": resolved_sandbox,
                "bypass_approvals_and_sandbox": bypass_approvals_and_sandbox,
                "timeout": timeout,
            },
            stop_reason="aborted",
            error_message=f"codex_invoke exceeded {timeout}s",
        )

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    with contextlib.suppress(OSError):
        paths["stdout"].write_text(stdout)
        paths["stderr"].write_text(stderr)

    try:
        result_text = paths["last_message"].read_text().strip()
    except OSError:
        result_text = ""
    if not result_text:
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        result_text = lines[-1] if lines else ""

    combined = f"{stdout}\n{stderr}"
    raw = {
        "engine": "codex",
        "returncode": proc.returncode,
        "stdout_path": str(paths["stdout"]),
        "stderr_path": str(paths["stderr"]),
        "last_message_path": str(paths["last_message"]),
        "tokens_used": _extract_codex_tokens(combined),
        "model": chosen_model,
        "sandbox": resolved_sandbox,
        "bypass_approvals_and_sandbox": bypass_approvals_and_sandbox,
    }
    session_id = _extract_codex_session_id(combined)
    if proc.returncode != 0:
        tail = (result_text or stderr or stdout or "").strip()[-1000:]
        classifier_text = f"{result_text}\n{stdout}\n{stderr}"
        # Hard plan/credit exhaustion is checked BEFORE the generic rate-limit
        # bucket. A spent budget prints "You've hit your usage limit ... try
        # again at <date>": that is not a 429 that clears on a short backoff,
        # so classifying it as error_rate_limit made the router keep firing
        # into a wall that stayed shut until the resume date. We split it into
        # its own error_quota_exhausted subtype, parse the resume instant into
        # raw["quota_resume_at"], and persist a per-engine backoff so the
        # scheduler parks codex until then instead of burning firings.
        if looks_quota_exhausted(classifier_text):
            resume_at = parse_quota_resume_at(classifier_text)
            raw["quota_resume_at"] = resume_at
            try:
                from .state import record_engine_quota_exhausted

                raw["quota_resume_at"] = record_engine_quota_exhausted(
                    "codex",
                    resume_at=resume_at,
                    reason=tail[-200:] or "codex usage limit reached",
                )
            except Exception:
                pass
            return ClaudeResult(
                success=False,
                subtype="error_quota_exhausted",
                num_turns=1,
                cost_usd=0.0,
                session_id=session_id,
                result_text=result_text or tail,
                raw=raw,
                stop_reason="error",
                error_message=(
                    "codex usage limit reached" + (f"; resumes {resume_at}" if resume_at else "")
                ),
            )
        subtype = "error_rate_limit" if _RATE_LIMIT_RESULT_RE.search(classifier_text) else "error"
        if subtype == "error" and _BUDGET_RESULT_RE.search(classifier_text):
            subtype = "error_rate_limit"
        return ClaudeResult(
            success=False,
            subtype=subtype,
            num_turns=1,
            cost_usd=0.0,
            session_id=session_id,
            result_text=result_text or tail,
            raw=raw,
            stop_reason="error",
            error_message=tail or f"codex exited {proc.returncode}",
        )
    if not result_text:
        return ClaudeResult(
            success=False,
            subtype="parse-failed",
            num_turns=1,
            cost_usd=0.0,
            session_id=session_id,
            result_text=stderr or stdout,
            raw=raw,
            stop_reason="error",
            error_message="codex produced no final message",
        )

    return ClaudeResult(
        success=True,
        subtype="success",
        num_turns=1,
        cost_usd=0.0,
        session_id=session_id,
        result_text=result_text,
        raw=raw,
        stop_reason="end_turn",
        error_message=None,
    )


# --------------------------------------------------------------------------
# Engine-aware dispatch
# --------------------------------------------------------------------------


def _resolve_firing_role(role: str | None, agent: str) -> str | None:
    """Resolve the skill-pack role for a firing.

    An explicit ``role`` always wins (the override). When it is ``None`` -- which
    is every production caller today, since none pass a role -- the role is
    derived from the agent ``codename`` via the canonical
    :data:`agent_roster.CODENAME_TO_PACK_ROLE` map. That is what makes skill
    injection active for the whole fleet without touching any caller. A codename
    with no engineering skill role (operational agents) resolves to ``None`` and
    injects nothing.
    """
    if role is not None:
        return role
    with contextlib.suppress(Exception):
        from agent_roster import pack_role_for_codename

        return pack_role_for_codename(agent)
    return None


def _with_skills_block(prompt: str, role: str | None, agent: str = "") -> str:
    """Append the role-scoped skills block to ``prompt`` when injection is on.

    ``role`` is the explicit override; when it is ``None`` the role is derived
    from the agent ``codename`` (:func:`_resolve_firing_role`), so every existing
    caller gets injection automatically. Metadata-only progressive disclosure:
    names the skills a firing of that role may invoke and where to read each one,
    without inlining any body. Gated by ``ALFRED_SKILLS_INJECT`` (default on)
    inside :func:`skills_context_for_role`. Behavior-preserving when no skills
    match, no role resolves, or the gate is off: the prompt is returned
    unchanged.
    """
    resolved = _resolve_firing_role(role, agent)
    block = ""
    with contextlib.suppress(Exception):
        block = skills_context_for_role(resolved)
    if not block:
        return prompt
    return f"{prompt}\n\n{block}"


def _with_skeleton_priming_block(
    prompt: str,
    *,
    repo: str | None,
    orientation_paths: list[str] | None,
    workdir: Path,
) -> str:
    """Append orientation skeletons for ``orientation_paths`` when armed.

    Gated OFF by default (``ALFRED_SKELETON_PRIMING``) and a full no-op unless a
    caller supplies orientation paths, so every production caller is unchanged.
    Orientation paths are structure-only outlines that reuse the code-map index;
    the firing's edit-target is never passed here and every elided body remains
    one full read away. Behavior-preserving on any error or empty selection.
    """
    if not repo or not orientation_paths:
        return prompt
    block = ""
    with contextlib.suppress(Exception):
        from .skeleton_priming import skeleton_priming_block

        block = skeleton_priming_block(repo, orientation_paths, workdir=workdir)
    if not block:
        return prompt
    return f"{prompt}\n\n{block}"


# --------------------------------------------------------------------------
# Self-grading rubric gate (opt-in)
#
# When a caller passes ``rubric=...`` (or sets ``ALFRED_RUBRIC``), a cheap
# SEPARATE grader LLM re-reads the finished run's result text against the
# rubric and returns a structured verdict (see lib/agent_runner/rubric.py).
# The verdict is stashed on ``result.raw["rubric_verdict"]`` so a runner can
# decide whether to open a PR. This is a forward-looking SUCCESS gate; it is
# fully OFF by default and never changes behavior when no rubric is set.
# --------------------------------------------------------------------------


def _resolve_rubric(rubric: str | None) -> str | None:
    """Resolve the active rubric: explicit arg wins, else ``ALFRED_RUBRIC``.

    Returns ``None`` (gate off) when neither is set to non-empty text.
    """
    if rubric is not None and rubric.strip():
        return rubric
    env_rubric = os.environ.get("ALFRED_RUBRIC", "").strip()
    return env_rubric or None


def _rubric_max_iterations() -> int:
    """Read the rubric loop bound from ``ALFRED_RUBRIC_MAX_ITERATIONS``.

    Defaults to 3 (the deepagents RubricMiddleware range is 2-3), clamped to
    ``[1, 10]``. Only the primitive loop in ``rubric.py`` consumes this;
    ``invoke_agent_engine`` grades once per run and leaves iteration to a
    caller-driven loop.
    """
    return env_int("ALFRED_RUBRIC_MAX_ITERATIONS", 3, minimum=1, maximum=10)


#: Grader engines the gate knows how to run. Only these two are cheap+local
#: engines here; the hybrid pseudo-engine is not a real grader target, so we
#: resolve it down to the cheap default rather than run a fallback chain for a
#: read-only judging pass.
_GRADER_ENGINES: frozenset[str] = frozenset({"codex", "claude"})

#: Default grader engine: the cheapest read-only judge available here.
_DEFAULT_GRADER_ENGINE = "codex"


def resolve_grader_engine(grader_engine: str | None) -> str:
    """Resolve the grader engine INDEPENDENTLY of the primary run's engine.

    The grader is a separate cheap judging pass, so its engine is chosen on its
    own axis and must never be inherited from the run under review by accident.
    Precedence: an explicit ``grader_engine`` (arg or
    ``ALFRED_RUBRIC_GRADER_ENGINE``) wins when it names a known grader engine;
    otherwise we default to the cheap read-only :data:`_DEFAULT_GRADER_ENGINE`.
    ``hybrid`` (or any unknown value) resolves to the cheap default rather than
    running a two-engine fallback chain for a grade.
    """
    candidate = (grader_engine or "").strip().lower()
    if candidate in _GRADER_ENGINES:
        return candidate
    return _DEFAULT_GRADER_ENGINE


class _GraderTransientError(RuntimeError):
    """Raised when a grader ENGINE returns a transient (retryable) result.

    Carries an HTTP-shaped ``status_code`` so ``llm_retry.classify_exception``
    maps it to a retryable code (429 -> rate_limit, 503 -> server_error), which
    lets ``retry_call`` back off and re-invoke the grader instead of erroring
    the whole gate. Non-transient failures never raise this; they just surface
    their (empty) text and the parser degrades to ``grader_error``.
    """

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


# Whether a failed grader result is worth a bounded retry (a temporary
# provider wall on the CHEAP grade) is decided by the SHARED classifier in
# reliability.py, not a duplicated list here, so the grader's retry policy can
# never drift from the engine-wide one (which already treats
# ``error_rate_limit`` / ``error_overloaded`` / ``error_timeout`` / ``error_api``
# as TRANSIENT).
def _grader_result_is_transient(result: ClaudeResult) -> bool:
    """True when a failed grader result is a transient, retryable failure.

    Delegates to the shared ``classify_result`` so the grade path and the
    primary invoke path share one source of truth for what "transient" means.
    """
    return not result.success and classify_result(result) is FailureClass.TRANSIENT


# Rate-limit / overload shapes map to 429; every other transient (timeout,
# generic transport ``error_api``, ...) maps to a 503 server-error shape so the
# HTTP-shaped classifier in ``llm_retry`` marks it retryable.
_GRADER_RATE_LIMIT_SUBTYPES: frozenset[str] = frozenset({"error_rate_limit", "error_overloaded"})


def _grader_status_for_subtype(subtype: str) -> int:
    """Map a transient grader subtype to an HTTP status for the classifier."""
    if subtype in _GRADER_RATE_LIMIT_SUBTYPES:
        return 429
    return 503  # error_timeout / error_api / any other transient -> server_error shape


def _default_rubric_grader(
    *,
    grader_engine: str | None,
    agent: str,
    firing_id: str,
    workdir: Path,
    codex_model: str | None,
) -> Callable[[str], str]:
    """Build a grader_fn that runs a cheap, read-only grader engine.

    The grader is a SEPARATE cheap invocation (Codex/Haiku-class): it reads the
    prompt and returns raw text for ``rubric.grade`` to parse. It runs
    read-only (no repo mutation) and short-timeout, because grading is a
    judging pass, not more implementation work. Injectable so tests never reach
    a real engine.

    The grader engine is resolved by :func:`resolve_grader_engine` on its OWN
    axis, so it is always the SELECTED grader engine and never silently the
    primary run's engine. ``codex_model`` is the PRIMARY run's Codex-specific
    model; it is forwarded to the grader ONLY when the grader engine is also
    Codex. A Claude grader never receives a Codex model string (and vice
    versa): a mismatched grader engine gets ``model=None`` so the engine picks
    its own default. A transient grader failure (rate limit / overload /
    timeout on the cheap grade) is retried with bounded backoff via
    ``llm_retry.retry_call`` instead of erroring the gate; a fatal failure
    surfaces empty text and the parser degrades to ``grader_error``.
    """
    from llm_retry import classify_exception, is_retryable_code, retry_call

    engine = resolve_grader_engine(grader_engine)
    # Only forward an engine-specific model to the engine it belongs to. The
    # only engine-specific model threaded in here is the primary run's Codex
    # model, so it is valid solely for a Codex grader; a Claude grader must not
    # receive it. When the grader engine does not match, the grader runs on its
    # own default model (``None`` -> the engine's CLI-default).
    grader_codex_model = codex_model if engine == "codex" else None

    def _invoke_once() -> ClaudeResult:
        if engine == "claude":
            # No Codex model leaks here; Claude uses its own default model.
            return claude_invoke(
                prompt_holder["prompt"],
                workdir=workdir,
                allowed_tools="",
                timeout=180,
                model=None,
            )
        return codex_invoke(
            prompt_holder["prompt"],
            workdir=workdir,
            agent=f"{agent}-grader",
            firing_id=f"{firing_id}-grader",
            timeout=180,
            sandbox="read-only",
            model=grader_codex_model,
        )

    def _invoke_raising_on_transient() -> ClaudeResult:
        res = _invoke_once()
        if _grader_result_is_transient(res):
            raise _GraderTransientError(
                f"grader engine {engine} transient failure: {res.subtype}",
                status_code=_grader_status_for_subtype(res.subtype),
            )
        return res

    prompt_holder: dict[str, str] = {}

    def _grader(prompt: str) -> str:
        prompt_holder["prompt"] = prompt
        try:
            res = retry_call(
                _invoke_raising_on_transient,
                is_retryable=lambda exc: is_retryable_code(classify_exception(exc)),
            )
        except _GraderTransientError:
            # Retries exhausted on a still-transient grader wall. Surface no
            # text so the parser records a safe grader_error verdict (which is
            # terminal + failed) rather than pretending the run passed.
            return ""
        return res.result_text or ""

    return _grader


def _apply_rubric_gate(
    result: ClaudeResult,
    *,
    rubric: str,
    grader_fn: Callable[[str], str],
) -> GraderVerdict:
    """Grade ``result.result_text`` against ``rubric`` and stash the verdict.

    Returns the verdict AND records it (serialized) on
    ``result.raw["rubric_verdict"]`` so callers reading only the
    ``ClaudeResult`` still see the gate outcome. Never raises: a grader that
    errors yields a ``grader_error`` verdict from ``grade`` itself.
    """
    verdict = grade_transcript(result.result_text or "", rubric, grader_fn=grader_fn)
    result.raw = dict(result.raw or {})
    result.raw["rubric_verdict"] = {
        "result": verdict.result,
        "explanation": verdict.explanation,
        "terminal_reason": verdict.terminal_reason,
        "criteria": [{"name": c.name, "passed": c.passed, "gap": c.gap} for c in verdict.criteria],
    }
    return verdict


def invoke_agent_engine(
    prompt: str,
    *,
    engine: str,
    agent: str,
    firing_id: str,
    workdir: Path,
    claude_allowed_tools: str,
    timeout: int,
    role: str | None = None,
    claude_max_turns: int | None = None,
    claude_model: str | None = None,
    codex_timeout: int | None = None,
    codex_model: str | None = None,
    codex_sandbox: str | None = None,
    codex_add_dirs: list[Path] | None = None,
    codex_approval_policy: str | None = None,
    codex_bypass_approvals_and_sandbox: bool = False,
    claude_fn: Callable[..., ClaudeResult] | None = None,
    codex_fn: Callable[..., ClaudeResult] | None = None,
    on_fallback: Callable[[ClaudeResult], None] | None = None,
    memory_repo: str | None = None,
    memory_query: str | None = None,
    memory_limit: int = 3,
    orientation_paths: list[str] | None = None,
    rubric: str | None = None,
    rubric_grader_engine: str | None = None,
    rubric_grader_fn: Callable[[str], str] | None = None,
) -> tuple[ClaudeResult, str]:
    """Invoke a prompt through Claude, Codex, or Claude-first hybrid.

    Returns ``(result, engine_used)`` where ``engine_used`` is one of
    ``"claude"``, ``"codex"``, or ``"codex-fallback"``. The
    ``on_fallback`` callback fires only when hybrid mode falls back
    after a Claude capability failure; useful for posting a
    one-line Slack warning.

    ``role`` is the firing's agent role (feature-dev, pr-review, planner, ...).
    It is an OPTIONAL override: when omitted (as every production caller does
    today), the role is derived from the agent ``codename`` via the canonical
    roster map, so skill injection is active for the whole fleet with no caller
    change. When a role resolves and ``ALFRED_SKILLS_INJECT`` is not disabled, a
    compact metadata-only block naming the skills recommended for that role is
    appended to the prompt (progressive disclosure: the agent reads each SKILL.md
    body on demand). A codename with no skill role, or the gate off, leaves the
    prompt untouched.

    ``rubric`` opts IN to the self-grading gate. When set (or when
    ``ALFRED_RUBRIC`` is exported), a cheap SEPARATE grader engine re-reads the
    finished run's ``result_text`` against the rubric and the structured verdict
    is stashed on ``result.raw["rubric_verdict"]`` (see ``rubric.py``) so a
    caller can decide whether to open a PR. This does NOT change the invocation
    result or open/block any PR itself. Fully OFF by default: with no rubric the
    behavior is byte-identical to before. ``rubric_grader_engine`` picks the
    grader engine (defaults to ``ALFRED_RUBRIC_GRADER_ENGINE`` or Codex, the
    cheapest here); ``rubric_grader_fn`` injects a grader directly (tests use
    this so no real LLM runs).

    ``orientation_paths`` opts IN to skeleton priming: when set AND
    ``ALFRED_SKELETON_PRIMING`` is armed, a compact structure-only outline of
    those files (bodies elided, reusing the code-map index) is appended to the
    prompt. These must be orientation files only, never the firing's
    edit-target. Off by default and a no-op when unset, so existing callers are
    byte-identical.
    """
    mode = normalize_engine(engine)
    claude_call = claude_fn or claude_invoke_streaming
    codex_call = codex_fn or codex_invoke
    memory_provider = load_runtime_memory() if memory_repo else None
    # Arm the cleanup BEFORE the firing's delta state can exist. with_memory_prompt
    # records injected lessons for this firing, and govern_prompt_context runs
    # right after, so both live inside the try: if injection recording or prompt
    # governance raises, the finally's clear_firing still releases the firing's
    # per-firing delta state. Clearing on completion keeps a finished firing's
    # injected-lesson set from lingering in the process-global table; the table
    # cap remains only a backstop for a crash before the finally runs. Reuse
    # counters are intentionally NOT cleared: reinforce-on-reuse is a cross-firing
    # signal by design.
    try:
        prompt_with_context = with_memory_prompt(
            prompt,
            memory_provider,
            codename=agent,
            repo=memory_repo,
            query=memory_query,
            limit=memory_limit,
            firing_id=firing_id,
            repo_root=str(workdir) if workdir else None,
            # The firing's orientation paths are the file signal available at
            # recall time (before any edit). Gated by ALFRED_MEMORY_ANCHOR_RECALL
            # inside with_memory_prompt, so this is a no-op unless armed.
            orientation_paths=orientation_paths,
        )
        prompt_with_context = _with_skills_block(prompt_with_context, role, agent)
        prompt_with_context = _with_skeleton_priming_block(
            prompt_with_context,
            repo=memory_repo,
            orientation_paths=orientation_paths,
            workdir=workdir,
        )
        prompt_for_engine, context_governance = govern_prompt_context(prompt_with_context)

        def _stamp_context_governance(result: ClaudeResult) -> ClaudeResult:
            if context_governance.applied:
                result.raw = dict(result.raw or {})
                result.raw["context_governor"] = context_governance.as_raw()
            return result

        def _invoke_claude() -> ClaudeResult:
            return claude_call(
                prompt_for_engine,
                workdir=workdir,
                allowed_tools=claude_allowed_tools,
                agent=agent,
                firing_id=firing_id,
                max_turns=claude_max_turns,
                timeout=timeout,
                model=claude_model,
            )

        def _invoke_codex() -> ClaudeResult:
            return codex_call(
                prompt_for_engine,
                workdir=workdir,
                agent=agent,
                firing_id=firing_id,
                timeout=codex_timeout or timeout,
                model=codex_model,
                sandbox=codex_sandbox,
                approval_policy=codex_approval_policy,
                bypass_approvals_and_sandbox=codex_bypass_approvals_and_sandbox,
                add_dirs=codex_add_dirs,
            )

        def _resilient_invoke(engine_name: str, invoke: Callable[[], ClaudeResult]) -> ClaudeResult:
            """Run one engine with a per-engine breaker + same-engine transient retry.

            TRANSIENT failures are absorbed here (bounded backoff with full
            jitter honouring any Retry-After) so they never reach the
            Claude->Codex fallback. The breaker trips after N consecutive
            transient failures on the engine and pauses it for a cooldown, so
            parallel workers cannot lockstep-retry into a deeper rate-limit.

            A hard quota-exhaustion wall recorded by a previous firing (the
            codex "hit your usage limit ... try again at <date>" case) short-
            circuits the invoke entirely: the engine is spent until its named
            resume instant, so firing into it again only wastes a run. We return
            an honest ``error_quota_exhausted`` result instead, which lets the
            hybrid caller keep claude running while codex is parked.
            """
            with contextlib.suppress(Exception):
                from .state import engine_quota_backoff

                parked = engine_quota_backoff(engine_name)
                if parked:
                    until = parked.get("until", "")
                    return ClaudeResult(
                        success=False,
                        subtype="error_quota_exhausted",
                        num_turns=0,
                        cost_usd=0.0,
                        session_id=None,
                        result_text=(
                            f"{engine_name} usage limit reached; parked until {until}. "
                            "Skipping this engine until its plan window resets."
                        ),
                        raw={
                            "quota_exhausted": True,
                            "engine": engine_name,
                            "quota_resume_at": until,
                        },
                        stop_reason="error",
                        error_message=f"{engine_name} quota exhausted (resumes {until})",
                    )
            breaker = CircuitBreaker(engine_name)
            if breaker.is_open():
                status = breaker.status()
                return ClaudeResult(
                    success=False,
                    subtype="error_rate_limit",
                    num_turns=0,
                    cost_usd=0.0,
                    session_id=None,
                    result_text=(
                        f"{engine_name} circuit breaker open until {status.until}: "
                        f"pausing calls to protect the shared provider quota"
                    ),
                    raw={"breaker_open": True, "engine": engine_name, "until": status.until},
                    stop_reason="error",
                    error_message=f"{engine_name} breaker open (cooldown until {status.until})",
                )

            def _on_retry(attempt: int, delay: float, outcome: ClaudeResult) -> None:
                breaker.record_transient_failure(reason=outcome.subtype)

            result = retry_with_backoff(
                invoke,
                classify=classify_result,
                retry_after_of=retry_after_seconds,
                on_retry=_on_retry,
            )
            if classify_result(result) is FailureClass.TRANSIENT:
                # Retries exhausted on a still-transient failure: count it so the
                # breaker can trip and stop a hot loop on the next firing.
                breaker.record_transient_failure(reason=result.subtype)
            elif result.success:
                breaker.record_success()
            return result

        if mode == "codex":
            result = _resilient_invoke("codex", _invoke_codex)
            engine_used = "codex"
        else:
            result = _resilient_invoke("claude", _invoke_claude)
            engine_used = "claude"
            # The fallback fires ONLY on a capability failure: Claude ran and
            # returned cleanly but produced nothing useful. Transient failures
            # were already retried on Claude above and never reach here; fatal
            # failures (auth/budget/schema) are surfaced honestly, never papered
            # over by burning the second engine.
            if mode == "hybrid" and classify_result(result) is FailureClass.CAPABILITY:
                trigger_subtype = result.subtype
                if on_fallback:
                    on_fallback(result)
                result = _resilient_invoke("codex", _invoke_codex)
                engine_used = "codex-fallback"
                # Stamp the Codex result with the Claude capability failure that
                # triggered the fallback so event logs can explain the path.
                result.fallback_from_subtype = trigger_subtype

        result = _stamp_context_governance(result)
        if memory_provider is not None and memory_repo:
            result_text = result.result_text or ""
            reflections = parse_memory_reflections(result_text)
            if BEGIN_MARKER in result_text:
                result.result_text = strip_memory_reflections(result_text)
            if reflections:
                record_reflections(
                    memory_provider,
                    reflections,
                    codename=agent,
                    repo=memory_repo,
                    firing_id=firing_id,
                )
            record_firing(
                memory_provider,
                codename=agent,
                repo=memory_repo,
                firing_id=firing_id,
                result=result,
                engine_used=engine_used,
            )

        # Opt-in self-grading gate. Only runs when a rubric is configured, so the
        # default path is untouched. The verdict is stashed on the result's raw
        # envelope for a caller to act on; PR-open blocking is a follow-up wired
        # in the runners (owned by a separate change).
        active_rubric = _resolve_rubric(rubric)
        if active_rubric is not None and not is_dry_run():
            if not result.success:
                # The primary run itself FAILED (Codex quota / rate-limit, Claude
                # auth error, timeout, ...). Grading a failed run is pointless and
                # wastes a grader call, so we skip it entirely and leave a clear
                # note rather than a verdict. This mirrors the runners, which
                # already gate their PR-open path on ``if not result.success``.
                result.raw = dict(result.raw or {})
                result.raw["rubric_verdict"] = {
                    "result": "not_graded",
                    "explanation": (
                        "primary run did not succeed "
                        f"(subtype={result.subtype}); rubric grading skipped"
                    ),
                    "terminal_reason": "primary_run_failed",
                    "criteria": [],
                }
            else:
                grader_fn = rubric_grader_fn or _default_rubric_grader(
                    grader_engine=(
                        rubric_grader_engine
                        or os.environ.get("ALFRED_RUBRIC_GRADER_ENGINE", "").strip()
                    ),
                    agent=agent,
                    firing_id=firing_id,
                    workdir=workdir,
                    codex_model=codex_model,
                )
                with contextlib.suppress(Exception):
                    _apply_rubric_gate(result, rubric=active_rubric, grader_fn=grader_fn)

        return result, engine_used
    finally:
        if firing_id:
            memory_ranking.clear_firing(firing_id)
