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


# --- resolve_intent: model verdict wins, except explicit read-only status ----


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


def test_read_only_setup_summary_overrides_model_build_intent() -> None:
    # Live repro from Desktop Ask: the model labelled this as build, which made
    # the client show a "Ready to file" card despite an explicit no-action ask.
    intent = cc.resolve_intent(
        "build",
        last_user_message=(
            "Summarize the current Alfred setup status on this Mac. "
            "Do not change files or open pull requests."
        ),
        draft=_empty_draft(),
        done=False,
    )
    assert intent == cc.INTENT_CONVERSATION


def test_modal_read_only_setup_summary_overrides_model_build_intent() -> None:
    intent = cc.resolve_intent(
        "build",
        last_user_message=(
            "Can you summarize the current Alfred setup status? "
            "Do not change files or open pull requests."
        ),
        draft=_empty_draft(),
        done=False,
    )
    assert intent == cc.INTENT_CONVERSATION


def test_read_only_override_does_not_win_mid_build() -> None:
    intent = cc.resolve_intent(
        "build",
        last_user_message=(
            "Summarize the current Alfred setup status on this Mac. "
            "Do not change files or open pull requests."
        ),
        draft=_build_draft(),
        done=False,
    )
    assert intent == cc.INTENT_BUILD


def test_read_only_override_ignores_repo_only_grounding() -> None:
    intent = cc.resolve_intent(
        "build",
        last_user_message=(
            "Summarize the current Alfred setup status on this Mac. "
            "Do not change files or open pull requests."
        ),
        draft=IssueDraft(title="", repos=["acme/alfred"]),
        done=False,
    )
    assert intent == cc.INTENT_CONVERSATION


def test_read_only_override_wins_over_done_model_intent() -> None:
    intent = cc.resolve_intent(
        "build",
        last_user_message=(
            "Summarize the current Alfred setup status on this Mac. "
            "Do not change files or open pull requests."
        ),
        draft=_empty_draft(),
        done=True,
    )
    assert intent == cc.INTENT_CONVERSATION


