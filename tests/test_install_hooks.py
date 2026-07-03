"""Coverage for the pre-push hook installer.

Drives ``bin/alfred-install-hooks.sh`` against throwaway git checkouts in a
tmp workspace, asserting the symlink install is idempotent and backs up an
existing hook.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "bin" / "alfred-install-hooks.sh"
_HOOK_SOURCE = _ROOT / "examples" / "git-hooks" / "pre-push"


def _run(workspace: Path, *args: str) -> subprocess.CompletedProcess:
    env = {
        "WORKSPACE_ROOT": str(workspace),
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        "HOME": str(workspace),
    }
    return subprocess.run(
        ["bash", str(_SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
    )


def _make_repo(workspace: Path, name: str) -> Path:
    repo = workspace / name
    (repo / ".git" / "hooks").mkdir(parents=True)
    return repo


def test_hook_source_ships_in_repo():
    assert _HOOK_SOURCE.is_file()


def test_installs_symlink_into_repo(tmp_path):
    ws = tmp_path / "code"
    ws.mkdir()
    repo = _make_repo(ws, "your-backend")

    result = _run(ws, "--repo", "your-backend")
    assert result.returncode == 0, result.stderr

    hook = repo / ".git" / "hooks" / "pre-push"
    assert hook.is_symlink()
    assert hook.resolve() == _HOOK_SOURCE.resolve()


def test_install_is_idempotent(tmp_path):
    ws = tmp_path / "code"
    ws.mkdir()
    _make_repo(ws, "your-backend")

    first = _run(ws, "--repo", "your-backend")
    second = _run(ws, "--repo", "your-backend")
    assert first.returncode == 0 and second.returncode == 0
    assert "already linked to canonical hook" in second.stdout


def test_backs_up_existing_hook(tmp_path):
    ws = tmp_path / "code"
    ws.mkdir()
    repo = _make_repo(ws, "your-backend")
    existing = repo / ".git" / "hooks" / "pre-push"
    existing.write_text("#!/bin/sh\necho custom\n")

    result = _run(ws, "--repo", "your-backend")
    assert result.returncode == 0, result.stderr
    assert "backed up existing pre-push" in result.stdout
    backups = list((repo / ".git" / "hooks").glob("pre-push.bak.*"))
    assert len(backups) == 1
    assert backups[0].read_text() == "#!/bin/sh\necho custom\n"


def test_errors_when_no_repos_found(tmp_path):
    ws = tmp_path / "code"
    ws.mkdir()  # empty workspace, no git repos
    result = _run(ws)
    assert result.returncode != 0
    assert "no target repos found" in result.stderr
