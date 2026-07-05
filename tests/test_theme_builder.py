"""Conversational roster theme builder: prompt seeding, turn parsing, proposals.

Covers the theme-builder converse flow (``lib/theme_builder.py``): the prompt is
seeded with the roster contract read from ``roster_manifest.json``; a model turn
emitting a valid ``propose_theme`` action surfaces a ``custom_names`` map covering
the roles; a malformed proposal degrades safely to a plain reply and never
raises. Models the compose-converse/action suites: no live model is called, the
engine is injected.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB = REPO_ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import compose_converse as cc  # noqa: E402
import theme_builder as tb  # noqa: E402
from roster_theme_store import roster_contract_agents  # noqa: E402

# The nine core engineering role-slugs the prompt lists and a complete proposal
# must cover. Kept as a literal here only to assert the code derives the SAME set;
# the completeness gate itself uses ``tb.required_codenames()``.
_ENGINEERING_SLUGS = {
    "triage",
    "planner",
    "spec-planner",
    "architect",
    "senior-dev",
    "test-engineer",
    "fixer",
    "reviewer",
    "e2e-runner",
}


def _messages(*texts: str) -> list[cc.ConverseMessage]:
    return [cc.ConverseMessage(role="user", content=text) for text in texts]


def _valid() -> frozenset[str]:
    return tb.valid_codenames()


def _required() -> frozenset[str]:
    return tb.required_codenames()


def _required_names(**overrides: str) -> dict[str, str]:
    """A minimal COMPLETE name map: every REQUIRED core role, no optional ones.

    A complete proposal must name every required role but MAY omit the optional
    ops/release agents. Seeding only the required roles proves that a small theme
    which skips the ops agents still parses as complete. Each role gets a distinct
    slug-derived name by default; ``overrides`` swap in specific names/slugs.
    """
    names = {slug: f"Name-{slug}" for slug in _required()}
    names.update(overrides)
    return names


def _full_names(**overrides: str) -> dict[str, str]:
    """A full-coverage name map: EVERY roster role (required + optional) named.

    A superset of ``_required_names`` used where a test wants the whole roster
    covered. Each role gets a distinct slug-derived name; ``overrides`` swap in
    specific names/slugs.
    """
    names = {slug: f"Name-{slug}" for slug in _valid()}
    names.update(overrides)
    return names


# --- roster contract seeding -------------------------------------------------


def test_roster_contract_agents_covers_the_engineering_roster() -> None:
    slugs = {agent.codename for agent in roster_contract_agents()}
    assert slugs >= _ENGINEERING_SLUGS


def test_valid_codenames_matches_the_manifest() -> None:
    assert tb.valid_codenames() == frozenset(a.codename for a in roster_contract_agents())


def test_required_codenames_is_exactly_the_core_engineering_roles() -> None:
    # The completeness set the code derives must equal the nine core roles the
    # prompt lists, so prompt and gate never diverge.
    assert tb.required_codenames() == frozenset(_ENGINEERING_SLUGS)


def test_required_codenames_excludes_optional_ops_and_release_agents() -> None:
    # Ops and release agents are optional: they are in the full roster but never
    # in the required set, so a theme may omit them.
    required = tb.required_codenames()
    optional = tb.valid_codenames() - required
    assert optional  # there ARE optional agents (ops/release)
    for agent in roster_contract_agents():
        if agent.role in tb.OPTIONAL_ROLES:
            assert agent.codename in optional
            assert agent.codename not in required


def test_build_roster_contract_lists_every_agent_with_slug_and_name() -> None:
    contract = tb.build_roster_contract(roster_contract_agents())
    # Each engineering slug appears verbatim so the model keys the proposal on it.
    for slug in _ENGINEERING_SLUGS:
        assert f"`{slug}`" in contract
    # The current Batman names appear so the model knows the starting point.
    assert "Batman" in contract  # architect
    assert "Ra's al Ghul" in contract  # reviewer


def test_render_system_prompt_injects_the_contract() -> None:
    captured: dict[str, object] = {}

    def fake_loader(path: Path, *, extra_vars: dict[str, str]) -> str:
        captured["path"] = path
        captured["vars"] = extra_vars
        return f"SYSTEM with ${{ROSTER_CONTRACT}} -> {extra_vars['ROSTER_CONTRACT']}"

    rendered = tb.render_system_prompt(
        prompt_path=Path("prompts/theme-builder.md"),
        loader=fake_loader,
    )
    assert "ROSTER_CONTRACT" in captured["vars"]  # type: ignore[operator]
    # The seeded contract carries the role-slugs the store persists under.
    assert "`architect`" in rendered
    assert "`reviewer`" in rendered


# --- parse_proposal: validation ----------------------------------------------


def test_parse_proposal_accepts_a_full_team() -> None:
    names = _full_names(architect="Gandalf", reviewer="Galadriel")
    proposal = tb.parse_proposal({"custom_names": names}, valid=_valid())
    assert proposal is not None
    assert proposal.custom_names == names
    assert proposal.custom_roles == {}


def test_parse_proposal_accepts_required_roles_omitting_optional_ops() -> None:
    # The core case that broke the flow: the model follows the prompt and names
    # every required engineering role but omits the optional ops/release agents.
    # That IS a complete, saveable proposal; the omitted agents keep base names.
    names = _required_names(architect="Gandalf", reviewer="Galadriel")
    assert not (tb.valid_codenames() - _required()) & set(names)  # no optional slugs present
    proposal = tb.parse_proposal({"custom_names": names}, valid=_valid())
    assert proposal is not None
    assert set(proposal.custom_names) == _required()


def test_parse_proposal_single_name_is_not_a_complete_proposal() -> None:
    # A single valid name is a half-named team, not a saveable theme. It must
    # degrade to None (in-progress) so a partial map never pre-fills the editor.
    assert tb.parse_proposal({"custom_names": {"architect": "Gandalf"}}, valid=_valid()) is None


def test_parse_proposal_omitting_a_required_role_is_not_complete() -> None:
    # Naming all but one REQUIRED role is still in-progress and degrades to None.
    names = _required_names()
    names.pop("architect")
    assert tb.parse_proposal({"custom_names": names}, valid=_valid()) is None


def test_parse_proposal_optional_ops_names_are_kept_when_present() -> None:
    # Naming an optional ops agent alongside the full required set is allowed: the
    # name is kept, and the proposal stays complete.
    optional_slug = next(iter(tb.valid_codenames() - _required()))
    names = _required_names()
    names[optional_slug] = "Extra"
    proposal = tb.parse_proposal({"custom_names": names}, valid=_valid())
    assert proposal is not None
    assert proposal.custom_names[optional_slug] == "Extra"
    assert _required() <= set(proposal.custom_names)


def test_parse_proposal_drops_unknown_slugs_and_stays_incomplete() -> None:
    # An unknown slug is dropped entry-by-entry; with only one real name left the
    # map does not cover the roster, so the proposal degrades to None.
    proposal = tb.parse_proposal(
        {"custom_names": {"architect": "Gandalf", "not-a-real-agent": "Nobody"}},
        valid=_valid(),
    )
    assert proposal is None


def test_parse_proposal_drops_unknown_slugs_but_completes_with_full_map() -> None:
    # Unknown slugs are dropped, but the surviving full-coverage map still lands.
    names = _full_names(architect="Gandalf")
    proposal = tb.parse_proposal(
        {"custom_names": {**names, "not-a-real-agent": "Nobody"}},
        valid=_valid(),
    )
    assert proposal is not None
    assert proposal.custom_names == names


def test_parse_proposal_accepts_names_alias() -> None:
    # A slightly-off model output using `names` instead of `custom_names` still lands.
    names = _full_names(architect="Gandalf")
    proposal = tb.parse_proposal({"names": names}, valid=_valid())
    assert proposal is not None
    assert proposal.custom_names == names


def test_parse_proposal_normalizes_dotted_slug() -> None:
    names = _full_names(architect="Gandalf")
    # Present the architect entry dotted; it must normalize to the bare slug and
    # still count toward full coverage.
    del names["architect"]
    proposal = tb.parse_proposal(
        {"custom_names": {**names, "alfred.architect": "Gandalf"}}, valid=_valid()
    )
    assert proposal is not None
    assert proposal.custom_names["architect"] == "Gandalf"


def test_parse_proposal_bounds_label_length() -> None:
    proposal = tb.parse_proposal({"custom_names": _full_names(architect="G" * 200)}, valid=_valid())
    assert proposal is not None
    assert len(proposal.custom_names["architect"]) == tb.MAX_LABEL_LEN


def test_parse_proposal_blank_or_non_string_label_leaves_map_incomplete() -> None:
    # A blank or non-string label drops that entry; the resulting map no longer
    # covers the roster, so the proposal degrades to None.
    names = _full_names()
    names["architect"] = "   "  # dropped by _clean_label
    assert tb.parse_proposal({"custom_names": names}, valid=_valid()) is None


def test_parse_proposal_returns_none_when_nothing_usable() -> None:
    assert tb.parse_proposal({"custom_names": {}}, valid=_valid()) is None
    assert tb.parse_proposal({"custom_names": {"ghost": "x"}}, valid=_valid()) is None
    assert tb.parse_proposal("not a dict", valid=_valid()) is None
    assert tb.parse_proposal(None, valid=_valid()) is None


def test_parse_proposal_carries_custom_roles() -> None:
    proposal = tb.parse_proposal(
        {
            "custom_names": _full_names(architect="Gandalf"),
            "custom_roles": {"architect": "Grey Wizard"},
        },
        valid=_valid(),
    )
    assert proposal is not None
    assert proposal.custom_roles == {"architect": "Grey Wizard"}


def test_parse_proposal_rejects_roles_only_proposal() -> None:
    # Naming the team is the whole point: a proposal that carries only role labels
    # and no display names is not a theme. It must degrade to None so the client
    # keeps chatting instead of pre-filling an empty editor.
    assert tb.parse_proposal({"custom_roles": {"architect": "Grey Wizard"}}, valid=_valid()) is None


def test_parse_proposal_rejects_when_names_present_but_all_invalid() -> None:
    # custom_names is present but every entry is an unknown slug, so no valid name
    # survives. With roles alongside, the proposal must STILL degrade to None
    # rather than forward a roles-only theme.
    assert (
        tb.parse_proposal(
            {
                "custom_names": {"ghost": "Nobody"},
                "custom_roles": {"architect": "Grey Wizard"},
            },
            valid=_valid(),
        )
        is None
    )


def test_parse_proposal_duplicate_display_name_leaves_map_incomplete() -> None:
    # Two agents cast as the same persona is a broken roster: the later duplicate
    # is dropped, which leaves its role unnamed, so the map is incomplete and the
    # proposal degrades to None.
    names = _full_names(architect="Gandalf", reviewer="Gandalf")
    assert tb.parse_proposal({"custom_names": names}, valid=_valid()) is None


def test_parse_proposal_dedup_is_case_and_whitespace_insensitive() -> None:
    # A case/whitespace-variant duplicate is still a duplicate; the dropped entry
    # leaves its role unnamed, so the whole proposal degrades to None.
    names = _full_names(architect="Gandalf", reviewer="  gandalf ")
    assert tb.parse_proposal({"custom_names": names}, valid=_valid()) is None


# --- parse_turn --------------------------------------------------------------


def test_parse_turn_vibe_question_has_no_proposal() -> None:
    raw = '{"reply": "What crew do you want? A sci-fi ship, a band, Greek gods?"}'
    turn = tb.parse_turn(raw, valid=_valid())
    assert turn is not None
    assert turn.proposal is None
    assert "crew" in turn.reply


def test_parse_turn_proposes_a_full_team() -> None:
    names = _full_names(architect="Gandalf")
    raw = (
        '{"reply": "Middle-earth it is. Your architect is Gandalf.",'
        '"action": {"tool": "propose_theme", "args": {"custom_names": ' + _json(names) + "}}}"
    )
    turn = tb.parse_turn(raw, valid=_valid())
    assert turn is not None
    assert turn.proposal is not None
    # Every roster role is named, and the whole map covers the contract.
    assert set(turn.proposal.custom_names) == _valid()
    assert set(turn.proposal.custom_names) >= _ENGINEERING_SLUGS


def test_parse_turn_required_only_team_forwards_a_complete_proposal() -> None:
    # The model names every required role and omits the optional ops agents (as the
    # prompt permits). The turn forwards a complete proposal, not a stalled reply.
    names = _required_names(architect="Gandalf")
    raw = (
        '{"reply": "Middle-earth it is.",'
        '"action": {"tool": "propose_theme", "args": {"custom_names": ' + _json(names) + "}}}"
    )
    turn = tb.parse_turn(raw, valid=_valid())
    assert turn is not None
    assert turn.proposal is not None
    assert set(turn.proposal.custom_names) == _required()


def test_parse_turn_partial_team_degrades_to_reply() -> None:
    # A propose_theme turn that names only some roles is in-progress: keep the
    # reply, forward no action, so the client keeps chatting instead of opening a
    # half-filled editor.
    raw = (
        '{"reply": "Your architect is Gandalf. Who should lead review?",'
        '"action": {"tool": "propose_theme", "args": {"custom_names": '
        '{"architect": "Gandalf"}}}}'
    )
    turn = tb.parse_turn(raw, valid=_valid())
    assert turn is not None
    assert turn.proposal is None
    assert "Gandalf" in turn.reply


def test_parse_turn_roles_only_action_degrades_to_reply() -> None:
    # A propose_theme turn with only role labels and no names keeps the reply but
    # forwards no action, so the client keeps chatting instead of opening an empty
    # editor.
    raw = (
        '{"reply": "Which vibe should the roles take?",'
        '"action": {"tool": "propose_theme", "args": {"custom_roles": '
        '{"architect": "Grey Wizard"}}}}'
    )
    turn = tb.parse_turn(raw, valid=_valid())
    assert turn is not None
    assert turn.proposal is None
    assert "vibe" in turn.reply


def test_parse_turn_duplicate_names_degrade_to_reply() -> None:
    # A duplicate display name across roles drops the collided entry, leaving a
    # role unnamed. That map no longer covers the roster, so the turn keeps its
    # reply and forwards no proposal.
    raw = (
        '{"reply": "Middle-earth it is.",'
        '"action": {"tool": "propose_theme", "args": {"custom_names": '
        '{"architect": "Gandalf", "reviewer": "Gandalf", "planner": "Aragorn"}}}}'
    )
    turn = tb.parse_turn(raw, valid=_valid())
    assert turn is not None
    assert turn.proposal is None
    assert "Middle-earth" in turn.reply


def test_parse_turn_ignores_non_propose_action() -> None:
    # A confused turn that names a different allowlisted tool degrades to a plain
    # reply with no proposal (the theme surface only forwards propose_theme).
    raw = '{"reply": "ok", "action": {"tool": "save_theme", "args": {}}}'
    turn = tb.parse_turn(raw, valid=_valid())
    assert turn is not None
    assert turn.proposal is None
    assert turn.reply == "ok"


def test_parse_turn_returns_none_on_unparseable_output() -> None:
    assert tb.parse_turn("not json at all", valid=_valid()) is None
    assert tb.parse_turn("", valid=_valid()) is None


def test_parse_turn_strips_code_fence() -> None:
    raw = '```json\n{"reply": "hi"}\n```'
    turn = tb.parse_turn(raw, valid=_valid())
    assert turn is not None
    assert turn.reply == "hi"


def test_parse_turn_malformed_proposal_degrades_to_reply() -> None:
    # An action with only unknown slugs keeps the reply and drops the proposal.
    raw = (
        '{"reply": "Here you go.",'
        '"action": {"tool": "propose_theme", "args": {"custom_names": {"ghost": "x"}}}}'
    )
    turn = tb.parse_turn(raw, valid=_valid())
    assert turn is not None
    assert turn.reply == "Here you go."
    assert turn.proposal is None


# --- run_turn (engine injected) ----------------------------------------------


@dataclass
class _FakeResult:
    success: bool
    result_text: str


def test_run_turn_parses_injected_engine_proposal() -> None:
    names = _full_names(architect="Ripley", reviewer="HAL")
    payload = (
        '{"reply": "Sci-fi crew, aye.",'
        '"action": {"tool": "propose_theme", "args": {"custom_names": ' + _json(names) + "}}}"
    )

    def fake_invoke(prompt: str, **kwargs: object) -> tuple[_FakeResult, str]:
        # The roster contract and the untrusted transcript both reach the prompt.
        assert "propose_theme" in prompt
        assert kwargs["agent"] == tb.BUILDER_AGENT
        return _FakeResult(success=True, result_text=payload), "claude"

    turn = tb.run_turn(
        system_prompt="SYSTEM propose_theme",
        messages=_messages("make them a sci-fi crew"),
        engine="claude",
        workdir=REPO_ROOT,
        valid_slugs=_valid(),
        invoke=fake_invoke,
    )
    assert turn is not None
    assert turn.proposal is not None
    assert set(turn.proposal.custom_names) == _valid()
    assert turn.proposal.custom_names["architect"] == "Ripley"
    assert turn.proposal.custom_names["reviewer"] == "HAL"


def test_run_turn_completes_on_required_roles_with_optional_ops_omitted() -> None:
    # End to end through run_turn: a required-only map (ops omitted) is complete,
    # using the default required set the route also passes.
    names = _required_names(architect="Ripley")
    payload = (
        '{"reply": "Sci-fi crew, aye.",'
        '"action": {"tool": "propose_theme", "args": {"custom_names": ' + _json(names) + "}}}"
    )

    def fake_invoke(prompt: str, **kwargs: object) -> tuple[_FakeResult, str]:
        return _FakeResult(success=True, result_text=payload), "claude"

    turn = tb.run_turn(
        system_prompt="SYSTEM propose_theme",
        messages=_messages("sci-fi crew"),
        engine="claude",
        workdir=REPO_ROOT,
        valid_slugs=_valid(),
        required_slugs=_required(),
        invoke=fake_invoke,
    )
    assert turn is not None
    assert turn.proposal is not None
    assert set(turn.proposal.custom_names) == _required()


def test_run_turn_returns_none_when_engine_fails() -> None:
    def fake_invoke(prompt: str, **kwargs: object) -> tuple[_FakeResult, str]:
        return _FakeResult(success=False, result_text=""), "claude"

    turn = tb.run_turn(
        system_prompt="SYSTEM",
        messages=_messages("hi"),
        engine="claude",
        workdir=REPO_ROOT,
        valid_slugs=_valid(),
        invoke=fake_invoke,
    )
    assert turn is None


def test_run_turn_never_raises_on_engine_exception() -> None:
    def fake_invoke(prompt: str, **kwargs: object) -> tuple[_FakeResult, str]:
        raise RuntimeError("boom")

    turn = tb.run_turn(
        system_prompt="SYSTEM",
        messages=_messages("hi"),
        engine="claude",
        workdir=REPO_ROOT,
        valid_slugs=_valid(),
        invoke=fake_invoke,
    )
    assert turn is None


# --- payload -----------------------------------------------------------------


def test_turn_payload_serializes_proposal_as_propose_theme_action() -> None:
    turn = tb.ThemeBuilderTurn(
        reply="Done.",
        proposal=tb.ThemeProposal(custom_names={"architect": "Gandalf"}, custom_roles={}),
    )
    payload = tb.turn_payload(turn)
    assert payload["reply"] == "Done."
    assert payload["action"] == {
        "tool": "propose_theme",
        "args": {"custom_names": {"architect": "Gandalf"}, "custom_roles": {}},
    }


def test_turn_payload_null_action_for_vibe_turn() -> None:
    turn = tb.ThemeBuilderTurn(reply="What vibe?")
    payload = tb.turn_payload(turn)
    assert payload["reply"] == "What vibe?"
    assert payload["action"] is None


def _json(obj: dict[str, str]) -> str:
    import json

    return json.dumps(obj)
