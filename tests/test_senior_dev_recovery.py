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
    hook = lucius._make_push_recovery_hook(
        "frontend", 7, "fid-9", tmp_path, "senior-dev/7", None, None
    )
    assert hook is None


def test_make_push_recovery_hook_enabled_returns_callable(monkeypatch, tmp_path):
    lucius = load_bin_module("senior-dev.py", monkeypatch)
    monkeypatch.setenv("ALFRED_RECOVERY_MAX_ATTEMPTS", "1")
    hook = lucius._make_push_recovery_hook(
        "frontend", 7, "fid-9", tmp_path, "senior-dev/7", None, None
    )
    assert callable(hook)


class _FakeSpend:
    """Minimal SpendState stub capturing increment kwargs."""

    def __init__(self, *, turns_today: int = 0):
        self.calls: list[dict] = []
        self.state = {"turns_today": turns_today}

    def increment(self, **kw):
        self.calls.append(kw)
        for key, value in kw.items():
            self.state[key] = self.state.get(key, 0) + value


def _engine_result(**over):
    base = {"subtype": "success", "num_turns": 3, "cost_usd": 0.12}
    base.update(over)
    return SimpleNamespace(**base)


def test_recovery_turn_requires_committed_fix_and_charges_spend(monkeypatch, tmp_path):
    """A recovery turn that leaves the tree dirty is a failed attempt, but its
    turns and cost are still charged to the ledger."""
    lucius = load_bin_module("senior-dev.py", monkeypatch)
    monkeypatch.setenv("ALFRED_RECOVERY_MAX_ATTEMPTS", "1")
    monkeypatch.setattr(
        lucius, "invoke_agent_engine", lambda *a, **kw: (_engine_result(), "claude")
    )
    monkeypatch.setattr(lucius, "codex_sandbox_for_agent", lambda *a, **kw: "workspace-write")
    monkeypatch.setattr(lucius, "local_repo_dir", lambda repo: repo)
    # Dirty worktree: the fix was edited but not committed.
    monkeypatch.setattr(lucius, "_worktree_status", lambda _wt: "?? fix.py")

    spend = _FakeSpend()
    retried: list[int] = []
    hook = lucius._make_push_recovery_hook(
        "frontend", 7, "fid-9", tmp_path, "senior-dev/7", None, spend
    )
    recovered = hook("eslint hook failed", "pre_push", lambda: retried.append(1) or True)

    assert recovered is False
    # Dirty tree short-circuits before the push retry.
    assert retried == []
    # The paid turn is still charged.
    assert spend.calls == [{"turns_today": 3, "cost_usd_today": 0.12}]


def test_recovery_turn_with_clean_tree_retries_and_charges_spend(monkeypatch, tmp_path):
    lucius = load_bin_module("senior-dev.py", monkeypatch)
    monkeypatch.setenv("ALFRED_RECOVERY_MAX_ATTEMPTS", "1")
    monkeypatch.setattr(
        lucius, "invoke_agent_engine", lambda *a, **kw: (_engine_result(), "claude")
    )
    monkeypatch.setattr(lucius, "codex_sandbox_for_agent", lambda *a, **kw: "workspace-write")
    monkeypatch.setattr(lucius, "local_repo_dir", lambda repo: repo)
    # Clean worktree: the fix was committed.
    monkeypatch.setattr(lucius, "_worktree_status", lambda _wt: "")
    monkeypatch.setattr(lucius, "slack_post", lambda *a, **kw: None)

    spend = _FakeSpend()
    retried: list[int] = []
    hook = lucius._make_push_recovery_hook(
        "frontend", 7, "fid-9", tmp_path, "senior-dev/7", None, spend
    )
    recovered = hook("eslint hook failed", "pre_push", lambda: retried.append(1) or True)

    assert recovered is True
    assert retried == [1]
    assert spend.calls == [{"turns_today": 3, "cost_usd_today": 0.12}]


