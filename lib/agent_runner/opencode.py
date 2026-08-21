"""OpenCode command, permission, and NDJSON contracts.

The adapter keeps OpenCode-specific protocol details out of the shared process
router. It does not start subprocesses. ``process.py`` owns process lifetime,
timeouts, and the engine-neutral ``ClaudeResult`` return shape.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class OpenCodeEvents:
    """Sanitized result extracted from one OpenCode NDJSON stream."""

    text: str
    session_id: str | None
    tokens_used: int
    cost_usd: float
    error: str | None = None
    tool_error: str | None = None
    parse_error: str | None = None
    event_count: int = 0


def build_opencode_command(
    binary: str,
    *,
    workdir: Path,
    model: str | None = None,
) -> list[str]:
    """Build one non-interactive command without a permission bypass flag."""

    command = [
        binary,
        "--pure",
        "run",
        "--format",
        "json",
        "--dir",
        str(workdir),
        "--agent",
        "alfred",
    ]
    if model:
        command.extend(["--model", model])
    return command


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


def _permission_policy(
    *,
    allow_writes: bool,
    shell_commands: tuple[str, ...] = (),
    mcp_tools: Mapping[str, tuple[str, ...]] | None = None,
) -> dict[str, Any]:
    for command in shell_commands:
        if (
            not command
            or command != command.strip()
            or "*" in command
            or "\n" in command
            or "\r" in command
        ):
            raise ValueError("OpenCode probe permissions require one exact shell command per entry")
    permissions: dict[str, Any] = {
        "*": "allow",
        "external_directory": "deny",
        "question": "deny",
        "plan_enter": "deny",
        "plan_exit": "deny",
        "task": "deny",
        "skill": "deny",
        "mcp_*": "deny",
    }
    permissions["edit"] = "allow" if allow_writes else "deny"
    if allow_writes:
        permissions["bash"] = {
            "*": "allow",
            "git push*": "deny",
            "gh pr merge*": "deny",
            "git checkout main*": "deny",
            "git switch main*": "deny",
        }
    elif shell_commands:
        permissions["bash"] = {"*": "deny"}
        permissions["bash"].update(dict.fromkeys(shell_commands, "allow"))
    else:
        permissions["bash"] = "deny"
    for server, tools in (mcp_tools or {}).items():
        permissions[f"{server}_*"] = "deny"
        for tool in tools:
            permissions[f"{server}_{tool}"] = "allow"
    return permissions


def _local_mcp_config(
    servers: Mapping[str, Mapping[str, Any]],
    *,
    workdir: Path | None,
) -> dict[str, dict[str, Any]]:
    """Translate Alfred's stdio server contract to OpenCode's local format."""

    out: dict[str, dict[str, Any]] = {}
    for name, server in servers.items():
        command = server.get("command")
        args = server.get("args", [])
        if not isinstance(command, str) or not command.strip():
            continue
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            continue
        config: dict[str, Any] = {
            "type": "local",
            "command": [command, *args],
            "enabled": True,
            "timeout": 5000,
        }
        if workdir is not None:
            config["cwd"] = str(workdir)
        out[name] = config
    return out


def opencode_environment(
    environ: Mapping[str, str],
    *,
    config_dir: Path,
    allow_writes: bool,
    shell_commands: tuple[str, ...] = (),
    workdir: Path | None = None,
    mcp_servers: Mapping[str, Mapping[str, Any]] | None = None,
    mcp_tools: Mapping[str, tuple[str, ...]] | None = None,
) -> dict[str, str]:
    """Return an isolated, deterministic OpenCode subprocess environment.

    Provider credentials remain available through the caller environment and
    OpenCode's normal data directory. Runtime configuration does not: Alfred's
    inline config is the last ordinary config layer, and ``--pure`` disables
    external plugins. System-managed OpenCode settings retain final authority.
    """

    local_mcp = _local_mcp_config(mcp_servers or {}, workdir=workdir)
    allowed_mcp_tools = {
        name: tuple(mcp_tools.get(name, ())) for name in local_mcp if mcp_tools is not None
    }
    permissions = _permission_policy(
        allow_writes=allow_writes,
        shell_commands=shell_commands,
        mcp_tools=allowed_mcp_tools,
    )
    config = {
        "$schema": "https://opencode.ai/config.json",
        "share": "disabled",
        "permission": permissions,
        "agent": {
            "alfred": {
                "description": "Run one Alfred firing in the selected worktree.",
                "mode": "primary",
                "permission": permissions,
            }
        },
    }
    if local_mcp:
        config["mcp"] = local_mcp
    child = dict(environ)
    child.pop("OPENCODE_CONFIG", None)
    child.update(
        {
            "NO_COLOR": "1",
            "OPENCODE_AUTO_SHARE": "false",
            "OPENCODE_CONFIG_DIR": str(config_dir),
            "OPENCODE_CONFIG_CONTENT": json.dumps(config, separators=(",", ":")),
            "OPENCODE_DISABLE_AUTOUPDATE": "1",
            "OPENCODE_DISABLE_CLAUDE_CODE": "1",
            "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
            "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1",
            "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
            "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
            "OPENCODE_DISABLE_TERMINAL_TITLE": "1",
            "XDG_CONFIG_HOME": str(config_dir),
        }
    )
    return child


def parse_opencode_mcp_status(
    output: str,
    expected_servers: tuple[str, ...],
) -> dict[str, str]:
    """Return scrubbed startup states for Alfred's expected MCP servers."""

    clean = _ANSI_ESCAPE_RE.sub("", output or "")
    states: dict[str, str] = {}
    for server in expected_servers:
        match = re.search(
            rf"(?:^|\s){re.escape(server)}\s+(connected|failed|disabled)(?:\s|$)",
            clean,
            flags=re.MULTILINE,
        )
        states[server] = match.group(1) if match else "unreported"
    return states


