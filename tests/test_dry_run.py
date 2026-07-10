"""Tests for alfred-os ``--dry-run`` / ``ALFRED_DRY_RUN`` mode.

Dry-run runs the whole firing lifecycle but stubs every side-effecting
boundary: no real LLM call, no spend mutation, no gh / Slack / git side
effects. These tests assert each seam is stubbed and that the example
runners complete a full lifecycle exit-0 with zero host config.

Run via ``pytest tests/``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _isolated_alfred_home(tmp_path, monkeypatch):
    """Point ALFRED_HOME at a clean tmp dir and import agent_runner fresh.

    Mirrors the fixture in test_agent_runner.py: every state file lives
    under ALFRED_HOME, so this is what keeps tests off the operator's
    real ~/.alfred/.
    """
    monkeypatch.setenv("ALFRED_HOME", str(tmp_path / "alfred"))
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))
    monkeypatch.delenv("ALFRED_DRY_RUN", raising=False)
    monkeypatch.delenv("GH_ORG", raising=False)
    monkeypatch.delenv("OPERATOR_NAME", raising=False)
    for mod in list(sys.modules):
        if mod == "agent_runner" or mod.startswith("agent_runner."):
            del sys.modules[mod]
    sys.path.insert(0, str(REPO_ROOT / "lib"))
    yield


# ---------- is_dry_run / set_dry_run ----------


@pytest.mark.parametrize(
    "val,expected",
    [
        ("1", True),
        ("true", True),
        ("yes", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("", False),
    ],
)
def test_is_dry_run_reads_env(monkeypatch, val, expected):
    import agent_runner as ar

    monkeypatch.setenv("ALFRED_DRY_RUN", val)
    assert ar.is_dry_run() is expected


def test_set_dry_run_round_trips(monkeypatch):
    import agent_runner as ar

    monkeypatch.delenv("ALFRED_DRY_RUN", raising=False)
    assert ar.is_dry_run() is False
    ar.set_dry_run(True)
    assert ar.is_dry_run() is True
    ar.set_dry_run(False)
    assert ar.is_dry_run() is False


# ---------- LLM seam: no real claude / codex subprocess ----------


def test_claude_invoke_dry_run_makes_no_subprocess(monkeypatch):
    import agent_runner as ar

    monkeypatch.setenv("ALFRED_DRY_RUN", "1")
    monkeypatch.setattr(
        ar, "run", lambda *a, **kw: pytest.fail("claude_invoke shelled out under dry-run")
    )

    result = ar.claude_invoke("a prompt", workdir=Path("/tmp"), allowed_tools="", model="opus")

    assert result.success is True
    assert result.subtype == "success"
    assert result.cost_usd == 0.0
    assert result.raw.get("dry_run") is True
    assert "[dry-run]" in result.result_text


def test_codex_invoke_dry_run_makes_no_subprocess(monkeypatch):
    import agent_runner as ar

    monkeypatch.setenv("ALFRED_DRY_RUN", "1")

    def boom(*a, **kw):
        pytest.fail("codex_invoke shelled out under dry-run")

    monkeypatch.setattr(ar.subprocess, "run", boom)

    result = ar.codex_invoke("review this", workdir=Path("/tmp"), agent="reviewer")

    assert result.success is True
    assert result.cost_usd == 0.0
    assert result.raw.get("engine") == "codex"
    assert "[dry-run]" in result.result_text


def test_invoke_agent_engine_dry_run_does_not_call_real_engines(monkeypatch):
    """The default claude_fn / codex_fn route through the stubbed wrappers."""
    import agent_runner as ar

    monkeypatch.setenv("ALFRED_DRY_RUN", "1")
    monkeypatch.setattr(
        ar, "run", lambda *a, **kw: pytest.fail("real claude subprocess under dry-run")
    )
    monkeypatch.setattr(
        ar.subprocess, "run", lambda *a, **kw: pytest.fail("real codex subprocess under dry-run")
    )

    result, engine_used = ar.invoke_agent_engine(
        "implement this",
        engine="hybrid",
        agent="lucius",
        firing_id="f1",
        workdir=Path("/tmp"),
        claude_allowed_tools="Read,Edit",
        timeout=60,
    )
    assert result.success is True
    assert engine_used == "claude"  # synthetic claude success => no fallback


# ---------- Spend seam: no real ledger mutation ----------


def test_spend_state_dry_run_writes_separate_ledger(monkeypatch):
    import agent_runner as ar

    monkeypatch.setenv("ALFRED_DRY_RUN", "1")
    spend = ar.SpendState("lucius")
    real_path = spend._path
    spend.increment(firings_today=1, turns_today=10, cost_usd_today=1.5)
    spend.set(consecutive_failures=0)

    # The real per-day ledger is never written under dry-run.
    assert not real_path.exists()
    # A clearly-separate dry-run ledger is used instead.
    dry_path = real_path.with_name(f"spend-dryrun-{ar.today_str()}.json")
    assert dry_path.exists()
    data = json.loads(dry_path.read_text())
    assert data["firings_today"] == 1
    assert data["turns_today"] == 10


def test_spend_state_real_ledger_untouched_after_dry_run(monkeypatch):
    """A dry-run firing must not inflate the agent's real counters."""
    import agent_runner as ar

    # First: a real firing writes the real ledger.
    monkeypatch.delenv("ALFRED_DRY_RUN", raising=False)
    real = ar.SpendState("bane")
    real.increment(firings_today=1)
    real_path = real._path
    assert json.loads(real_path.read_text())["firings_today"] == 1

    # Then: a dry-run firing must leave that real ledger untouched.
    monkeypatch.setenv("ALFRED_DRY_RUN", "1")
    dry = ar.SpendState("bane")
    dry.increment(firings_today=5, turns_today=99)
    assert json.loads(real_path.read_text())["firings_today"] == 1


