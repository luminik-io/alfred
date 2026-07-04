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


def _resolve_sample_repo_dir() -> Path:
    """Locate the bundled ``examples/demo-repo`` across all install layouts.

    Three layouts are supported, mirroring how :mod:`skill_packs` resolves the
    curated skill registry so the demo works everywhere the CLI does, not just
    in a source checkout:

    * Source checkout: ``lib/demo/sample_repo.py`` -> repo root ->
      ``examples/demo-repo``.
    * Deployed runtime: ``deploy.sh`` copies ``lib/`` to ``$ALFRED_HOME/lib``
      and ``examples/`` to ``$ALFRED_HOME/examples``, so the same
      root-relative walk resolves (``$ALFRED_HOME/lib/demo/sample_repo.py`` ->
      ``$ALFRED_HOME/examples/demo-repo``).
    * Installed wheel: ``sources = ["lib"]`` flattens ``lib`` to the wheel
      root and ``force-include`` packages the sample as
      ``demo/examples/demo-repo``, a sibling tree of this module.

    The packaged sibling is checked first (it only exists in a wheel), then the
    repo-root layout used by a source checkout and a deployed runtime. The
    first location that actually exists wins; the root layout is the fallback
    so a fresh checkout still resolves.
    """
    here = Path(__file__).resolve().parent
    packaged = here / "examples" / "demo-repo"
    if packaged.is_dir():
        return packaged
    return here.parents[1] / "examples" / "demo-repo"


SAMPLE_REPO_DIR: Path = _resolve_sample_repo_dir()


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
