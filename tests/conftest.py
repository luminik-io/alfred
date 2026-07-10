"""Shared test isolation for repository-wide pytest runs."""

from __future__ import annotations

import os

import pytest

_OPERATOR_ENV_NAMES = (
    "ALFREDRC",
    "ALFRED_REPO_LOCAL_MAP",
    "ARCHITECT_ROLLOUT_ORDER",
    "BATMAN_ROLLOUT_ORDER",
    "SLACK_APPROVER_USER_ID",
)


def _operator_env_names() -> tuple[str, ...]:
    wildcard_names = tuple(
        name
        for name in tuple(os.environ)
        if (
            name == "AGENT_CODENAME"
            or name.startswith("AGENT_CODENAME_")
            or (name.startswith("ALFRED_") and name.endswith("_REPOS"))
        )
    )
    return (*wildcard_names, *_OPERATOR_ENV_NAMES)


def _scrub_operator_env() -> None:
    for name in _operator_env_names():
        os.environ.pop(name, None)


_scrub_operator_env()


@pytest.fixture(autouse=True)
def isolate_external_operator_env(monkeypatch: pytest.MonkeyPatch) -> None:
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
