"""Intent classification for the Compose conversational spec-builder.

Covers the new "conversation vs build" turn kind that lets a plain question
("who are you?") get a chat answer instead of a forced planning card, while a
real build request still produces the structured draft.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB = REPO_ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import compose_converse as cc  # noqa: E402
from spec_helper import IssueDraft  # noqa: E402


def _empty_draft() -> IssueDraft:
    return IssueDraft(title="")


def _build_draft() -> IssueDraft:
    return IssueDraft(
        title="Add a dark mode toggle",
        desired_behavior="Settings page has a dark mode toggle.",
        repos=["your-org/frontend"],
    )


# --- resolve_intent: model verdict wins -------------------------------------


def test_model_conversation_intent_is_honored() -> None:
    intent = cc.resolve_intent(
        "conversation",
        last_user_message="add a dark mode toggle to the settings page",
        draft=_build_draft(),
        done=False,
    )
    assert intent == cc.INTENT_CONVERSATION


def test_model_build_intent_is_honored() -> None:
    intent = cc.resolve_intent(
        "build",
        last_user_message="who are you?",
        draft=_empty_draft(),
        done=False,
    )
    assert intent == cc.INTENT_BUILD


def test_unknown_model_intent_falls_back_to_build() -> None:
    # An unexpected value must never suppress the plan surface for real work.
    intent = cc.resolve_intent(
        "smalltalk",
        last_user_message="add a CSV export button",
        draft=_empty_draft(),
        done=False,
    )
    assert intent == cc.INTENT_BUILD


def test_unknown_model_intent_does_not_fall_through_to_heuristic() -> None:
    # The model returned a present-but-unrecognized label. Even when the last
    # user message is itself a known conversational opener and the draft is
    # empty, the unknown label must resolve straight to build and never reach
    # the heuristic (which would otherwise read "hi" as conversation and
    # suppress the plan surface), honoring the documented guarantee.
    intent = cc.resolve_intent(
        "greeting",
        last_user_message="hi",
        draft=_empty_draft(),
        done=False,
    )
    assert intent == cc.INTENT_BUILD


# --- resolve_intent: heuristic backstop when the model omits intent ----------


def test_heuristic_classifies_identity_question_as_conversation() -> None:
    intent = cc.resolve_intent(
        None,
        last_user_message="Who are you?",
        draft=_empty_draft(),
        done=False,
    )
    assert intent == cc.INTENT_CONVERSATION


def test_heuristic_classifies_capability_question_as_conversation() -> None:
    intent = cc.resolve_intent(
        None,
        last_user_message="what can you do",
        draft=_empty_draft(),
        done=False,
    )
    assert intent == cc.INTENT_CONVERSATION


def test_heuristic_classifies_build_request_as_build() -> None:
    intent = cc.resolve_intent(
        None,
        last_user_message="Add a dark mode toggle to the settings page",
        draft=_empty_draft(),
        done=False,
    )
    assert intent == cc.INTENT_BUILD


def test_heuristic_keeps_build_when_a_draft_already_has_content() -> None:
    # A "thanks" mid-build must not flip an in-progress spec to conversation and
    # wipe the plan; existing draft content forces build.
    intent = cc.resolve_intent(
        None,
        last_user_message="thanks",
        draft=_build_draft(),
        done=False,
    )
    assert intent == cc.INTENT_BUILD


def test_heuristic_mixed_message_stays_build() -> None:
    # "who are you, and can you add X" is a build turn: the opener only matches
    # when the WHOLE short message is a known greeting.
    intent = cc.resolve_intent(
        None,
        last_user_message="who are you, and can you add a dark mode toggle?",
        draft=_empty_draft(),
        done=False,
    )
    assert intent == cc.INTENT_BUILD


def test_heuristic_empty_message_defaults_to_build() -> None:
    intent = cc.resolve_intent(None, last_user_message="", draft=_empty_draft(), done=False)
    assert intent == cc.INTENT_BUILD


# --- parse_turn threads intent through -------------------------------------


def test_parse_turn_reads_model_intent() -> None:
    raw = json.dumps(
        {
            "intent": "conversation",
            "reply": "I'm Alfred. I turn an outcome into a planned change.",
            "draft": {},
            "readiness": {"score": 0, "ready": False, "missing": []},
            "done": False,
        }
    )
    turn = cc.parse_turn(raw, base_draft=_empty_draft(), last_user_message="who are you?")
    assert turn is not None
    assert turn.intent == cc.INTENT_CONVERSATION


def test_parse_turn_backfills_intent_from_heuristic_when_model_omits_it() -> None:
    raw = json.dumps(
        {
            "reply": "I can plan a change with you.",
            "draft": {},
            "readiness": {"score": 0, "ready": False, "missing": []},
            "done": False,
        }
    )
    turn = cc.parse_turn(raw, base_draft=_empty_draft(), last_user_message="what can you do")
    assert turn is not None
    assert turn.intent == cc.INTENT_CONVERSATION


def test_parse_turn_build_request_yields_build_intent() -> None:
    raw = json.dumps(
        {
            "reply": "Which repo is the settings page in?",
            "draft": {"title": "Dark mode toggle"},
            "readiness": {"score": 30, "ready": False, "missing": ["repo scope"]},
            "done": False,
        }
    )
    turn = cc.parse_turn(
        raw,
        base_draft=_empty_draft(),
        last_user_message="add a dark mode toggle to the settings page",
    )
    assert turn is not None
    assert turn.intent == cc.INTENT_BUILD


def test_default_converse_turn_intent_is_build() -> None:
    # The dataclass default keeps older call sites planner-first by default.
    turn = cc.ConverseTurn(
        reply="hi",
        draft=_empty_draft(),
        readiness=cc.ConverseReadiness(score=0, ready=False),
        done=False,
    )
    assert turn.intent == cc.INTENT_BUILD


# --- looks_like_question: deterministic question detector -------------------


def test_looks_like_question_detects_the_live_repro() -> None:
    # The exact question from the live bug report must read as a question so the
    # no-engine fallback answers it instead of drafting a plan.
    assert cc.looks_like_question("What is the current state of the fleet, in one short paragraph?")


def test_looks_like_question_detects_interrogative_without_trailing_mark() -> None:
    assert cc.looks_like_question("How many agents are paused")


def test_looks_like_question_rejects_plain_build_request() -> None:
    assert not cc.looks_like_question("Add a dark mode toggle to the settings page")


def test_looks_like_question_rejects_build_verb_phrased_as_question() -> None:
    # "Can you add X?" is work phrased as a question; the build verb wins so the
    # plan surface is not suppressed for a real request.
    assert not cc.looks_like_question("Can you add a dark mode toggle?")


def test_looks_like_question_rejects_modal_change_requests() -> None:
    # Request-shaped questions with unlisted verbs are still change requests: a
    # modal opener not aimed at the assistant is work, never a plain question.
    assert not cc.looks_like_question("Can we show paused agents in the roster?")
    assert not cc.looks_like_question("Could the dashboard include a pause button?")
    assert not cc.looks_like_question("Should we retry failed firings automatically?")
    assert not cc.looks_like_question("Would it be possible to show more history?")


def test_looks_like_question_keeps_assistant_directed_modal_questions() -> None:
    # A modal aimed at the assistant itself, with no build verb, is a question.
    assert cc.looks_like_question("Can you explain how review works?")
    assert cc.looks_like_question("Could you summarize the fleet status?")


def test_looks_like_question_rejects_empty() -> None:
    assert not cc.looks_like_question("   ")


# --- classify_message_intent: shared no-engine backstop ---------------------


def test_classify_message_intent_status_question_is_conversation() -> None:
    intent = cc.classify_message_intent(
        "What is the current state of the fleet, in one short paragraph?",
        draft=_empty_draft(),
    )
    assert intent == cc.INTENT_CONVERSATION


def test_classify_message_intent_change_request_is_build() -> None:
    intent = cc.classify_message_intent(
        "Add a CSV export button to the reports page",
        draft=_empty_draft(),
    )
    assert intent == cc.INTENT_BUILD


def test_classify_message_intent_build_verb_question_is_build() -> None:
    intent = cc.classify_message_intent(
        "Can you add a dark mode toggle?",
        draft=_empty_draft(),
    )
    assert intent == cc.INTENT_BUILD


def test_classify_message_intent_keeps_build_when_draft_has_content() -> None:
    # A question mid-build ("and the mobile app?") must not wipe the spec.
    intent = cc.classify_message_intent(
        "and what about the mobile app?",
        draft=_build_draft(),
    )
    assert intent == cc.INTENT_BUILD


def test_classify_message_intent_greeting_still_conversation() -> None:
    # The existing greeting-opener heuristic still resolves to conversation.
    intent = cc.classify_message_intent("who are you", draft=_empty_draft())
    assert intent == cc.INTENT_CONVERSATION


def test_classify_message_intent_modal_change_requests_are_build() -> None:
    # Planning asks phrased as questions with unlisted verbs must keep the
    # no-engine planning path (the modal-opener rule, not the verb list, wins).
    for message in (
        "Can we show paused agents in the roster?",
        "Could the dashboard include a pause button?",
        "Should we retry failed firings automatically?",
    ):
        assert cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_BUILD


def test_classify_message_intent_ignores_grounding_repos() -> None:
    # The desktop Ask sends the selected repo in draft.repos with EVERY fallback
    # turn as grounding context. A repo-only draft must not read as work: the
    # live-repro question stays a conversation turn in a one-repo setup.
    repo_only = IssueDraft(title="", repos=["your-org/frontend"])
    intent = cc.classify_message_intent(
        "What is the current state of the fleet, in one short paragraph?",
        draft=repo_only,
    )
    assert intent == cc.INTENT_CONVERSATION


def test_classify_message_intent_real_content_still_wins_over_question() -> None:
    # Repos are ignored, but any REAL draft content (title, desired behavior,
    # acceptance criteria) still forces build, question-shaped or not.
    intent = cc.classify_message_intent(
        "and what about the mobile app?",
        draft=IssueDraft(title="Add a dark mode toggle", repos=["your-org/frontend"]),
    )
    assert intent == cc.INTENT_BUILD