def test_recovery_turn_is_not_invoked_when_daily_spend_cap_is_reached(monkeypatch, tmp_path):
    monkeypatch.setenv("ALFRED_SENIOR_DEV_TURN_CAP", "10")
    lucius = load_bin_module("senior-dev.py", monkeypatch)
    monkeypatch.setenv("ALFRED_RECOVERY_MAX_ATTEMPTS", "1")
    invoked: list[int] = []
    monkeypatch.setattr(
        lucius,
        "invoke_agent_engine",
        lambda *a, **kw: invoked.append(1) or (_engine_result(), "claude"),
    )

    spend = _FakeSpend(turns_today=10)
    events: list[tuple[str, dict]] = []
    event_log = SimpleNamespace(emit=lambda event, **payload: events.append((event, payload)))
    hook = lucius._make_push_recovery_hook(
        "frontend", 7, "fid-9", tmp_path, "senior-dev/7", event_log, spend
    )

    assert hook("eslint hook failed", "pre_push", lambda: True) is False
    assert invoked == []
    assert spend.calls == []
    assert events[-1][0] == "recovery_skipped"
    assert events[-1][1]["reason"] == (
        "insufficient daily turn budget for recovery (0 remaining; requires up to 12)"
    )


def test_recovery_turn_is_not_invoked_when_remaining_budget_cannot_cover_bound(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("ALFRED_SENIOR_DEV_TURN_CAP", "20")
    lucius = load_bin_module("senior-dev.py", monkeypatch)
    monkeypatch.setenv("ALFRED_RECOVERY_MAX_ATTEMPTS", "1")
    invoked: list[int] = []
    monkeypatch.setattr(
        lucius,
        "invoke_agent_engine",
        lambda *a, **kw: invoked.append(1) or (_engine_result(), "claude"),
    )

    spend = _FakeSpend(turns_today=9)
    hook = lucius._make_push_recovery_hook(
        "frontend", 7, "fid-9", tmp_path, "senior-dev/7", None, spend
    )

    assert hook("eslint hook failed", "pre_push", lambda: True) is False
    assert invoked == []
    assert spend.calls == []


def test_recovery_rechecks_daily_spend_cap_between_attempts(monkeypatch, tmp_path):
    monkeypatch.setenv("ALFRED_SENIOR_DEV_TURN_CAP", "14")
    lucius = load_bin_module("senior-dev.py", monkeypatch)
    monkeypatch.setenv("ALFRED_RECOVERY_MAX_ATTEMPTS", "2")
    invoked: list[int] = []

    def invoke(*_args, **_kwargs):
        invoked.append(1)
        return _engine_result(subtype="failure"), "claude"

    monkeypatch.setattr(lucius, "invoke_agent_engine", invoke)
    monkeypatch.setattr(lucius, "codex_sandbox_for_agent", lambda *a, **kw: "workspace-write")
    monkeypatch.setattr(lucius, "local_repo_dir", lambda repo: repo)

    spend = _FakeSpend()
    hook = lucius._make_push_recovery_hook(
        "frontend", 7, "fid-9", tmp_path, "senior-dev/7", None, spend
    )

    assert hook("eslint hook failed", "pre_push", lambda: True) is False
    assert invoked == [1]
    assert spend.state["turns_today"] == 3


def test_failed_recovery_preserves_latest_gate_not_stale_one(monkeypatch, tmp_path):
    """When a recovery turn moves the failure to a different gate and still fails,
    the preserve message and release outcome reflect the current blocker."""
    lucius = load_bin_module("senior-dev.py", monkeypatch)

    # First push run fails pre-push; the recovery retry fails workflow validation.
    pre_push_results = iter(
        [
            lucius.PrePushResult(ok=False, command="npm run lint", stderr="eslint: 2 errors"),
            lucius.PrePushResult(ok=True, command="npm run lint", stdout="clean"),
        ]
    )
    monkeypatch.setattr(lucius, "run_pre_push_checks", lambda _r, _w: next(pre_push_results))
    workflow_results = iter(
        [
            SimpleNamespace(
                ok=False,
                stdout="",
                stderr="workflow syntax error",
                reason="actionlint_failed",
                files=[".github/workflows/ci.yml"],
            ),
        ]
    )
    monkeypatch.setattr(
        lucius, "validate_changed_workflows", lambda *_a, **_kw: next(workflow_results)
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

    # The hook "heals" the first (pre-push) failure, so the retry re-runs the
    # push path which now passes pre-push but fails workflow validation.
    ok = lucius._push_or_preserve(
        "frontend",
        7,
        "fid-9",
        tmp_path,
        "senior-dev/7",
        "push-failed",
        recover=lambda failure_text, kind, retry: retry(),
    )

    assert ok is False
    # Released and warned against the CURRENT blocker (workflow), not pre-push.
    assert released == [
        {
            "issue": 7,
            "codename": "senior-dev",
            "firing_id": "fid-9",
            "outcome": "workflow-validation-failed",
        }
    ]
    assert posts and "WORKFLOW-VALIDATION-FAILED" in posts[0]
    assert ".github/workflows/ci.yml" in posts[0]
