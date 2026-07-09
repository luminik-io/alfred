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
