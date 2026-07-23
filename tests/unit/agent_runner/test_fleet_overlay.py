"""Tests for the ``ALFRED_FLEET_OVERLAY`` import hook.

The hook lets an operator drop a single Python module on sys.path that
mutates fleet-wide dicts (``GH_REPO_TO_LOCAL``, ``STANDARD_LABELS``,
``HANDOFFS``) instead of forking every ``bin/*.py``. Three cases need
coverage:

* Missing overlay -> silent no-op (the OSS standalone case).
* Present overlay -> module-level side effects run before any consumer
  reads the dicts.
* Broken overlay -> import error propagates (so an operator typo
  doesn't fail silently).
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest


def _wipe_agent_runner_modules() -> None:
    for mod in list(sys.modules):
        if mod == "agent_runner" or mod.startswith("agent_runner."):
            del sys.modules[mod]


@pytest.fixture()
def overlay_root(tmp_path, monkeypatch):
    """Tmp dir on sys.path where the test can drop a fake overlay module."""
    monkeypatch.setenv("ALFRED_HOME", str(tmp_path / "alfred"))
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))
    monkeypatch.delenv("ALFRED_REPO_LOCAL_MAP", raising=False)
    monkeypatch.delenv("ALFRED_FLEET_OVERLAY", raising=False)
    monkeypatch.delenv("ALFREDRC", raising=False)
    monkeypatch.delenv("GH_ORG", raising=False)
    sys.path.insert(0, str(tmp_path))
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
    _wipe_agent_runner_modules()
    yield tmp_path


def test_overlay_absent_is_silent(overlay_root):
    """Default name ``fleet_overlay`` not on the path: import succeeds,
    ``GH_REPO_TO_LOCAL`` stays empty."""
    import agent_runner

    assert agent_runner.GH_REPO_TO_LOCAL == {}


def test_repo_local_map_env_loads_without_overlay(overlay_root, monkeypatch):
    """A deployed OSS runtime should not need a Python overlay just to map
    a GitHub slug to a local checkout path."""
    monkeypatch.setenv(
        "ALFRED_REPO_LOCAL_MAP",
        "test-org/alfred=/tmp/alfred-os test-org-api=services/api",
    )
    _wipe_agent_runner_modules()

    import agent_runner

    assert agent_runner.GH_REPO_TO_LOCAL["test-org/alfred"] == "/tmp/alfred-os"
    assert agent_runner.GH_REPO_TO_LOCAL["alfred"] == "/tmp/alfred-os"
    assert agent_runner.local_repo_dir("test-org/alfred") == "/tmp/alfred-os"
    assert agent_runner.local_repo_dir("test-org-api") == "services/api"


def test_repo_local_map_env_preserves_trailing_comma_paths(overlay_root, monkeypatch):
    monkeypatch.setenv(
        "ALFRED_REPO_LOCAL_MAP",
        "test-org/api=/tmp/api test-org/web=/tmp/archive,",
    )
    _wipe_agent_runner_modules()

    import agent_runner

    assert agent_runner.GH_REPO_TO_LOCAL["test-org/api"] == "/tmp/api"
    assert agent_runner.GH_REPO_TO_LOCAL["test-org/web"] == "/tmp/archive,"
    assert agent_runner.local_repo_dir("web") == "/tmp/archive,"


def test_repo_local_map_env_recovers_comma_delimited_path_entries(overlay_root, monkeypatch):
    monkeypatch.setenv(
        "ALFRED_REPO_LOCAL_MAP",
        "test-org/api=/tmp/api, test-org/web=/tmp/web",
    )
    _wipe_agent_runner_modules()

    import agent_runner

    assert agent_runner.local_repo_dir("api") == "/tmp/api"
    assert agent_runner.local_repo_dir("web") == "/tmp/web"


def test_repo_local_map_env_recovers_compact_comma_delimited_path_entries(
    overlay_root, monkeypatch
):
    monkeypatch.setenv(
        "ALFRED_REPO_LOCAL_MAP",
        "test-org/api=/tmp/api,test-org/web=/tmp/web",
    )
    _wipe_agent_runner_modules()

    import agent_runner

    assert agent_runner.local_repo_dir("api") == "/tmp/api"
    assert agent_runner.local_repo_dir("web") == "/tmp/web"


def test_repo_local_map_env_preserves_comma_and_equals_paths(overlay_root, monkeypatch):
    monkeypatch.setenv(
        "ALFRED_REPO_LOCAL_MAP",
        "test-org/api=/tmp/archive,build=2/api",
    )
    _wipe_agent_runner_modules()

    import agent_runner

    assert agent_runner.local_repo_dir("api") == "/tmp/archive,build=2/api"


def test_repo_local_map_env_decodes_canonical_paths(overlay_root, monkeypatch):
    monkeypatch.setenv(
        "ALFRED_REPO_LOCAL_MAP",
        "test-org/api=url:/tmp/archive%2C test-org/web=/tmp/web",
    )
    _wipe_agent_runner_modules()

    import agent_runner

    assert agent_runner.local_repo_dir("api") == "/tmp/archive,"
    assert agent_runner.local_repo_dir("web") == "/tmp/web"


def test_repo_local_map_env_adds_case_insensitive_aliases(overlay_root, monkeypatch):
    monkeypatch.setenv(
        "ALFRED_REPO_LOCAL_MAP",
        "Test-Org/MyApp=/tmp/MyApp",
    )
    _wipe_agent_runner_modules()

    import agent_runner

    assert agent_runner.local_repo_dir("Test-Org/MyApp") == "/tmp/MyApp"
    assert agent_runner.local_repo_dir("test-org/myapp") == "/tmp/MyApp"
    assert agent_runner.local_repo_dir("MyApp") == "/tmp/MyApp"
    assert agent_runner.local_repo_dir("myapp") == "/tmp/MyApp"


def test_repo_local_map_env_preserves_decoded_space_paths(overlay_root, monkeypatch):
    monkeypatch.setenv(
        "ALFRED_REPO_LOCAL_MAP",
        "test-org/api=/Users/me/My Repos/api test-org/web=/tmp/web",
    )
    _wipe_agent_runner_modules()

    import agent_runner

    assert agent_runner.GH_REPO_TO_LOCAL["test-org/api"] == "/Users/me/My Repos/api"
    assert agent_runner.local_repo_dir("api") == "/Users/me/My Repos/api"
    assert agent_runner.local_repo_dir("test-org/web") == "/tmp/web"


def test_repo_local_map_updates_after_runtime_import(overlay_root, monkeypatch):
    monkeypatch.setenv("ALFRED_REPO_LOCAL_MAP", "test-org/api=/tmp/old-api")
    _wipe_agent_runner_modules()

    import agent_runner

    assert agent_runner.local_repo_dir("api") == "/tmp/old-api"

    monkeypatch.setenv("ALFRED_REPO_LOCAL_MAP", "test-org/api=/tmp/new-api")

    assert agent_runner.repo_to_local_map()["test-org/api"] == "/tmp/new-api"
    assert agent_runner.local_repo_dir("api") == "/tmp/new-api"


def test_explicit_empty_runtime_repo_map_clears_import_snapshot(overlay_root, monkeypatch):
    monkeypatch.setenv("ALFRED_REPO_LOCAL_MAP", "test-org/api=/tmp/api")
    _wipe_agent_runner_modules()

    import agent_runner

    monkeypatch.setenv("ALFRED_REPO_LOCAL_MAP", "")

    assert agent_runner.repo_to_local_map() == {}
    assert agent_runner.local_repo_dir("api") == "api"


def test_overlay_named_via_env_loads_and_mutates(overlay_root, monkeypatch):
    """A custom overlay module pointed at by ``ALFRED_FLEET_OVERLAY``
    runs its module-level side effects during ``agent_runner`` init."""
    (overlay_root / "my_fleet.py").write_text(
        textwrap.dedent(
            """
            from agent_runner import GH_REPO_TO_LOCAL
            GH_REPO_TO_LOCAL.update({
                "test-org-backend": "backend",
                "test-org-frontend": "frontend",
            })
            """
        ).lstrip()
    )
    monkeypatch.setenv("ALFRED_FLEET_OVERLAY", "my_fleet")
    _wipe_agent_runner_modules()
    import agent_runner

    assert agent_runner.GH_REPO_TO_LOCAL.get("test-org-backend") == "backend"
    assert agent_runner.GH_REPO_TO_LOCAL.get("test-org-frontend") == "frontend"


def test_overlay_broken_import_propagates(overlay_root, monkeypatch):
    """An overlay module that raises during its own import must surface
    that error, not get swallowed. Otherwise an operator typo (e.g.
    importing a name that does not exist) fails silently and fleet
    constants stay at defaults with no warning."""
    (overlay_root / "broken_fleet.py").write_text("from nonexistent_module_xyz import something\n")
    monkeypatch.setenv("ALFRED_FLEET_OVERLAY", "broken_fleet")
    _wipe_agent_runner_modules()
    with pytest.raises(ImportError):
        __import__("agent_runner")