def test_set_global_block_dry_run_writes_no_poison_pill(monkeypatch):
    import agent_runner as ar

    monkeypatch.setenv("ALFRED_DRY_RUN", "1")
    until = ar.set_global_block(hours=1, reason="lucius-error_rate_limit")
    assert until  # caller still gets the until-string for its messaging
    assert not ar.GLOBAL_BLOCKED_FILE.exists()
    # And the fleet is therefore not actually blocked.
    assert ar.is_globally_blocked() is None


# ---------- Slack seam: no real webhook POST ----------


def test_slack_post_dry_run_does_not_hit_webhook(monkeypatch):
    import agent_runner as ar

    monkeypatch.setenv("ALFRED_DRY_RUN", "1")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example.test/x")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **kw: pytest.fail("slack_post hit the webhook under dry-run"),
    )

    assert ar.slack_post("the build shipped", severity="warn") is True


def test_slack_post_dry_run_logs_the_line(monkeypatch, capsys):
    import agent_runner as ar

    monkeypatch.setenv("ALFRED_DRY_RUN", "1")
    ar.slack_post("staging is down", severity="alert")
    out = capsys.readouterr().out
    assert "[dry-run]" in out
    assert "would post to Slack" in out
    assert "severity=alert" in out
    assert "staging is down" in out


# ---------- Slack seam: app-native send preferred over webhook ----------


class _WebhookProbe:
    """Records webhook POSTs so a test can assert the webhook was / was not hit."""

    def __init__(self):
        self.hits = 0

    def install(self, monkeypatch):
        outer = self

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b""

        def fake_urlopen(*a, **kw):
            outer.hits += 1
            return _Resp()

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        return self


def test_slack_post_prefers_app_when_home_channel_declared(monkeypatch):
    """With an explicit ``SLACK_HOME_CHANNEL``, ``slack_post`` sends via the
    app and never touches the webhook."""
    import agent_runner as ar
    import slack_format

    monkeypatch.delenv("ALFRED_DRY_RUN", raising=False)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example.test/x")
    monkeypatch.setenv("SLACK_HOME_CHANNEL", "eng-fleet")
    calls = {}

    def fake_post_flat(text, *, severity="info", channel=None):
        calls["text"] = text
        calls["severity"] = severity
        return True

    monkeypatch.setattr(slack_format, "post_flat", fake_post_flat)
    probe = _WebhookProbe().install(monkeypatch)

    assert ar.slack_post("shipped the fix", severity="warn") is True
    assert probe.hits == 0
    # Severity decoration still runs before the app hand-off.
    assert calls["severity"] == "warn"
    assert "shipped the fix" in calls["text"]


def test_slack_post_keeps_webhook_channel_when_not_opted_in(monkeypatch):
    """A webhook is configured but no home channel / opt-in: the app path
    must NOT silently take over the webhook's bound channel."""
    import agent_runner as ar
    import slack_format

    monkeypatch.delenv("ALFRED_DRY_RUN", raising=False)
    monkeypatch.delenv("SLACK_HOME_CHANNEL", raising=False)
    monkeypatch.delenv("ALFRED_SLACK_NATIVE_SENDS", raising=False)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example.test/x")
    monkeypatch.setattr(
        slack_format,
        "post_flat",
        lambda *a, **kw: pytest.fail("app path took over the webhook's channel without opt-in"),
    )
    probe = _WebhookProbe().install(monkeypatch)

    assert ar.slack_post("staging is down", severity="alert") is True
    assert probe.hits == 1


