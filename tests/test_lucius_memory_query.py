"""Lucius must pass an issue-derived ``memory_query`` into the engine.

The recall->inject path already prepends recalled lessons, but only becomes
issue-relevant if the runner supplies a ``memory_query`` derived from the issue
it is working. This test drives the real ``main()`` path with every external
side effect stubbed, captures the kwargs handed to ``invoke_agent_engine``, and
asserts the query carries issue-derived text (not ``None``) alongside the repo.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load_bin_module(name: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ALFRED_HOME", str(ROOT))
    sys.path.insert(0, str(ROOT / "lib"))
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "bin" / name)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.pop(spec.name, None)
    spec.loader.exec_module(module)
    return module


class _StopAfterInvoke(Exception):
    """Sentinel raised from the stubbed engine to end main() right after the
    invoke call, so the post-invoke lifecycle need not be stubbed."""


@pytest.fixture
def lucius(monkeypatch):
    monkeypatch.setenv("GH_ORG", "myorg")
    monkeypatch.setenv("ALFRED_LUCIUS_REPOS", "api")
    return load_bin_module("lucius.py", monkeypatch)


def test_run_passes_issue_derived_memory_query(lucius, monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    fake_issue = {
        "number": 42,
        "title": "Fix GraphQL schema loader",
        "body": "The loader crashes on empty schema files during startup.",
        "url": "https://example.test/issue/42",
        "_attempts": 0,
    }

    def fake_invoke(prompt, **kwargs):
        captured.update(kwargs)
        raise _StopAfterInvoke

    # Neutralize every side-effecting step between firing start and the invoke.
    monkeypatch.setattr(lucius, "GH_ORG", "myorg")
    monkeypatch.setattr(lucius, "with_lock", lambda *a, **k: None)
    monkeypatch.setattr(lucius, "is_dry_run", lambda: False)
    monkeypatch.setattr(lucius, "doctor_requested", lambda: False)
    monkeypatch.setattr(lucius, "doctor_mode", lambda: False)
    monkeypatch.setattr(lucius, "preflight", lambda *a, **k: None)
    monkeypatch.setattr(lucius, "_refresh_pre_push_config", lambda *a, **k: None)
    monkeypatch.setattr(lucius, "is_globally_blocked", lambda: None)
    monkeypatch.setattr(lucius, "pick_issue", lambda: ("api", fake_issue))
    monkeypatch.setattr(lucius, "issue_author_trusted", lambda *a, **k: (True, "trusted"))
    monkeypatch.setattr(lucius, "gh_issue_edit", lambda *a, **k: None)
    monkeypatch.setattr(lucius, "claim_issue", lambda *a, **k: True)
    monkeypatch.setattr(
        lucius,
        "reuse_or_make_worktree",
        lambda *a, **k: (tmp_path, "lucius/42", False),
    )
    monkeypatch.setattr(lucius, "build_prompt", lambda *a, **k: "PROMPT")
    monkeypatch.setattr(lucius, "_make_debug_dir", lambda *a, **k: tmp_path)
    monkeypatch.setattr(lucius, "_write_debug_file", lambda *a, **k: None)
    monkeypatch.setattr(lucius, "invoke_agent_engine", fake_invoke)

    class _Spend:
        def __init__(self) -> None:
            self.state = {"turns_today": 0, "consecutive_failures": 0}

        def is_blocked(self):
            return None

        def increment(self, *a, **k):
            return None

    monkeypatch.setattr(lucius, "SpendState", lambda *a, **k: _Spend())

    with pytest.raises(_StopAfterInvoke):
        lucius.main()

    assert captured.get("memory_repo") == "myorg/api"
    query = captured.get("memory_query")
    assert query is not None
    assert "Fix GraphQL schema loader" in str(query)
    assert "loader crashes on empty schema files" in str(query)
