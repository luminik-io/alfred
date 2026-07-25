"""Intent classification for the Compose conversational spec-builder.

Covers the new "conversation vs build" turn kind that lets a plain question
("who are you?") get a chat answer instead of a forced planning card, while a
real build request still produces the structured draft.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

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
        "Show me the queue.",
        "Show me the queues.",
        "List the queue.",
        "List the queues.",
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
    # conversation while "can we show/add X" stays build. A capability question
    # about a runtime actor stays conversational, while a UI noun subject
    # ("could the dashboard include X") names a thing to change and stays build.
    for message in (
        "Can I see the current state of the fleet?",
        "Can I get the fleet status?",
        "Could we get the list of paused agents?",
        "Can the worker retry failed jobs?",
        "May the engine restart safely?",
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


def test_parse_turn_polite_read_only_request_scrubs_model_plan() -> None:
    raw = json.dumps(
        {
            "intent": "build",
            "reply": "I drafted a starter plan.",
            "draft": {"title": "Explain engine status"},
            "readiness": {"score": 60, "ready": False, "missing": []},
            "done": False,
        }
    )

    for message in (
        "Could you please explain the current engine status? Do not change code.",
        "Alfred, could you kindly explain the current engine status? Do not change code.",
    ):
        turn = cc.parse_turn(raw, base_draft=_empty_draft(), last_user_message=message)
        assert turn is not None
        assert turn.intent == cc.INTENT_CONVERSATION, message
        assert turn.draft.title == "", message
        assert turn.readiness.score == 0, message
        assert "starter plan" not in turn.reply.lower(), message


def test_parse_turn_explanatory_modal_read_only_request_scrubs_model_action() -> None:
    raw = json.dumps(
        {
            "intent": "build",
            "reply": "I filed an implementation issue.",
            "draft": {"title": "Add Codex support"},
            "readiness": {"score": 60, "ready": False, "missing": []},
            "done": False,
            "action": {"tool": "file_issue", "args": {}},
        }
    )
    for message in (
        "Explain which agents can support Codex. Do not change code or file an issue.",
        "Please explain which agents can support Codex. Do not change code or file an issue.",
        "Please tell me which agents can support Codex. Do not change code or file an issue.",
    ):
        turn = cc.parse_turn(raw, base_draft=_empty_draft(), last_user_message=message)

        assert turn is not None
        assert turn.intent == cc.INTENT_CONVERSATION, message
        assert turn.draft.title == "", message
        assert turn.action is None, message
        assert "filed" not in turn.reply.lower(), message


def test_parse_turn_explicit_read_only_wh_question_scrubs_model_action() -> None:
    raw = json.dumps(
        {
            "intent": "build",
            "reply": "I filed an implementation issue.",
            "draft": {"title": "Change fleet status"},
            "readiness": {"score": 90, "ready": True, "missing": []},
            "done": True,
            "action": {"tool": "file_issue", "args": {}},
        }
    )

    for message in (
        "What is the fleet status? Do not change code or file an issue.",
        "Which engines are installed? Do not start a plan.",
        "How does the gate work? Do not change files.",
        "Are any agents paused? No changes.",
        "What is the fleet status? Make no changes.",
        "What is the fleet status? Please make no changes.",
        "What is the fleet status? Make no plan.",
        "What is the fleet status? Do not restart the worker.",
        "Which files are stale? Don't delete files.",
        "What is deployed? Never deploy anything.",
    ):
        turn = cc.parse_turn(raw, base_draft=_empty_draft(), last_user_message=message)

        assert turn is not None
        assert turn.intent == cc.INTENT_CONVERSATION, message
        assert turn.draft.title == "", message
        assert turn.action is None, message
        assert turn.done is False, message
        assert "filed" not in turn.reply.lower(), message


def test_parse_turn_explicit_read_only_question_scrubs_textual_action_claim() -> None:
    for intent, reply in (
        ("conversation", "I filed an issue with the status."),
        ("build", "I will restart the worker now."),
        ("conversation", "I'm going to update the fleet configuration."),
        ("conversation", "I changed the configuration and the fleet is healthy."),
        ("conversation", "I deleted the stale logs; the fleet is healthy."),
        ("conversation", "I restarted the worker and it is healthy now."),
        ("conversation", "I deployed the fix and all agents are healthy."),
        ("conversation", "I made a plan with the fleet status."),
        ("conversation", "I built a status report and saved it."),
        ("conversation", "I wrote the status to a file."),
        ("conversation", "I checked the fleet and restarted the worker."),
        ("conversation", "I reviewed the status, then filed an issue."),
        ("conversation", "I inspected the logs and deleted them."),
        ("conversation", "I am creating a plan now."),
        ("conversation", "We are filing an issue now."),
        ("conversation", "I checked the status and am drafting a plan."),
        ("conversation", "The issue has been filed."),
        ("conversation", "Alfred created a plan."),
        ("conversation", "Restarting the worker now."),
        ("conversation", "The worker was restarted."),
        ("conversation", "Plans were made."),
        ("conversation", "No files were changed, but the worker was restarted."),
        ("conversation", "The worker is being restarted."),
        ("conversation", "The issues are being filed."),
        ("conversation", "The issue has been getting filed."),
        ("conversation", "The requested changes are being applied."),
    ):
        raw = json.dumps(
            {
                "intent": intent,
                "reply": reply,
                "draft": {},
                "readiness": {"score": 0, "ready": False, "missing": []},
                "done": False,
            }
        )
        turn = cc.parse_turn(
            raw,
            base_draft=_empty_draft(),
            last_user_message=("What is the fleet status? Do not change files or file an issue."),
        )

        assert turn is not None
        assert turn.intent == cc.INTENT_CONVERSATION
        assert turn.reply == cc.READ_ONLY_OVERRIDE_REPLY
        assert turn.action is None


def test_parse_turn_explicit_read_only_question_keeps_negated_action_claim() -> None:
    for reply in (
        "I will not restart the worker; the fleet is healthy.",
        "I have not opened a pull request; the fleet is healthy.",
        "I filed no issue; the fleet is healthy.",
        "I created no plan; the fleet is healthy.",
        "I never changed the configuration; the fleet is healthy.",
        "The worker was not restarted.",
    ):
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
            last_user_message="What is the fleet status? Do not change files.",
        )

        assert turn is not None
        assert turn.reply == reply
        assert turn.intent == cc.INTENT_CONVERSATION


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


def test_looks_like_question_rejects_followup_work_after_a_question() -> None:
    for message in (
        "What is the status, and add a retry panel.",
        "How does the gate work? Then update the docs.",
        "Which engines are installed? Also add OpenCode.",
        "Why did dispatch fail? Fix it.",
        "Is the runtime healthy? Add logging.",
        "What is the status and add a pause button?",
    ):
        assert not cc.looks_like_question(message), message
        assert cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_BUILD, message


def test_looks_like_question_keeps_coordinated_guidance_read_only() -> None:
    for message in (
        "How do I add and remove an agent?",
        "How do I add, remove, and update agents?",
        "How do I add an agent? How do I remove it?",
        "How do I add an agent, and how do I remove it?",
        "What changed? How do I update the docs?",
        "What changed? Can you explain how to update the docs?",
        "How can I add a repo? How can I remove it?",
        "Why did this fail? How should I fix it?",
        "How does this work? Should I update it?",
        "Which engines are installed? Which engine should I add?",
        "Can I see which agents also support Codex?",
        "Can you explain which agents also support Codex?",
        "Can I get agents that also support Codex?",
    ):
        assert cc.looks_like_question(message), message
        assert (
            cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_CONVERSATION
        ), message


def test_looks_like_question_keeps_build_word_noun_questions_read_only() -> None:
    for message in (
        "Build status?",
        "Update status?",
        "What are the file and build statuses?",
        "Which build, update, and support jobs failed?",
        "What are the fix and build versions?",
    ):
        assert cc.looks_like_question(message), message
        assert (
            cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_CONVERSATION
        ), message


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
    assert cc.looks_like_read_only_info_request(
        "In one short sentence, tell me which installed coding engines you can use right now. "
        "Do not start a plan."
    )
    assert cc.looks_like_read_only_info_request("Briefly, list the installed engines.")


def test_read_only_info_request_detects_known_repo_scoped_explanation() -> None:
    repo = "luminik-io/alfred"
    suffix = (
        "explain how the current engine readiness gate prevents an unauthenticated coding "
        "engine from dispatching. Do not change code or create an issue."
    )

    for message in (
        f"In {repo}, {suffix}",
        f"In {repo}: {suffix}",
        f"In {repo}. {suffix}",
        f"In {repo} {suffix}",
        f"In `{repo}`, {suffix}",
        f"In `luminik-io/alfred.tools`. {suffix}",
    ):
        context_repo = "luminik-io/alfred.tools" if "alfred.tools" in message else repo
        assert cc.looks_like_read_only_info_request(message, context_repos=[context_repo]), message
        assert (
            cc.classify_message_intent(
                message,
                draft=_empty_draft(),
                context_repos=[context_repo],
            )
            == cc.INTENT_CONVERSATION
        ), message


def test_read_only_marker_after_repo_scope_stays_conversational() -> None:
    for message in (
        "In acme/repo, read-only: inspect the current engine status.",
        "In acme/repo, read only: show the current engine status.",
        "In acme/repo, read only: display the current engine status.",
        "In acme/repo, read-only: please explain the current engine status.",
        "In acme/repo, read only: Alfred, display the current engine status.",
    ):
        assert cc.looks_like_read_only_info_request(message, context_repos=["acme/repo"])
        assert (
            cc.classify_message_intent(
                message,
                draft=_empty_draft(),
                context_repos=["acme/repo"],
            )
            == cc.INTENT_CONVERSATION
        )


def test_read_only_info_request_does_not_strip_unknown_path_prefix() -> None:
    message = "In ui/dashboard, show the engine status. Do not change files."

    assert not cc.looks_like_read_only_info_request(
        message,
        context_repos=["luminik-io/alfred"],
    )
    assert (
        cc.classify_message_intent(
            message,
            draft=_empty_draft(),
            context_repos=["luminik-io/alfred"],
        )
        == cc.INTENT_BUILD
    )


def test_read_only_info_request_keeps_repo_scoped_build_request_as_build() -> None:
    message = (
        "In luminik-io/alfred, add an engine readiness panel. "
        "Do not change the existing CLI output."
    )

    assert not cc.looks_like_read_only_info_request(
        message,
        context_repos=["luminik-io/alfred"],
    )
    assert (
        cc.classify_message_intent(
            message,
            draft=_empty_draft(),
            context_repos=["luminik-io/alfred"],
        )
        == cc.INTENT_BUILD
    )


def test_read_only_info_request_ignores_explanatory_how_to_build_verb() -> None:
    message = "Explain how to fix the engine readiness gate without changing code."

    assert cc.looks_like_read_only_info_request(message)
    assert cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_CONVERSATION


def test_read_only_info_request_accepts_also_in_modal_status_question() -> None:
    message = "Can you also show me the current fleet status?"

    assert cc.looks_like_read_only_info_request(message)
    assert cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_CONVERSATION


def test_read_only_info_request_keeps_modal_feature_request_as_build() -> None:
    message = "Can you also add a fleet status panel?"

    assert not cc.looks_like_read_only_info_request(message)
    assert cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_BUILD


def test_read_only_info_request_keeps_chained_imperative_after_explanation_as_build() -> None:
    for message in (
        "Explain how to fix the engine readiness gate, then update the docs. Do not change the API.",
        "Explain how to fix the engine readiness gate, also update the docs. Do not change the API.",
        "Explain how to fix the engine readiness gate; also update the docs. Do not change the API.",
        "Explain how to fix the engine readiness gate. Also update the docs. Do not change the API.",
        "Explain how to fix the engine, but add a status panel. Do not change the API.",
        "Explain the engine status. Restart the worker. Do not change code.",
        "Explain the engine status. Restart worker. Do not change code.",
        "Explain the engine status. Restart. Do not change code.",
        "Explain the engine status, archive the old logs. Do not change code.",
        "Explain the engine status. Can you explain the logs and update the docs? "
        "Do not change code.",
        "Explain the engine status. Would you recommend a fix and implement it? "
        "Do not change code.",
    ):
        assert not cc.looks_like_read_only_info_request(message), message
        assert cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_BUILD, message


def test_read_only_info_request_keeps_later_modal_build_clause_as_build() -> None:
    for message in (
        "Explain how to fix the engine gate. We should update the docs, but do not change the API.",
        "Explain how to fix the engine gate, and we should update the docs without changing code.",
        "We should update the engine docs without changing the API.",
        "Explain the engine status. It should add retries without changing the API.",
        "Explain the engine status. They must update the docs without changing code.",
        "Explain the engine status. The dashboard must display retries without changing code.",
        "Explain the engine status. The dashboard will display retries without changing code.",
        "Explain the engine status. The worker could add retries without changing code.",
        "Explain the engine status. The worker should retry failed jobs. Do not change the API.",
        "Explain the engine status. The dashboard must indicate failures. Do not change the API.",
        "Explain the engine status. The worker could process retries. Do not change the API.",
        "Explain the engine status. The UI will offer retry controls. Do not change the API.",
        "Explain the engine status. Could you retry failed jobs? Do not change the API.",
        "Explain the engine status. While you are there add logging. Do not change the API.",
        "Explain why the run failed and the worker must restart. Do not change code.",
    ):
        assert not cc.looks_like_read_only_info_request(message), message
        assert cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_BUILD, message


def test_read_only_info_request_does_not_treat_object_modal_as_work() -> None:
    message = "Explain the engine status. It can support Codex without changing code."

    assert cc.looks_like_read_only_info_request(message)
    assert cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_CONVERSATION


def test_read_only_info_request_does_not_treat_status_nouns_as_commands() -> None:
    for sentence in (
        "Support for Codex is enabled.",
        "Update status is pending.",
        "Fix version is 1.2.",
        "Build health is green.",
        "Change history is in the logs.",
        "File ownership is documented.",
    ):
        message = f"Explain the engine status. {sentence} Do not change code."
        assert cc.looks_like_read_only_info_request(message), message
        assert (
            cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_CONVERSATION
        ), message

    command = "Explain the engine status. Add support details without changing code."
    assert not cc.looks_like_read_only_info_request(command)
    assert cc.classify_message_intent(command, draft=_empty_draft()) == cc.INTENT_BUILD


def test_read_only_info_request_ignores_coordinated_how_to_verbs() -> None:
    for message in (
        "Explain how to add and remove an engine without changing code.",
        "Explain how to add and also remove an engine without changing code.",
        "Explain how to add or remove an engine without changing code.",
        "Explain how to add an engine and remove it without changing code.",
        "Explain how to add and then remove an engine without changing code.",
        "Explain how to add, remove, and update engines without changing code.",
        "Explain how to also update the engine without changing code.",
    ):
        assert cc.looks_like_read_only_info_request(message), message
        assert (
            cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_CONVERSATION
        ), message


def test_read_only_info_request_ignores_explanatory_modal_build_verbs() -> None:
    for message in (
        "Explain which agents can support Codex. Do not change code.",
        "Explain whether the worker should retry failed jobs. Do not change code.",
        "Describe when operators must restart the worker. Do not change code.",
        "Explain why the dashboard will display retries. Do not change code.",
        "Please explain which agents can support Codex. Do not change code.",
        "Kindly explain whether the worker should retry failed jobs. Do not change code.",
        "Please tell me which engines support Codex. Do not change code.",
        "Explain why the worker needs to be restarted. Do not change code.",
        "Describe when the worker is expected to restart. Do not change code.",
    ):
        assert cc.looks_like_read_only_info_request(message), message
        assert cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_CONVERSATION


def test_read_only_info_request_keeps_comma_separated_imperative_as_build() -> None:
    for message in (
        "Explain the current engine status, update the docs. Do not change the API.",
        "Explain how to fix the engine readiness gate, update the docs. Do not change the API.",
        "Explain how to fix the engine gate, and file an issue afterward. Do not change code.",
    ):
        assert not cc.looks_like_read_only_info_request(message), message
        assert cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_BUILD, message


def test_separator_aware_build_tokenizer_scales_linearly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = cc._is_build_verb_form
    calls = 0

    def counted(token: str) -> bool:
        nonlocal calls
        calls += 1
        return original(token)

    monkeypatch.setattr(cc, "_is_build_verb_form", counted)
    repeated = 2_000
    tokens = cc._separator_aware_build_tokens(
        "Explain how to " + ", ".join(["add"] * repeated) + " without changing code."
    )

    assert tokens.count("add") == repeated
    assert calls < repeated * 5


def test_explanatory_build_verb_scan_scales_linearly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = cc._is_build_verb_form
    calls = 0

    def counted(token: str) -> bool:
        nonlocal calls
        calls += 1
        return original(token)

    monkeypatch.setattr(cc, "_is_build_verb_form", counted)
    repeated = 2_000
    tokens = ("how to add and " * repeated).split()
    ignored = cc._explanatory_build_verb_indices(tokens)

    assert len(ignored) == repeated
    assert calls < repeated * 3


def test_public_intent_classifier_scales_linearly(monkeypatch: pytest.MonkeyPatch) -> None:
    original = cc._is_build_verb_form
    calls = 0

    def counted(token: str) -> bool:
        nonlocal calls
        calls += 1
        return original(token)

    monkeypatch.setattr(cc, "_is_build_verb_form", counted)
    repeated = 2_000
    message = "How do I add " + " and remove" * repeated + "?"

    assert cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_CONVERSATION
    assert calls < repeated * 15


def test_modal_requirement_scan_scales_linearly(monkeypatch: pytest.MonkeyPatch) -> None:
    original = cc._is_build_verb_form
    calls = 0

    def counted(token: str) -> bool:
        nonlocal calls
        calls += 1
        return original(token)

    monkeypatch.setattr(cc, "_is_build_verb_form", counted)
    repeated = 2_000
    message = "Explain the engine status. " + "The worker should add retries and " * repeated
    message += "do not change the API."

    assert cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_BUILD
    assert calls < repeated * 20


def test_modal_subject_scan_does_not_copy_growing_token_prefixes() -> None:
    durations: list[float] = []
    for repeated in (1_000, 2_000, 4_000):
        message = "What " + "it can add x " * repeated + "?"
        started = time.perf_counter()
        cc.classify_message_intent(message, draft=_empty_draft())
        durations.append(time.perf_counter() - started)

    assert durations[-1] < max(0.5, durations[0] * 8)


def test_repo_scoped_guidance_question_stays_conversational() -> None:
    message = "In luminik-io/alfred, how do I add a new agent?"

    assert (
        cc.classify_message_intent(
            message,
            draft=_empty_draft(),
            context_repos=["luminik-io/alfred"],
        )
        == cc.INTENT_CONVERSATION
    )


def test_parse_turn_materializes_generator_repo_context() -> None:
    raw = json.dumps(
        {
            "reply": "The readiness gate checks authentication before dispatch.",
            "draft": {},
            "readiness": {"score": 0, "ready": False, "missing": []},
            "done": False,
        }
    )
    context_repos = (repo for repo in ["luminik-io/alfred"])

    turn = cc.parse_turn(
        raw,
        base_draft=_empty_draft(),
        last_user_message=(
            "In luminik-io/alfred, explain how the engine gate works. Do not change code."
        ),
        context_repos=context_repos,
    )

    assert turn is not None
    assert turn.intent == cc.INTENT_CONVERSATION


def test_read_only_info_request_rejects_real_build_request_with_no_action_clause() -> None:
    # "Do not change" is often a constraint inside real work. It only makes a
    # turn conversational when the command itself is informational.
    assert not cc.looks_like_read_only_info_request(
        "Add a setup status panel. Do not change the existing sidebar."
    )
    for message in (
        "What is the status? Do not change files, but restart the worker.",
        "What is the status? Do not change files. Restart the worker.",
        "Which files are stale? Don't delete files, but open an issue.",
        "Explain the status. Do not edit anything; the worker needs to be restarted.",
        "Explain the status. Do not edit anything; the worker has to restart.",
        "Explain the status. Do not edit anything; the worker is expected to restart.",
    ):
        assert not cc.looks_like_read_only_info_request(message), message
        assert cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_BUILD


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
        "Check repository status and fix failures.",
        "List runs and retry jobs.",
        "Explain the engine status. Kindly update the docs. Do not change the API.",
        "What is the status? Kindly restart the worker.",
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


def test_read_only_info_request_keeps_coordinated_noun_objects_read_only() -> None:
    for message in (
        "List build artifacts and deploy status.",
        "Check release status and update history.",
    ):
        assert cc.looks_like_read_only_info_request(message), message
        assert (
            cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_CONVERSATION
        ), message


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


@pytest.mark.parametrize(
    "message",
    (
        "Check repository status and fix failures.",
        "List runs and retry jobs.",
    ),
)
def test_parse_turn_coordinated_mutation_preserves_model_draft(message: str) -> None:
    raw = json.dumps(
        {
            "intent": "build",
            "reply": "I drafted the requested work.",
            "draft": {
                "title": "Repair failed work",
                "acceptance_criteria": ["The failed work is repaired."],
            },
            "readiness": {"score": 60, "ready": False, "missing": []},
            "done": False,
        }
    )

    turn = cc.parse_turn(
        raw,
        base_draft=_empty_draft(),
        last_user_message=message,
    )

    assert turn is not None
    assert turn.intent == cc.INTENT_BUILD
    assert turn.draft.title == "Repair failed work"


def test_parse_turn_preserves_mixed_imperative_and_modal_work() -> None:
    for message in (
        "Explain the engine status. Build a retry panel that is accessible. Do not change the API.",
        "Explain the engine status. Build retry controls that are accessible. Do not change the API.",
        "Explain the engine status. Update docs that are stale. Do not change the API.",
        "Explain the engine status. Fix tests that are failing. Do not change the API.",
        "Explain the engine status. The dashboard must display retry controls. "
        "Do not change the API.",
        "Explain the engine status. The dashboard will display retry controls. "
        "Do not change the API.",
        "Explain the engine status. The worker could add retries. Do not change the API.",
        "Explain the engine status. The worker should retry failed jobs. Do not change the API.",
        "Explain the engine status. The dashboard must indicate failures. Do not change the API.",
        "Explain the engine status. The worker could process retries. Do not change the API.",
        "Explain the engine status. The UI will offer retry controls. Do not change the API.",
        "Explain the engine status. Could you retry failed jobs? Do not change the API.",
        "Explain the engine status and the worker should retry failed jobs. Do not change the API.",
        "Show status and reboot the host.",
        "What is the status? Have it restart the worker.",
        "What is the status? Process retries and notify operators.",
    ):
        raw = json.dumps(
            {
                "intent": "build",
                "reply": "I drafted the retry-control work.",
                "draft": {
                    "title": "Display retry controls",
                    "desired_behavior": "The dashboard displays accessible retry controls.",
                },
                "readiness": {"score": 60, "ready": False, "missing": []},
                "done": False,
            }
        )

        turn = cc.parse_turn(
            raw,
            base_draft=_empty_draft(),
            last_user_message=message,
        )

        assert turn is not None
        assert turn.intent == cc.INTENT_BUILD, message
        assert turn.draft.title == "Display retry controls", message
        assert turn.draft.desired_behavior, message


def test_parse_turn_explicit_no_plan_engine_question_scrubs_model_plan() -> None:
    raw = json.dumps(
        {
            "intent": "build",
            "reply": "I saved a starter plan that is ready to review.",
            "draft": {
                "title": "Report installed engines",
                "acceptance_criteria": ["List Claude and Codex."],
            },
            "readiness": {"score": 90, "ready": True, "missing": []},
            "done": True,
        }
    )

    turn = cc.parse_turn(
        raw,
        base_draft=_empty_draft(),
        last_user_message=(
            "In one short sentence, tell me which installed coding engines you can use right now. "
            "Do not start a plan."
        ),
    )

    assert turn is not None
    assert turn.intent == cc.INTENT_CONVERSATION
    assert turn.reply == cc.READ_ONLY_OVERRIDE_REPLY
    assert turn.draft == _empty_draft()
    assert turn.done is False


def test_parse_turn_repo_scoped_read_only_question_scrubs_model_plan() -> None:
    raw = json.dumps(
        {
            "intent": "build",
            "reply": "I saved a starter plan that is ready to review.",
            "draft": {
                "title": "Explain the engine readiness gate",
                "acceptance_criteria": ["Describe the authentication boundary."],
            },
            "readiness": {"score": 90, "ready": True, "missing": []},
            "done": True,
        }
    )

    turn = cc.parse_turn(
        raw,
        base_draft=IssueDraft(title="", repos=["luminik-io/alfred"]),
        last_user_message=(
            "In luminik-io/alfred, explain how the current engine readiness gate prevents an "
            "unauthenticated coding engine from dispatching. Do not change code or create an issue."
        ),
    )

    assert turn is not None
    assert turn.intent == cc.INTENT_CONVERSATION
    assert turn.reply == cc.READ_ONLY_OVERRIDE_REPLY
    assert turn.draft == _empty_draft()
    assert turn.done is False


def test_parse_turn_preserves_dated_work_history_while_scrubbing_plan_state() -> None:
    reply = "The fleet merged pull request 42 this morning. The worker restarted at 10:00 UTC."
    raw = json.dumps(
        {
            "intent": "build",
            "reply": reply,
            "draft": {
                "title": "Report recent work",
                "acceptance_criteria": ["List completed fleet activity."],
            },
            "readiness": {"score": 90, "ready": True, "missing": []},
            "done": True,
        }
    )

    turn = cc.parse_turn(
        raw,
        base_draft=_empty_draft(),
        last_user_message="What shipped this morning? Do not change anything.",
    )

    assert turn is not None
    assert turn.intent == cc.INTENT_CONVERSATION
    assert turn.reply == reply
    assert turn.draft == _empty_draft()
    assert turn.done is False


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
    for message in (
        "Build logs?",
        "Build failures?",
        "Open issues?",
        "Support matrix?",
        "Change log?",
        "Fix details?",
        "File list?",
        "Update notes?",
        "Build output?",
        "Build artifacts?",
        "Open pull requests?",
    ):
        assert looks_like_question(message), message
        assert cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_CONVERSATION


def test_compound_status_fragments_stay_questions() -> None:
    for message in (
        "Status and logs?",
        "Queue and logs?",
        "Logs and queue?",
        "Status and queue?",
        "Runtime status and logs?",
        "Queues or runs?",
        "Install and runtime?",
    ):
        assert cc.looks_like_question(message), message
        assert (
            cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_CONVERSATION
        ), message


def test_direct_object_and_identifier_commands_stay_work() -> None:
    for message in (
        "Build it?",
        "Fix it?",
        "Open it?",
        "Render them?",
        "Build auth_service?",
        "Open owner/repo?",
        "Fix failing tests?",
        "Build authentication?",
        "Open issue #123?",
    ):
        assert not cc.looks_like_question(message), message
        assert cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_BUILD, message


def test_noun_question_with_followup_work_stays_build() -> None:
    for message in (
        "Open issues, and fix the oldest one?",
        "Build status, and add retry logging?",
        "Update status? Also add retry logs?",
        "Build logs? Then add retries?",
        "Open issues and then fix the oldest one?",
        "Build status and then add retry logging?",
        "Open issues and close the oldest one?",
        "Open issues and resolve the oldest one?",
        "Build logs and investigate the latest failure?",
        "Update status and notify the team?",
        "Show status and reboot the host.",
    ):
        assert cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_BUILD


def test_coordinated_build_word_nouns_stay_questions() -> None:
    for message in (
        "What are the file states and build statuses?",
        "What are the current status and build logs?",
        "Which build logs, support tickets, and update notes are current?",
        "What are the build logs, support tickets, and change notes?",
        "What are the build logs, deployment metrics, and update statuses?",
        "What are build logs, errors, and fix versions?",
        "Which engines support Claude, Codex, or OpenCode?",
        "What are the errors? Timeouts, retries, and bad tokens?",
        "Build logs and update notes?",
        "Fix versions and build logs?",
        "Open issues and pull requests?",
        "Support tickets and update notes?",
        "Change history and file details?",
        "Build logs, update notes, and fix versions?",
    ):
        assert cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_CONVERSATION


def test_comma_then_how_to_question_stays_conversational() -> None:
    for message in (
        "How do I add a repo, then remove it?",
        "How do I inspect a repo, then remove it?",
        "How do I authenticate, then add a repo?",
    ):
        assert cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_CONVERSATION


def test_later_guidance_question_stays_conversational() -> None:
    for message in (
        "Explain the engine status. What should I update?",
        "Explain the engine status. Why should we change it?",
        "Explain the engine status. How should I fix it?",
        "What changed? Would you recommend updating the docs?",
        "What changed? Could you suggest improving the docs?",
        "What changed? Can you advise changing the docs?",
    ):
        assert cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_CONVERSATION


def test_top_level_recommendation_questions_stay_conversational() -> None:
    for message in (
        "Can you recommend that we add retries?",
        "Could you advise me how to update the docs?",
        "Would you suggest we remove the legacy endpoint?",
        "Can you please recommend whether we add retries?",
        "Do you recommend we add retries?",
        "Do you recommend adding retries and updating the docs?",
        "Do you recommend adding retries and removing the old endpoint?",
        "What would you recommend adding and removing?",
        "What approach do you recommend for adding and removing agents?",
    ):
        assert cc.looks_like_question(message), message
        assert cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_CONVERSATION


def test_question_plus_non_allowlisted_modal_work_stays_build() -> None:
    for message in (
        "What is the status? The worker should retry failed jobs.",
        "What is the status? The UI will offer retry controls.",
    ):
        assert cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_BUILD


def test_question_plus_non_allowlisted_imperative_stays_build() -> None:
    for message in (
        "What is the status? Restart the worker.",
        "What are the logs, archive the old ones?",
        "Which logs are current, fix the stale ones?",
        "What is the status? Restart worker.",
    ):
        assert not cc.looks_like_question(message), message
        assert cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_BUILD, message


def test_top_level_unknown_imperative_question_stays_build() -> None:
    for message in (
        "Reboot the host?",
        "Reboot host?",
        "Reschedule the job?",
        "Reschedule job?",
        "Rotate the keys?",
        "Rotate keys?",
        "Investigate the failure?",
        "Investigate failure?",
        "Notify operators?",
        "Purge cache?",
        "Flush cache?",
        "Regenerate index?",
        "Purge logs?",
        "Flush queues?",
        "Investigate failures?",
        "Rotate queues?",
        "Immediately reboot host?",
        "Safely rotate the keys?",
        "Quietly archive the logs?",
    ):
        assert not cc.looks_like_question(message), message
        assert cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_BUILD, message

    for message in (
        "Authentication behavior?",
        "Production deploy?",
        "Retry scheduling?",
        "Worker startup?",
    ):
        assert cc.looks_like_question(message), message
        assert (
            cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_CONVERSATION
        ), message


def test_build_word_status_fragments_stay_conversational() -> None:
    for message in (
        "Archive status?",
        "Deploy status?",
        "Execute status?",
        "Process status?",
        "Restart status?",
        "Start status?",
        "Stop status?",
        "Deploy state?",
        "Restart state?",
        "Build queue?",
        "Fleet status?",
        "Agent state?",
        "Runtime health?",
        "Queue status?",
        "Queues status?",
        "Fleet states?",
        "Agent states?",
        "Runtime states?",
        "State?",
        "Health?",
        "Queue?",
    ):
        assert cc.looks_like_question(message), message
        assert (
            cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_CONVERSATION
        ), message


def test_read_only_explanation_accepts_noun_subject_capability_modal() -> None:
    for message in (
        "Explain the engine status. The engine can support Codex without changing code.",
        "Explain the engine status. This engine may support Codex. Do not change code.",
        "Explain the engine status. Agents can support Codex. Do not change code.",
        "Explain the engine status. The worker might retry failed jobs. Do not change code.",
        "Explain the engine status. The worker may need to restart after failure. Do not change code.",
        "Explain the engine status. The worker might have to retry the job. Do not change code.",
    ):
        assert cc.looks_like_read_only_info_request(message), message
        assert (
            cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_CONVERSATION
        ), message

    command = "What is the status? Can you restart the worker?"
    assert not cc.looks_like_question(command)
    assert cc.classify_message_intent(command, draft=_empty_draft()) == cc.INTENT_BUILD


def test_declarative_actor_capability_questions_stay_conversational() -> None:
    for message in (
        "The worker can retry failed jobs?",
        "Workers might process retries?",
        "The worker may restart safely?",
        "Are workers able to restart?",
        "The agents are able to restart?",
        "Are the engines capable of retrying?",
        "Can Alfred deploy?",
        "Alfred can deploy?",
    ):
        assert cc.looks_like_question(message), message
        assert (
            cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_CONVERSATION
        ), message

    request = "The dashboard can display retries?"
    assert not cc.looks_like_question(request)
    assert cc.classify_message_intent(request, draft=_empty_draft()) == cc.INTENT_BUILD
    assert cc.classify_message_intent("Can you deploy?", draft=_empty_draft()) == cc.INTENT_BUILD
    assert (
        cc.classify_message_intent("Could Alfred add retries?", draft=_empty_draft())
        == cc.INTENT_BUILD
    )


def test_read_only_capability_answers_are_not_action_claims() -> None:
    for reply in (
        "I support Claude and Codex.",
        "The code supports Claude and Codex.",
        "The configuration enables retries.",
        "I support creating and filing issues.",
        "Workers can restart and retry jobs.",
        "Workers cannot restart and retry jobs.",
    ):
        assert not cc._reply_claims_plan_or_action(reply), reply

    for reply in (
        "The request has been queued.",
        "Work is being planned.",
        "The change is now being implemented.",
        "The branch was pushed.",
        "The ticket has been closed.",
        "The requested changes are being applied.",
        "Created a plan.",
        "Opened a pull request.",
        "Built the feature.",
        "Made changes.",
        "Wrote tests.",
        "Opened two issues that list regressions.",
        "Opened 2 issues that list regressions.",
        "Opened issues that list regressions.",
        "Built new artifacts that contain the fix.",
        "Created several plans that describe the rollout.",
        "I show the current status and create a plan.",
        "I include setup details and created a plan.",
        "Summary: Created a plan.",
        "Actions taken: Opened a pull request.",
        "Successfully created a plan.",
        "Summary: Successfully opened a pull request.",
        "Added tests: 3.",
        "Opened issues: 2.",
        "Created report: rollout details.",
        "Created detailed plans.",
        "Opened critical issues.",
        "Built reusable artifacts.",
        "Added comprehensive tests.",
        "Wrote unit tests.",
        "Created rollout plans.",
        "Opened GitHub issues.",
        "Deployed updated services.",
        "Fixed failing tests.",
        "Created 0 reports and deployed production.",
        "Created no reports but opened an issue.",
        "Opened 0 issues and created a plan.",
        "Created zero plans; opened an issue.",
        "Created zero plans, then deployed the service.",
        "Created plans: 0; opened issues: 1.",
        "Created report: no regressions found.",
        "Created plan: none of the services need changes.",
        "Opened issue: no agents are healthy.",
        "Added tests: no failures.",
        "Created: a plan.",
        "Opened: two issues.",
        "Deployed in production.",
        "Committed on main.",
        "Filed in Jira.",
        "Published on GitHub.",
        "Created zero-downtime deployment.",
        "Opened zero-day issue.",
        "Added 0-byte file.",
        "Created not only a plan but also tests.",
        "Created blue widgets in the repository.",
        "Opened GitHub issues in the tracker.",
        "Created plan named rollout.",
        "Opened issue titled BUG-123.",
        "Added test called regression.",
        "Built artifact named release.",
        "Created plans named rollout.",
        "Created zero trust policy.",
        "Added zero trust configuration.",
        "Opened zero trust issue.",
        "Created plans, status is healthy.",
        "Opened issues, all agents are healthy.",
        "Built artifacts and status is healthy.",
        "Created plans are visible, then opened an issue.",
        "Created plans are visible, but I opened an issue.",
        "Built artifacts are available and Alfred created a plan.",
        "Updated files contain the fix: Alfred created a plan.",
        "I created nothing but a plan.",
        "Alfred opened nothing but an issue.",
        "Created zero plans and one issue.",
        "Created 0 issues, 1 plan.",
        "Added zero tests and three files.",
        "Created plan via Jira.",
        "Opened issues as requested.",
        "Added tests alongside the fix.",
        "Built artifacts according to the spec.",
        "Created plans after review.",
        "Alfred: Created a plan.",
        "Created issues tracking regressions.",
        "Added tests covering the failure.",
        "Created plans based on feedback.",
        "Opened issues related to the outage.",
        "Added tests designed for the regression.",
        "Built artifacts intended for production.",
        "Created plans are visible, but the worker opened an issue.",
        "Opened issues remain visible, and the agent created a plan.",
        "Created zero plans and a report.",
        "Added zero tests and a file.",
        "Created by Alfred.",
        "Deployed by Alfred.",
        "Deployed with zero downtime.",
        "Created a plan plus a report.",
        "Opened 0 issues plus a plan.",
        "Created a plan together with a report.",
        "Created zero plans and one dashboard.",
        "Created.",
        "Summary: Created.",
        "Issue 123 was opened.",
        "Pull request 123 was created.",
        "Tests added.",
        "Yesterday created a plan.",
        "Just created a plan.",
        "Already opened an issue.",
        "Now deployed the service.",
        "Created no plan initially, but later opened an issue.",
    ):
        assert cc._reply_claims_plan_or_action(reply), reply

    for reply in (
        "The requested changes are not being applied.",
        "No changes are being applied.",
        "The changes can be applied safely.",
        "Opened pull requests are visible in the repository.",
        "Built artifacts are available for inspection.",
        "Closed issues include BUG-123.",
        "Merged pull requests include #1.",
        "Updated files contain the fix.",
        "Created plans describe the rollout.",
        "Opened issues list regressions.",
        "Opened issues that list regressions are visible.",
        "Closed issues: 0.",
        "Opened pull requests: none.",
        "Built artifacts: none.",
        "Updated status shows all agents healthy.",
        "Archived logs are available below.",
        "Processed output shows the current state.",
        "Created report describes the current setup.",
        "Closed source is not supported.",
        "Fixed costs are listed below.",
        "Updated documentation is available online.",
        "Opened issues remain visible.",
        "Created plans can be inspected below.",
        "Opened issues that list regressions remain visible.",
        "Created plans and reports are visible.",
        "Created plans and opened issues are visible.",
        "Created plans, opened issues, and built artifacts are visible.",
        "Updated files match the release.",
        "Created plans reduce risk.",
        "Opened ports expose the service.",
        "Closed issues disappeared from the board.",
        "Built artifacts match the release.",
        "Opened issue blocks deployment.",
        "Created plan awaits approval.",
        "Updated branch needs attention.",
        "Created plans are visible, which helps.",
        "Opened issues remain visible, which is useful.",
        "Updated files contain the fix, which is documented.",
        "Fixed costs total 100.",
        "Fixed costs exceed the budget.",
        "Archived logs occupy 2 GB.",
        "Created detailed plans are visible.",
        "Opened critical issues remain visible.",
        "Built reusable artifacts are available.",
        "Built with Python.",
        "Built by CI.",
        "Created by Alice.",
        "Created with Terraform.",
        "Written with Rust.",
        "Updated: yesterday.",
        "Updated: 2026-07-25.",
        "Created: July 1, 2025.",
        "Updated: 2 hours ago.",
        "Updated: 10:30 today.",
        "Created: 3 days ago.",
        "Created date: 2026-07-25.",
        "Updated version: 2.",
        "Deployed version: 1.2.3.",
        "Removed agents appear below.",
        "Updated agents look healthy.",
        "Added tests pass.",
        "Fixed tests pass.",
        "Created a total of zero plans.",
        "Created nothing.",
        "Opened nothing.",
        "Created neither plan.",
        "Created files differ from tracked versions.",
        "Fixed costs increased by 10 percent.",
        "Updated dependencies introduce no regressions.",
        "Closed issues number five.",
        "Opened ports pose a risk.",
        "Removed agents caused the outage.",
        "Published reports cover the incident.",
        "Written policies govern access.",
        "Created exactly 0 plans.",
        "Opened approximately zero issues.",
        "Created only no plans.",
        "Created zero plans.",
        "Opened 0 issues.",
        "Opened 0 issues in the repository.",
        "Created 0 new issues.",
        "Opened zero critical issues.",
        "Added exactly zero failing tests.",
        "Created 0 GitHub issues.",
        "Created zero plans for the rollout.",
        "Created exactly zero plans in Jira.",
        "Opened no pull request.",
        "Status: closed.",
        "Issue status: closed.",
        "Deployment status: deployed.",
        "Branch: updated.",
        "Plan summary: created yesterday.",
        "Issue summary: opened yesterday.",
        "Updated: last week.",
        "Updated: just now.",
        "Created: earlier today.",
        "Published today: quarterly report.",
        "Updated yesterday: dependency status.",
        "Updated configuration matches the runtime.",
        "Created meeting is on the calendar.",
        "Built for reliability, the runtime is healthy.",
        "Created after review, the plan remains pending.",
        "Created zero plans and reports.",
        "Opened 0 issues and pull requests.",
        "Added zero tests, files, or artifacts.",
        "Opened issues that track regressions remain visible.",
        "Created plans that address the risks are pending.",
        "Built artifacts that target Linux are available.",
        "Added tests that reproduce the bug are failing.",
        "Opened security issue blocks deployment.",
        "Built release artifact matches the release.",
        "Created rollout plan awaits approval.",
        "Tests added: 0.",
        "Opened zero draft pull requests.",
        "Created 0 high-priority issues.",
        "Updated configuration in production matches the runtime.",
        "Updated on July 1, 2025.",
        "Deployed at 10:30 today.",
        "Added rules apply here.",
        "Built agents create reports.",
        "The system queue is empty.",
        "The agent queue is empty.",
        "The fleet queue is healthy.",
        "The system updates are available.",
        "The system processes run hourly.",
        "The fleet merged pull request 42 this morning.",
        "The worker restarted at 10:00 UTC.",
        "Alfred opened issue 42 yesterday.",
        "The branch was pushed last Friday.",
    ):
        assert not cc._reply_claims_plan_or_action(reply), reply

    for reply in (
        "Created plans to support the rollout.",
        "Opened issues to track regressions.",
        "Added tests to show the bug.",
        "Built artifacts to use in production.",
        "Created rollout plans for teams.",
        "The system updates configuration.",
        "The agent queues work.",
        "The fleet processes requests.",
        "The fleet merged pull request 42 now.",
        "The worker restarted the service.",
        "We merged pull request 42 yesterday.",
        "I restarted at 10:00 UTC.",
        "The worker will restart at 10:00 UTC.",
    ):
        assert cc._reply_claims_plan_or_action(reply), reply


def test_textual_action_claim_scan_is_linear(monkeypatch: pytest.MonkeyPatch) -> None:
    original = cc._is_mutating_action_form
    calls = 0

    def counted(token: str) -> bool:
        nonlocal calls
        calls += 1
        return original(token)

    monkeypatch.setattr(cc, "_is_mutating_action_form", counted)
    repeated = 2_000

    assert not cc._reply_claims_plan_or_action("I checked and " * repeated + "all healthy.")
    assert calls < repeated * 4

    started = time.monotonic()
    cc._reply_claims_plan_or_action("Summary: Deployed versions are visible: " * repeated)
    assert time.monotonic() - started < 1.0


def test_question_plus_declarative_followup_stays_conversational() -> None:
    for message in (
        "What is the status? Everything looks healthy.",
        "What is the status? Nothing needs attention.",
        "Which agents are running? Batman handles reviews.",
        "What changed? Retry behavior remained stable.",
        "What is the status? Probably healthy.",
        "Explain the engine status. Everything looks healthy. Do not change code.",
        "Explain the engine status. Batman handles reviews. Do not change code.",
    ):
        assert cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_CONVERSATION


def test_question_plus_terse_noun_fragment_stays_conversational() -> None:
    for message in (
        "What changed? Login behavior?",
        "Which areas changed? Authentication behavior?",
        "What failed? Production deploy?",
        "What is broken? Retry scheduling?",
        "What changed? API authentication?",
        "What changed? Authentication and logging?",
        "What is the status? CI green?",
        "What failed? Worker startup?",
        "What happened? Deploy failed?",
        "What happened? Build failed?",
        "Explain the current engine status. Retry failed. Do not change code.",
    ):
        assert cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_CONVERSATION


def test_guidance_question_plus_coordinated_work_stays_build() -> None:
    for message in (
        "What changed? Would you recommend a fix and implement it?",
        "What changed? Can you explain it and update the docs?",
        "How does the gate work? Could you summarize it, then add retries?",
        "How do I add a repo and please update the docs?",
        "How do I add a repo and you update the docs?",
    ):
        assert cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_BUILD, message


def test_later_polite_recommendation_stays_conversational() -> None:
    for message in (
        "What changed? Can you please recommend whether we add retries?",
        "What changed? Do you recommend we add retries?",
    ):
        assert cc.classify_message_intent(message, draft=_empty_draft()) == cc.INTENT_CONVERSATION


def test_conjunction_heavy_question_classifier_scales_near_linearly() -> None:
    durations: list[float] = []
    for repeated in (500, 1_000, 2_000):
        message = "What are the current items " + " and the item" * repeated + "?"
        started = time.perf_counter()
        cc.classify_message_intent(message, draft=_empty_draft())
        durations.append(time.perf_counter() - started)

    assert durations[-1] < max(0.08, durations[0] * 7)


def test_information_clause_scan_scales_near_linearly() -> None:
    durations: list[float] = []
    for repeated in (2_000, 4_000, 8_000):
        message = "Check status. " * repeated
        tokens = cc._separator_aware_build_tokens(message)
        started = time.perf_counter()
        cc._has_followup_build_clause(tokens)
        durations.append(time.perf_counter() - started)

    assert durations[-1] < max(0.5, durations[0] * 7)


def test_leading_conjunction_run_scales_near_linearly() -> None:
    durations: list[float] = []
    for repeated in (1_000, 2_000, 4_000):
        message = "What changed? " + "and " * repeated + "status?"
        started = time.perf_counter()
        cc.classify_message_intent(message, draft=_empty_draft())
        durations.append(time.perf_counter() - started)

    assert durations[-1] < max(0.5, durations[0] * 8)


def test_read_only_guidance_conjunction_run_scales_near_linearly() -> None:
    durations: list[float] = []
    for repeated in (1_000, 2_000, 4_000):
        message = "Explain the current setup. " + "and " * repeated + "status. Do not change files."
        started = time.perf_counter()
        cc.classify_message_intent(message, draft=_empty_draft())
        durations.append(time.perf_counter() - started)

    assert durations[-1] < max(0.5, durations[0] * 8)


def test_comma_heavy_question_classifier_scales_near_linearly() -> None:
    durations: list[float] = []
    for repeated in (500, 1_000, 2_000):
        message = "Which engines support " + ", ".join(["Claude"] * repeated) + "?"
        started = time.perf_counter()
        cc.classify_message_intent(message, draft=_empty_draft())
        durations.append(time.perf_counter() - started)

    assert durations[-1] < max(0.08, durations[0] * 7)


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


def test_operator_notes_round_trip_through_payload() -> None:
    # Regression: operator_notes must survive the hand-written compose draft
    # serializer round-trip instead of being silently dropped.
    draft = IssueDraft(
        title="Add export",
        problem="Operators need a CSV export.",
        operator_notes="Operator note: prioritize the mobile path first.",
    )
    payload = cc._draft_to_dict(draft)
    assert payload["operator_notes"] == "Operator note: prioritize the mobile path first."
    restored = cc.draft_from_payload(payload)
    assert restored.operator_notes == "Operator note: prioritize the mobile path first."