def test_slack_post_native_preferred_skips_webhook_resolution(monkeypatch):
    """When native sends are preferred and the app posts, the webhook resolver
    (which can block on an 8s AWS lookup) must not be called at all."""
    import agent_runner as ar
    import slack_format
    from agent_runner import notify as _notify

    monkeypatch.delenv("ALFRED_DRY_RUN", raising=False)
    monkeypatch.setenv("SLACK_HOME_CHANNEL", "eng-fleet")
    monkeypatch.setattr(slack_format, "post_flat", lambda *a, **kw: True)
    monkeypatch.setattr(
        _notify,
        "_resolve_webhook",
        lambda: pytest.fail("resolved the webhook before the app path"),
    )
    assert ar.slack_post("shipped", severity="info") is True


def test_slack_post_opt_in_flag_prefers_app_over_webhook(monkeypatch):
    """``ALFRED_SLACK_NATIVE_SENDS=1`` opts an install into app sends even
    when a webhook is present."""
    import agent_runner as ar
    import slack_format

    monkeypatch.delenv("ALFRED_DRY_RUN", raising=False)
    monkeypatch.delenv("SLACK_HOME_CHANNEL", raising=False)
    monkeypatch.setenv("ALFRED_SLACK_NATIVE_SENDS", "1")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example.test/x")
    monkeypatch.setattr(slack_format, "post_flat", lambda *a, **kw: True)
    probe = _WebhookProbe().install(monkeypatch)

    assert ar.slack_post("shipped", severity="info") is True
    assert probe.hits == 0


def test_slack_post_falls_back_to_webhook_when_app_declines(monkeypatch):
    """App preferred (home channel set) but post_flat returns False -> the
    legacy webhook still fires."""
    import agent_runner as ar
    import slack_format

    monkeypatch.delenv("ALFRED_DRY_RUN", raising=False)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example.test/x")
    monkeypatch.setenv("SLACK_HOME_CHANNEL", "eng-fleet")
    monkeypatch.setattr(slack_format, "post_flat", lambda *a, **kw: False)
    probe = _WebhookProbe().install(monkeypatch)

    assert ar.slack_post("staging is down", severity="alert") is True
    assert probe.hits == 1


# ---------- GitHub seam: no gh mutation ----------


def test_gh_mutators_dry_run_make_no_subprocess(monkeypatch):
    """gh helpers stub out cleanly even with NOTHING configured, no GH_ORG,
    no gh auth. ``_full_repo`` falls back to a clearly-fake org placeholder."""
    import agent_runner as ar

    monkeypatch.setenv("ALFRED_DRY_RUN", "1")
    monkeypatch.delenv("GH_ORG", raising=False)
    monkeypatch.setattr(
        ar, "run", lambda *a, **kw: pytest.fail("gh helper shelled out under dry-run")
    )

    assert ar.gh_issue_edit("backend", 7, add_labels=["agent:in-flight"]) is True
    assert ar.gh_issue_comment("backend", 7, "a comment") is True
    assert ar.gh_pr_comment("backend", 7, "a comment") is True
    ar.ensure_labels("backend")  # must not raise / shell out
    url = ar.gh_pr_create(
        "backend", title="feat: x", body_file=Path("/tmp/none"), head="b", labels=["agent:authored"]
    )
    # No GH_ORG configured -> the clearly-fake dry-run placeholder org.
    assert url and url.startswith("https://github.com/dry-run-org/backend/pull/")


def test_claim_and_release_issue_dry_run_make_no_subprocess(monkeypatch):
    import agent_runner as ar

    monkeypatch.setenv("ALFRED_DRY_RUN", "1")
    monkeypatch.delenv("GH_ORG", raising=False)
    monkeypatch.setattr(
        ar, "run", lambda *a, **kw: pytest.fail("claim/release shelled out under dry-run")
    )
    monkeypatch.setattr(
        ar, "_issue_state", lambda *a, **kw: pytest.fail("claim/release read gh under dry-run")
    )

    assert ar.claim_issue("backend", 7, codename="lucius", firing_id="f1") is True
    assert (
        ar.release_issue(
            "backend",
            7,
            codename="lucius",
            firing_id="f1",
            outcome="success",
            transition_to="agent:pr-open",
            pr_url="https://example.com/pr/1",
        )
        is True
    )
    assert (
        ar.force_release_stale_claim(
            "backend",
            7,
            sweep_id="sweep-1",
            released_codename="lucius",
            released_firing_id="f1",
        )
        is True
    )


# ---------- git seam: no real worktree mutation ----------


