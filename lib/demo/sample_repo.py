"""Materialize the bundled sample project into a throwaway git repo.

``alfred demo`` never touches the operator's own code. It copies the small
``examples/demo-repo`` project (the ``textkit`` string library) into a temp
directory and initializes a real git repo there, so the build step can run
in a real worktree and the ship step can produce a real diff and merge.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

# examples/demo-repo lives two levels up from lib/demo/sample_repo.py:
#   lib/demo/sample_repo.py -> lib/demo -> lib -> <repo root>
SAMPLE_REPO_DIR: Path = Path(__file__).resolve().parents[2] / "examples" / "demo-repo"


def _git(args: list[str], *, cwd: Path) -> None:
    """Run a git command in ``cwd`` and raise with context on failure."""
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")


def materialize_sample_repo(dest: Path, *, source: Path = SAMPLE_REPO_DIR) -> Path:
    """Copy the sample project into ``dest`` and make it a git repo.

    Returns the path to the initialized working copy. The commit identity
    is set locally (repo-scoped, never global) so the demo works on a host
    with no configured git user and never mutates the operator's config.
    """
    if not source.is_dir():
        raise FileNotFoundError(f"sample repo not found at {source}")

    dest.mkdir(parents=True, exist_ok=True)
    for item in sorted(source.iterdir()):
        if item.name == ".git":
            continue
        target = dest / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)

    _git(["init", "--quiet", "--initial-branch", "main"], cwd=dest)
    # Isolate the throwaway repo from the host's global git config: point
    # core.hooksPath at a repo-local (empty) directory so global pre-commit /
    # identity-guard hooks never fire on demo commits, and set a local commit
    # identity so a host with no configured git user still works. Both are
    # repo-scoped and never touch the operator's global config.
    hooks_dir = dest / ".git" / "demo-empty-hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    _git(["config", "core.hooksPath", str(hooks_dir)], cwd=dest)
    _git(["config", "user.name", "Alfred Demo"], cwd=dest)
    _git(["config", "user.email", "demo@example.com"], cwd=dest)
    # Keep the demo self-contained: never sign the throwaway commit, even if
    # the host has commit.gpgsign=true configured globally.
    _git(["config", "commit.gpgsign", "false"], cwd=dest)
    _git(["add", "-A"], cwd=dest)
    _git(["commit", "--quiet", "-m", "Initial textkit snapshot"], cwd=dest)
    return dest
