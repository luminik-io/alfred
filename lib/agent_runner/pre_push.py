"""Shared per-repo pre-push command resolution.

Senior-dev and fixer both read per-repo pre-push commands from
``$ALFRED_HOME/agents/<codename>.toml`` and fall back to the same
language defaults (gradle for backend/api repos, a Python lint+type+test
chain for repos with a ``pyproject.toml``). They only diverge on how a Node
repo's default command is derived, so that one step is injected via
``node_default`` and everything else is shared here as one pure function.

test-engineer intentionally does NOT use this loader: it reads a different
TOML schema (a ``[repos]`` table of per-repo ``{pre_push, coverage_hint}``
entries) and returns a ``dict[str, dict[str, str]]``, so it keeps its own
``_load_repo_config``.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable, Iterable
from pathlib import Path

# Python repos (identified by a ``pyproject.toml``) get the same default
# lint + typecheck + test chain in both callers.
PYTHON_PRE_PUSH_DEFAULT = "uv run ruff check . && uv run mypy . && uv run pytest"

# Backend / API repos build with gradle in both callers.
BACKEND_PRE_PUSH_DEFAULT = "./gradlew check"


def load_pre_push_config(
    *,
    agent_codename: str,
    repos: Iterable[str],
    alfred_home: Path,
    workspace: Path,
    local_repo_dir: Callable[[str], str],
    node_default: Callable[[str, Path], str],
) -> dict[str, str]:
    """Resolve the per-repo pre-push command map for ``agent_codename``.

    Operator overrides come from the ``[pre_push]`` table in
    ``<alfred_home>/agents/<agent_codename>.toml``; a malformed or missing
    file falls back silently to inferred defaults. For each repo the order is:

    1. explicit operator override, else
    2. ``./gradlew check`` for ``*-backend`` / ``*-api`` repos, else
    3. ``node_default(repo, local_dir)`` when it returns a non-empty command,
       else
    4. the Python default when the checkout has a ``pyproject.toml``, else
    5. ``""`` (no pre-push; the agent reports that in its PR body).

    Pure: all environment (home, workspace, repo-dir resolver, node default)
    is injected so it is trivially unit-testable and shared without importing
    caller module state.
    """
    cfg_path = alfred_home / "agents" / f"{agent_codename}.toml"
    user_cfg: dict[str, str] = {}
    if cfg_path.exists():
        try:
            data = tomllib.loads(cfg_path.read_text())
            user_cfg = dict(data.get("pre_push", {}) or {})
        except (OSError, tomllib.TOMLDecodeError):
            user_cfg = {}

    out: dict[str, str] = {}
    for repo in repos:
        if repo in user_cfg:
            out[repo] = user_cfg[repo]
            continue
        if repo.endswith("-backend") or repo.endswith("-api"):
            out[repo] = BACKEND_PRE_PUSH_DEFAULT
            continue
        local_dir = workspace / local_repo_dir(repo)
        node_cmd = node_default(repo, local_dir)
        if node_cmd:
            out[repo] = node_cmd
            continue
        if (local_dir / "pyproject.toml").exists():
            out[repo] = PYTHON_PRE_PUSH_DEFAULT
        else:
            out[repo] = ""
    return out
