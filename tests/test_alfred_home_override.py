"""Regression tests: an explicitly-set ``ALFRED_HOME`` is honored end to end.

An operator who runs ``ALFRED_HOME=/some/fresh/dir alfred serve`` expects that
home to anchor every state/config path - state, the fleet-brain DB, the runtime
``.env``, and ``launchd/agents.conf`` - even when the *code* is loaded from a
different install. These tests lock that contract at the resolver level (the
runtime path functions read the environment at call time) and at the launcher
level (the managed interpreter falls back to the install's venv, never a home
swap, and the requested home's scaffolding is materialized rather than dropped).
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


def _load_cli_module():
    loader = importlib.machinery.SourceFileLoader("alfred_cli_home_test", str(ROOT / "bin/alfred"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_state_root_resolves_under_explicit_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fresh = tmp_path / "fresh-home"
    monkeypatch.setenv("ALFRED_HOME", str(fresh))
    from server import reader

    assert reader.default_state_root() == fresh / "state"


def test_fleet_brain_db_resolves_under_explicit_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fresh = tmp_path / "fresh-home"
    monkeypatch.setenv("ALFRED_HOME", str(fresh))
    monkeypatch.delenv("ALFRED_FLEET_BRAIN_DB", raising=False)
    from fleet_brain import store

    assert store.default_db_path() == fresh / "fleet-brain.db"


def test_runtime_home_and_setup_home_resolve_under_explicit_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fresh = tmp_path / "fresh-home"
    monkeypatch.setenv("ALFRED_HOME", str(fresh))
    from agent_runner import paths
    from server import setup as setup_mod

    assert paths.runtime_home() == fresh
    # The setup surface resolves config paths (``.env``, ``agents.conf``) off the
    # same home, so the onboarding status a fresh home reports is its own.
    resolved_env = setup_mod._runtime_config_env()
    assert resolved_env["ALFRED_HOME"] == str(fresh)
    assert setup_mod._alfred_home(resolved_env) == fresh
    assert setup_mod._env_path(resolved_env) == fresh / ".env"


def test_managed_python_falls_back_to_install_venv_not_bare_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh home with no venv must borrow the install's venv, not swap homes.

    The requested ``ALFRED_HOME`` carries no interpreter of its own, so the code
    (and its deps) come from the install. Falling through to a bare
    ``sys.executable`` that lacks the dashboard deps is the silent-swap failure
    this guards against: the install venv is used while the home stays put.
    """
    cli = _load_cli_module()
    fresh = tmp_path / "fresh-home"
    fresh.mkdir()
    # A stand-in for the install's own venv, kept under tmp so the test never
    # writes into the real checkout.
    install_python = tmp_path / "install" / "venv" / "bin" / "python"

    monkeypatch.setenv("ALFRED_HOME", str(fresh))
    monkeypatch.setattr(cli, "_install_managed_python_candidates", lambda: [install_python])

    # Interpreter absent -> last-resort sys.executable, home unchanged.
    resolved_missing = cli._alfred_managed_python()
    assert resolved_missing == sys.executable

    # Interpreter present -> the install venv, never a different home.
    install_python.parent.mkdir(parents=True, exist_ok=True)
    install_python.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    install_python.chmod(0o755)
    resolved_present = cli._alfred_managed_python()
    assert resolved_present == str(install_python)


def test_serve_materializes_requested_home_state_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _load_cli_module()
    fresh = tmp_path / "fresh-home"
    monkeypatch.setenv("ALFRED_HOME", str(fresh))
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))

    args = SimpleNamespace(host=None, port=7012, no_browser=True, log_level=None)
    assert cli.cmd_serve(args) == 0

    # The requested home is materialized rather than silently swapped away.
    assert (fresh / "state").is_dir()
