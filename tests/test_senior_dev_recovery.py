"""Integration coverage for auto-recovery wiring in senior-dev's push step.

These exercise ``_push_or_preserve`` with a recovery hook (the real dispatch is
unit-tested in ``test_recovery``): a hook that heals the failure short-circuits
the preserve/HOLD fallback, and a hook that cannot heal falls through to the
exact preserve behaviour. ``_make_push_recovery_hook`` returning ``None`` when
recovery is disabled keeps the push path byte-identical to before.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

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


def test_recovery_hook_heals_pre_push_failure_and_pushes(monkeypatch, tmp_path):
    """A failing pre-push that the hook heals on retry pushes without releasing."""
    lucius = load_bin_module("senior-dev.py", monkeypatch)

    # First pre-push run fails; the retry (after the hook "fixes" it) passes.
    pre_push_results = iter(
        [
            lucius.PrePushResult(ok=False, command="npm run lint", stderr="eslint: 2 errors"),
            lucius.PrePushResult(ok=True, command="npm run lint", stdout="clean"),
        ]
    )
    monkeypatch.setattr(lucius, "run_pre_push_checks", lambda _r, _w: next(pre_push_results))
    monkeypatch.setattr(
        lucius,
        "validate_changed_workflows",
        lambda *_a, **_kw: SimpleNamespace(ok=True, stdout="", stderr="", reason="", files=[]),
    )
    monkeypatch.setattr(
        lucius, "push_remote_and_pr_head", lambda wt, repo, branch: ("origin", branch)
    )
    monkeypatch.setattr(
        lucius, "push_current_branch", lambda *a, **kw: SimpleNamespace(returncode=0)
    )

    released: list[dict] = []
    monkeypatch.setattr(
        lucius,
        "release_issue",
        lambda repo, issue_num, **kw: released.append({"issue": issue_num, **kw}),
    )
    monkeypatch.setattr(lucius, "slack_post", lambda *a, **kw: None)

    captured_kind: list[str] = []

    def hook(failure_text, kind, retry):
        # The hook sees the classified failure text and re-runs the push path.
        captured_kind.append(kind)
        assert "eslint" in failure_text
        return retry()

    holder: list = []
    ok = lucius._push_or_preserve(
        "frontend",
        7,
        "fid-9",
        tmp_path,
        "senior-dev/7",
        "push-failed",
        pre_push_out=holder,
        recover=hook,
    )

    assert ok is True
    assert captured_kind == ["pre_push"]
    # Healed: no release/HOLD.
    assert released == []
    # Evidence holder reflects the PASSING retry run, not the failing first one.
    assert holder and holder[0].ok is True


def test_recovery_hook_that_cannot_heal_falls_through_to_preserve(monkeypatch, tmp_path):
    """A hook that returns False preserves + releases exactly as before."""
    lucius = load_bin_module("senior-dev.py", monkeypatch)

    monkeypatch.setattr(
        lucius,
        "run_pre_push_checks",
        lambda _r, _w: lucius.PrePushResult(
            ok=False, command="npm run lint", stderr="eslint: 2 errors"
        ),
    )
    monkeypatch.setattr(lucius, "create_recovery_ref", lambda _wt, *, branch: "refs/recovery/x")
    released: list[dict] = []
    posts: list[str] = []
    monkeypatch.setattr(
        lucius,
        "release_issue",
        lambda repo, issue_num, **kw: released.append({"issue": issue_num, **kw}),
    )
    monkeypatch.setattr(lucius, "slack_post", lambda message, **_kw: posts.append(message))
    monkeypatch.setattr(
        lucius,
        "push_current_branch",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not push")),
    )

    ok = lucius._push_or_preserve(
        "frontend",
        7,
        "fid-9",
        tmp_path,
        "senior-dev/7",
        "push-failed",
        recover=lambda failure_text, kind, retry: False,
    )

    assert ok is False
    assert released == [
        {
            "issue": 7,
            "codename": "senior-dev",
            "firing_id": "fid-9",
            "outcome": "pre-push-checks-failed",
        }
    ]
    assert posts and "PRE-PUSH-FAILED" in posts[0] and "eslint: 2 errors" in posts[0]


def test_make_push_recovery_hook_disabled_returns_none(monkeypatch, tmp_path):
    lucius = load_bin_module("senior-dev.py", monkeypatch)
    monkeypatch.setenv("ALFRED_RECOVERY_MAX_ATTEMPTS", "0")
    hook = lucius._make_push_recovery_hook("frontend", 7, "fid-9", tmp_path, "senior-dev/7", None)
    assert hook is None


def test_make_push_recovery_hook_enabled_returns_callable(monkeypatch, tmp_path):
    lucius = load_bin_module("senior-dev.py", monkeypatch)
    monkeypatch.setenv("ALFRED_RECOVERY_MAX_ATTEMPTS", "1")
    hook = lucius._make_push_recovery_hook("frontend", 7, "fid-9", tmp_path, "senior-dev/7", None)
    assert callable(hook)
