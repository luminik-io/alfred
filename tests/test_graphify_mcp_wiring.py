#!/usr/bin/env python3
"""graphify is an opt-in, mutually-exclusive alternative to codebase-memory-mcp.

These tests pin the attach behaviour: off by default, takes the code-graph slot
when ALFRED_GRAPHIFY_MCP is set (and code-memory is then NOT attached), and its
read-only tools land in the allowlist. No Claude invocation, no graphify install.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_LIB = Path(__file__).resolve().parent.parent / "lib"
sys.path.insert(0, str(_LIB))

import agent_runner  # noqa: E402
from agent_runner import process as _proc  # noqa: E402

_OK = (
    '{"type":"result","subtype":"success","is_error":false,'
    '"stop_reason":"end_turn","num_turns":1,"total_cost_usd":0,"result":""}'
)


def _capture(monkeypatch, *, graphify_present: bool = True) -> list[str]:
    monkeypatch.delenv("ALFRED_DRY_RUN", raising=False)
    monkeypatch.setattr(_proc, "_memory_mcp_script", lambda: Path("/repo/bin/alfred-mcp.py"))
    monkeypatch.setattr(_proc, "_code_memory_launcher", lambda: Path("/repo/bin/code-memory-mcp"))
    # Pretend graphify-mcp is (or is not) on PATH without touching the host.
    real_which = _proc.shutil.which

    def fake_which(name):
        if name == "graphify-mcp":
            return "/usr/local/bin/graphify-mcp" if graphify_present else None
        return real_which(name)

    monkeypatch.setattr(_proc.shutil, "which", fake_which)
    captured: dict = {}

    def fake_run(cmd, *, cwd=None, timeout=60, capture=True, env=None, **kwargs):
        captured["cmd"] = list(cmd)
        return SimpleNamespace(returncode=0, stdout=_OK, stderr="")

    with mock.patch.dict(agent_runner.claude_invoke.__globals__, {"run": fake_run}):
        agent_runner.claude_invoke(
            prompt="hi", workdir=Path("/tmp"), allowed_tools="Read,Bash", max_turns=None, timeout=10
        )
    return captured["cmd"]


def _mcp_config(cmd: list[str]) -> dict | None:
    if "--mcp-config" not in cmd:
        return None
    return json.loads(cmd[cmd.index("--mcp-config") + 1])


def _allowed(cmd: list[str]) -> str:
    return cmd[cmd.index("--allowedTools") + 1]


def test_graphify_off_by_default_keeps_code_memory(monkeypatch) -> None:
    monkeypatch.delenv("ALFRED_GRAPHIFY_MCP", raising=False)
    monkeypatch.delenv("ALFRED_CODE_MEMORY_MCP", raising=False)
    cfg = _mcp_config(_capture(monkeypatch))
    assert cfg is not None
    assert "code_memory" in cfg["mcpServers"]
    assert "graphify" not in cfg["mcpServers"]


def test_graphify_enabled_takes_code_graph_slot(monkeypatch) -> None:
    monkeypatch.setenv("ALFRED_GRAPHIFY_MCP", "1")
    monkeypatch.delenv("ALFRED_CODE_MEMORY_MCP", raising=False)
    cmd = _capture(monkeypatch)
    cfg = _mcp_config(cmd)
    assert cfg is not None
    servers = cfg["mcpServers"]
    # graphify present, code-memory NOT attached (mutual exclusion).
    assert "graphify" in servers
    assert "code_memory" not in servers
    assert servers["graphify"]["command"] == "graphify-mcp"
    assert servers["graphify"]["args"] == ["--transport", "stdio"]
    allowed = _allowed(cmd)
    for name in _proc._graphify_tool_names():
        assert name in allowed, f"{name} missing from allowlist"
    for name in _proc._code_memory_tool_names():
        assert name not in allowed


def test_graphify_enabled_but_missing_binary_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("ALFRED_GRAPHIFY_MCP", "1")
    monkeypatch.delenv("ALFRED_CODE_MEMORY_MCP", raising=False)
    cfg = _mcp_config(_capture(monkeypatch, graphify_present=False))
    assert cfg is not None
    # No graphify on PATH -> code-memory keeps the slot rather than nothing.
    assert "graphify" not in cfg["mcpServers"]
    assert "code_memory" in cfg["mcpServers"]


def test_graphify_tool_names_use_server_prefix() -> None:
    names = _proc._graphify_tool_names()
    assert "mcp__graphify__query_graph" in names
    assert "mcp__graphify__shortest_path" in names
    assert all(n.startswith("mcp__graphify__") for n in names)


if __name__ == "__main__":
    import pytest as _pytest

    sys.exit(_pytest.main([__file__, "-v"]))
