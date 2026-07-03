#!/usr/bin/env python3
"""Tests for the durable-goal runtime bridge (``lib/goal_context.py``).

Covers the read-mostly bridge that turns active goals into a standing-objective
block a runner can inject, plus the feature-guard that keeps it a no-op for
hosts with no active goals:

  * Selection: active goals are picked by repo (exact, org-prefixed, .git, and
    case-insensitive spellings), empty-``repos`` goals are fleet-wide, and
    draft/paused/terminal goals are never selected. The no-active-goal and
    feature-off cases both return ``[]`` (the no-op signal).
  * Rendering: ``render_goal_context`` renders the operator contract fields and
    caps the goal count; an empty list renders blank.
  * Injection helpers: ``append_system_prompt_args`` emits the native Claude
    ``--append-system-prompt`` flag with the block, and ``prepend_to_prompt``
    prepends the same block for the Codex path (unchanged when no goal).
  * Lifecycle logging: ``log_pr_event_for_repo`` appends an ``attempted`` /
    ``evidence_added`` event to every matching active goal, never raises, and
    is a no-op when the feature is off.

All ledger state lands in a tmp root passed explicitly, so nothing touches the
runtime's real ``$ALFRED_HOME/state/goals`` tree. Wiring the block into the
Claude/Codex command lines and the SessionStart hook is a separate runner-side
change; those consumer paths are exercised where they live, not here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import goal_context  # noqa: E402
import goals  # noqa: E402


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A tmp goals ledger root, passed explicitly to every goals call."""
    d = tmp_path / "alfred_home" / "state" / "goals"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def armed(monkeypatch):
    """Arm the bridge feature-guard for the duration of a test."""
    monkeypatch.setenv(goal_context.GOAL_WIRING_ENV, "1")


def _active_goal(root: Path, outcome: str, **kw) -> goals.Goal:
    """Create + approve a goal so it is in ACTIVE status on disk."""
    g = goals.create(outcome, root=root, **kw)
    goals.approve(g.id, root=root)
    return goals.get(g.id, root=root)


# ---------------------------------------------------------------------------
# Feature guard
# ---------------------------------------------------------------------------
def test_feature_off_is_noop_even_with_active_goal(root, monkeypatch):
    monkeypatch.delenv(goal_context.GOAL_WIRING_ENV, raising=False)
    _active_goal(root, "Ship billing", repos=["your-backend"])
    assert goal_context.active_goals_for_repo("your-backend", root=root) == []
    assert goal_context.goal_context_block("your-backend", root=root) == ""
    assert goal_context.append_system_prompt_args("your-backend", root=root) == []
    assert goal_context.prepend_to_prompt("PROMPT", "your-backend", root=root) == "PROMPT"


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE"])
def test_feature_guard_truthy_vocab(monkeypatch, value):
    monkeypatch.setenv(goal_context.GOAL_WIRING_ENV, value)
    assert goal_context.goal_wiring_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "  "])
def test_feature_guard_falsy_vocab(monkeypatch, value):
    monkeypatch.setenv(goal_context.GOAL_WIRING_ENV, value)
    assert goal_context.goal_wiring_enabled() is False


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------
def test_no_active_goal_is_noop(root, armed):
    # A draft goal exists but is not active: selection must skip it.
    goals.create("Draft only", root=root, repos=["your-backend"])
    assert goal_context.active_goals_for_repo("your-backend", root=root) == []
    assert goal_context.goal_context_block("your-backend", root=root) == ""


def test_empty_ledger_is_noop(root, armed):
    assert goal_context.active_goals_for_repo("your-backend", root=root) == []


def test_selects_active_goal_for_matching_repo(root, armed):
    g = _active_goal(root, "Ship billing", repos=["your-backend"])
    selected = goal_context.active_goals_for_repo("your-backend", root=root)
    assert [s.id for s in selected] == [g.id]


def test_does_not_select_for_unrelated_repo(root, armed):
    _active_goal(root, "Ship billing", repos=["your-backend"])
    assert goal_context.active_goals_for_repo("your-frontend", root=root) == []


def test_empty_repos_goal_is_fleet_wide(root, armed):
    g = _active_goal(root, "Reduce CI flakiness everywhere")  # no repos => all
    assert [s.id for s in goal_context.active_goals_for_repo("your-backend", root=root)] == [g.id]
    assert [s.id for s in goal_context.active_goals_for_repo("your-mobile", root=root)] == [g.id]


@pytest.mark.parametrize(
    "goal_repo,firing_repo",
    [
        ("your-org/your-backend", "your-backend"),
        ("your-backend.git", "your-backend"),
        ("Your-Backend", "your-backend"),
        ("your-backend", "your-org/your-backend"),
        ("https://github.com/your-org/your-backend", "your-backend"),
    ],
)
def test_repo_normalization_matches(root, armed, goal_repo, firing_repo):
    g = _active_goal(root, "Normalize me", repos=[goal_repo])
    selected = goal_context.active_goals_for_repo(firing_repo, root=root)
    assert [s.id for s in selected] == [g.id]


def test_blank_repo_is_noop(root, armed):
    _active_goal(root, "Ship billing")  # fleet-wide
    assert goal_context.active_goals_for_repo("", root=root) == []


