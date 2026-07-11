"""Coverage for the one-command remote installer ``get.sh``.

Drives ``get.sh`` under ``/bin/sh`` in a throwaway ``HOME`` with a stubbed
``PATH``. No network: ``git`` is a stub that fakes ``clone``/``pull`` locally,
so the test asserts the preflight messages, the clone, the demo-first next
steps, and idempotent re-runs without ever reaching GitHub.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "get.sh"
_SH = shutil.which("sh") or "/bin/sh"

# The real coreutils get.sh shells out to. We symlink exactly these into the
# controlled bin so the test PATH holds nothing else: a tool the harness does
# not stub (e.g. git in the missing-git case) is genuinely absent, not leaked
# in from /usr/bin.
_COREUTILS = ("uname", "awk", "ls", "head", "mkdir", "chmod", "cat", "printf", "env")


def _write_stub(directory: Path, name: str, body: str) -> None:
    stub = directory / name
    stub.write_text("#!/bin/sh\n" + body)
    stub.chmod(0o755)


def _make_stub_bin(directory: Path, *, git=True, python=True, claude=True, gh=True) -> Path:
    """Build a fully controlled bin dir: curated coreutils plus tool stubs."""
    directory.mkdir(parents=True, exist_ok=True)

    for tool in _COREUTILS:
        real = shutil.which(tool)
        if real:
            (directory / tool).symlink_to(real)

    if git:
        # Fakes `git --version`, `git clone ... <dest>` (creates a checkout
        # skeleton locally), and `git -C <dir> pull` (no-op success).
        _write_stub(
            directory,
            "git",
            'case "$1" in\n'
            '  --version) echo "git version 2.44.0" ;;\n'
            "  clone)\n"
            '    for a in "$@"; do dest="$a"; done\n'
            '    mkdir -p "$dest/.git" "$dest/bin"\n'
            '    printf "#!/usr/bin/env python3\\n" > "$dest/bin/alfred"\n'
            '    chmod +x "$dest/bin/alfred"\n'
            "    ;;\n"
            "  -C) exit 0 ;;\n"
            "  *) exit 0 ;;\n"
            "esac\n",
        )
    if python:
        _write_stub(directory, "python3.11", 'echo "Python 3.11.9"\n')
        _write_stub(directory, "python3", 'echo "Python 3.11.9"\n')
    if claude:
        _write_stub(directory, "claude", "exit 0\n")
    if gh:
        _write_stub(directory, "gh", 'echo "gh version 2.50.0 (2024-01-01)"\n')
    return directory


def _run(home: Path, stub_bin: Path, checkout: Path) -> subprocess.CompletedProcess:
    env = {
        "HOME": str(home),
        "PATH": str(stub_bin),
        "ALFRED_CHECKOUT": str(checkout),
        "ALFRED_REPO_URL": "https://example.invalid/alfred.git",
    }
    return subprocess.run(
        [_SH, str(_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
    )


def test_script_ships_executable():
    assert _SCRIPT.is_file()
    assert os.access(_SCRIPT, os.X_OK)


def test_happy_path_preflight_and_clone(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    stub_bin = _make_stub_bin(tmp_path / "bin")
    checkout = tmp_path / "alfred"

    result = _run(home, stub_bin, checkout)
    assert result.returncode == 0, result.stderr

    out = result.stdout
    # Preflight reported every tool.
    assert "git 2.44.0" in out
    assert "python3.11 3.11.9" in out
    assert "claude CLI on PATH" in out
    assert "gh 2.50.0" in out
    # Cloned into the chosen checkout.
    assert "cloned into" in out
    assert (checkout / ".git").is_dir()
    # Demo-first next steps, not the heavy install.
    assert "./bin/alfred demo" in out
    assert str(checkout) in out


def test_rerun_is_idempotent(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    stub_bin = _make_stub_bin(tmp_path / "bin")
    checkout = tmp_path / "alfred"

    first = _run(home, stub_bin, checkout)
    second = _run(home, stub_bin, checkout)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert "cloned into" in first.stdout
    # Second run finds the checkout and updates instead of cloning again.
    assert "existing checkout found, updating" in second.stdout
    assert "cloned into" not in second.stdout


def test_missing_coding_cli_fails_with_guidance(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    stub_bin = _make_stub_bin(tmp_path / "bin", claude=False)
    checkout = tmp_path / "alfred"

    result = _run(home, stub_bin, checkout)
    assert result.returncode != 0
    assert "No coding CLI found" in result.stderr
    assert "@anthropic-ai/claude-code" in result.stderr
    # It must stop before cloning.
    assert not (checkout / ".git").exists()


def test_missing_git_fails_with_guidance(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    stub_bin = _make_stub_bin(tmp_path / "bin", git=False)
    checkout = tmp_path / "alfred"

    result = _run(home, stub_bin, checkout)
    assert result.returncode != 0
    assert "git is not installed" in result.stderr


def test_missing_gh_warns_but_succeeds(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    stub_bin = _make_stub_bin(tmp_path / "bin", gh=False)
    checkout = tmp_path / "alfred"

    result = _run(home, stub_bin, checkout)
    assert result.returncode == 0, result.stderr
    assert "gh (GitHub CLI) is not installed" in result.stderr
    # gh is not required for the demo path, so it still clones.
    assert (checkout / ".git").is_dir()


def test_non_alfred_checkout_is_refused(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    stub_bin = _make_stub_bin(tmp_path / "bin")
    checkout = tmp_path / "alfred"
    checkout.mkdir()
    (checkout / "unrelated.txt").write_text("not alfred\n")

    result = _run(home, stub_bin, checkout)
    assert result.returncode != 0
    assert "not an Alfred checkout" in result.stderr
