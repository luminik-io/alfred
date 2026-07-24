"""Stable facade over ``agent_runner`` internals for the server (UI) layer.

The dashboard/API layer needs a few runtime primitives, the prompt loader, the
product workspace root, and the GitHub-slug to local-path map, that live in the
``agent_runner`` runtime package. Importing those modules directly from
``server/*`` couples the localhost UI to runtime internals and their import
paths, which the 2026-07-11 engineering audit flagged.

This module is the ONE place ``server/*`` reaches into ``agent_runner``.
Everything else under ``server/`` imports the accessors here instead of the
runtime modules. ``tests/test_server_runtime_facade_boundary.py`` enforces that
with a grep-style lint: a new ``from agent_runner.`` import anywhere under
``lib/server/`` except this file fails the test.

Each accessor degrades exactly the way the inline call sites used to: it returns
a safe fallback (or ``None`` for the loader) when the runtime package is not
importable, so the localhost UI keeps working on a bare checkout without the
private fleet mounted.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

# A prompt loader reads a prompt file and renders its ``${VAR}`` placeholders.
# Signature: ``(path, *, extra_vars=None) -> str`` (see
# ``agent_runner.metadata.load_prompt``).
PromptLoader = Callable[..., str]


def prompt_loader() -> PromptLoader | None:
    """Return ``agent_runner``'s prompt loader, or ``None`` when unavailable.

    Callers pass the returned callable as ``loader=`` to a ``render_system_prompt``
    helper. ``None`` signals the runtime package is not importable so the caller
    can degrade (the converse routes answer ``503`` and fall back to the manual
    form), matching the previous inline ``try/except ImportError`` behavior.
    """
    try:
        from agent_runner.metadata import load_prompt
    except Exception:  # pragma: no cover - defensive: loader is always importable
        return None
    return load_prompt


def workspace_root() -> Path:
    """Return the product workspace root (``agent_runner.paths.WORKSPACE``).

    Falls back to ``$WORKSPACE_ROOT/product`` (or ``~/code/product``) when the
    runtime package is not importable, so Compose grounding still resolves repo
    checkouts on a bare install.
    """
    try:
        from agent_runner.paths import WORKSPACE

        return Path(WORKSPACE)
    except Exception:  # pragma: no cover - defensive
        base = os.environ.get("WORKSPACE_ROOT") or os.path.expanduser("~/code")
        return Path(base) / "product"


def repo_to_local() -> dict[str, str]:
    """Return the live GitHub-slug to local-path map.

    Reads through the runtime accessor so checkout changes saved during desktop
    onboarding are visible to native Ask immediately. Returns an empty map when
    the runtime package is not importable.
    """
    try:
        from agent_runner.github import repo_to_local_map
        from agent_runner.paths import launcher_env

        return repo_to_local_map(launcher_env())
    except Exception:  # pragma: no cover - defensive
        return {}


def model_providers() -> frozenset[str]:
    """Return the provider names accepted by the runtime model router."""

    from agent_runner.config import MODEL_ENGINES

    return MODEL_ENGINES


def model_selection(agent: str, provider: str, *, state_root: Path) -> dict[str, str | None]:
    """Return the resolved and persisted model state for one provider."""

    from agent_runner.config import agent_model_selection

    selection = agent_model_selection(agent, provider, state_root=state_root)
    return {
        "resolved": selection.model,
        "persisted": selection.persisted,
        "source": selection.source,
    }


def save_agent_model(agent: str, provider: str, model: str, *, state_root: Path) -> None:
    """Persist one validated per-agent provider model."""

    from agent_runner.config import persist_agent_model

    persist_agent_model(agent, provider, model, state_root=state_root)


def clear_agent_model(agent: str, provider: str, *, state_root: Path) -> None:
    """Clear one persisted per-agent provider model."""

    from agent_runner.config import clear_agent_model as clear

    clear(agent, provider, state_root=state_root)
