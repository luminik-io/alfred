"""Tests for ``lib/goals.py`` - the durable goal ledger.

Covers create/get/list, the validated lifecycle state machine (legal +
rejected transitions), pause/resume/clear, events.jsonl append behavior,
and evidence/attempt recording. All on-disk state lands in a tmp
ALFRED_HOME via the ``goals_root`` override, so nothing touches the
operator's real ``$ALFRED_HOME/state/goals`` tree.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent / "lib"
sys.path.insert(0, str(LIB))

import goals  # noqa: E402


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A tmp goals ledger root, passed explicitly to every goals call."""
    d = tmp_path / "alfred_home" / "state" / "goals"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_goal_cli():
    path = Path(__file__).resolve().parent.parent / "bin" / "alfred-goal.py"
    spec = importlib.util.spec_from_file_location("alfred_goal_cli_under_test", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Id scheme - deterministic, no clock / RNG reliance.
# ---------------------------------------------------------------------------
def test_make_goal_id_is_deterministic():
    a = goals.make_goal_id("Make onboarding work end to end")
    b = goals.make_goal_id("Make onboarding work end to end")
    assert a == b
    assert a.startswith("make-onboarding-work-end-to-end-")
    # slug + 8-char hex suffix
    assert len(a.rsplit("-", 1)[1]) == 8


def test_make_goal_id_differs_by_outcome():
    assert goals.make_goal_id("ship feature A") != goals.make_goal_id("ship feature B")


def test_slugify_handles_empty_and_symbols():
    assert goals.slugify("") == "goal"
    assert goals.slugify("!!!") == "goal"
    assert goals.slugify("Hello, World!") == "hello-world"


# ---------------------------------------------------------------------------
# create / get / list.
# ---------------------------------------------------------------------------
def test_create_returns_draft_with_fields(root: Path):
    g = goals.create(
        "Make the shipped board truthful",
        verification=["pytest green", "manual board screenshot"],
        constraints=["do not touch billing"],
        non_goals=["redesign the UI"],
        iteration_policy="retry failing checks; ask after 3 fails",
        human_gates=["before merging"],
        blocked_condition="gh auth fails twice",
        owner="alice",
        repos=["your-org/your-repo"],
        source_refs=["slack:thread-123"],
        root=root,
    )
    assert g.status == goals.DRAFT
    assert g.outcome == "Make the shipped board truthful"
    assert g.verification == ["pytest green", "manual board screenshot"]
    assert g.constraints == ["do not touch billing"]
    assert g.non_goals == ["redesign the UI"]
    assert g.iteration_policy.startswith("retry")
    assert g.human_gates == ["before merging"]
    assert g.blocked_condition == "gh auth fails twice"
    assert g.owner == "alice"
    assert g.repos == ["your-org/your-repo"]
    assert g.source_refs == ["slack:thread-123"]
    assert g.created_at and g.updated_at


def test_create_persists_goal_json(root: Path):
    g = goals.create("ship a thing", root=root)
    gj = root / g.id / "goal.json"
    assert gj.exists()
    data = json.loads(gj.read_text())
    assert data["id"] == g.id
    assert data["status"] == goals.DRAFT
    assert data["outcome"] == "ship a thing"


def test_create_rejects_empty_outcome(root: Path):
    with pytest.raises(ValueError):
        goals.create("   ", root=root)


def test_get_round_trips(root: Path):
    g = goals.create("round trip me", owner="alice", root=root)
    loaded = goals.get(g.id, root=root)
    assert loaded.id == g.id
    assert loaded.outcome == "round trip me"
    assert loaded.owner == "alice"


def test_get_missing_raises(root: Path):
    with pytest.raises(goals.GoalNotFound):
        goals.get("does-not-exist", root=root)


def test_str_coercion_for_single_values(root: Path):
    g = goals.create("coerce me", verification="only one check", root=root)
    assert g.verification == ["only one check"]


def test_duplicate_outcome_gets_suffixed_id(root: Path):
    g1 = goals.create("same outcome", root=root)
    g2 = goals.create("same outcome", root=root)
    assert g1.id != g2.id
    assert g2.id == f"{g1.id}-2"
    assert goals.exists(g1.id, root=root)
    assert goals.exists(g2.id, root=root)


def test_explicit_id_collision_raises(root: Path):
    goals.create("first", goal_id="fixed-id", root=root)
    with pytest.raises(goals.GoalExists):
        goals.create("second", goal_id="fixed-id", root=root)


@pytest.mark.parametrize(
    "bad_id",
    [
        "",
        "../escape",
        "/tmp/escape",
        "bad/id",
        "bad\\id",
        "bad.id",
        "UPPER",
        "bad-",
    ],
)
def test_goal_id_validation_rejects_path_components(root: Path, bad_id: str):
    with pytest.raises(ValueError):
        goals.exists(bad_id, root=root)
    with pytest.raises(ValueError):
        goals.create("unsafe explicit id", goal_id=bad_id, root=root)

    assert not any(root.iterdir())


def test_list_empty(root: Path):
    assert goals.list_goals(root=root) == []


def test_list_returns_all_and_filters_by_status(root: Path):
    a = goals.create("goal a", root=root)
    b = goals.create("goal b", root=root)
    goals.approve(b.id, root=root)
    all_goals = goals.list_goals(root=root)
    assert {g.id for g in all_goals} == {a.id, b.id}
    drafts = goals.list_goals(status=goals.DRAFT, root=root)
    assert [g.id for g in drafts] == [a.id]
    actives = goals.list_goals(status=goals.ACTIVE, root=root)
    assert [g.id for g in actives] == [b.id]


def test_list_skips_corrupt_goal_dir(root: Path):
    good = goals.create("good goal", root=root)
    bad_dir = root / "broken-goal"
    bad_dir.mkdir()
    (bad_dir / "goal.json").write_text("{not valid json")
    listed = goals.list_goals(root=root)
    assert [g.id for g in listed] == [good.id]


def test_list_skips_valid_json_missing_required_goal_fields(root: Path):
    good = goals.create("good goal", root=root)
    bad_dir = root / "missing-required-fields"
    bad_dir.mkdir()
    (bad_dir / "goal.json").write_text(json.dumps({"status": goals.DRAFT}))
    listed = goals.list_goals(root=root)
    assert [g.id for g in listed] == [good.id]


def test_get_valid_json_missing_required_goal_fields_raises_not_found(root: Path):
    bad_dir = root / "missing-required-fields"
    bad_dir.mkdir()
    (bad_dir / "goal.json").write_text(json.dumps({"status": goals.DRAFT}))
    with pytest.raises(goals.GoalNotFound):
        goals.get("missing-required-fields", root=root)


# ---------------------------------------------------------------------------
# Lifecycle state machine - legal + rejected transitions.
# ---------------------------------------------------------------------------
def test_transition_table_is_consistent():
    # Every transition's src/dst are real statuses.
    for t in goals.all_transitions():
        assert t.src in goals.STATUSES
        assert t.dst in goals.STATUSES
    # Terminal states have no outgoing transitions.
    for terminal in goals.TERMINAL_STATUSES:
        assert goals.legal_transitions(terminal) == ()


def test_is_legal_transition_predicates():
    assert goals.is_legal_transition(goals.DRAFT, goals.ACTIVE)
    assert goals.is_legal_transition(goals.ACTIVE, goals.PAUSED)
    assert goals.is_legal_transition(goals.PAUSED, goals.ACTIVE)
    assert goals.is_legal_transition(goals.ACTIVE, goals.ACHIEVED)
    assert not goals.is_legal_transition(goals.DRAFT, goals.ACHIEVED)
    assert not goals.is_legal_transition(goals.DRAFT, goals.PAUSED)
    assert not goals.is_legal_transition(goals.ACHIEVED, goals.ACTIVE)


def test_approve_draft_to_active(root: Path):
    g = goals.create("approve me", root=root)
    out = goals.approve(g.id, root=root)
    assert out.status == goals.ACTIVE
    assert goals.get(g.id, root=root).status == goals.ACTIVE


def test_set_status_rejects_illegal_move(root: Path):
    g = goals.create("no skipping", root=root)
    with pytest.raises(goals.InvalidTransition):
        goals.set_status(g.id, goals.ACHIEVED, root=root)
    # Goal unchanged on disk.
    assert goals.get(g.id, root=root).status == goals.DRAFT


def test_set_status_rejects_unknown_status(root: Path):
    g = goals.create("bad status", root=root)
    with pytest.raises(ValueError):
        goals.set_status(g.id, "frozen", root=root)


def test_achieve_is_terminal(root: Path):
    g = goals.create("finish me", root=root)
    goals.approve(g.id, root=root)
    goals.achieve(g.id, root=root)
    assert goals.get(g.id, root=root).status == goals.ACHIEVED
    with pytest.raises(goals.InvalidTransition):
        goals.resume(g.id, root=root)
    with pytest.raises(goals.InvalidTransition):
        goals.set_status(g.id, goals.ACTIVE, root=root)


def test_updated_at_advances_on_transition(root: Path):
    g = goals.create("touch me", root=root)
    before = goals.get(g.id, root=root).updated_at
    goals.approve(g.id, root=root)
    after = goals.get(g.id, root=root).updated_at
    assert after >= before


# ---------------------------------------------------------------------------
# pause / resume / clear.
# ---------------------------------------------------------------------------
def test_pause_resume_round_trip(root: Path):
    g = goals.create("pause me", root=root)
    goals.approve(g.id, root=root)
    goals.pause(g.id, root=root)
    assert goals.get(g.id, root=root).status == goals.PAUSED
    goals.resume(g.id, root=root)
    assert goals.get(g.id, root=root).status == goals.ACTIVE


def test_pause_is_idempotent(root: Path):
    g = goals.create("idempotent pause", root=root)
    goals.approve(g.id, root=root)
    goals.pause(g.id, root=root)
    # Second pause is a no-op, not an error.
    out = goals.pause(g.id, root=root)
    assert out.status == goals.PAUSED


def test_cannot_pause_a_draft(root: Path):
    g = goals.create("draft pause", root=root)
    with pytest.raises(goals.InvalidTransition):
        goals.pause(g.id, root=root)


def test_resume_does_not_approve_draft(root: Path):
    g = goals.create("draft resume", root=root)
    with pytest.raises(goals.InvalidTransition):
        goals.resume(g.id, root=root)
    assert goals.get(g.id, root=root).status == goals.DRAFT
    assert [e["event"] for e in goals.read_events(g.id, root=root)] == [goals.EVENT_CREATED]


def test_clear_from_draft(root: Path):
    g = goals.create("clear draft", root=root)
    goals.clear(g.id, root=root)
    assert goals.get(g.id, root=root).status == goals.CLEARED


def test_clear_from_paused(root: Path):
    g = goals.create("clear paused", root=root)
    goals.approve(g.id, root=root)
    goals.pause(g.id, root=root)
    goals.clear(g.id, root=root)
    assert goals.get(g.id, root=root).status == goals.CLEARED


def test_clear_is_idempotent(root: Path):
    g = goals.create("clear twice", root=root)
    goals.clear(g.id, root=root)
    out = goals.clear(g.id, root=root)
    assert out.status == goals.CLEARED


def test_block_then_unblock(root: Path):
    g = goals.create("block me", root=root)
    goals.approve(g.id, root=root)
    goals.block(g.id, root=root)
    assert goals.get(g.id, root=root).status == goals.BLOCKED
    goals.resume(g.id, root=root)
    assert goals.get(g.id, root=root).status == goals.ACTIVE


# ---------------------------------------------------------------------------
# events.jsonl append + evidence/attempts.
# ---------------------------------------------------------------------------
def test_create_emits_created_event(root: Path):
    g = goals.create("watch my events", owner="alice", root=root)
    events = goals.read_events(g.id, root=root)
    assert len(events) == 1
    assert events[0]["event"] == goals.EVENT_CREATED
    assert events[0]["goal_id"] == g.id
    assert events[0]["owner"] == "alice"
    assert "ts" in events[0]


def test_lifecycle_events_append_in_order(root: Path):
    g = goals.create("full lifecycle", root=root)
    goals.approve(g.id, root=root)
    goals.start(g.id, firing_id="20260602-0001-ab", root=root)
    goals.add_attempt(g.id, firing_id="20260602-0001-ab", engine="claude", root=root)
    goals.add_evidence(g.id, kind="tests", ref="pytest -q", root=root)
    goals.pause(g.id, reason="operator stepped away", root=root)
    goals.resume(g.id, root=root)
    goals.achieve(g.id, root=root)
    names = [e["event"] for e in goals.read_events(g.id, root=root)]
    assert names == [
        goals.EVENT_CREATED,
        goals.EVENT_APPROVED,
        goals.EVENT_STARTED,
        goals.EVENT_ATTEMPTED,
        goals.EVENT_EVIDENCE_ADDED,
        goals.EVENT_PAUSED,
        goals.EVENT_RESUMED,
        goals.EVENT_ACHIEVED,
    ]


def test_events_jsonl_is_one_record_per_line(root: Path):
    g = goals.create("line check", root=root)
    goals.approve(g.id, root=root)
    raw = (root / g.id / "events.jsonl").read_text()
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    assert len(lines) == 2
    for ln in lines:
        json.loads(ln)  # each line parses on its own


def test_add_event_rejects_unknown_event(root: Path):
    g = goals.create("bad event", root=root)
    with pytest.raises(ValueError):
        goals.add_event(g.id, "exploded", root=root)


def test_add_evidence_records_fields(root: Path):
    g = goals.create("evidence carrier", root=root)
    goals.approve(g.id, root=root)
    goals.add_evidence(g.id, kind="screenshot", ref="https://example/evidence.png", root=root)
    ev = [e for e in goals.read_events(g.id, root=root) if e["event"] == goals.EVENT_EVIDENCE_ADDED]
    assert len(ev) == 1
    assert ev[0]["kind"] == "screenshot"
    assert ev[0]["ref"] == "https://example/evidence.png"


def test_add_evidence_rejected_on_terminal_goal(root: Path):
    g = goals.create("terminal evidence", root=root)
    goals.clear(g.id, root=root)
    with pytest.raises(goals.InvalidTransition):
        goals.add_evidence(g.id, kind="tests", root=root)


def test_start_requires_active(root: Path):
    g = goals.create("start gate", root=root)
    with pytest.raises(goals.InvalidTransition):
        goals.start(g.id, root=root)


def test_read_events_missing_returns_empty(root: Path):
    assert goals.read_events("nope", root=root) == []


# ---------------------------------------------------------------------------
# STATE_ROOT-based default path resolution (the production code path).
#
# The non-test code path resolves goals_root() from
# agent_runner_paths.STATE_ROOT lazily. We assert that resolution here
# rather than calling create() with no root, so this test never risks
# writing into the operator's real ~/.alfred/state/goals if another test
# in a long suite leaves STATE_ROOT in an unexpected place.
# ---------------------------------------------------------------------------
def test_goals_root_follows_state_root(monkeypatch, tmp_path: Path):
    from agent_runner import paths as agent_runner_paths

    fake_state = tmp_path / "fake_state"
    monkeypatch.setattr(agent_runner_paths, "STATE_ROOT", fake_state)

    resolved = goals.goals_root()
    assert resolved == fake_state / "goals"

    # And create() with no explicit root lands under the resolved dir.
    g = goals.create("via state root", root=resolved)
    assert (resolved / g.id / "goal.json").exists()
    assert goals.get(g.id, root=resolved).outcome == "via state root"


def test_goals_root_explicit_override_wins(tmp_path: Path):
    """An explicit root bypasses STATE_ROOT entirely."""
    override = tmp_path / "explicit"
    assert goals.goals_root(override) == override


def test_goal_cli_approve_activates_draft(monkeypatch, root: Path, capsys):
    from agent_runner import paths as agent_runner_paths

    monkeypatch.setattr(agent_runner_paths, "STATE_ROOT", root.parent)
    cli = _load_goal_cli()

    assert cli.main(["create", "CLI activation", "--id", "cli-activation"]) == 0
    created = capsys.readouterr()
    assert "alfred goal approve cli-activation" in created.out

    assert cli.main(["approve", "cli-activation", "--reason", "operator ready"]) == 0
    assert goals.get("cli-activation", root=root).status == goals.ACTIVE
    events = goals.read_events("cli-activation", root=root)
    assert [e["event"] for e in events] == [goals.EVENT_CREATED, goals.EVENT_APPROVED]
    assert events[-1]["reason"] == "operator ready"


def test_goal_cli_activate_alias_approves(monkeypatch, root: Path, capsys):
    from agent_runner import paths as agent_runner_paths

    monkeypatch.setattr(agent_runner_paths, "STATE_ROOT", root.parent)
    cli = _load_goal_cli()

    assert cli.main(["create", "CLI activation alias", "--id", "cli-activate"]) == 0
    capsys.readouterr()
    assert cli.main(["activate", "cli-activate"]) == 0
    assert goals.get("cli-activate", root=root).status == goals.ACTIVE
