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

# The engineering role-slugs the prompt asks the model to cover first, plus a few
# ops slugs, all sourced from the shipped manifest.
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


# --- roster contract seeding -------------------------------------------------


def test_roster_contract_agents_covers_the_engineering_roster() -> None:
    slugs = {agent.codename for agent in roster_contract_agents()}
    assert slugs >= _ENGINEERING_SLUGS


def test_valid_codenames_matches_the_manifest() -> None:
    assert tb.valid_codenames() == frozenset(a.codename for a in roster_contract_agents())


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


def test_parse_proposal_accepts_known_slugs() -> None:
    proposal = tb.parse_proposal(
        {"custom_names": {"architect": "Gandalf", "reviewer": "Galadriel"}},
        valid=_valid(),
    )
    assert proposal is not None
    assert proposal.custom_names == {"architect": "Gandalf", "reviewer": "Galadriel"}
    assert proposal.custom_roles == {}


def test_parse_proposal_drops_unknown_slugs_but_keeps_the_rest() -> None:
    proposal = tb.parse_proposal(
        {"custom_names": {"architect": "Gandalf", "not-a-real-agent": "Nobody"}},
        valid=_valid(),
    )
    assert proposal is not None
    assert proposal.custom_names == {"architect": "Gandalf"}


def test_parse_proposal_accepts_names_alias() -> None:
    # A slightly-off model output using `names` instead of `custom_names` still lands.
    proposal = tb.parse_proposal({"names": {"architect": "Gandalf"}}, valid=_valid())
    assert proposal is not None
    assert proposal.custom_names == {"architect": "Gandalf"}


def test_parse_proposal_normalizes_dotted_slug() -> None:
    proposal = tb.parse_proposal({"custom_names": {"alfred.architect": "Gandalf"}}, valid=_valid())
    assert proposal is not None
    assert proposal.custom_names == {"architect": "Gandalf"}


def test_parse_proposal_bounds_label_length() -> None:
    proposal = tb.parse_proposal({"custom_names": {"architect": "G" * 200}}, valid=_valid())
    assert proposal is not None
    assert len(proposal.custom_names["architect"]) == tb.MAX_LABEL_LEN


def test_parse_proposal_drops_blank_and_non_string_labels() -> None:
    proposal = tb.parse_proposal(
        {"custom_names": {"architect": "   ", "reviewer": 42, "planner": "Ok"}},
        valid=_valid(),
    )
    assert proposal is not None
    assert proposal.custom_names == {"planner": "Ok"}


def test_parse_proposal_returns_none_when_nothing_usable() -> None:
    assert tb.parse_proposal({"custom_names": {}}, valid=_valid()) is None
    assert tb.parse_proposal({"custom_names": {"ghost": "x"}}, valid=_valid()) is None
    assert tb.parse_proposal("not a dict", valid=_valid()) is None
    assert tb.parse_proposal(None, valid=_valid()) is None


def test_parse_proposal_carries_custom_roles() -> None:
    proposal = tb.parse_proposal(
        {
            "custom_names": {"architect": "Gandalf"},
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


def test_parse_proposal_drops_duplicate_display_names_first_wins() -> None:
    # Two agents cast as the same persona is a broken roster. The first slug keeps
    # the name; the later duplicate is dropped so the surviving names stay distinct.
    proposal = tb.parse_proposal(
        {"custom_names": {"architect": "Gandalf", "reviewer": "Gandalf"}},
        valid=_valid(),
    )
    assert proposal is not None
    assert proposal.custom_names == {"architect": "Gandalf"}


def test_parse_proposal_dedup_is_case_and_whitespace_insensitive() -> None:
    proposal = tb.parse_proposal(
        {"custom_names": {"architect": "Gandalf", "reviewer": "  gandalf "}},
        valid=_valid(),
    )
    assert proposal is not None
    assert proposal.custom_names == {"architect": "Gandalf"}


def test_parse_proposal_keeps_distinct_names_after_dropping_a_duplicate() -> None:
    proposal = tb.parse_proposal(
        {
            "custom_names": {
                "architect": "Gandalf",
                "reviewer": "Gandalf",
                "planner": "Aragorn",
            }
        },
        valid=_valid(),
    )
    assert proposal is not None
    assert proposal.custom_names == {"architect": "Gandalf", "planner": "Aragorn"}


def test_parse_proposal_drops_role_label_for_a_deduped_name() -> None:
    # A role override must not cling to a slug whose name was dropped as a duplicate.
    proposal = tb.parse_proposal(
        {
            "custom_names": {"architect": "Gandalf", "reviewer": "Gandalf"},
            "custom_roles": {"reviewer": "The Grey"},
        },
        valid=_valid(),
    )
    assert proposal is not None
    assert proposal.custom_names == {"architect": "Gandalf"}
    assert proposal.custom_roles == {}


# --- parse_turn --------------------------------------------------------------


def test_parse_turn_vibe_question_has_no_proposal() -> None:
    raw = '{"reply": "What crew do you want? A sci-fi ship, a band, Greek gods?"}'
    turn = tb.parse_turn(raw, valid=_valid())
    assert turn is not None
    assert turn.proposal is None
    assert "crew" in turn.reply


def test_parse_turn_proposes_a_full_team() -> None:
    names = {slug: slug.title().replace("-", "") for slug in _ENGINEERING_SLUGS}
    raw = (
        '{"reply": "Middle-earth it is. Your architect is Gandalf.",'
        '"action": {"tool": "propose_theme", "args": {"custom_names": ' + _json(names) + "}}}"
    )
    turn = tb.parse_turn(raw, valid=_valid())
    assert turn is not None
    assert turn.proposal is not None
    # Every engineering role is named.
    assert set(turn.proposal.custom_names) >= _ENGINEERING_SLUGS


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


def test_parse_turn_does_not_forward_duplicate_names() -> None:
    # A duplicate display name across roles is deduped (first wins); the surviving
    # distinct proposal still forwards.
    raw = (
        '{"reply": "Middle-earth it is.",'
        '"action": {"tool": "propose_theme", "args": {"custom_names": '
        '{"architect": "Gandalf", "reviewer": "Gandalf", "planner": "Aragorn"}}}}'
    )
    turn = tb.parse_turn(raw, valid=_valid())
    assert turn is not None
    assert turn.proposal is not None
    assert turn.proposal.custom_names == {"architect": "Gandalf", "planner": "Aragorn"}


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
    payload = (
        '{"reply": "Sci-fi crew, aye.",'
        '"action": {"tool": "propose_theme", "args": {"custom_names": '
        '{"architect": "Ripley", "reviewer": "HAL"}}}}'
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
    assert turn.proposal.custom_names == {"architect": "Ripley", "reviewer": "HAL"}


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
