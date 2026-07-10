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


def _capture(
    monkeypatch,
    tmp_path: Path,
    *,
    graphify_present: bool = True,
    graph_present: bool = True,
) -> list[str]:
    monkeypatch.delenv("ALFRED_DRY_RUN", raising=False)
    monkeypatch.setattr(_proc, "_memory_mcp_script", lambda: Path("/repo/bin/alfred-mcp.py"))
    monkeypatch.setattr(_proc, "_code_memory_launcher", lambda: Path("/repo/bin/code-memory-mcp"))
    # Pretend graphify-mcp is (or is not) on PATH without touching the host.
    real_which = _proc.shutil.which

    def fake_which(name):
        if name == "graphify-mcp":
            return "/usr/local/bin/graphify-mcp" if graphify_present else None
        if name == "uvx":
            return None
        return real_which(name)

    monkeypatch.setattr(_proc.shutil, "which", fake_which)
    monkeypatch.setattr(
        _proc,
        "_graphify_entrypoint_works",
        lambda command: graphify_present,
    )
    if graph_present:
        graph = tmp_path / "graphify-out" / "graph.json"
        graph.parent.mkdir(parents=True, exist_ok=True)
        graph.write_text('{"nodes": [], "links": []}', encoding="utf-8")
    captured: dict = {}

    def fake_run(cmd, *, cwd=None, timeout=60, capture=True, env=None, **kwargs):
        captured["cmd"] = list(cmd)
        return SimpleNamespace(returncode=0, stdout=_OK, stderr="")

    with mock.patch.dict(agent_runner.claude_invoke.__globals__, {"run": fake_run}):
        agent_runner.claude_invoke(
            prompt="hi", workdir=tmp_path, allowed_tools="Read,Bash", max_turns=None, timeout=10
        )
    return captured["cmd"]


def _mcp_config(cmd: list[str]) -> dict | None:
    if "--mcp-config" not in cmd:
        return None
    return json.loads(cmd[cmd.index("--mcp-config") + 1])


def _allowed(cmd: list[str]) -> str:
    return cmd[cmd.index("--allowedTools") + 1]


def test_graphify_off_by_default_keeps_code_memory(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ALFRED_GRAPHIFY_MCP", raising=False)
    monkeypatch.delenv("ALFRED_CODE_MEMORY_MCP", raising=False)
    cfg = _mcp_config(_capture(monkeypatch, tmp_path))
    assert cfg is not None
    assert "code_memory" in cfg["mcpServers"]
    assert "graphify" not in cfg["mcpServers"]


def test_graphify_enabled_takes_code_graph_slot(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALFRED_GRAPHIFY_MCP", "1")
    monkeypatch.delenv("ALFRED_CODE_MEMORY_MCP", raising=False)
    cmd = _capture(monkeypatch, tmp_path)
    cfg = _mcp_config(cmd)
    assert cfg is not None
    servers = cfg["mcpServers"]
    # graphify present, code-memory NOT attached (mutual exclusion).
    assert "graphify" in servers
    assert "code_memory" not in servers
    assert servers["graphify"]["command"] == "/usr/local/bin/graphify-mcp"
    assert servers["graphify"]["args"] == [
        str(tmp_path / "graphify-out" / "graph.json"),
        "--transport",
        "stdio",
    ]
    allowed = _allowed(cmd)
    for name in _proc._graphify_tool_names():
        assert name in allowed, f"{name} missing from allowlist"
    for name in _proc._code_memory_tool_names():
        assert name not in allowed


def test_graphify_enabled_but_missing_binary_falls_back(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALFRED_GRAPHIFY_MCP", "1")
    monkeypatch.delenv("ALFRED_CODE_MEMORY_MCP", raising=False)
    cfg = _mcp_config(_capture(monkeypatch, tmp_path, graphify_present=False))
    assert cfg is not None
    # No graphify on PATH -> code-memory keeps the slot rather than nothing.
    assert "graphify" not in cfg["mcpServers"]
    assert "code_memory" in cfg["mcpServers"]


def test_graphify_enabled_without_repo_graph_falls_back(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALFRED_GRAPHIFY_MCP", "1")
    monkeypatch.setenv("ALFRED_CODE_MEMORY_MCP", "0")
    cfg = _mcp_config(_capture(monkeypatch, tmp_path, graph_present=False))
    assert cfg is not None
    assert "graphify" not in cfg["mcpServers"]
    assert "code_memory" in cfg["mcpServers"]


def test_graphify_prefers_a_verified_installed_entrypoint_over_uvx(monkeypatch) -> None:
    monkeypatch.delenv("ALFRED_GRAPHIFY_BIN", raising=False)
    monkeypatch.setattr(
        _proc.shutil,
        "which",
        lambda name: f"/usr/local/bin/{name}" if name in {"graphify-mcp", "uvx"} else None,
    )
    monkeypatch.setattr(_proc, "_graphify_entrypoint_works", lambda command: True)

    assert _proc._graphify_command() == ("/usr/local/bin/graphify-mcp", [])


def test_graphify_uses_pinned_uvx_fallback_and_explicit_graph(monkeypatch, tmp_path: Path) -> None:
    graph = tmp_path / "graphify-out" / "graph.json"
    graph.parent.mkdir(parents=True)
    graph.write_text('{"nodes": [], "links": []}', encoding="utf-8")
    monkeypatch.setenv("ALFRED_GRAPHIFY_MCP", "1")
    monkeypatch.setenv("ALFRED_GRAPHIFY_GRAPH", str(graph))
    monkeypatch.setattr(
        _proc.shutil,
        "which",
        lambda name: "/usr/local/bin/uvx" if name == "uvx" else None,
    )

    server = _proc._graphify_mcp_server()

    assert server == {
        "graphify": {
            "command": "/usr/local/bin/uvx",
            "args": [
                "--from",
                "graphifyy[mcp]==0.9.8",
                "graphify-mcp",
                str(graph),
                "--transport",
                "stdio",
            ],
        }
    }


def test_graphify_expands_home_relative_graph_path(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    graph = home / "graphs" / "repo.json"
    graph.parent.mkdir(parents=True)
    graph.write_text('{"nodes": [], "links": []}', encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ALFRED_GRAPHIFY_MCP", "1")
    monkeypatch.setenv("ALFRED_GRAPHIFY_GRAPH", "~/graphs/repo.json")
    monkeypatch.setattr(
        _proc.shutil,
        "which",
        lambda name: "/usr/local/bin/uvx" if name == "uvx" else None,
    )

    server = _proc._graphify_mcp_server(tmp_path)

    assert server is not None
    assert server["graphify"]["args"][3] == str(graph)


def test_graphify_tool_names_use_server_prefix() -> None:
    names = _proc._graphify_tool_names()
    assert "mcp__graphify__query_graph" in names
    assert "mcp__graphify__shortest_path" in names
    assert all(n.startswith("mcp__graphify__") for n in names)


if __name__ == "__main__":
    import pytest as _pytest

    sys.exit(_pytest.main([__file__, "-v"]))
