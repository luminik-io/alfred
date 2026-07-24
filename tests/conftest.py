"""Shared test isolation for repository-wide pytest runs."""

from __future__ import annotations

import os

import pytest

_OPERATOR_ENV_NAMES = (
    "ALFREDRC",
    "ALFRED_CODE_MEMORY_AUTOFETCH",
    "ALFRED_CODE_MEMORY_MCP",
    "ALFRED_GRAPHIFY_FALLBACK",
    "ALFRED_GRAPHIFY_MCP",
    "ALFRED_REPO_LOCAL_MAP",
    "ARCHITECT_PARENT_REPO",
    "ARCHITECT_ROLLOUT_ORDER",
    "SLACK_APPROVER_USER_ID",
)


def _operator_env_names() -> tuple[str, ...]:
    wildcard_names = tuple(
        name
        for name in tuple(os.environ)
        if name == "AGENT_CODENAME" or (name.startswith("ALFRED_") and name.endswith("_REPOS"))
    )
    return (*wildcard_names, *_OPERATOR_ENV_NAMES)


def _scrub_operator_env() -> None:
    for name in _operator_env_names():
        os.environ.pop(name, None)


_scrub_operator_env()


@pytest.fixture(autouse=True)
def isolate_external_operator_env(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """Tests that need live operator env values set them explicitly."""

    for name in _operator_env_names():
        monkeypatch.delenv(name, raising=False)

    # The Slack intent router defaults ON in production (Slack is Alfred's
    # default interface), so a listener built without an injected engine will
    # otherwise resolve a LIVE engine invoker and spawn a real ``claude`` /
    # ``codex`` subprocess. On a developer box where those binaries exist the
    # call blocks indefinitely and hangs the suite. Default the router OFF for
    # tests; the router's own tests still opt in explicitly by both setting
    # ``ALFRED_INTENT_ROUTER_ENABLED=1`` and injecting a stub engine (an
    # injected engine takes precedence over this env either way).
    monkeypatch.setenv("ALFRED_INTENT_ROUTER_ENABLED", "0")

    # Slack converse likewise defaults ON in production and engages whenever a
    # converse engine resolves from the environment. A converse engine key that
    # leaks in from an earlier test (``ALFRED_SLACK_CONVERSE_ENGINE`` or its
    # ``ALFRED_COMPOSE_CONVERSE_ENGINE`` fallback) would make the listener take
    # the converse path ahead of the intent router, flaking tests by their run
    # order. Pin converse OFF per test; converse's own tests inject a runner or
    # set these explicitly.
    monkeypatch.setenv("ALFRED_SLACK_CONVERSE_ENABLED", "0")
    monkeypatch.delenv("ALFRED_SLACK_CONVERSE_ENGINE", raising=False)
    monkeypatch.delenv("ALFRED_COMPOSE_CONVERSE_ENGINE", raising=False)

    raw_adapter_modules = {
        "test_agent_notifications.py",
        "test_claude_max_turns_default.py",
        "test_code_memory_mcp_wiring.py",
        "test_graphify_mcp_wiring.py",
        "test_memory_mcp_wiring.py",
    }
    if request.node.path.name in raw_adapter_modules:
        # These modules replace the work subprocess and exercise command,
        # timeout, and MCP wiring behavior. Give them an explicit ready engine
        # boundary so clean CI does not depend on installed CLIs or credentials.
        import agent_runner
        import agent_runner.process as process

        def ready_probe(engine: str):
            descriptor = agent_runner.DEFAULT_ENGINE_REGISTRY.descriptor(engine)
            return agent_runner.EngineProbeResult(
                descriptor=descriptor,
                installed=True,
                protocol_compatible=True,
                ready=True,
                state="ready",
                detail="ready",
                binary=descriptor.default_binary,
                version="test",
            )

        monkeypatch.setattr(process, "_probe_dispatch_engine", ready_probe)