def test_read_only_override_does_not_win_for_unknown_surface_placement() -> None:
    intent = cc.resolve_intent(
        "build",
        last_user_message="Show me the current fleet status in the accordion.",
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


def test_no_engine_classifier_keeps_list_status_questions_conversational() -> None:
    # "list"/"give"/"provide" lean informational: a modal status question routed
    # through the no-engine Ask fallback must not be misread as build work and
    # persist a plan card (regression: these verbs were build hints).
    for message in (
        "Can you list the currently live agents?",
        "Could you give me the status of the fleet?",
        "Can you provide an overview of what shipped today?",
    ):
        assert (
            cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_CONVERSATION
        ), message
    # A genuine build request phrased the same way still routes to build.
    assert (
        cc.classify_message_intent("Can you add a dark mode toggle?", draft=_empty_draft())
        == cc.INTENT_BUILD
    )
    assert (
        cc.classify_message_intent(
            "Can you show paused agents in the roster?", draft=_empty_draft()
        )
        == cc.INTENT_BUILD
    )


def test_no_engine_classifier_routes_modal_status_questions_by_subject() -> None:
    # A modal opener with a personal-pronoun subject (you/i/we) is a question
    # unless it carries a build verb, so "can I see/get the status" stays
    # conversation while "can we show/add X" stays build. A non-pronoun subject
    # ("could the dashboard include X") names a thing to change and stays build.
    for message in (
        "Can I see the current state of the fleet?",
        "Can I get the fleet status?",
        "Could we get the list of paused agents?",
    ):
        assert (
            cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_CONVERSATION
        ), message
    for message in (
        "Can we show paused agents in the roster?",
        "Could the dashboard include a pause button?",
        # An info verb does not win over a build verb also in verb position.
        "Can we find a way to add dark mode?",
        "Could we get the app to support markdown?",
    ):
        assert cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_BUILD, message


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


def test_parse_turn_read_only_setup_summary_keeps_clean_conversation_reply() -> None:
    reply = "Your local Alfred setup is healthy: three agents are idle and no failures are active."
    raw = json.dumps(
        {
            "intent": "conversation",
            "reply": reply,
            "draft": {},
            "readiness": {"score": 0, "ready": False, "missing": []},
            "done": False,
        }
    )
    turn = cc.parse_turn(
        raw,
        base_draft=_empty_draft(),
        last_user_message=(
            "Summarize the current Alfred setup status on this Mac. "
            "Do not change files or open pull requests."
        ),
    )
    assert turn is not None
    assert turn.intent == cc.INTENT_CONVERSATION
    assert turn.reply == reply
    assert turn.draft.title == ""
    assert turn.readiness.ready is False
    assert turn.action is None


def test_parse_turn_read_only_setup_summary_keeps_status_reply_while_scrubbing_draft() -> None:
    reply = (
        "Your local Alfred setup is healthy: the runtime is installed and no failures are active."
    )
    raw = json.dumps(
        {
            "intent": "build",
            "reply": reply,
            "draft": {"title": "Summarize Alfred setup status"},
            "readiness": {"score": 60, "ready": False, "missing": []},
            "done": False,
        }
    )
    turn = cc.parse_turn(
        raw,
        base_draft=_empty_draft(),
        last_user_message=(
            "Can you summarize the current Alfred setup status? "
            "Do not change files or open pull requests."
        ),
    )
    assert turn is not None
    assert turn.intent == cc.INTENT_CONVERSATION
    assert turn.reply == reply
    assert turn.draft.title == ""
    assert turn.readiness.score == 0
    assert turn.action is None


def test_parse_turn_read_only_setup_summary_keeps_negated_action_status_reply() -> None:
    reply = "No pull requests have been filed today, and no files were changed."
    raw = json.dumps(
        {
            "intent": "build",
            "reply": reply,
            "draft": {"title": "Summarize Alfred setup status"},
            "readiness": {"score": 60, "ready": False, "missing": []},
            "done": False,
        }
    )
    turn = cc.parse_turn(
        raw,
        base_draft=_empty_draft(),
        last_user_message=(
            "Review the current Alfred setup status. Do not change files or open pull requests."
        ),
    )
    assert turn is not None
    assert turn.intent == cc.INTENT_CONVERSATION
    assert turn.reply == reply
    assert turn.draft.title == ""
    assert turn.readiness.score == 0


def test_parse_turn_read_only_setup_summary_replaces_first_person_action_claim() -> None:
    raw = json.dumps(
        {
            "intent": "build",
            "reply": "I filed a pull request with the setup status summary.",
            "draft": {"title": "Summarize Alfred setup status"},
            "readiness": {"score": 60, "ready": False, "missing": []},
            "done": False,
        }
    )
    turn = cc.parse_turn(
        raw,
        base_draft=_empty_draft(),
        last_user_message=(
            "Review the current Alfred setup status. Do not change files or open pull requests."
        ),
    )
    assert turn is not None
    assert turn.intent == cc.INTENT_CONVERSATION
    assert "did not start a plan" in turn.reply
    assert "filed a pull request" not in turn.reply


def test_parse_turn_read_only_setup_summary_ignores_model_created_draft() -> None:
    raw = json.dumps(
        {
            "intent": "build",
            "reply": "I saved a starter plan that is ready to review.",
            "draft": {"title": "Summarize Alfred setup status"},
            "readiness": {"score": 60, "ready": False, "missing": []},
            "done": False,
        }
    )
    turn = cc.parse_turn(
        raw,
        base_draft=_empty_draft(),
        last_user_message=(
            "Summarize the current Alfred setup status on this Mac. "
            "Do not change files or open pull requests."
        ),
    )
    assert turn is not None
    assert turn.intent == cc.INTENT_CONVERSATION
    assert turn.draft.title == ""
    assert turn.draft.repos == []
    assert turn.readiness.score == 0


def test_parse_turn_read_only_setup_summary_ignores_done_model_draft() -> None:
    raw = json.dumps(
        {
            "intent": "build",
            "reply": "I saved a starter plan that is ready to review.",
            "draft": {
                "title": "Summarize Alfred setup status",
                "desired_behavior": "Open a pull request with a setup report.",
            },
            "readiness": {"score": 100, "ready": True, "missing": []},
            "done": True,
            "action": {"tool": "file_issue", "args": {"draft_id": "compose-bad"}},
        }
    )
    turn = cc.parse_turn(
        raw,
        base_draft=_empty_draft(),
        last_user_message=(
            "Summarize the current Alfred setup status on this Mac. "
            "Do not change files or open pull requests."
        ),
    )
    assert turn is not None
    assert turn.intent == cc.INTENT_CONVERSATION
    assert turn.draft.title == ""
    assert turn.draft.desired_behavior == ""
    assert turn.done is False
    assert turn.readiness.ready is False
    assert turn.action is None
    assert "did not start a plan" in turn.reply
    assert "starter plan" not in turn.reply.lower()


def test_parse_turn_read_only_setup_summary_ignores_repo_only_grounding() -> None:
    raw = json.dumps(
        {
            "intent": "build",
            "reply": "I saved a starter plan that is ready to review.",
            "draft": {"title": "Summarize Alfred setup status"},
            "readiness": {"score": 60, "ready": False, "missing": []},
            "done": False,
        }
    )
    turn = cc.parse_turn(
        raw,
        base_draft=IssueDraft(title="", repos=["acme/alfred"]),
        last_user_message=(
            "Summarize the current Alfred setup status on this Mac. "
            "Do not change files or open pull requests."
        ),
    )
    assert turn is not None
    assert turn.intent == cc.INTENT_CONVERSATION
    assert turn.draft.title == ""
    assert turn.draft.repos == []


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


# --- looks_like_read_only_info_request: imperative info/status detector -------


def test_read_only_info_request_detects_live_ask_repro() -> None:
    assert cc.looks_like_read_only_info_request(
        "Summarize the current Alfred setup status on this Mac. "
        "Do not change files or open pull requests."
    )
    assert cc.looks_like_read_only_info_request(
        "Can you summarize the current Alfred setup status? "
        "Do not change files or open pull requests."
    )
    assert cc.looks_like_read_only_info_request("Can you show me the current fleet status?")
    assert cc.looks_like_read_only_info_request("Show me the current repos.")
    assert cc.looks_like_read_only_info_request("Show me the configured repositories.")
    assert cc.looks_like_read_only_info_request(
        "Show me the current fleet status in one short paragraph."
    )
    assert cc.looks_like_read_only_info_request(
        "Summarize the current dashboard status. Do not change files or open pull requests."
    )
    assert cc.looks_like_read_only_info_request(
        "Summarize the current API status. Do not change files or open pull requests."
    )
    assert cc.looks_like_read_only_info_request(
        "Review the current Alfred setup status. Do not change files or open pull requests."
    )
    assert cc.looks_like_read_only_info_request(
        "Verify the current Alfred setup status. Do not change files or open pull requests."
    )
    assert cc.looks_like_read_only_info_request(
        "Confirm the current Alfred setup status. Do not change files or open pull requests."
    )


def test_read_only_info_request_rejects_real_build_request_with_no_action_clause() -> None:
    # "Do not change" is often a constraint inside real work. It only makes a
    # turn conversational when the command itself is informational.
    assert not cc.looks_like_read_only_info_request(
        "Add a setup status panel. Do not change the existing sidebar."
    )


def test_read_only_info_request_keeps_feature_show_requests_as_build() -> None:
    assert not cc.looks_like_read_only_info_request("Show paused agents in the roster.")
    assert not cc.looks_like_read_only_info_request("Show me paused agents in the roster.")
    assert not cc.looks_like_read_only_info_request(
        "Show me the current fleet status in the dropdown."
    )
    assert not cc.looks_like_read_only_info_request(
        "Show me the current fleet status in the modal."
    )
    assert not cc.looks_like_read_only_info_request(
        "Show me the current fleet status in the tooltip."
    )
    assert not cc.looks_like_read_only_info_request(
        "Show me the current fleet status in the accordion."
    )
    assert not cc.looks_like_read_only_info_request("Show the current fleet status in the CLI.")
    assert not cc.looks_like_read_only_info_request("Show the current fleet status in Slack.")
    assert not cc.looks_like_read_only_info_request("Show the current fleet status in the API.")
    assert not cc.looks_like_read_only_info_request("Show the current fleet status in the docs.")
    assert not cc.looks_like_read_only_info_request("Show me the selected repo in the header.")
    assert not cc.looks_like_read_only_info_request("List paused agents in the roster.")
    assert not cc.looks_like_read_only_info_request("Report failing runs in the dashboard.")
    assert not cc.looks_like_read_only_info_request(
        "List paused agents in the roster. Do not change the existing sidebar."
    )
    assert cc.looks_like_read_only_info_request("Show me the current fleet status.")
    assert cc.looks_like_read_only_info_request("Show me the current fleet status in one sentence.")
    assert cc.looks_like_read_only_info_request("View the current fleet status.")


def test_read_only_info_request_rejects_status_plus_chained_work() -> None:
    assert not cc.looks_like_read_only_info_request(
        "Show me the current fleet status and add a pause button."
    )
    assert not cc.looks_like_read_only_info_request(
        "Show me the fleet status and add a filter for paused agents."
    )
    for message in (
        "Show me the current fleet status; add retry logging.",
        "Show me the current fleet status ; add retry logging.",
        "Show me the current fleet status. Add retry logging.",
        "Show me the current fleet status . Add retry logging.",
        "Show me the current fleet status? Add retry logging.",
        "Show me the current fleet status ? Add retry logging.",
        "Show me the current fleet status! Add retry logging.",
        "Show me the current fleet status ! Add retry logging.",
        "Show me the current fleet status: implement retry logging.",
        "Show me the current fleet status : implement retry logging.",
        "Show me the current fleet status, add retry logging.",
        "Show me the current fleet status , add retry logging.",
        "Show me the current fleet status, then add retry logging.",
        "Show me the current fleet status , then add retry logging.",
        "Inspect the repo and file an issue for the bug.",
        "Review the current Alfred setup status and file an issue for any bug.",
    ):
        assert not cc.looks_like_read_only_info_request(message), message
        assert cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_BUILD
    assert (
        cc.classify_message_intent(
            "Show me the current fleet status and add a pause button.",
            draft=_empty_draft(),
        )
        == cc.INTENT_BUILD
    )


def test_read_only_info_request_ignores_space_padded_prefix_punctuation() -> None:
    assert cc.looks_like_read_only_info_request("Alfred , show me the current fleet status.")


def test_parse_turn_status_plus_chained_work_preserves_model_draft() -> None:
    raw = json.dumps(
        {
            "intent": "build",
            "reply": "I drafted the retry logging work.",
            "draft": {
                "title": "Add retry logging",
                "acceptance_criteria": ["Retry logging appears in the fleet status run output."],
            },
            "readiness": {"score": 60, "ready": False, "missing": []},
            "done": False,
        }
    )
    turn = cc.parse_turn(
        raw,
        base_draft=_empty_draft(),
        last_user_message="Show me the current fleet status; add retry logging.",
    )
    assert turn is not None
    assert turn.intent == cc.INTENT_BUILD
    assert turn.draft.title == "Add retry logging"
    assert turn.draft.acceptance_criteria == [
        "Retry logging appears in the fleet status run output."
    ]


# --- classify_message_intent: shared no-engine backstop ---------------------


def test_classify_message_intent_status_question_is_conversation() -> None:
    intent = cc.classify_message_intent(
        "What is the current state of the fleet, in one short paragraph?",
        draft=_empty_draft(),
    )
    assert intent == cc.INTENT_CONVERSATION


def test_classify_message_intent_imperative_setup_summary_is_conversation() -> None:
    intent = cc.classify_message_intent(
        "Summarize the current Alfred setup status on this Mac. "
        "Do not change files or open pull requests.",
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


def test_classify_message_intent_show_me_ui_requests_are_build() -> None:
    for message in (
        "Show me paused agents in the roster.",
        "Show me the selected repo in the header.",
        "Show me the current fleet status in the accordion.",
        "List paused agents in the roster.",
        "Report failing runs in the dashboard.",
    ):
        intent = cc.classify_message_intent(message, draft=_empty_draft())
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


def test_noun_use_of_build_verb_stays_question():
    from compose_converse import looks_like_question

    assert looks_like_question("What support options are available?")
    assert looks_like_question("What changes landed this week?")
    assert looks_like_question("Which fix went out yesterday?")


def test_verb_position_build_hints_stay_work():
    from compose_converse import looks_like_question

    assert not looks_like_question("Can we support markdown exports?")
    assert not looks_like_question("Is it possible to add retries?")
    assert not looks_like_question("Please update the docs")


def test_helper_phrasings_stay_work():
    from compose_converse import looks_like_question

    assert not looks_like_question("Can you help me add a CSV export?")
    assert not looks_like_question("Can you help add a dark mode toggle?")
    assert not looks_like_question("Help us fix the login redirect")


def test_how_to_questions_stay_questions():
    from compose_converse import looks_like_question

    assert looks_like_question("How do I add a new repo?")
    assert looks_like_question("What changes should we make first?")
    assert looks_like_question("Where do I update the token?")


def test_proposal_gerunds_stay_work():
    from compose_converse import looks_like_question

    assert not looks_like_question("What about adding search?")
    assert not looks_like_question("How about making the header sticky?")


def test_feature_request_verbs_stay_work():
    from compose_converse import looks_like_question as q

    assert not q("Can you show paused agents in the roster?")
    assert not q("Could you include a pause button on the dashboard?")
    assert not q("Can you surface the awaiting-approval count?")


def test_communication_verbs_stay_questions():
    from compose_converse import looks_like_question as q

    assert q("Can you explain how review works?")
    assert q("Could you describe the approval gate?")