def test_terminal_and_paused_goals_excluded(root, armed):
    achieved = goals.create("Done thing", root=root, repos=["your-backend"])
    goals.approve(achieved.id, root=root)
    goals.achieve(achieved.id, root=root)
    paused = goals.create("Parked thing", root=root, repos=["your-backend"])
    goals.approve(paused.id, root=root)
    goals.pause(paused.id, root=root)
    assert goal_context.active_goals_for_repo("your-backend", root=root) == []


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def test_render_contains_contract_fields(root, armed):
    g = _active_goal(
        root,
        "Reach 99.9% uptime",
        repos=["your-backend"],
        verification=["staging green for 7 days"],
        constraints=["no schema migrations"],
        non_goals=["frontend redesign"],
        human_gates=["before touching prod config"],
        iteration_policy="retry up to 3x",
        blocked_condition="rate limit from provider",
    )
    block = goal_context.goal_context_block("your-backend", root=root)
    assert goal_context.CONTEXT_HEADER in block
    assert "Reach 99.9% uptime" in block
    assert "staging green for 7 days" in block
    assert "no schema migrations" in block
    assert "frontend redesign" in block
    assert "before touching prod config" in block
    assert "retry up to 3x" in block
    assert "rate limit from provider" in block
    assert g.id in block


def test_render_empty_list_is_blank():
    assert goal_context.render_goal_context([]) == ""


def test_render_caps_goal_count(root, armed):
    for i in range(5):
        _active_goal(root, f"Objective number {i}", repos=["your-backend"])
    block = goal_context.goal_context_block("your-backend", root=root)
    # Only _MAX_GOALS outcomes are rendered, even though 5 are active.
    rendered = sum(f"Objective number {i}" in block for i in range(5))
    assert rendered == goal_context._MAX_GOALS


# ---------------------------------------------------------------------------
# Claude injection helper - native --append-system-prompt
# ---------------------------------------------------------------------------
def test_append_system_prompt_args_present_when_goal(root, armed):
    _active_goal(root, "Ship billing", repos=["your-backend"])
    args = goal_context.append_system_prompt_args("your-backend", root=root)
    assert args[0] == "--append-system-prompt"
    assert goal_context.CONTEXT_HEADER in args[1]


def test_append_system_prompt_args_empty_when_no_goal(root, armed):
    assert goal_context.append_system_prompt_args("your-backend", root=root) == []


# ---------------------------------------------------------------------------
# Codex injection helper - prompt prepend
# ---------------------------------------------------------------------------
def test_prepend_to_prompt_adds_block(root, armed):
    _active_goal(root, "Ship billing", repos=["your-backend"])
    out = goal_context.prepend_to_prompt("DO THE TASK", "your-backend", root=root)
    assert goal_context.CONTEXT_HEADER in out
    assert out.rstrip().endswith("DO THE TASK")


def test_prepend_to_prompt_noop_without_goal(root, armed):
    assert goal_context.prepend_to_prompt("DO THE TASK", "your-backend", root=root) == "DO THE TASK"


# ---------------------------------------------------------------------------
# Lifecycle logging
# ---------------------------------------------------------------------------
def test_log_pr_event_appends_attempted(root, armed):
    g = _active_goal(root, "Ship billing", repos=["your-backend"])
    logged = goal_context.log_pr_event_for_repo(
        "your-backend",
        root=root,
        firing_id="f1",
        engine="claude",
        pr_url="https://pr/1",
    )
    assert logged == [g.id]
    events = goals.read_events(g.id, root=root)
    attempts = [e for e in events if e["event"] == goals.EVENT_ATTEMPTED]
    assert len(attempts) == 1
    assert attempts[0]["pr_url"] == "https://pr/1"
    assert attempts[0]["engine"] == "claude"
    # Status is unchanged - logging is additive, not a transition.
    assert goals.get(g.id, root=root).status == goals.ACTIVE


def test_log_pr_event_evidence_variant(root, armed):
    g = _active_goal(root, "Ship billing", repos=["your-backend"])
    goal_context.log_pr_event_for_repo(
        "your-backend",
        root=root,
        event=goals.EVENT_EVIDENCE_ADDED,
        kind="tests",
        ref="https://pr/1",
    )
    events = goals.read_events(g.id, root=root)
    assert any(e["event"] == goals.EVENT_EVIDENCE_ADDED for e in events)


def test_log_pr_event_logs_every_matching_goal(root, armed):
    g1 = _active_goal(root, "Goal one", repos=["your-backend"])
    g2 = _active_goal(root, "Goal two")  # fleet-wide also matches
    logged = set(goal_context.log_pr_event_for_repo("your-backend", root=root, firing_id="f1"))
    assert logged == {g1.id, g2.id}


def test_log_pr_event_noop_when_feature_off(root, monkeypatch):
    monkeypatch.delenv(goal_context.GOAL_WIRING_ENV, raising=False)
    g = _active_goal(root, "Ship billing", repos=["your-backend"])
    assert goal_context.log_pr_event_for_repo("your-backend", root=root) == []
    assert not any(e["event"] == goals.EVENT_ATTEMPTED for e in goals.read_events(g.id, root=root))


def test_log_pr_event_noop_for_unrelated_repo(root, armed):
    g = _active_goal(root, "Ship billing", repos=["your-backend"])
    assert goal_context.log_pr_event_for_repo("your-frontend", root=root) == []
    assert not any(e["event"] == goals.EVENT_ATTEMPTED for e in goals.read_events(g.id, root=root))
