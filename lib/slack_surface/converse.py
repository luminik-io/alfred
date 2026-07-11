"""Conversational, streamed Slack answers for Alfred mentions and thread replies.

This module gives Slack the same conversational surface the desktop Ask /
Compose converse path already has. When a trusted user @-mentions Alfred or
replies in an Alfred-started thread, the listener can route the turn here
instead of immediately building a planning draft. The turn is:

1. CLASSIFIED through the SAME intent path the desktop converse uses
   (``compose_converse.run_turn`` -> ``resolve_intent``). A ``conversation``
   turn (greeting, "what are you", "how does review work", a plain question)
   gets a real, repo-grounded answer. A ``build`` turn gets a prose reply that
   OFFERS to file an issue via the existing approved-Slack-plan-to-issue bridge,
   instead of forcing a planning form on the user.

2. STREAMED into Slack. We post a placeholder message immediately, then tail the
   running turn's stream-json transcript and progressively ``chat.update`` the
   message as assistant text arrives. Updates are throttled so a fast token
   stream cannot trip Slack's per-method rate limit (``chat.update`` is Tier 3,
   roughly 50/min; one update per ``THROTTLE`` seconds keeps us well under).

3. GROUNDED in bounded thread context. Prior messages in the same thread are
   gathered (capped) and threaded into the converse transcript so a reply like
   "and what about the mobile app?" is answered with the earlier turns in view.

SAFETY. This module never mutates anything and never files an issue on its own.
A ``build`` turn only ever produces PROSE that offers the existing approval
path; the issue is created solely by the existing
``SlackIssueBridge``/operator-approval gate the listener already owns. Every
guard the listener applies upstream (trust gating, channel allowlist, the
seen-event de-dup) still runs before we are called.

Everything here is config-driven (``SlackConverseConfig.from_env``) and inert
unless explicitly enabled, so the listener keeps its exact prior behavior by
default. The model engine and the Slack client are both injected, so the unit
tests drive the full path against a fake client and a fake runner with no
network and no live model.
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from compose_converse import (
    INTENT_BUILD,
    INTENT_CONVERSATION,
    ConverseMessage,
    ConverseTurn,
)

from slack_surface.posting import SlackPoster

# Environment knobs. All optional; unset means the feature is off (or a safe
# default), so dropping this module into the listener changes nothing until an
# operator opts in.
ENV_ENABLED = "ALFRED_SLACK_CONVERSE_ENABLED"
ENV_CHANNELS = "ALFRED_SLACK_CONVERSE_CHANNELS"
ENV_ENGINE = "ALFRED_SLACK_CONVERSE_ENGINE"
# Reuse the Compose converse engine as a fallback so an operator who already
# configured the desktop converse surface gets Slack converse for free.
ENV_FALLBACK_ENGINE = "ALFRED_COMPOSE_CONVERSE_ENGINE"
ENV_TIMEOUT = "ALFRED_SLACK_CONVERSE_TIMEOUT"
ENV_THREAD_CONTEXT = "ALFRED_SLACK_CONVERSE_THREAD_CONTEXT"
ENV_THROTTLE = "ALFRED_SLACK_CONVERSE_STREAM_THROTTLE"

DEFAULT_TIMEOUT = 180
# How many prior thread messages to gather as context. Bounded so a long thread
# never blows up the prompt or the Slack read.
DEFAULT_THREAD_CONTEXT = 12
# Minimum seconds between ``chat.update`` calls while streaming. Slack's
# ``chat.update`` is Tier 3 (~50/min). One update per second is ~60/min worst
# case, so we default a touch above that to stay comfortably inside the limit
# even with clock jitter.
DEFAULT_THROTTLE = 1.2
# While streaming, trim each partial into a single short message so a fast
# growing stream stays small and cheap to re-post. The final reconciled answer
# is NOT held to this cap (see ``finalize``); it is only bounded by Slack's own
# message-body limit so the full prose lands.
MAX_STREAM_CHARS = 3500
# Slack's hard message-body limit is ~40000 characters. We bound the final
# reconciled write to a touch under that so a long answer lands in full yet an
# update can never fail for length.
MAX_MESSAGE_CHARS = 39000
# How many times the FINAL write (``finalize``) retries after a Slack 429 rate
# limit before giving up. The reconciled answer is the one write that must land,
# so it is allowed a few honored ``Retry-After`` waits. A streaming partial gets
# no extra attempts: the next update supersedes it, so a partial that 429s is
# simply dropped rather than blocking the stream.
MAX_FINALIZE_RATE_LIMIT_RETRIES = 5
# Fallback wait (seconds) when Slack signals a 429 but supplies no parseable
# ``Retry-After`` header. Slack's docs say the header is always present on a 429;
# this is only a defensive floor so a malformed response still backs off.
DEFAULT_RETRY_AFTER_SECONDS = 1.0
# Upper bound on any single honored ``Retry-After`` wait, so a hostile or buggy
# header (``Retry-After: 99999``) cannot wedge the poster for minutes.
MAX_RETRY_AFTER_SECONDS = 30.0
# Default upper bound on the CUMULATIVE backoff one poster call may sleep. The
# poster runs on a Socket Mode handler thread, so a long retry loop would hold
# that thread (and, on a busy pool, stall other Slack events) for minutes.
# Capping the total honored wait bounds how long any single call can block, even
# across several 429s. The per-poster value is read from
# ``ALFRED_SLACK_MAX_TOTAL_BACKOFF_SECONDS`` in ``SlackStreamPoster.__init__``
# (config-driven-tunables rule), defaulting to this.
MAX_TOTAL_BACKOFF_SECONDS = 30.0

# The placeholder shown the instant a mention lands, before the first token.
PLACEHOLDER = "_Alfred is thinking…_"


class StreamingSlackClient(SlackPoster, Protocol):
    """The Slack Web API subset the streaming poster needs.

    Extends the package-wide :class:`slack_surface.posting.SlackPoster` (which supplies
    ``chat_postMessage``) with the streaming ``chat_update`` verb, so the
    ``chat_postMessage`` shim lives in exactly one place. ``slack_sdk.WebClient``
    satisfies this natively; tests pass a fake with the same method names.
    ``conversations_replies`` is optional (thread context is best-effort and
    degrades to no context when it is absent)."""

    def chat_update(self, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class SlackConverseConfig:
    """Resolved, immutable converse configuration for one listener instance."""

    enabled: bool = False
    channels: frozenset[str] = frozenset()
    engine: str = ""
    timeout: int = DEFAULT_TIMEOUT
    thread_context: int = DEFAULT_THREAD_CONTEXT
    throttle: float = DEFAULT_THROTTLE

    @classmethod
    def from_env(cls) -> SlackConverseConfig:
        return cls(
            # Conversation is Alfred's default Slack surface. Converse is ON by
            # default and only stands down when the operator explicitly disables
            # it (``ALFRED_SLACK_CONVERSE_ENABLED=0``) or when no engine can be
            # resolved (``engages`` returns False without an engine, so an
            # unconfigured runtime still degrades to planning intake rather than
            # posting an error). Previously this was off-by-default, which is why
            # every mention fell through to a planning draft.
            enabled=_env_flag(ENV_ENABLED, default=True),
            channels=frozenset(_parse_channels(os.environ.get(ENV_CHANNELS))),
            engine=(
                os.environ.get(ENV_ENGINE) or os.environ.get(ENV_FALLBACK_ENGINE) or ""
            ).strip(),
            timeout=_env_int(ENV_TIMEOUT, DEFAULT_TIMEOUT),
            thread_context=_env_int(ENV_THREAD_CONTEXT, DEFAULT_THREAD_CONTEXT),
            throttle=_env_float(ENV_THROTTLE, DEFAULT_THROTTLE),
        )

    def engages(self, channel: str) -> bool:
        """True iff converse should run for ``channel``.

        On by default (conversation is Alfred's default Slack surface), and only
        when an engine resolves, so an unconfigured runtime still declines and
        falls back to planning intake rather than erroring. When on, it is scoped
        to the channel allowlist. An empty allowlist means "every channel the
        listener already trusts" -- the listener has already gated trust and (for
        ambient) its own allowlist before we are reached, so an empty converse
        allowlist is not a blast radius, it just declines to add a second,
        narrower gate. An operator who wants converse limited to specific channels
        lists them explicitly, or disables it entirely with
        ``ALFRED_SLACK_CONVERSE_ENABLED=0``.
        """
        if not self.enabled or not self.engine:
            return False
        if not self.channels:
            return True
        return channel in self.channels


# ---------------------------------------------------------------------------
# Thread context gathering (bounded, best-effort)
# ---------------------------------------------------------------------------


# Hard cap on how many thread messages we will page through, so even a very
# long thread cannot turn context-gathering into an unbounded Slack read. We
# page from the thread root (Slack returns replies oldest-first) until we either
# exhaust the thread or hit this many messages, then keep the most recent
# ``limit`` turns -- the ones the user is actually replying to.
THREAD_SCAN_CAP = 400
# Per-page size for ``conversations_replies`` while paging toward the newest
# turns. Slack accepts up to 1000; a few hundred keeps each call cheap.
THREAD_PAGE_SIZE = 200


def gather_thread_context(
    client: Any,
    *,
    channel: str,
    root_ts: str,
    bot_user_id: str = "",
    limit: int = DEFAULT_THREAD_CONTEXT,
    exclude_ts: str = "",
) -> list[ConverseMessage]:
    """Read prior thread messages as converse context, oldest-first and bounded.

    Best-effort: a missing ``conversations_replies`` method, an API error, or a
    not-ok response all degrade to an empty context rather than raising, so a
    transient Slack read never blocks the answer. Only the bot's own messages
    (``user`` equals ``bot_user_id``) map to the ``assistant`` role; every other
    author -- humans and any third-party bot alike -- maps to ``user`` so a
    stray integration's posts are never mistaken for Alfred's own prior answers.
    ``exclude_ts`` drops the triggering message itself (it is supplied
    separately as the latest user turn).

    ``conversations_replies`` returns replies oldest-first, so a single
    ``limit``-sized read of a long thread would only ever see the *oldest* turns
    and miss the recent back-and-forth the user is replying to. We page forward
    (bounded by :data:`THREAD_SCAN_CAP`) to reach the end of the thread, then
    keep the most recent ``limit`` turns in chronological order.
    """
    if limit <= 0:
        return []
    replies = getattr(client, "conversations_replies", None)
    if replies is None:
        return []
    bot = (bot_user_id or "").strip()
    out: list[ConverseMessage] = []
    cursor = ""
    while len(out) < THREAD_SCAN_CAP:
        kwargs: dict[str, Any] = {
            "channel": channel,
            "ts": root_ts,
            "limit": THREAD_PAGE_SIZE,
        }
        if cursor:
            kwargs["cursor"] = cursor
        try:
            resp = replies(**kwargs)
        except Exception:
            break
        data = _as_mapping(resp)
        if not data.get("ok", False):
            break
        for message in data.get("messages") or []:
            if not isinstance(message, dict):
                continue
            ts = str(message.get("ts") or "")
            if exclude_ts and ts == exclude_ts:
                continue
            raw_text = str(message.get("text") or "")
            text = _clean_text(raw_text)
            if not text:
                continue
            author = str(message.get("user") or "")
            is_alfred = bool(bot) and author == bot
            role = "assistant" if is_alfred else "user"
            out.append(ConverseMessage(role=role, content=text))
        cursor = _next_cursor(data)
        if not cursor:
            break
    # Keep the most recent ``limit`` turns, preserving chronological order.
    return out[-limit:]


def _next_cursor(data: dict[str, Any]) -> str:
    """Pull the next-page cursor from a Slack response, empty when exhausted."""
    meta = data.get("response_metadata")
    if isinstance(meta, dict):
        cursor = meta.get("next_cursor")
        if isinstance(cursor, str):
            return cursor.strip()
    return ""


# ---------------------------------------------------------------------------
# Reactive rate-limit (HTTP 429 / Retry-After) handling
# ---------------------------------------------------------------------------


def _retry_after_seconds(exc: BaseException) -> float | None:
    """Return the ``Retry-After`` wait for a Slack 429, or ``None`` otherwise.

    ``slack_sdk`` raises ``SlackApiError`` with a ``response`` whose
    ``status_code`` is 429 and whose ``headers`` carry ``Retry-After`` (seconds)
    when a Web API method is rate limited. We read both off the attached response
    without importing ``slack_sdk`` (so the module stays import-light and the
    tests can pass a plain fake), returning the clamped wait when this is a 429
    and ``None`` for any other error so the caller can re-raise / give up.

    The wait is floored at ``DEFAULT_RETRY_AFTER_SECONDS`` when the header is
    missing or unparseable, and capped at ``MAX_RETRY_AFTER_SECONDS`` so a buggy
    or hostile header can never wedge the poster.
    """
    response = getattr(exc, "response", None)
    if response is None:
        return None
    status = _response_status_code(response)
    if status != 429:
        return None
    headers = _response_headers(response)
    raw = headers.get("Retry-After") or headers.get("retry-after")
    try:
        wait = float(raw) if raw is not None else DEFAULT_RETRY_AFTER_SECONDS
    except (TypeError, ValueError):
        wait = DEFAULT_RETRY_AFTER_SECONDS
    if wait <= 0:
        wait = DEFAULT_RETRY_AFTER_SECONDS
    return min(wait, MAX_RETRY_AFTER_SECONDS)


def _response_status_code(response: Any) -> int | None:
    status = getattr(response, "status_code", None)
    if status is None and isinstance(response, dict):
        status = response.get("status_code")
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _response_headers(response: Any) -> dict[str, str]:
    headers = getattr(response, "headers", None)
    if headers is None and isinstance(response, dict):
        headers = response.get("headers")
    if isinstance(headers, dict):
        return headers
    # Some slack_sdk responses expose a mapping-like headers object; coerce it.
    if headers is not None:
        try:
            return dict(headers)
        except (TypeError, ValueError):
            return {}
    return {}


# ---------------------------------------------------------------------------
# Streaming poster: placeholder -> throttled chat.update
# ---------------------------------------------------------------------------


class SlackStreamPoster:
    """Post a placeholder, then progressively ``chat.update`` as text arrives.

    The poster owns exactly one Slack message. :meth:`start` posts the
    placeholder and records its ``ts``. :meth:`update` rewrites the message with
    the latest streamed text, but only when at least ``throttle`` seconds have
    passed since the last update (so a fast token stream cannot exceed Slack's
    ``chat.update`` rate limit). :meth:`finalize` always writes the final text,
    ignoring the throttle, so the reconciled answer is never dropped.

    PROACTIVE throttling (``throttle``) keeps us under the per-method limit in
    the common case; REACTIVE backoff handles the case where Slack still returns
    a 429. On a 429 the poster honors the ``Retry-After`` header and retries: the
    final write (:meth:`finalize`) retries up to
    :data:`MAX_FINALIZE_RATE_LIMIT_RETRIES` times so the reconciled answer always
    lands, while a streaming partial (:meth:`update`) is dropped on a 429 because
    the next update supersedes it. Any other transport error never propagates, it
    just means that one update is skipped.

    ``now`` and ``sleep`` are both injectable so tests drive the throttle and the
    ``Retry-After`` backoff deterministically without touching the wall clock.
    """

    def __init__(
        self,
        client: Any,
        *,
        channel: str,
        thread_ts: str,
        throttle: float = DEFAULT_THROTTLE,
        now: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._client = client
        self._channel = channel
        self._thread_ts = thread_ts
        self._throttle = max(0.0, throttle)
        self._now = now or time.monotonic
        self._sleep = sleep or time.sleep
        self._max_total_backoff = max(
            0.0, _env_float("ALFRED_SLACK_MAX_TOTAL_BACKOFF_SECONDS", MAX_TOTAL_BACKOFF_SECONDS)
        )
        self._message_ts: str = ""
        self._last_update_at: float = 0.0
        self._last_text: str = ""

    @property
    def message_ts(self) -> str:
        return self._message_ts

    def start(self, placeholder: str = PLACEHOLDER) -> bool:
        """Post the placeholder message. Returns True iff a ts was obtained."""
        post = getattr(self._client, "chat_postMessage", None)
        if post is None:
            return False
        attempt = 0
        slept = 0.0
        while True:
            try:
                resp = post(
                    channel=self._channel,
                    thread_ts=self._thread_ts,
                    text=placeholder,
                )
                break
            except Exception as exc:
                # Without the placeholder there is no ts to stream into, so the
                # whole turn would be silent. Honor a 429 Retry-After and retry
                # a bounded number of times before giving up, but never sleep
                # past the cumulative backoff budget: this runs on a Slack
                # handler thread, so a long retry must not stall other events.
                wait = _retry_after_seconds(exc)
                if (
                    wait is not None
                    and attempt < MAX_FINALIZE_RATE_LIMIT_RETRIES
                    and slept + wait <= self._max_total_backoff
                ):
                    self._sleep(wait)
                    slept += wait
                    attempt += 1
                    continue
                return False
        data = _as_mapping(resp)
        self._message_ts = str(data.get("ts") or "")
        self._last_text = placeholder
        self._last_update_at = self._now()
        return bool(self._message_ts)

    def update(self, text: str) -> None:
        """Throttled progressive update. Skips when called too soon or unchanged."""
        text = _trim_stream(text)
        if not self._message_ts or not text or text == self._last_text:
            return
        if self._now() - self._last_update_at < self._throttle:
            return
        self._write(text)

    def finalize(self, text: str) -> bool:
        """Final update, never throttled and never stream-trimmed.

        The reconciled answer lands in full, bounded only by Slack's own
        message-body limit (:data:`MAX_MESSAGE_CHARS`) so a long answer is not
        clipped to the much smaller streaming cap.

        Returns True iff the reconciled answer is on Slack: either it was
        written, or the last streamed update already carried it (text unchanged).
        Returns False when there is no message to update, the final answer is
        empty (so only the placeholder/partial is showing -- nothing reconciled
        landed), or the final write never landed (a persistent 429 past the
        budget, or a non-429 error), so the caller can surface that the
        reconciled answer did not reach Slack.
        """
        text = _cap_message(text)
        if not self._message_ts:
            return False
        if not text:
            # An empty reconciled answer never replaces the placeholder/partial,
            # so delivery did NOT produce a real answer: report failure.
            return False
        if text == self._last_text:
            # Nothing new to write; the current message already holds the answer.
            return True
        return self._write(text, retries=MAX_FINALIZE_RATE_LIMIT_RETRIES)

    def _write(self, text: str, *, retries: int = 0) -> bool:
        """Send one ``chat.update``. On a Slack 429 this honors ``Retry-After``
        and retries up to ``retries`` more times: a streaming partial passes 0
        (it is dropped, since the next update supersedes it) while the final
        write passes a few so the reconciled answer always lands. Retries stop
        once the cumulative backoff budget is spent so a handler thread is never
        held for minutes. Any non-429 transport error is swallowed (one update
        skipped). Returns True iff the write landed.
        """
        update = getattr(self._client, "chat_update", None)
        if update is None:
            return False
        attempt = 0
        slept = 0.0
        while True:
            try:
                update(channel=self._channel, ts=self._message_ts, text=text)
            except Exception as exc:
                wait = _retry_after_seconds(exc)
                if (
                    wait is not None
                    and attempt < retries
                    and slept + wait <= self._max_total_backoff
                ):
                    self._sleep(wait)
                    slept += wait
                    attempt += 1
                    continue
                return False
            self._last_text = text
            self._last_update_at = self._now()
            return True


# ---------------------------------------------------------------------------
# Streaming runner: run the turn on a worker while tailing the transcript
# ---------------------------------------------------------------------------


@dataclass
class ConverseStreamResult:
    """Outcome of a streamed converse turn."""

    turn: ConverseTurn | None
    streamed: bool = False
    error: str = ""
    # False when the final reconciled answer never reached Slack (a persistent
    # 429 past the backoff budget, or a non-429 error on the final chat.update).
    # The turn still ran server-side, but Slack may show only a partial.
    finalized: bool = True

    @property
    def ok(self) -> bool:
        return self.turn is not None


def stream_converse_to_slack(
    *,
    run_turn: Callable[[], ConverseTurn | None],
    poster: SlackStreamPoster,
    transcript_path: Path,
    extract_tokens: Callable[[Path], list[str]],
    poll_seconds: float = 0.2,
    render: Callable[[ConverseTurn], str] | None = None,
) -> ConverseStreamResult:
    """Run one converse turn while progressively updating a Slack message.

    ``run_turn`` is the blocking interrogator call (it tees assistant text to
    ``transcript_path``). It runs on a worker thread so this loop can tail the
    transcript with ``extract_tokens`` and ``poster.update`` the partial text as
    it grows. When the turn returns, ``render`` shapes the final reply text and
    ``poster.finalize`` writes it. ``run_turn`` returning ``None`` (no live
    session / unparseable output) yields a result the caller surfaces honestly.

    Pure orchestration: no Slack or model specifics live here, so the unit tests
    drive it with a fake runner that writes a transcript and a fake poster.
    """
    result_box: dict[str, Any] = {}
    done = threading.Event()

    def _worker() -> None:
        try:
            result_box["turn"] = run_turn()
        except Exception as exc:  # never let the worker crash the listener
            result_box["error"] = str(exc) or exc.__class__.__name__
        finally:
            done.set()

    worker = threading.Thread(target=_worker, name="slack-converse-stream", daemon=True)
    worker.start()

    streamed = False
    while not done.wait(poll_seconds):
        partial = _join_tokens(_safe_extract(extract_tokens, transcript_path))
        if partial:
            poster.update(partial)
            streamed = True

    worker.join(1.0)

    if "error" in result_box:
        return ConverseStreamResult(turn=None, streamed=streamed, error=result_box["error"])
    turn = result_box.get("turn")
    if turn is None:
        return ConverseStreamResult(turn=None, streamed=streamed)
    final_text = render(turn) if render is not None else turn.reply
    finalized = poster.finalize(final_text)
    if not finalized:
        # The turn ran, but the final chat.update did not land (Slack may still
        # show only a partial). Surface it instead of reporting a clean success.
        print(
            "[SLACK-CONVERSE-WARN] final chat.update did not land; "
            "Slack may show only a partial answer",
            file=sys.stderr,
        )
    return ConverseStreamResult(turn=turn, streamed=streamed, finalized=finalized)


# ---------------------------------------------------------------------------
# Reply rendering: conversation answer vs build offer
# ---------------------------------------------------------------------------


@dataclass
class ConverseReply:
    """The reply text and whether it offered to file an issue."""

    text: str
    intent: str
    offered_issue: bool = False
    fields: dict[str, Any] = field(default_factory=dict)
    # A stable fingerprint of the file affordance this turn WOULD show (its lead
    # line, keyed off the draft title). The listener persists it per thread and
    # feeds it back on the next turn as ``prior_offer_signature`` so the offer is
    # shown once, when the affordance first appears or materially changes, not
    # re-appended verbatim to every build turn. Empty when no offer applies.
    offer_signature: str = ""


def _offer_signature(turn: ConverseTurn) -> str:
    """A stable fingerprint of the file affordance a build turn would show.

    The affordance text only varies by the draft title (see :func:`_build_offer`),
    so the title is the whole signal: two turns that would show the same lead
    line share a signature and the offer is not repeated. Normalised (stripped,
    lowercased, collapsed whitespace) so trivial rewordings of the same title do
    not read as a change and re-trigger the block.
    """
    title = " ".join((turn.draft.title or "").split()).strip().lower()
    return f"offer:{title}"


def render_converse_reply(
    turn: ConverseTurn,
    *,
    bridge_enabled: bool,
    prior_offer_signature: str = "",
) -> ConverseReply:
    """Shape a converse turn into the Slack reply text.

    A ``conversation`` turn is returned as-is: a plain, warm answer. A ``build``
    turn keeps the model's prose reply and, the FIRST time the affordance appears
    or its material content changes, APPENDS a short, optional offer to file an
    issue through the existing approval bridge -- never a forced form. When the
    bridge is disabled the offer is omitted (we do not advertise a path that
    cannot run); the conversational answer still stands on its own.

    Repetition guard: ``prior_offer_signature`` is the affordance fingerprint the
    previous turn in this thread showed (persisted by the listener). When this
    turn's signature matches it, the file-affordance block is SUPPRESSED so the
    identical "reply ``ship it`` to file it" boilerplate is not appended turn
    after turn. The block returns only when the affordance first becomes fileable
    or the draft title materially changes. The current signature is returned on
    the reply so the caller can persist it for the next turn.
    """
    reply = (turn.reply or "").strip()
    if turn.intent == INTENT_CONVERSATION:
        # A conversation turn never carries a file affordance; carry the prior
        # signature forward unchanged so a chat aside mid-build does not reset
        # the dedup state and re-trigger the offer on the next build turn.
        return ConverseReply(
            text=reply,
            intent=INTENT_CONVERSATION,
            offer_signature=prior_offer_signature,
        )

    # build turn: offer, do not force.
    if not bridge_enabled:
        return ConverseReply(text=reply, intent=INTENT_BUILD, offered_issue=False)

    signature = _offer_signature(turn)
    if signature == prior_offer_signature:
        # The affordance is unchanged since the last turn; showing it again would
        # just repeat the same boilerplate. Keep the model's prose, drop the block.
        return ConverseReply(
            text=reply,
            intent=INTENT_BUILD,
            offered_issue=False,
            offer_signature=signature,
        )

    offer = _build_offer(turn)
    text = f"{reply}\n\n{offer}" if reply else offer
    return ConverseReply(
        text=text,
        intent=INTENT_BUILD,
        offered_issue=True,
        offer_signature=signature,
    )


def _build_offer(turn: ConverseTurn) -> str:
    title = (turn.draft.title or "").strip()
    if title:
        lead = f"I can turn this into a tracked issue (“{title}”) when you are ready."
    else:
        lead = "I can turn this into a tracked issue when you are ready."
    return (
        f"{lead} Reply `ship it` to file it, or keep talking and I will refine the "
        "scope first. Nothing is filed and no code runs until you approve."
    )


# ---------------------------------------------------------------------------
# Top-level orchestration: classify + stream one Slack converse turn
# ---------------------------------------------------------------------------


@dataclass
class SlackConverseOutcome:
    """What the listener needs back after a streamed converse turn."""

    handled: bool
    intent: str = ""
    offered_issue: bool = False
    streamed: bool = False
    detail: str = ""
    # False when the final reconciled answer never reached Slack (a persistent
    # 429 past the backoff budget, or a non-429 error on the final chat.update).
    # The turn still ran, but the listener should treat delivery as degraded
    # rather than a clean success when this is False.
    finalized: bool = True
    # False when the converse turn could NOT produce a real answer (engine error,
    # timeout, or unparseable output) and only a generic "could not reach"
    # message was posted. The listener uses this to fall through to the
    # deterministic status / planning handler instead of leaving a transient
    # engine failure hiding locally available fleet status. True whenever a real
    # conversation or build turn was rendered.
    answered: bool = True
    # The file-affordance fingerprint the listener should persist for the next
    # turn, passed back as ``prior_offer_signature`` so the offer block is not
    # repeated verbatim. This is the CURRENT turn's fingerprint only when the
    # offer actually reached Slack (the final chat.update landed); on a degraded
    # delivery it is the PRIOR fingerprint carried forward unchanged, so an offer
    # the user never saw is not treated as already shown. Empty when no answer
    # was rendered.
    offer_signature: str = ""


def run_slack_converse(
    *,
    client: Any,
    config: SlackConverseConfig,
    channel: str,
    thread_ts: str,
    user_message: str,
    bot_user_id: str = "",
    exclude_ts: str = "",
    bridge_enabled: bool = False,
    workdir: Path | None = None,
    build_turn: Callable[..., ConverseTurn | None] | None = None,
    transcript_for: Callable[[str], Path] | None = None,
    extract_tokens: Callable[[Path], list[str]] | None = None,
    now: Callable[[], float] | None = None,
    suppress_engine_error: bool = False,
    prior_offer_signature: str = "",
) -> SlackConverseOutcome:
    """Classify, stream, and post one conversational Slack answer.

    The whole pipeline:

    1. Gather bounded prior thread context (best-effort).
    2. Append the triggering ``user_message`` as the latest user turn.
    3. Post a placeholder, then run the converse turn while progressively
       updating the Slack message from the streamed transcript.
    4. Render the final reply: a plain answer for a ``conversation`` turn, or a
       prose answer plus an OPTIONAL offer to file an issue for a ``build`` turn.

    ``build_turn`` runs one interrogator turn and returns a :class:`ConverseTurn`
    (or ``None`` when no live session / unparseable). It defaults to the real
    Compose-grounded runner; tests inject a fake that writes a transcript and
    returns a canned turn, so no model or network is touched. Returns a
    :class:`SlackConverseOutcome`; ``handled`` is False only when there was no
    usable answer (the listener then falls through to its prior behavior).

    ``suppress_engine_error`` changes the FAILURE path only. When a caller has
    its own deterministic fallback (a status query answered from local fleet
    state, or planning intake), it passes True so that a failed turn does not
    strand the user on the generic "could not reach the engine" message. In that
    mode a failed turn finalizes the placeholder to a short transitional line and
    returns ``handled=False, answered=False``, so the listener falls through to
    that deterministic handler, which posts the real answer as a follow-up.
    Left False (the default), a failed turn keeps posting the generic guidance
    and returns ``handled=True, answered=False`` (the caller then has nothing
    better to fall through to, so the guidance is the honest outcome).

    ``prior_offer_signature`` is the file-affordance fingerprint the previous
    turn in this thread showed (the listener persists it per thread). It gates
    whether the "reply ``ship it`` to file it" block is appended: the block is
    shown only when the affordance first appears or its material content changes,
    never verbatim turn after turn. The turn's own signature comes back on
    :attr:`SlackConverseOutcome.offer_signature` for the caller to persist.
    """
    if build_turn is None:
        build_turn = _default_build_turn
    if transcript_for is None:
        transcript_for = _default_transcript_for
    if extract_tokens is None:
        extract_tokens = _default_extract_tokens()

    context = gather_thread_context(
        client,
        channel=channel,
        root_ts=thread_ts,
        bot_user_id=bot_user_id,
        limit=config.thread_context,
        exclude_ts=exclude_ts,
    )
    clean_message = _clean_text(user_message)
    if not clean_message:
        return SlackConverseOutcome(handled=False, detail="empty message")
    messages = [*context, ConverseMessage(role="user", content=clean_message)]

    firing_id = _converse_firing_id()
    transcript_path = transcript_for(firing_id)

    poster = SlackStreamPoster(
        client,
        channel=channel,
        thread_ts=thread_ts,
        throttle=config.throttle,
        now=now,
    )
    if not poster.start():
        return SlackConverseOutcome(handled=False, detail="could not post placeholder")

    def _run() -> ConverseTurn | None:
        return build_turn(
            messages=messages,
            engine=config.engine,
            timeout=config.timeout,
            firing_id=firing_id,
            workdir=workdir or Path.cwd(),
        )

    reply_box: dict[str, ConverseReply] = {}

    def _render(turn: ConverseTurn) -> str:
        reply = render_converse_reply(
            turn,
            bridge_enabled=bridge_enabled,
            prior_offer_signature=prior_offer_signature,
        )
        reply_box["reply"] = reply
        return reply.text

    result = stream_converse_to_slack(
        run_turn=_run,
        poster=poster,
        transcript_path=transcript_path,
        extract_tokens=extract_tokens,
        render=_render,
    )

    if not result.ok:
        detail = result.error or "live_session_unavailable"
        if suppress_engine_error:
            # The caller has a deterministic fallback (status from local state, or
            # planning intake). Do not strand the user on the generic guidance:
            # leave a short transitional line on the placeholder and report the
            # turn as unanswered so the caller's fallback posts the real answer.
            poster.finalize("One moment, pulling that up directly.")
            return SlackConverseOutcome(
                handled=False,
                answered=False,
                streamed=result.streamed,
                detail=detail,
            )
        fallback_landed = poster.finalize(
            "I could not reach the conversational engine just now. "
            "Try again in a moment, or send the request as a plan."
        )
        return SlackConverseOutcome(
            handled=True,
            answered=False,
            streamed=result.streamed,
            detail=detail,
            finalized=fallback_landed,
        )

    reply = reply_box.get("reply")
    # The affordance fingerprint the listener should persist for the next turn.
    # The file offer is only appended in the FINAL render, so it reached the user
    # only if the final chat.update landed (``result.finalized``). When delivery
    # was degraded (a persistent 429 past the backoff budget, or a transport
    # error on the final update), the user never saw the "reply ``ship it``"
    # affordance, so we must NOT advance the signature: carry the prior one
    # forward unchanged so the next turn re-shows the offer rather than
    # suppressing one that never landed.
    if result.finalized:
        offer_signature = reply.offer_signature if reply else prior_offer_signature
    else:
        offer_signature = prior_offer_signature
    return SlackConverseOutcome(
        handled=True,
        intent=reply.intent if reply else "",
        offered_issue=bool(reply and reply.offered_issue),
        streamed=result.streamed,
        # Carry the delivery signal forward: result.finalized is False when the
        # reconciled answer did not land on Slack despite the turn running, so
        # the listener can tell a clean success from a degraded delivery.
        finalized=result.finalized,
        # Carry the affordance fingerprint forward so the listener can persist it
        # and feed it back next turn, keeping the file offer from repeating. Only
        # advanced when the offer actually reached Slack (see above).
        offer_signature=offer_signature,
    )


def _default_build_turn(
    *,
    messages: list[ConverseMessage],
    engine: str,
    timeout: int,
    firing_id: str,
    workdir: Path,
) -> ConverseTurn | None:
    """Run one Compose-grounded interrogator turn for Slack (real model path).

    Reuses every Compose converse primitive so the intent classification and
    spec building are identical to the desktop Ask surface: the same system
    prompt, repo grounding, code map, and ``run_turn`` (which calls
    ``resolve_intent``). Streaming is forced so the turn tees assistant tokens to
    the transcript the caller tails. Returns ``None`` on any setup failure so the
    listener degrades to its prior planning intake rather than raising.
    """
    try:
        import compose_converse as cc
        from agent_runner.metadata import load_prompt
    except Exception:
        return None

    repos = _context_repos(messages)
    try:
        workspace_root = _workspace_root()
        repo_grounding = cc.build_repo_grounding(
            repos,
            workspace_root=workspace_root,
            repo_to_local=_repo_to_local(),
        )
        code_map = cc.load_code_map(_code_map_path())
        intake_guidance = cc.intake_guidance_for(os.environ.get("ALFRED_INTAKE_PROFILE") or "")
        # Live fleet snapshot so a status question ("what's the fleet doing?",
        # "why did lucius fail on #1038?", "what shipped today?") is answered
        # from real runtime state, not the repo grounding. Best-effort: a missing
        # reader or a read failure degrades to an empty block.
        operational_grounding = _operational_grounding()
        system_prompt = cc.render_system_prompt(
            prompt_path=_interrogator_prompt_path(),
            repo_grounding=repo_grounding,
            code_map=code_map,
            intake_guidance=intake_guidance,
            loader=load_prompt,
            operational_grounding=operational_grounding,
        )
    except OSError:
        return None
    except Exception:
        return None

    from spec_helper import IssueDraft

    return cc.run_turn(
        system_prompt=system_prompt,
        messages=messages,
        repo_grounding=repo_grounding,
        code_map=code_map,
        intake_guidance=intake_guidance,
        base_draft=IssueDraft(title=""),
        engine=engine,
        workdir=workdir,
        timeout=timeout,
        firing_id=firing_id,
    )


def _default_transcript_for(firing_id: str) -> Path:
    """Resolve the transcript JSONL the converse turn tees to.

    Mirrors ``agent_runner.transcript_path`` bucketing so the tail reads the same
    file the streaming Claude path writes.
    """
    try:
        from agent_runner import transcript_path
        from compose_converse import CONVERSE_AGENT

        return transcript_path(CONVERSE_AGENT, firing_id)
    except Exception:  # pragma: no cover - defensive
        from datetime import UTC, datetime

        base = os.environ.get("ALFRED_HOME") or os.path.expanduser("~/.alfred")
        month = datetime.now(UTC).strftime("%Y-%m")
        return (
            Path(base)
            / "state"
            / "transcripts"
            / "compose-interrogator"
            / month
            / f"{firing_id}.jsonl"
        )


def _default_extract_tokens() -> Callable[[Path], list[str]]:
    try:
        from server.streaming import assistant_text_fragments

        return assistant_text_fragments
    except Exception:  # pragma: no cover - defensive
        return _fallback_assistant_text_fragments


def _fallback_assistant_text_fragments(transcript_path: Path) -> list[str]:
    """Minimal stream-json assistant-text extractor (mirror of server helper)."""
    import json

    try:
        text = transcript_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    fragments: list[str] = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict) or obj.get("type") != "assistant":
            continue
        message = obj.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                value = block.get("text")
                if isinstance(value, str) and value:
                    fragments.append(value)
    return fragments


def _converse_firing_id() -> str:
    try:
        from compose_converse import converse_firing_id

        return converse_firing_id()
    except Exception:  # pragma: no cover - defensive
        from datetime import UTC, datetime

        return datetime.now(UTC).strftime("slack-converse-%Y%m%d-%H%M%S-%f")


def _context_repos(messages: Iterable[ConverseMessage]) -> list[str]:
    """Pull any ``owner/repo`` slugs mentioned in the conversation for grounding."""
    seen: set[str] = set()
    out: list[str] = []
    for message in messages:
        for match in re.findall(r"\b[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\b", message.content):
            if match not in seen:
                seen.add(match)
                out.append(match)
    return out


def _workspace_root() -> Path:
    try:
        from agent_runner.paths import WORKSPACE

        return Path(WORKSPACE)
    except Exception:  # pragma: no cover - defensive
        base = os.environ.get("WORKSPACE_ROOT") or os.path.expanduser("~/code")
        return Path(base)


def _repo_to_local() -> dict[str, str]:
    try:
        from agent_runner.github import GH_REPO_TO_LOCAL

        return dict(GH_REPO_TO_LOCAL)
    except Exception:  # pragma: no cover - defensive
        return {}


def _code_map_path() -> Path:
    base = os.environ.get("ALFRED_HOME") or os.path.expanduser("~/.alfred")
    return Path(base) / "state" / "code-map.json"


def _operational_grounding() -> str:
    """Build the live fleet snapshot for a Slack converse turn (best-effort).

    Reads the same read-only fleet reader the desktop client uses and formats a
    bounded status block. Any failure (import error, missing runtime, read error)
    degrades to an empty string so a mention is still answered from the repo
    grounding alone.
    """
    try:
        from converse_grounding import (
            build_operational_grounding,
            default_operational_reader_factory,
        )

        reader = default_operational_reader_factory()()
        return build_operational_grounding(reader)
    except Exception:  # pragma: no cover - defensive
        return ""


def _interrogator_prompt_path() -> Path:
    override = os.environ.get("ALFRED_SPEC_INTERROGATOR_PROMPT")
    if override:
        return Path(override)
    relative = Path("prompts") / "spec-interrogator.md"
    candidates: list[Path] = []
    runtime_home = os.environ.get("ALFRED_HOME")
    if runtime_home:
        candidates.append(Path(runtime_home) / relative)
    candidates.append(Path(__file__).resolve().parents[1] / relative)
    candidates.append(Path.cwd() / relative)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[-1]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _join_tokens(tokens: Iterable[str]) -> str:
    return "".join(tokens).strip()


def _safe_extract(extract: Callable[[Path], list[str]], path: Path) -> list[str]:
    try:
        return extract(path)
    except Exception:
        return []


def _trim_stream(text: str) -> str:
    return _cap_text(text, MAX_STREAM_CHARS)


def _cap_message(text: str) -> str:
    return _cap_text(text, MAX_MESSAGE_CHARS)


def _cap_text(text: str, cap: int) -> str:
    text = (text or "").strip()
    if len(text) <= cap:
        return text
    return text[: cap - 1].rstrip() + "…"


def _clean_text(text: str) -> str:
    # Strip Slack mention tokens and link markup so the converse turn reads
    # plain prose, mirroring the listener's own cleaning.
    text = re.sub(r"<@[A-Z0-9]+>", " ", text)
    text = re.sub(r"<mailto:[^|>]+\|([^>]+)>", r"\1", text)
    text = re.sub(r"<([^|>]+)\|([^>]+)>", r"\2", text)
    return " ".join(text.split())


def _as_mapping(resp: Any) -> dict[str, Any]:
    if isinstance(resp, dict):
        return resp
    data = getattr(resp, "data", None)
    if isinstance(data, dict):
        return data
    if hasattr(resp, "get"):
        try:
            return dict(resp)
        except Exception:
            return {}
    return {}


def _parse_channels(raw: str | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in re.split(r"[,;\s]+", str(raw or "")):
        channel = item.strip()
        if channel and channel not in seen:
            seen.add(channel)
            out.append(channel)
    return out


def _env_flag(name: str, *, default: bool = False) -> bool:
    """Read a boolean env var with an explicit default.

    Returns ``default`` when unset/blank, ``True`` for ``1/true/yes/on`` and
    ``False`` for ``0/false/no/off`` (case-insensitive). Any other non-blank
    value falls back to ``default``. Mirrors ``slack_surface.intent._env_flag`` so the
    converse enable flag and the intent-router flag read env the same way.
    """
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(0, value)


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(0.0, value)


__all__ = [
    "DEFAULT_THREAD_CONTEXT",
    "DEFAULT_THROTTLE",
    "DEFAULT_TIMEOUT",
    "ENV_CHANNELS",
    "ENV_ENABLED",
    "ENV_ENGINE",
    "ENV_THREAD_CONTEXT",
    "ENV_THROTTLE",
    "ENV_TIMEOUT",
    "PLACEHOLDER",
    "ConverseReply",
    "ConverseStreamResult",
    "SlackConverseConfig",
    "SlackConverseOutcome",
    "SlackStreamPoster",
    "StreamingSlackClient",
    "gather_thread_context",
    "render_converse_reply",
    "run_slack_converse",
    "stream_converse_to_slack",
]
