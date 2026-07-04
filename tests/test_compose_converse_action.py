"""Optional structured action channel for the Compose conversational flows.

Covers the request/execute split: a converse turn may REQUEST a well-typed,
allowlisted action (theme builder / onboarding steps) that a later client
orchestrator executes under the token gate. Nothing here executes an action;
this suite only exercises parse + validation + serialization. A bad action must
always degrade to a normal turn and never raise.
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


# --- parse_action: validation ------------------------------------------------


def test_parse_action_accepts_allowlisted_tool_with_args() -> None:
    action = cc.parse_action({"tool": "propose_theme", "args": {"mood": "warm paper"}})
    assert action is not None
    assert action.tool == "propose_theme"
    assert action.args == {"mood": "warm paper"}


def test_parse_action_every_allowlisted_name_is_accepted() -> None:
    for tool in cc.ACTION_ALLOWLIST:
        action = cc.parse_action({"tool": tool, "args": {}})
        assert action is not None, tool
        assert action.tool == tool


def test_parse_action_rejects_unknown_tool() -> None:
    assert cc.parse_action({"tool": "rm_rf", "args": {"path": "/"}}) is None


def test_parse_action_missing_args_defaults_to_empty_dict() -> None:
    action = cc.parse_action({"tool": "list_repos"})
    assert action is not None
    assert action.tool == "list_repos"
    assert action.args == {}


def test_parse_action_rejects_non_dict_block() -> None:
    assert cc.parse_action("propose_theme") is None
    assert cc.parse_action(["propose_theme"]) is None
    assert cc.parse_action(None) is None


def test_parse_action_rejects_missing_or_non_string_tool() -> None:
    assert cc.parse_action({"args": {}}) is None
    assert cc.parse_action({"tool": 42, "args": {}}) is None


def test_parse_action_rejects_non_dict_args() -> None:
    assert cc.parse_action({"tool": "propose_theme", "args": ["not", "a", "dict"]}) is None
    assert cc.parse_action({"tool": "propose_theme", "args": "warm"}) is None


def test_parse_action_rejects_oversized_args_by_key_count() -> None:
    args = {f"k{i}": i for i in range(cc.MAX_ACTION_ARGS_KEYS + 1)}
    assert cc.parse_action({"tool": "save_theme", "args": args}) is None


def test_parse_action_rejects_oversized_args_by_serialized_size() -> None:
    args = {"blob": "x" * (cc.MAX_ACTION_ARGS_CHARS + 100)}
    assert cc.parse_action({"tool": "save_theme", "args": args}) is None


def test_parse_action_never_raises_on_garbage() -> None:
    # A grab-bag of hostile / malformed inputs must all degrade to None.
    for garbage in ({}, {"tool": ""}, {"tool": "  "}, {"tool": "unknown", "args": 5}, 3.14, True):
        assert cc.parse_action(garbage) is None


# --- parse_turn: action threads through --------------------------------------


def test_parse_turn_attaches_valid_action() -> None:
    raw = json.dumps(
        {
            "reply": "Here is a theme to try.",
            "draft": {},
            "readiness": {"score": 0, "ready": False, "missing": []},
            "done": False,
            "action": {"tool": "propose_theme", "args": {"accent": "teal"}},
        }
    )
    turn = cc.parse_turn(raw, base_draft=_empty_draft(), last_user_message="make it warm")
    assert turn is not None
    assert turn.action is not None
    assert turn.action.tool == "propose_theme"
    assert turn.action.args == {"accent": "teal"}


def test_parse_turn_drops_unknown_action_but_keeps_reply() -> None:
    raw = json.dumps(
        {
            "reply": "I cannot do that, but here is what I can do.",
            "draft": {},
            "readiness": {"score": 0, "ready": False, "missing": []},
            "done": False,
            "action": {"tool": "delete_everything", "args": {}},
        }
    )
    turn = cc.parse_turn(raw, base_draft=_empty_draft(), last_user_message="wipe it")
    assert turn is not None
    assert turn.action is None
    assert turn.reply == "I cannot do that, but here is what I can do."


def test_parse_turn_drops_oversized_action_without_raising() -> None:
    raw = json.dumps(
        {
            "reply": "Saving your theme.",
            "draft": {},
            "readiness": {"score": 0, "ready": False, "missing": []},
            "done": False,
            "action": {
                "tool": "save_theme",
                "args": {"blob": "x" * (cc.MAX_ACTION_ARGS_CHARS + 1)},
            },
        }
    )
    turn = cc.parse_turn(raw, base_draft=_empty_draft(), last_user_message="save it")
    assert turn is not None
    assert turn.action is None
    assert turn.reply == "Saving your theme."


def test_parse_turn_no_action_leaves_action_none() -> None:
    raw = json.dumps(
        {
            "reply": "Which repo is the settings page in?",
            "draft": {"title": "Dark mode toggle"},
            "readiness": {"score": 30, "ready": False, "missing": ["repo scope"]},
            "done": False,
        }
    )
    turn = cc.parse_turn(raw, base_draft=_empty_draft(), last_user_message="add dark mode")
    assert turn is not None
    assert turn.action is None
    # Unchanged behavior: the rest of the turn is intact.
    assert turn.reply == "Which repo is the settings page in?"
    assert turn.intent == cc.INTENT_BUILD


def test_default_converse_turn_action_is_none() -> None:
    turn = cc.ConverseTurn(
        reply="hi",
        draft=_empty_draft(),
        readiness=cc.ConverseReadiness(score=0, ready=False),
        done=False,
    )
    assert turn.action is None


# --- API serialization: _converse_turn_payload -------------------------------


def _turn_payload(turn: cc.ConverseTurn) -> dict:
    import pytest

    pytest.importorskip("fastapi")
    import server.views as server_views

    return server_views._converse_turn_payload(turn, draft_id="d1", saved_path=Path("/tmp/d1.json"))


def test_api_payload_includes_action_when_present() -> None:
    turn = cc.ConverseTurn(
        reply="Here is a theme.",
        draft=_empty_draft(),
        readiness=cc.ConverseReadiness(score=0, ready=False),
        done=False,
        action=cc.ConverseAction(tool="propose_theme", args={"accent": "teal"}),
    )
    payload = _turn_payload(turn)
    assert payload["action"] == {"tool": "propose_theme", "args": {"accent": "teal"}}
    # Backward-compatible: the pre-existing fields are unchanged.
    assert payload["reply"] == "Here is a theme."
    assert payload["intent"] == cc.INTENT_BUILD
    assert payload["readiness"] == {"score": 0, "ready": False, "missing": []}
    assert payload["done"] is False
    assert "draft" in payload and "title" in payload["draft"]


def test_api_payload_action_is_null_when_absent() -> None:
    turn = cc.ConverseTurn(
        reply="Which repo?",
        draft=_empty_draft(),
        readiness=cc.ConverseReadiness(score=0, ready=False),
        done=False,
    )
    payload = _turn_payload(turn)
    assert payload["action"] is None
    # The rest of the contract is byte-for-byte the same as before the field.
    assert set(payload) >= {"reply", "intent", "readiness", "done", "draft", "draft_id"}