def test_make_worktree_dry_run_uses_throwaway_repo(monkeypatch):
    import agent_runner as ar

    monkeypatch.setenv("ALFRED_DRY_RUN", "1")
    wt, branch = ar.make_worktree("backend", "lucius", "275")

    # A real, self-contained git repo in a temp dir, never the operator's
    # configured WORKSPACE checkout, never WORKTREE_ROOT.
    assert wt.exists()
    assert str(ar.WORKSPACE) not in str(wt)
    assert str(ar.WORKTREE_ROOT) not in str(wt)
    assert branch.startswith("lucius/275-")

    # The throwaway repo is coherent: one commit ahead of origin/main, so a
    # runner inspecting it sees the "engine committed" state.
    revs = ar.run(["git", "rev-list", "origin/main..HEAD"], cwd=str(wt), timeout=10).stdout.strip()
    assert len([line for line in revs.splitlines() if line.strip()]) == 1

    ar.remove_worktree("backend", wt)
    assert not wt.exists()


def test_make_worktree_dry_run_accepts_absolute_repo_map_path(monkeypatch, tmp_path):
    import agent_runner as ar

    absolute_repo = str(tmp_path / "tools" / "alfred-os")
    monkeypatch.setenv("ALFRED_DRY_RUN", "1")

    wt, branch = ar.make_worktree(absolute_repo, "senior-dev", "275")

    assert wt.exists()
    assert "/" not in wt.name
    assert "alfred-os" in wt.name
    assert branch.startswith("senior-dev/275-")

    ar.remove_worktree(absolute_repo, wt)
    assert not wt.exists()


def test_review_worktree_dry_run_accepts_absolute_repo_map_path(monkeypatch, tmp_path):
    import agent_runner as ar

    absolute_repo = str(tmp_path / "tools" / "alfred-os")
    monkeypatch.setenv("ALFRED_DRY_RUN", "1")

    wt = ar.make_worktree_from_branch(absolute_repo, "reviewer", "feature/runtime-map", "436")

    assert wt.exists()
    assert "/" not in wt.name
    assert "alfred-os" in wt.name

    ar.remove_worktree(absolute_repo, wt)
    assert not wt.exists()


# ---------- end-to-end: example runners complete exit-0 with zero config ----------


def _run_example(script: str, env_extra: dict, tmp_path) -> subprocess.CompletedProcess:
    env = {
        "ALFRED_HOME": str(tmp_path / "alfred"),
        "WORKSPACE_ROOT": str(tmp_path / "workspace"),
        "PYTHONPATH": str(REPO_ROOT / "lib"),
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        **env_extra,
    }
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / script)],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )


def test_hello_example_dry_run_completes_exit_0(tmp_path):
    proc = _run_example("examples/bin/hello.py", {"ALFRED_DRY_RUN": "1"}, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "[dry-run]" in proc.stdout
    assert "would post to Slack" in proc.stdout


def test_echo_example_dry_run_completes_full_lifecycle_exit_0(tmp_path):
    """Echo runs pick -> claim -> invoke -> comment -> release with zero
    host config (no gh auth, no Claude, no Slack) and exits 0."""
    proc = _run_example("examples/bin/echo_summarise.py", {"ALFRED_DRY_RUN": "1"}, tmp_path)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    # The whole lifecycle is narrated.
    assert "(pick)" in out
    assert "would claim" in out
    assert "would invoke claude" in out
    assert "would `gh issue comment" in out
    assert "would release" in out
    assert "would post to Slack" in out


def test_senior_dev_runner_dry_run_completes_full_lifecycle_exit_0(tmp_path):
    """Lucius runs pick -> claim -> worktree -> invoke -> push/PR -> release
    with zero host config and exits 0 on the happy path."""
    proc = _run_example(
        "bin/senior-dev.py",
        {"ALFRED_DRY_RUN": "1", "AGENT_CODENAME": "lucius-dry-run-test"},
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "would `git worktree add" in out
    assert "would invoke claude" in out
    assert "would `git push" in out
    assert "would `gh pr create" in out
    assert "shipped" in out


def test_dry_run_writes_no_real_spend_ledger_for_runners(tmp_path):
    """After a dry-run firing only the dry-run ledger exists, never the real one."""
    proc = _run_example("examples/bin/echo_summarise.py", {"ALFRED_DRY_RUN": "1"}, tmp_path)
    assert proc.returncode == 0, proc.stderr
    echo_state = tmp_path / "alfred" / "state" / "echo"
    ledgers = sorted(p.name for p in echo_state.glob("spend-*.json")) if echo_state.exists() else []
    # Exactly the dry-run ledger, no real spend-<date>.json.
    assert ledgers, "expected a dry-run ledger to be written"
    assert all(name.startswith("spend-dryrun-") for name in ledgers), ledgers
