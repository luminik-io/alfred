"""Slack integration package for Alfred.

Cohesive home for Alfred's Slack surface: trust gating, approval, posting,
intent routing, conversational replies, thread status/registry, the issue
bridge, control commands, event dedup, and the socket-mode listener that wires
them together.

Submodules are imported directly (``from slack.trust import SlackTrustStore``).
This ``__init__`` additionally re-exports the headline public API as a
convenience facade, resolved lazily via :pep:`562` ``__getattr__`` so importing
a light submodule (``slack.trust``) never drags in the heavy ones
(``slack.listener`` / ``slack.converse`` and their ``slack_sdk`` deps).
"""

from __future__ import annotations

# name -> submodule that defines it. Kept explicit (not reflection) so the
# facade never eagerly imports a submodule just to discover its exports.
_FACADE: dict[str, str] = {
    # trust
    "SlackTrustStore": "trust",
    "normalize_slack_user_id": "trust",
    "trusted_user_ids": "trust",
    "operator_user_id_from_env": "trust",
    # approval
    "SlackApproval": "approval",
    "ApprovalRequest": "approval",
    "ApprovalResult": "approval",
    "ThreadFeedback": "approval",
    "default_slack_client": "approval",
    "resolve_bot_token": "approval",
    "trusted_feedback_user_ids_from_env": "approval",
    "collect_trusted_thread_feedback": "approval",
    "APPROVAL_GRANTED": "approval",
    "APPROVAL_REJECTED": "approval",
    "APPROVAL_TIMEOUT": "approval",
    "APPROVAL_TRANSPORT_DOWN": "approval",
    # posting
    "SlackThreadPoster": "posting",
    "build_chat_postmessage_payload": "posting",
    "escape_mrkdwn": "posting",
    "github_issue_link": "posting",
    "github_url_link": "posting",
    "themed_agent_name": "posting",
    "themed_agent_role": "posting",
    # intent
    "classify_intent": "intent",
    "Intent": "intent",
    "ConversationContext": "intent",
    "RepoCatalog": "intent",
    # converse
    "SlackConverseConfig": "converse",
    "SlackStreamPoster": "converse",
    "run_slack_converse": "converse",
    "gather_thread_context": "converse",
    "stream_converse_to_slack": "converse",
    # memory
    "SlackMemoryCandidateProposer": "memory",
    "SlackConverseOfferStore": "memory",
    "CONVERSE_OFFER_SIGNATURE_KEY": "memory",
    # threads
    "SlackThreadRegistry": "threads",
    "SlackThreadRecord": "threads",
    "SlackThreadStatusTracker": "threads",
    "ThreadStatusRecord": "threads",
    "render_status_update": "threads",
    "default_issue_state_fetcher": "threads",
    # bridge
    "SlackIssueBridge": "bridge",
    "BridgeConfig": "bridge",
    "BridgeOutcome": "bridge",
    "build_issue_body": "bridge",
    # control
    "SlackControlHandler": "control",
    "is_control_message": "control",
    "parse_control_command": "control",
    # dedup
    "SeenEventStore": "dedup",
    # listener
    "SlackPlanningListener": "listener",
    "SlackInputEvent": "listener",
    "ListenerResult": "listener",
    "SlackPoster": "listener",
    "parse_slack_payload": "listener",
    "run_socket_mode": "listener",
}

__all__ = sorted(_FACADE)


def __getattr__(name: str) -> object:
    submodule = _FACADE.get(name)
    if submodule is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(f"{__name__}.{submodule}")
    return getattr(module, name)


def __dir__() -> list[str]:
    return sorted(set(__all__) | set(globals()))