def _message(error: object) -> str:
    if isinstance(error, Mapping):
        data = error.get("data")
        if isinstance(data, Mapping):
            value = data.get("message")
            if isinstance(value, str) and value.strip():
                return value.strip()[:2000]
        value = error.get("message")
        if isinstance(value, str) and value.strip():
            return value.strip()[:2000]
        value = error.get("name")
        if isinstance(value, str) and value.strip():
            return value.strip()[:2000]
    if isinstance(error, str):
        return error.strip()[:2000]
    return ""


def _token_total(tokens: object) -> int:
    if not isinstance(tokens, Mapping):
        return 0
    total = 0
    for key in ("input", "output", "reasoning", "cache_read", "cache_write"):
        value = tokens.get(key)
        if isinstance(value, int) and value > 0:
            total += value
    cache = tokens.get("cache")
    if isinstance(cache, Mapping):
        for value in cache.values():
            if isinstance(value, int) and value > 0:
                total += value
    return total


def parse_opencode_events(output: str) -> OpenCodeEvents:
    """Parse OpenCode's NDJSON stream and reject partial or malformed output."""

    texts: list[str] = []
    session_id: str | None = None
    tokens_used = 0
    cost_usd = 0.0
    error: str | None = None
    tool_error: str | None = None
    malformed = False
    count = 0

    for raw_line in (output or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            malformed = True
            continue
        if not isinstance(event, Mapping):
            malformed = True
            continue
        count += 1
        raw_session = event.get("sessionID")
        if isinstance(raw_session, str) and raw_session.strip():
            if session_id is not None and session_id != raw_session:
                malformed = True
            else:
                session_id = raw_session
        event_type = event.get("type")
        part = event.get("part")
        if event_type == "text" and isinstance(part, Mapping):
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())
        elif event_type == "step_finish" and isinstance(part, Mapping):
            tokens_used += _token_total(part.get("tokens"))
            cost = part.get("cost")
            if isinstance(cost, (int, float)) and cost >= 0:
                cost_usd += float(cost)
        elif event_type == "error":
            error = _message(event.get("error")) or "OpenCode reported an unknown error."
        elif event_type == "tool_use" and isinstance(part, Mapping):
            state = part.get("state")
            if isinstance(state, Mapping) and state.get("status") == "error":
                tool_error = _message(state.get("error")) or "OpenCode tool execution failed."

    return OpenCodeEvents(
        text="\n\n".join(texts),
        session_id=session_id,
        tokens_used=tokens_used,
        cost_usd=cost_usd,
        error=error,
        tool_error=tool_error,
        parse_error="OpenCode emitted malformed JSON events." if malformed else None,
        event_count=count,
    )


__all__ = [
    "OpenCodeEvents",
    "build_opencode_command",
    "opencode_environment",
    "parse_opencode_events",
    "parse_opencode_mcp_status",
]
