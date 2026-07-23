"""Shared runner capability inventory for CLI and server surfaces."""

from __future__ import annotations

ENGINE_AGENT_CODENAMES: tuple[str, ...] = (
    "architect",
    "fixer",
    "planner",
    "reviewer",
    "senior-dev",
    "test-engineer",
    "triage",
)
CUSTOM_AGENT_SCRIPT = "custom-agent.py"
BUILTIN_ENGINE_SCRIPTS: frozenset[str] = frozenset(
    f"{agent}.py" for agent in ENGINE_AGENT_CODENAMES
)
ENGINE_AGENT_SCRIPTS: frozenset[str] = frozenset(
    (*BUILTIN_ENGINE_SCRIPTS, CUSTOM_AGENT_SCRIPT)
)
