"""Every engineering runner self-halts on a consecutive-failure streak.

Two layers of coverage:

1. The shared ``maybe_halt_on_fail_streak`` gate: at/over the threshold it
   posts, emits the pause events, and boots the launchd job out; below it, it
   is a no-op and returns ``False``.
2. Characterization of each runner's ``main()`` fail-streak gate: a spend
   ledger at/over the shared threshold trips the ``launchctl bootout`` before
   any work is picked; below it the runner proceeds past the gate to its
   (idle) work-pick step.

Plus a unit test for the shared ``load_pre_push_config`` extraction.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _agent_runner():
    sys.path.insert(0, str(ROOT / "lib"))
    import agent_runner as ar

    return ar


def load_bin_module(name: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ALFRED_HOME", str(ROOT))
    sys.path.insert(0, str(ROOT / "lib"))
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "bin" / name)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.pop(spec.name, None)
    spec.loader.exec_module(module)
    return module


class _FakeEvents:
    def __init__(self, *a, **kw):
        self.firing_id = "fid-test"
        self.emitted: list[tuple[str, dict]] = []

    def emit(self, name, **kw):
        self.emitted.append((name, kw))


def _patch_bootout_capture(monkeypatch, ar):
    """Capture the launchctl bootout + Slack alert the gate fires.

    The gate runs inside ``agent_runner.state``; patching the package
    attribute fans the override out to every submodule that carries it, so the
    helper's ``run``/``slack_post`` calls land in these capture lists.
    """
    runs: list[list[str]] = []
    posts: list[str] = []
    monkeypatch.setattr(ar, "run", lambda cmd, **kw: runs.append(cmd))
    monkeypatch.setattr(ar, "slack_post", lambda msg, **kw: posts.append(msg))
    # Stub the marker write so these in-process gate/runner tests never touch a
    # real ``$ALFRED_HOME/state/_paused`` dir; the real write is covered by
    # test_halt_writes_pause_marker with an isolated ALFRED_HOME.
    monkeypatch.setattr(ar, "write_agent_pause_marker", lambda *a, **kw: None)
    return runs, posts


# ---------------------------------------------------------------------------
# Shared gate: maybe_halt_on_fail_streak
# ---------------------------------------------------------------------------


class _Spend:
    def __init__(self, consecutive_failures: int):
        self.state = {"consecutive_failures": consecutive_failures}


def test_gate_halts_at_threshold(monkeypatch):
    ar = _agent_runner()
    runs, posts = _patch_bootout_capture(monkeypatch, ar)
    events = _FakeEvents()

    tripped = ar.maybe_halt_on_fail_streak(
        "lucius", _Spend(ar.FAIL_STREAK_THRESHOLD), events, "my.fleet.lucius"
    )

    assert tripped is True
    assert runs and runs[0][:2] == ["launchctl", "bootout"]
    assert runs[0][2].endswith("/my.fleet.lucius")
    assert posts and "FAIL-STREAK" in posts[0]
    names = [name for name, _ in events.emitted]
    assert "agent_paused" in names
    assert ("firing_complete", {"outcome": "paused_fail_streak"}) in events.emitted


def test_gate_halts_over_threshold(monkeypatch):
    ar = _agent_runner()
    runs, _ = _patch_bootout_capture(monkeypatch, ar)

    tripped = ar.maybe_halt_on_fail_streak(
        "lucius", _Spend(ar.FAIL_STREAK_THRESHOLD + 4), _FakeEvents(), "my.fleet.lucius"
    )

    assert tripped is True
    assert runs and runs[0][:2] == ["launchctl", "bootout"]


def test_gate_proceeds_below_threshold(monkeypatch):
    ar = _agent_runner()
    runs, posts = _patch_bootout_capture(monkeypatch, ar)
    events = _FakeEvents()

    tripped = ar.maybe_halt_on_fail_streak(
        "lucius", _Spend(ar.FAIL_STREAK_THRESHOLD - 1), events, "my.fleet.lucius"
    )

    assert tripped is False
    assert runs == []
    assert posts == []
    assert events.emitted == []


def test_gate_missing_counter_is_treated_as_zero(monkeypatch):
    ar = _agent_runner()
    runs, _ = _patch_bootout_capture(monkeypatch, ar)

    class _Empty:
        def __init__(self):
            self.state: dict = {}

    assert ar.maybe_halt_on_fail_streak("lucius", _Empty(), _FakeEvents(), "lbl") is False
    assert runs == []


def test_gate_honours_explicit_threshold(monkeypatch):
    ar = _agent_runner()
    _patch_bootout_capture(monkeypatch, ar)

    assert ar.maybe_halt_on_fail_streak("x", _Spend(3), _FakeEvents(), "lbl", threshold=3) is True
    assert ar.maybe_halt_on_fail_streak("x", _Spend(2), _FakeEvents(), "lbl", threshold=3) is False


# ---------------------------------------------------------------------------
# Per-runner characterization: main() fail-streak gate
# ---------------------------------------------------------------------------

# (module, repos-attr, picker-name, picker-idle-return, pre-gate spend fields)
_RUNNERS = [
    ("test-engineer.py", "ROTATION", "pick_repo", None, {}),
    ("fixer.py", "WATCH_REPOS", "pick_target", (None, None, []), {"turns_today": 0}),
    (
        "reviewer.py",
        "REVIEW_REPOS",
        "pick_pr",
        (None, None),
        {"turns_today": 0, "reviews_posted": 0},
    ),
    ("triage.py", "TRIAGE_REPOS", "list_untriaged", [], {"triaged_today": 0, "turns_today": 0}),
]


def _drive_gate(monkeypatch, module_name, repos_attr, picker_name, picker_idle, extra, streak):
    ar = _agent_runner()
    runs, posts = _patch_bootout_capture(monkeypatch, ar)
    runner = load_bin_module(module_name, monkeypatch)

    picker_calls: list[int] = []

    class _Spend:
        def __init__(self, *a, **kw):
            self.state = {"consecutive_failures": streak, **extra}

        def increment(self, **kw):
            pass

        def set(self, **kw):
            self.state.update(kw)

        def is_blocked(self):
            return None

    monkeypatch.setattr(runner, "with_lock", lambda agent: None)
    monkeypatch.setattr(runner, repos_attr, ["backend"])
    monkeypatch.setattr(runner, "preflight", lambda spec: None)
    monkeypatch.setattr(runner, "doctor_mode", lambda: False)
    monkeypatch.setattr(runner, "EventLog", _FakeEvents)
    monkeypatch.setattr(runner, "is_globally_blocked", lambda: None)
    monkeypatch.setattr(runner, "SpendState", _Spend)
    if hasattr(runner, "_refresh_pre_push_config"):
        monkeypatch.setattr(runner, "_refresh_pre_push_config", lambda: None)

    def _picker(*a, **kw):
        picker_calls.append(1)
        return picker_idle

    monkeypatch.setattr(runner, picker_name, _picker)

    rc = runner.main()
    return rc, runs, posts, picker_calls


@pytest.mark.parametrize(
    ("module_name", "repos_attr", "picker_name", "picker_idle", "extra"), _RUNNERS
)
def test_runner_halts_at_or_over_threshold(
    monkeypatch, module_name, repos_attr, picker_name, picker_idle, extra
):
    ar = _agent_runner()
    rc, runs, posts, picker_calls = _drive_gate(
        monkeypatch,
        module_name,
        repos_attr,
        picker_name,
        picker_idle,
        extra,
        streak=ar.FAIL_STREAK_THRESHOLD,
    )

    assert rc == 0
    # The launchctl bootout fired before any work was picked.
    assert any(cmd[:2] == ["launchctl", "bootout"] for cmd in runs), runs
    assert any("FAIL-STREAK" in p for p in posts)
    assert picker_calls == []


@pytest.mark.parametrize(
    ("module_name", "repos_attr", "picker_name", "picker_idle", "extra"), _RUNNERS
)
def test_runner_proceeds_below_threshold(
    monkeypatch, module_name, repos_attr, picker_name, picker_idle, extra
):
    ar = _agent_runner()
    rc, runs, _posts, picker_calls = _drive_gate(
        monkeypatch,
        module_name,
        repos_attr,
        picker_name,
        picker_idle,
        extra,
        streak=ar.FAIL_STREAK_THRESHOLD - 1,
    )

    assert rc == 0
    # No bootout, and the runner advanced to its (idle) work-pick step.
    assert not any(cmd[:2] == ["launchctl", "bootout"] for cmd in runs), runs
    assert picker_calls == [1]


# ---------------------------------------------------------------------------
# Shared pre-push config loader extraction
# ---------------------------------------------------------------------------


def test_load_pre_push_config_resolution_order(tmp_path):
    ar = _agent_runner()
    home = tmp_path / "home"
    (home / "agents").mkdir(parents=True)
    (home / "agents" / "lucius.toml").write_text('[pre_push]\nacme-web = "custom check"\n')

    workspace = tmp_path / "ws"
    (workspace / "py-svc").mkdir(parents=True)
    (workspace / "py-svc" / "pyproject.toml").write_text("")
    (workspace / "node-frontend").mkdir()
    (workspace / "plain").mkdir()

    node_seen: list[tuple[str, Path]] = []

    def node_default(repo: str, local_dir: Path) -> str:
        node_seen.append((repo, local_dir))
        return "NODE-CMD" if repo == "node-frontend" else ""

    cfg = ar.load_pre_push_config(
        agent_codename="lucius",
        repos=["service-backend", "acme-web", "node-frontend", "py-svc", "plain"],
        alfred_home=home,
        workspace=workspace,
        local_repo_dir=lambda r: r,
        node_default=node_default,
    )

    assert cfg["service-backend"] == ar.BACKEND_PRE_PUSH_DEFAULT
    assert cfg["acme-web"] == "custom check"  # operator override wins
    assert cfg["node-frontend"] == "NODE-CMD"  # injected node default
    assert cfg["py-svc"] == ar.PYTHON_PRE_PUSH_DEFAULT  # pyproject.toml default
    assert cfg["plain"] == ""  # no signal -> no pre-push
    # node_default receives (repo, workspace/local_repo_dir(repo)) and is not
    # consulted for backend/api repos or for operator overrides.
    assert ("node-frontend", workspace / "node-frontend") in node_seen
    assert not any(repo == "service-backend" for repo, _ in node_seen)
    assert not any(repo == "acme-web" for repo, _ in node_seen)


def test_load_pre_push_config_ignores_malformed_toml(tmp_path):
    ar = _agent_runner()
    home = tmp_path / "home"
    (home / "agents").mkdir(parents=True)
    (home / "agents" / "lucius.toml").write_text("this is = = not valid toml")

    cfg = ar.load_pre_push_config(
        agent_codename="lucius",
        repos=["service-backend"],
        alfred_home=home,
        workspace=tmp_path / "ws",
        local_repo_dir=lambda r: r,
        node_default=lambda repo, local_dir: "",
    )

    assert cfg["service-backend"] == ar.BACKEND_PRE_PUSH_DEFAULT


# ---------------------------------------------------------------------------
# Re-review finding #4: the halt writes the canonical pause marker
# ---------------------------------------------------------------------------


def _fresh_agent_runner(tmp_path, monkeypatch):
    """Import agent_runner with an isolated ALFRED_HOME (real marker writes)."""
    monkeypatch.setenv("ALFRED_HOME", str(tmp_path / "alfred"))
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))
    for mod in list(sys.modules):
        if mod == "agent_runner" or mod.startswith("agent_runner."):
            del sys.modules[mod]
    sys.path.insert(0, str(ROOT / "lib"))
    import agent_runner as ar

    monkeypatch.setattr(ar, "run", lambda cmd, **kw: None)
    monkeypatch.setattr(ar, "slack_post", lambda msg, **kw: None)
    return ar


def test_halt_writes_pause_marker(tmp_path, monkeypatch):
    ar = _fresh_agent_runner(tmp_path, monkeypatch)

    assert ar.is_agent_paused("lucius") is False

    tripped = ar.maybe_halt_on_fail_streak(
        "lucius", _Spend(ar.FAIL_STREAK_THRESHOLD), _FakeEvents(), "my.fleet.lucius"
    )

    assert tripped is True
    marker = ar.agent_pause_marker_path("lucius")
    assert marker.is_file()
    assert f"fail_streak={ar.FAIL_STREAK_THRESHOLD}" in marker.read_text()
    # The marker the halt writes is exactly the one alfred status / doctor read.
    assert ar.is_agent_paused("lucius") is True


def test_below_threshold_writes_no_marker(tmp_path, monkeypatch):
    ar = _fresh_agent_runner(tmp_path, monkeypatch)

    tripped = ar.maybe_halt_on_fail_streak(
        "lucius", _Spend(ar.FAIL_STREAK_THRESHOLD - 1), _FakeEvents(), "my.fleet.lucius"
    )

    assert tripped is False
    assert ar.is_agent_paused("lucius") is False


# ---------------------------------------------------------------------------
# Re-review finding #1: a non-successful engine result advances the streak
# ---------------------------------------------------------------------------


def _engine_failure_result():
    return SimpleNamespace(
        success=False,
        subtype="error_execution",
        num_turns=2,
        cost_usd=0.0,
        result_text="",
        raw={},
        fallback_from_subtype=None,
    )


def test_test_engineer_engine_failure_increments_streak(monkeypatch, tmp_path):
    monkeypatch.setenv("ALFRED_TEST_ENGINEER_REPOS", "backend")
    bane = load_bin_module("test-engineer.py", monkeypatch)
    increments: list[dict] = []

    class FakeSpend:
        def __init__(self, *a, **kw):
            self.state = {"consecutive_failures": 0}

        def increment(self, **kw):
            increments.append(kw)

        def set(self, **kw):
            self.state.update(kw)

    monkeypatch.setattr(bane, "with_lock", lambda a: None)
    monkeypatch.setattr(bane, "preflight", lambda s: None)
    monkeypatch.setattr(bane, "doctor_mode", lambda: False)
    monkeypatch.setattr(bane, "EventLog", _FakeEvents)
    monkeypatch.setattr(bane, "is_globally_blocked", lambda: None)
    monkeypatch.setattr(bane, "SpendState", FakeSpend)
    monkeypatch.setattr(bane, "pick_repo", lambda: "backend")
    monkeypatch.setattr(bane, "local_repo_dir", lambda repo: repo)
    monkeypatch.setattr(bane, "WORKSPACE", tmp_path)
    monkeypatch.setattr(bane, "make_worktree", lambda repo, agent, kind: (tmp_path, "bane/1"))
    monkeypatch.setattr(bane, "maybe_set_global_block_for_result", lambda *a, **kw: None)
    monkeypatch.setattr(bane, "remove_worktree", lambda repo, path: None)
    monkeypatch.setattr(bane, "slack_post", lambda *a, **kw: None)
    monkeypatch.setattr(
        bane, "invoke_agent_engine", lambda *a, **kw: (_engine_failure_result(), "codex")
    )

    assert bane.main() == 0
    assert {"failures_today": 1, "consecutive_failures": 1} in increments


def test_fixer_all_engine_failures_increments_streak(monkeypatch, tmp_path):
    monkeypatch.setenv("GH_ORG", "acme")
    monkeypatch.setenv("ALFRED_FIXER_REPOS", "service-web")
    nightwing = load_bin_module("fixer.py", monkeypatch)
    increments: list[dict] = []
    sets: list[dict] = []

    class FakeSpend:
        def __init__(self, *a, **kw):
            self.state = {"turns_today": 0, "consecutive_failures": 0}

        def increment(self, **kw):
            increments.append(kw)

        def set(self, **kw):
            sets.append(kw)
            self.state.update(kw)

    monkeypatch.setattr(nightwing, "with_lock", lambda a: None)
    monkeypatch.setattr(nightwing, "preflight", lambda s: None)
    monkeypatch.setattr(nightwing, "_refresh_pre_push_config", lambda: None)
    monkeypatch.setattr(nightwing, "doctor_mode", lambda: False)
    monkeypatch.setattr(nightwing, "is_globally_blocked", lambda: None)
    monkeypatch.setattr(nightwing, "EventLog", _FakeEvents)
    monkeypatch.setattr(nightwing, "SpendState", FakeSpend)
    monkeypatch.setattr(nightwing, "load_fixed_ids", lambda: set())
    monkeypatch.setattr(nightwing, "save_fixed_ids", lambda _ids: None)
    monkeypatch.setattr(nightwing, "load_no_commit_streaks", lambda: {})
    monkeypatch.setattr(nightwing, "save_no_commit_streaks", lambda _streaks: None)
    monkeypatch.setattr(nightwing, "reset_label_present", lambda *_a: False)
    monkeypatch.setattr(nightwing, "local_repo_dir", lambda _repo: tmp_path / "repo")
    monkeypatch.setattr(
        nightwing,
        "pick_target",
        lambda _fixed_ids: (
            "service-web",
            {"number": 123, "headRefName": "feature/fix"},
            [
                {
                    "body": "fix it",
                    "path": "src/x.py",
                    "line": 3,
                    "user": "rev",
                    "id": 9,
                    "severity": "P1",
                }
            ],
        ),
    )
    monkeypatch.setattr(nightwing, "make_worktree_from_branch", lambda *_a, **_kw: tmp_path / "wt")
    monkeypatch.setattr(nightwing, "build_prompt", lambda *a, **kw: "prompt")
    monkeypatch.setattr(nightwing, "maybe_set_global_block_for_result", lambda *a, **kw: None)
    monkeypatch.setattr(
        nightwing, "invoke_agent_engine", lambda *a, **kw: (_engine_failure_result(), "codex")
    )
    monkeypatch.setattr(nightwing, "remove_worktree", lambda repo, path: None)
    monkeypatch.setattr(nightwing, "slack_post", lambda *a, **kw: None)

    assert nightwing.main() == 0
    # Zero fixes + an engine failure is a failed firing: streak advances...
    assert {"failures_today": 1, "consecutive_failures": 1} in increments
    # ...and the streak is NOT reset on this path.
    assert {"consecutive_failures": 0} not in sets


# ---------------------------------------------------------------------------
# Re-review findings #2 + #3: daily-cap hit resets the streak and uses one
# canonical sentinel for both the runner emit and the in-prompt detection
# ---------------------------------------------------------------------------


def _planner_spend(sets, increments):
    class FakeSpend:
        def __init__(self, *a, **kw):
            self.state = {"consecutive_failures": 0}

        def is_blocked(self):
            return None

        def increment(self, **kw):
            increments.append(kw)

        def set(self, **kw):
            sets.append(kw)
            self.state.update(kw)

    return FakeSpend


def test_planner_preflight_cap_resets_streak_and_emits_canonical_sentinel(monkeypatch, capsys):
    monkeypatch.setenv("ALFRED_PLANNER_REPOS", "backend")
    drake = load_bin_module("planner.py", monkeypatch)
    sets: list[dict] = []

    assert drake.DAILY_CAP_SENTINEL == "[DRAKE-DAILY-CAP-HIT]"

    monkeypatch.setattr(drake, "with_lock", lambda a: None)
    monkeypatch.setattr(drake, "PLANNER_REPOS", ["backend"])
    monkeypatch.setattr(drake, "preflight", lambda s: None)
    monkeypatch.setattr(drake, "doctor_mode", lambda: False)
    monkeypatch.setattr(drake, "EventLog", _FakeEvents)
    monkeypatch.setattr(drake, "is_globally_blocked", lambda: None)
    monkeypatch.setattr(drake, "SpendState", _planner_spend(sets, []))
    monkeypatch.setattr(drake, "_issues_authored_in_last_24h", lambda: 10**6)
    monkeypatch.setattr(drake, "slack_post", lambda *a, **kw: None)

    assert drake.main() == 0
    # Cap hit resets the streak (healthy, done for the day) ...
    assert {"consecutive_failures": 0} in sets
    # ... and the runner emits the one canonical sentinel.
    assert drake.DAILY_CAP_SENTINEL in capsys.readouterr().out


def test_planner_in_prompt_cap_detected_via_same_sentinel(monkeypatch, tmp_path):
    monkeypatch.setenv("ALFRED_PLANNER_REPOS", "backend")
    drake = load_bin_module("planner.py", monkeypatch)
    sets: list[dict] = []
    increments: list[dict] = []

    prompt_file = tmp_path / "planner.md"
    prompt_file.write_text("prompt")

    monkeypatch.setattr(drake, "with_lock", lambda a: None)
    monkeypatch.setattr(drake, "PLANNER_REPOS", ["backend"])
    monkeypatch.setattr(drake, "preflight", lambda s: None)
    monkeypatch.setattr(drake, "doctor_mode", lambda: False)
    monkeypatch.setattr(drake, "EventLog", _FakeEvents)
    monkeypatch.setattr(drake, "is_globally_blocked", lambda: None)
    monkeypatch.setattr(drake, "SpendState", _planner_spend(sets, increments))
    monkeypatch.setattr(drake, "_issues_authored_in_last_24h", lambda: 0)
    monkeypatch.setattr(drake, "PROMPT_PATH", prompt_file)
    monkeypatch.setattr(drake, "build_prompt", lambda: "prompt")
    monkeypatch.setattr(drake, "slack_post", lambda *a, **kw: None)
    monkeypatch.setattr(
        drake,
        "invoke_agent_engine",
        lambda *a, **kw: (
            SimpleNamespace(
                success=True,
                subtype="success",
                num_turns=1,
                cost_usd=0.0,
                result_text=f"work done {drake.DAILY_CAP_SENTINEL} stop",
            ),
            "codex",
        ),
    )

    assert drake.main() == 0
    # The in-prompt grep detects the same sentinel: healthy success + reset.
    assert {"successes_today": 1} in increments
    assert {"consecutive_failures": 0} in sets


# ---------------------------------------------------------------------------
# Re-review finding: a healthy terminal outcome must clear a prior streak
# (a consecutive-failure streak survives only across actual failures)
# ---------------------------------------------------------------------------


class _PresetSpend:
    """SpendState double preloaded with a streak; records set()/increment()."""

    def __init__(self, *a, **kw):
        self.state = {"consecutive_failures": 3, "turns_today": 0, "reviews_posted": 0}
        self.sets: list[dict] = []
        self.increments: list[dict] = []

    def is_blocked(self):
        return None

    def increment(self, **kw):
        self.increments.append(kw)

    def set(self, **kw):
        self.sets.append(kw)
        self.state.update(kw)


def _healthy_result(result_text):
    return SimpleNamespace(
        success=True,
        subtype="success",
        num_turns=3,
        cost_usd=0.0,
        result_text=result_text,
        raw={},
        fallback_from_subtype=None,
    )


def _drive_test_engineer_healthy(monkeypatch, tmp_path, result_text, extra=None):
    monkeypatch.setenv("ALFRED_TEST_ENGINEER_REPOS", "backend")
    bane = load_bin_module("test-engineer.py", monkeypatch)
    spend = _PresetSpend()

    monkeypatch.setattr(bane, "with_lock", lambda a: None)
    monkeypatch.setattr(bane, "preflight", lambda s: None)
    monkeypatch.setattr(bane, "doctor_mode", lambda: False)
    monkeypatch.setattr(bane, "EventLog", _FakeEvents)
    monkeypatch.setattr(bane, "is_globally_blocked", lambda: None)
    monkeypatch.setattr(bane, "SpendState", lambda *a, **kw: spend)
    monkeypatch.setattr(bane, "pick_repo", lambda: "backend")
    monkeypatch.setattr(bane, "local_repo_dir", lambda repo: repo)
    monkeypatch.setattr(bane, "WORKSPACE", tmp_path)
    monkeypatch.setattr(bane, "make_worktree", lambda repo, agent, kind: (tmp_path, "bane/1"))
    monkeypatch.setattr(bane, "remove_worktree", lambda repo, path: None)
    monkeypatch.setattr(bane, "slack_post", lambda *a, **kw: None)
    monkeypatch.setattr(
        bane, "invoke_agent_engine", lambda *a, **kw: (_healthy_result(result_text), "codex")
    )
    if extra:
        extra(bane)

    assert bane.main() == 0
    return spend


def test_test_engineer_bane_silent_resets_streak(monkeypatch, tmp_path):
    spend = _drive_test_engineer_healthy(monkeypatch, tmp_path, "[BANE-SILENT] all covered")
    assert spend.state["consecutive_failures"] == 0
    assert {"consecutive_failures": 0} in spend.sets


def test_test_engineer_bug_found_resets_streak(monkeypatch, tmp_path):
    def _extra(bane):
        monkeypatch.setattr(
            bane,
            "run",
            lambda *a, **kw: SimpleNamespace(
                returncode=0, stdout="https://github.com/acme/backend/issues/9\n", stderr=""
            ),
        )

    spend = _drive_test_engineer_healthy(
        monkeypatch, tmp_path, "[BUG-FOUND] null deref in foo", extra=_extra
    )
    assert spend.state["consecutive_failures"] == 0
    assert {"consecutive_failures": 0} in spend.sets


def test_test_engineer_bug_found_filing_failure_advances_streak(monkeypatch, tmp_path):
    def _extra(bane):
        # `gh issue create` fails: the bug was found but never filed, so the
        # healthy action did not complete. This is a failed firing.
        monkeypatch.setattr(
            bane,
            "run",
            lambda *a, **kw: SimpleNamespace(returncode=1, stdout="", stderr="gh: HTTP 500"),
        )

    spend = _drive_test_engineer_healthy(
        monkeypatch, tmp_path, "[BUG-FOUND] null deref in foo", extra=_extra
    )
    # Reaching the [BUG-FOUND] branch is not enough: filing failed, so the
    # streak advances and is NOT cleared.
    assert {"failures_today": 1, "consecutive_failures": 1} in spend.increments
    assert {"consecutive_failures": 0} not in spend.sets
    assert spend.state["consecutive_failures"] == 3


def test_reviewer_pr_stale_resets_streak(monkeypatch, tmp_path):
    monkeypatch.setenv("GH_ORG", "acme")
    monkeypatch.setenv("ALFRED_REVIEWER_REPOS", "service-web")
    rasalghul = load_bin_module("reviewer.py", monkeypatch)
    spend = _PresetSpend()

    def fake_gh_json(cmd, *a, **kw):
        joined = " ".join(cmd)
        if "/comments" in joined:
            return []
        if "additions" in joined:  # the PR meta fetch
            return {"title": "t", "body": "b", "additions": 1, "deletions": 0, "headRefOid": "abc"}
        return {"state": "CLOSED"}  # the post-review re-verify: PR closed under us

    monkeypatch.setattr(rasalghul, "with_lock", lambda a: None)
    monkeypatch.setattr(rasalghul, "preflight", lambda s: None)
    monkeypatch.setattr(rasalghul, "doctor_mode", lambda: False)
    monkeypatch.setattr(rasalghul, "EventLog", _FakeEvents)
    monkeypatch.setattr(rasalghul, "is_globally_blocked", lambda: None)
    monkeypatch.setattr(rasalghul, "SpendState", lambda *a, **kw: spend)
    monkeypatch.setattr(rasalghul, "pick_pr", lambda: ("service-web", {"number": 5}))
    monkeypatch.setattr(rasalghul, "slack_post", lambda *a, **kw: None)
    monkeypatch.setattr(rasalghul, "gh_pr_comment", lambda *a, **kw: True)
    monkeypatch.setattr(
        rasalghul,
        "run",
        lambda *a, **kw: SimpleNamespace(stdout="diff --git a b\n+line\n", stderr="", returncode=0),
    )
    monkeypatch.setattr(rasalghul, "gh_json", fake_gh_json)
    monkeypatch.setattr(
        rasalghul,
        "invoke_agent_engine",
        lambda *a, **kw: (
            _healthy_result("Ra's al Ghul review\n\n## Blockers (P0)\n- none\n\nShip-ready: yes"),
            "codex",
        ),
    )

    assert rasalghul.main() == 0
    assert spend.state["consecutive_failures"] == 0
    assert {"consecutive_failures": 0} in spend.sets


# ---------------------------------------------------------------------------
# Re-review finding: a fixer firing that landed no fixes because attempts
# failed (engine, no-commit, OR push) is a FAILED firing, not a healthy no-op
# ---------------------------------------------------------------------------


def _fixer_comment(cid, *, severity="P1", body="fix it"):
    return {
        "body": body,
        "path": "src/x.py",
        "line": 3,
        "user": "rev",
        "id": cid,
        "severity": severity,
    }


def _drive_fixer(monkeypatch, tmp_path, comments, *, invoke, run_fn, workflow_ok=True, block=None):
    monkeypatch.setenv("GH_ORG", "acme")
    monkeypatch.setenv("ALFRED_FIXER_REPOS", "service-web")
    nightwing = load_bin_module("fixer.py", monkeypatch)
    increments: list[dict] = []
    sets: list[dict] = []

    class FakeSpend:
        def __init__(self, *a, **kw):
            self.state = {"turns_today": 0, "consecutive_failures": 0}

        def increment(self, **kw):
            increments.append(kw)

        def set(self, **kw):
            sets.append(kw)
            self.state.update(kw)

    monkeypatch.setattr(nightwing, "with_lock", lambda a: None)
    monkeypatch.setattr(nightwing, "preflight", lambda s: None)
    monkeypatch.setattr(nightwing, "_refresh_pre_push_config", lambda: None)
    monkeypatch.setattr(nightwing, "doctor_mode", lambda: False)
    monkeypatch.setattr(nightwing, "is_globally_blocked", lambda: None)
    monkeypatch.setattr(nightwing, "EventLog", _FakeEvents)
    monkeypatch.setattr(nightwing, "SpendState", FakeSpend)
    monkeypatch.setattr(nightwing, "load_fixed_ids", lambda: set())
    monkeypatch.setattr(nightwing, "save_fixed_ids", lambda _ids: None)
    monkeypatch.setattr(nightwing, "load_no_commit_streaks", lambda: {})
    monkeypatch.setattr(nightwing, "save_no_commit_streaks", lambda _s: None)
    monkeypatch.setattr(nightwing, "reset_label_present", lambda *_a: False)
    monkeypatch.setattr(nightwing, "local_repo_dir", lambda _repo: tmp_path / "repo")
    monkeypatch.setattr(
        nightwing,
        "pick_target",
        lambda _fixed: ("service-web", {"number": 123, "headRefName": "feature/fix"}, comments),
    )
    monkeypatch.setattr(nightwing, "make_worktree_from_branch", lambda *_a, **_kw: tmp_path / "wt")
    monkeypatch.setattr(nightwing, "build_prompt", lambda *a, **kw: "prompt")
    monkeypatch.setattr(
        nightwing, "maybe_set_global_block_for_result", block or (lambda *a, **kw: None)
    )
    monkeypatch.setattr(nightwing, "invoke_agent_engine", invoke)
    monkeypatch.setattr(
        nightwing,
        "validate_changed_workflows",
        lambda *a, **kw: SimpleNamespace(ok=workflow_ok, files=(), stdout="", stderr="", reason=""),
    )
    monkeypatch.setattr(nightwing, "run", run_fn)
    monkeypatch.setattr(nightwing, "gh_pr_comment", lambda *a, **kw: True)
    monkeypatch.setattr(nightwing, "gh_json", lambda *a, **kw: [])
    monkeypatch.setattr(nightwing, "remove_worktree", lambda repo, path: None)
    monkeypatch.setattr(nightwing, "slack_post", lambda *a, **kw: None)

    assert nightwing.main() == 0
    return increments, sets


def _commit_lands_run(*, push_returncode):
    """git-log reports a fresh commit; git-push returns the given code."""

    def run_fn(cmd, **kw):
        if len(cmd) >= 2 and cmd[1] == "push":
            return SimpleNamespace(returncode=push_returncode, stdout="", stderr="rejected")
        if len(cmd) >= 2 and cmd[1] == "log":
            if "origin/" in cmd[-1]:
                return SimpleNamespace(returncode=0, stdout="parentsha\n", stderr="")
            return SimpleNamespace(returncode=0, stdout="newsha123\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return run_fn


def test_fixer_all_push_failed_advances_streak(monkeypatch, tmp_path):
    increments, sets = _drive_fixer(
        monkeypatch,
        tmp_path,
        [_fixer_comment(1), _fixer_comment(2)],
        invoke=lambda *a, **kw: (_healthy_result("done"), "codex"),
        run_fn=_commit_lands_run(push_returncode=1),  # every push fails
    )
    # Zero fixes landed, every attempt failed at push: a failed firing.
    assert {"failures_today": 1, "consecutive_failures": 1} in increments
    assert {"consecutive_failures": 0} not in sets


def test_fixer_landed_fix_resets_streak(monkeypatch, tmp_path):
    increments, sets = _drive_fixer(
        monkeypatch,
        tmp_path,
        [_fixer_comment(1)],
        invoke=lambda *a, **kw: (_healthy_result("done"), "codex"),
        run_fn=_commit_lands_run(push_returncode=0),  # push succeeds, fix lands
    )
    # A landed fix is healthy work: reset, no failure increment.
    assert {"consecutive_failures": 0} in sets
    assert {"failures_today": 1, "consecutive_failures": 1} not in increments


def test_fixer_healthy_skip_noop_resets_streak(monkeypatch, tmp_path):
    def _no_engine(*a, **kw):
        raise AssertionError("engine must not run on a P0-security-skipped comment")

    # A P0 security finding is deferred to manual review: a healthy skip, not a
    # failed attempt. The firing lands no fixes but is a legitimate no-op.
    increments, sets = _drive_fixer(
        monkeypatch,
        tmp_path,
        [_fixer_comment(1, severity="P0", body="possible secret leak in auth handler")],
        invoke=_no_engine,
        run_fn=lambda *a, **kw: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    assert {"consecutive_failures": 0} in sets
    assert {"failures_today": 1, "consecutive_failures": 1} not in increments


def test_fixer_provider_block_persists_prior_failures(monkeypatch, tmp_path):
    # First comment fails at the engine (failed_attempts=1, committed only at the
    # post-loop step); the second comment trips a provider block that returns
    # mid-loop. The accumulated failure must be persisted before that early
    # return, not dropped, so the failing firing still advances the streak.
    calls = {"n": 0}

    def fake_block(*a, **kw):
        calls["n"] += 1
        return "2026-07-09T12:00:00Z" if calls["n"] >= 2 else None

    increments, sets = _drive_fixer(
        monkeypatch,
        tmp_path,
        [_fixer_comment(1), _fixer_comment(2)],
        invoke=lambda *a, **kw: (_engine_failure_result(), "codex"),
        run_fn=lambda *a, **kw: SimpleNamespace(returncode=0, stdout="", stderr=""),
        block=fake_block,
    )
    # The earlier engine failure is persisted despite the provider-block return.
    assert {"failures_today": 1, "consecutive_failures": 1} in increments
    assert {"consecutive_failures": 0} not in sets
