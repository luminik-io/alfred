"""Conversational Ask-driven onboarding: set Alfred up by talking to it.

This module powers ``POST /api/onboarding/converse``. Each call runs ONE
assistant turn: Alfred asks a short setup question, then REQUESTS a structured
action (check the engines, connect GitHub, pick repos, name the team, set a
schedule, finish) that the DESKTOP CLIENT executes under the existing token
gate. The model never writes config, never writes a token, never deploys: it
only proposes the next step. Each turn returns ``{reply, action?, done}``.

Design notes:

* Turn-by-turn, one model invocation per HTTP call, routed through the same
  ``invoke_agent_engine`` dispatch (Claude / Codex / hybrid) the compose
  interrogator and the theme builder use. The model is sandboxed read-only
  (Read/Grep/Glob); the request/execute split keeps it that way.
* UNTRUSTED INPUT: the person's chat is wrapped in the same hashed sentinel
  boundary the compose interrogator uses, so a stray "run this" in a message
  cannot inject instructions or forge a side-effect.
* REUSE, not rebuild: message parsing, the untrusted-transcript boundary, the
  action allowlist + args bounds, JSON extraction, engine dispatch, and the
  soft retryable-turn semantics all come from ``compose_converse`` and mirror
  ``theme_builder``. This module only adds the onboarding-specific prompt, the
  scoped action set, a light per-action args validator, and the ``done`` flag.
* SINGLE SOURCE OF TRUTH: every action here maps to an EXISTING setup primitive
  the stepped OnboardingView already drives (the GitHub device flow, the repo
  save, the roster theme save, the starter-playbook/demo path). The client runs
  the SAME handler for both paths, so the conversational and stepped flows can
  never drift. Nothing new writes config.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import compose_converse as cc

# The engine driving the onboarding chat. Reuses the compose converse engine env
# so an operator configures one knob; ``ALFRED_ONBOARDING_ENGINE`` overrides it
# for onboarding specifically. Empty means "no live session", the degrade path
# the caller turns into a 503 so the client falls back to the stepped flow.
ENGINE_ENV = "ALFRED_ONBOARDING_ENGINE"

# The codename every onboarding turn fires under, kept distinct from the compose
# interrogator and the theme builder so its transcripts and any cost show up
# separately.
GUIDE_AGENT = "onboarding-guide"

# One assistant turn per call; a single read-capable pass is plenty. The model
# never needs the repo, but we keep the same sandbox as the interrogator.
DEFAULT_TIMEOUT = 120
DEFAULT_MAX_TURNS = 4

# The soft reply surfaced when the engine RAN but returned output we could not
# parse into a turn. A malformed one-off is a transient hiccup, not an outage, so
# we keep the chat open and ask the person to try again rather than dropping to
# the stepped flow. The engine being missing or truly unavailable stays a
# separate, terminal signal the route turns into a 503.
RETRY_REPLY = "Sorry, I lost the thread on that one. Could you say it again?"

# The prompt lives with the other engineering prompts and is loaded via
# ``load_prompt`` per the repo convention.
_PROMPT_RELATIVE = Path("prompts") / "onboarding.md"

# The bounded vocabulary of onboarding actions a turn may REQUEST. Each maps to
# an EXISTING setup primitive the stepped OnboardingView already drives; the
# client executes the request under the per-launch token gate, the model only
# names it. This is the SCOPED allowlist onboarding passes to
# ``compose_converse.parse_action`` (via its ``allowlist`` param), so a confused
# turn cannot leak a planning action (file_issue, start_runtime) into the
# onboarding surface, and the shared compose interrogator never has to list these
# setup tools in its own prompt. ``propose_theme`` / ``save_theme`` reuse the
# #418 theme builder's editor/save path.
#
# Read-only triggers (check_engine, connect_github) surface status the client
# already fetches. Side-effectful actions (set_repos, save_theme, set_schedule,
# finish_setup) run behind the SAME human/token gate as every stepped write.
ONBOARDING_ACTIONS: frozenset[str] = frozenset(
    {
        "check_engine",
        "connect_github",
        "set_repos",
        "pick_agents",
        "propose_theme",
        "save_theme",
        "set_batteries",
        "set_schedule",
        "finish_setup",
    }
)

# ``propose_theme`` / ``save_theme`` are the two onboarding actions the shared
# theme builder also owns; they must stay members of the shared allowlist too so
# their reuse path is consistent. The rest are onboarding-only. Asserted in tests.
THEME_ACTIONS: frozenset[str] = frozenset({"propose_theme", "save_theme"})


# Per-action bounds so a runaway turn cannot smuggle an oversized list.
MAX_REPOS = 50
MAX_ROLES = 40
MAX_SLUG_LEN = 80
MAX_SCHEDULE_LEN = 64
MAX_BATTERIES = 20

# The set of schedule cadences the client understands. The model may only pick a
# cadence from this set (or omit the action); anything else degrades to no
# action so a stray value never reaches the setup writer. "off" means no
# scheduled sweep; the others map to the client's cadence control.
SCHEDULE_CADENCES: frozenset[str] = frozenset({"off", "hourly", "daily", "weekly"})


@dataclass(frozen=True)
class OnboardingTurn:
    """The result of one onboarding turn: a reply, an optional action, and done.

    ``action`` is a validated ``compose_converse.ConverseAction`` REQUEST for one
    onboarding tool, or ``None`` for a plain question turn. ``done`` is True only
    on the terminal ``finish_setup`` turn, so the client knows the guided flow is
    complete and can route to the board. This object carries NO authority: the
    client executes the action under the token gate.
    """

    reply: str
    action: cc.ConverseAction | None = None
    done: bool = False


def retry_turn() -> OnboardingTurn:
    """A soft, retryable turn for a transient malformed-output hiccup.

    Returned when the engine RAN but its output did not parse into a usable turn.
    Carries a plain reply asking the person to try again and NO action, so the
    chat stays open and the person can just resend, rather than the route
    surfacing a terminal engine-unavailable signal for a one-off parse miss.
    """
    return OnboardingTurn(reply=RETRY_REPLY)


def engine_from_env() -> str:
    """Resolve the engine driving onboarding, or "" when none is set."""
    return (os.environ.get(ENGINE_ENV) or cc.converse_engine_from_env()).strip()


def prompt_relative_path() -> Path:
    return _PROMPT_RELATIVE


def render_system_prompt(
    *,
    prompt_path: Path,
    loader: Callable[..., str],
) -> str:
    """Render the onboarding system prompt.

    The onboarding contract (the fixed step order and the actions the model may
    request) is static text in the prompt, so there is nothing to seed here.
    Rendered via ``load_prompt`` (one ``string.Template`` pass) so hostile
    transcript text, appended later, is never re-substituted.
    """
    return loader(prompt_path)


def build_prompt(*, system_prompt: str, messages: Iterable[cc.ConverseMessage]) -> str:
    """Assemble the full single-turn prompt: system + untrusted transcript.

    Reuses ``compose_converse.format_untrusted_transcript`` so the person's chat
    is wrapped in the same hashed injection boundary. Onboarding carries no
    structured draft: its only state is the transcript, which the model re-reads
    each turn to decide the next step.
    """
    transcript = cc.format_untrusted_transcript(messages)
    return f"""{system_prompt}

## Conversation so far

{transcript}

Now produce your single JSON turn following the output contract exactly.
"""


def _clean_slug(value: Any) -> str | None:
    """Normalize a repo/role token to a bounded, non-empty string, or None."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split()).strip()
    if not text or len(text) > MAX_SLUG_LEN:
        return None
    return text


def _validate_set_repos(args: dict[str, Any]) -> dict[str, Any] | None:
    """Validate a ``set_repos`` action's args into a bounded, deduped repo list.

    Reuses ``compose_converse.normalize_repos`` (the same ``owner/repo`` slug
    validator the compose flow uses) so a proposed repo the client forwards to
    ``POST /api/setup/repos`` matches what the setup writer accepts. Returns
    ``None`` (drop the action, keep the reply) when no valid repo survives:
    naming at least one repo is the point of the step.
    """
    raw = args.get("repos")
    if raw is None:
        raw = args.get("repo")
    repos = cc.normalize_repos(raw)[:MAX_REPOS]
    if not repos:
        return None
    return {"repos": repos}


def _validate_pick_agents(args: dict[str, Any]) -> dict[str, Any] | None:
    """Validate a ``pick_agents`` action's args into a bounded role-slug list.

    The client maps these to the roster roles it enables (a display concern, not
    a config write on its own): the fleet is fixed, this only records which roles
    the person wants surfaced. Unknown-shaped or empty input drops the action.
    """
    raw = args.get("roles")
    if raw is None:
        raw = args.get("agents")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return None
    roles: list[str] = []
    seen: set[str] = set()
    for value in raw:
        slug = _clean_slug(value)
        if slug is None:
            continue
        key = slug.lower()
        if key in seen:
            continue
        seen.add(key)
        roles.append(slug)
        if len(roles) >= MAX_ROLES:
            break
    if not roles:
        return None
    return {"roles": roles}


def _validate_set_batteries(args: dict[str, Any]) -> dict[str, Any] | None:
    """Validate a ``set_batteries`` action into a bounded list of real opt-in ids.

    Only ids of real OPT-IN batteries (from the shared ``batteries`` manifest)
    survive: unknown ids and always-on built-ins are dropped, so the model can
    never name something the client cannot enable. Two mutually-exclusive primary
    memory stores (Redis and pgvector) are a conflict, so the action degrades to
    ``None`` (the reply stands and asks the person to pick one) rather than
    forwarding a selection that would collide. An empty result drops the action.
    """
    import batteries

    raw = args.get("batteries")
    if raw is None:
        raw = args.get("battery")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return None
    valid_opt_ins = {b.id for b in batteries.opt_in_batteries()}
    ids: list[str] = []
    seen: set[str] = set()
    for value in raw:
        slug = _clean_slug(value)
        if slug is None:
            continue
        key = slug.lower()
        if key in seen or key not in valid_opt_ins:
            continue
        seen.add(key)
        ids.append(key)
        if len(ids) >= MAX_BATTERIES:
            break
    if not ids:
        return None
    if batteries.selection_conflict(ids):
        return None
    return {"batteries": ids}


def _validate_set_schedule(args: dict[str, Any]) -> dict[str, Any] | None:
    """Validate a ``set_schedule`` action's args into a known cadence.

    Only a cadence from ``SCHEDULE_CADENCES`` is accepted; anything else drops
    the action so a stray value never reaches the client's cadence control. An
    optional free-text ``cron`` hint is passed through bounded but advisory: the
    client owns how a cadence maps to a concrete schedule.
    """
    cadence = args.get("cadence")
    if not isinstance(cadence, str):
        return None
    cadence = cadence.strip().lower()
    if cadence not in SCHEDULE_CADENCES:
        return None
    out: dict[str, Any] = {"cadence": cadence}
    cron = args.get("cron")
    if isinstance(cron, str):
        cron = " ".join(cron.split()).strip()
        if cron and len(cron) <= MAX_SCHEDULE_LEN:
            out["cron"] = cron
    return out


def _validate_theme_action(action: cc.ConverseAction) -> cc.ConverseAction | None:
    """Validate a shared ``propose_theme`` / ``save_theme`` action.

    Delegates the role-slug + display-name shaping to ``theme_builder`` (the
    #418 owner of the roster contract) so onboarding never re-implements the
    completeness gate. A proposal that does not name every required core role
    degrades to ``None`` (in-progress), exactly as it does in the theme builder.
    ``save_theme`` is passed through with the same validated maps; the client
    saves it via the existing ``POST /api/roster-theme`` under the token gate.
    """
    import theme_builder as tb

    valid = tb.valid_codenames()
    required = tb.required_codenames()
    proposal = tb.parse_proposal(action.args, valid=valid, required=required)
    if proposal is None or proposal.is_empty():
        return None
    return cc.ConverseAction(
        tool=action.tool,
        args={
            "custom_names": dict(proposal.custom_names),
            "custom_roles": dict(proposal.custom_roles),
        },
    )


# Per-action validators for the args-carrying onboarding actions. An action not
# listed here (check_engine, connect_github, finish_setup) carries no meaningful
# args and is accepted with an empty args dict. propose_theme / save_theme are
# handled specially via ``_validate_theme_action`` since they reuse #418.
_ARG_VALIDATORS: dict[str, Callable[[dict[str, Any]], dict[str, Any] | None]] = {
    "set_repos": _validate_set_repos,
    "pick_agents": _validate_pick_agents,
    "set_batteries": _validate_set_batteries,
    "set_schedule": _validate_set_schedule,
}

# Actions that carry no args: the client already knows how to run them (fetch
# status, kick the device flow, generate). An empty args dict is fine.
_ARGLESS_ACTIONS: frozenset[str] = frozenset({"check_engine", "connect_github", "finish_setup"})


def _action_from_obj(raw_action: Any) -> cc.ConverseAction | None:
    """Extract and validate a scoped onboarding action from a turn's ``action``.

    Reuses ``compose_converse.parse_action`` (passing the scoped
    ``ONBOARDING_ACTIONS`` allowlist) for the args bounds + non-finite rejection,
    then validates the args per tool. Any tool outside ``ONBOARDING_ACTIONS``, or
    an action whose args fail validation, yields ``None`` so the turn's reply
    stands as a plain conversational reply. NEVER raises.
    """
    action = cc.parse_action(raw_action, allowlist=ONBOARDING_ACTIONS)
    if action is None:
        return None
    if action.tool in THEME_ACTIONS:
        return _validate_theme_action(action)
    if action.tool in _ARGLESS_ACTIONS:
        # Accept the action but drop any smuggled args: these steps take none.
        return cc.ConverseAction(tool=action.tool, args={})
    validator = _ARG_VALIDATORS.get(action.tool)
    if validator is None:
        # An onboarding tool with no validator and not arg-less is a coding
        # oversight; be conservative and drop it rather than forward raw args.
        return None
    validated = validator(action.args)
    if validated is None:
        return None
    return cc.ConverseAction(tool=action.tool, args=validated)


def parse_turn(raw_text: str) -> OnboardingTurn | None:
    """Parse the model's JSON output into an ``OnboardingTurn``.

    Returns ``None`` when the model did not return usable JSON so the caller can
    surface an honest error rather than a fabricated turn. A turn always carries a
    ``reply``; an ``action`` is attached only when it validates against the scoped
    onboarding allowlist. A malformed/unknown/out-of-scope action degrades to no
    action (the reply still stands), never raises. ``done`` is honored only on a
    ``finish_setup`` action so the terminal signal cannot be forged by a bare
    ``"done": true`` on an ordinary turn.
    """
    obj = cc._extract_json_object(raw_text)
    if obj is None:
        return None
    reply = str(obj.get("reply") or "").strip()
    action = _action_from_obj(obj.get("action"))
    if not reply and action is None:
        # A turn with no reply and no action is useless; treat as a parse miss.
        return None
    # ``done`` is a client routing signal, not a config effect. Anchor it to the
    # terminal action so a stray "done": true on a mid-flow turn never short
    # circuits the guided setup.
    done = action is not None and action.tool == "finish_setup"
    return OnboardingTurn(reply=reply, action=action, done=done)


def run_turn(
    *,
    system_prompt: str,
    messages: Iterable[cc.ConverseMessage],
    engine: str,
    workdir: Path,
    timeout: int = DEFAULT_TIMEOUT,
    invoke: Callable[..., Any] | None = None,
    firing_id: str | None = None,
) -> OnboardingTurn | None:
    """Run one onboarding turn through the agent-engine dispatch.

    ``invoke`` defaults to ``agent_runner.invoke_agent_engine`` but is injected in
    tests so no live model call is made.

    Distinguishes two failure modes so the caller can react differently:

    * The engine could not run at all (unimportable dispatch, raised, returned
      nothing, or reported failure): returns ``None``. This is the terminal
      engine-unavailable signal the route turns into a 503.
    * The engine RAN and produced text, but that text did not parse into a usable
      turn: returns a soft ``retry_turn()`` instead of ``None``. A malformed
      one-off is transient, so the caller keeps the chat open and lets the person
      resend rather than surfacing a terminal outage.
    """
    engine_invoke = invoke
    if engine_invoke is None:
        try:
            from agent_runner import invoke_agent_engine

            engine_invoke = invoke_agent_engine
        except Exception:
            return None
    if not firing_id:
        firing_id = cc.converse_firing_id()

    prompt = build_prompt(system_prompt=system_prompt, messages=messages)

    try:
        result, _engine_used = engine_invoke(
            prompt,
            engine=engine,
            agent=GUIDE_AGENT,
            firing_id=firing_id,
            workdir=workdir,
            claude_allowed_tools="Read,Grep,Glob",
            timeout=timeout,
            claude_max_turns=DEFAULT_MAX_TURNS,
            codex_timeout=timeout,
            hybrid_fallback_on_provider_failure=True,
        )
    except Exception:
        return None
    if result is None:
        return None
    if not getattr(result, "success", False) or not getattr(result, "result_text", ""):
        return None
    # The engine ran and returned text. If that text does not parse, it is a
    # transient hiccup, not an outage: degrade to a soft retryable turn (chat
    # stays open) rather than the terminal ``None``.
    turn = parse_turn(result.result_text)
    if turn is None:
        return retry_turn()
    return turn


def action_payload(action: cc.ConverseAction | None) -> dict[str, Any] | None:
    """Serialize a validated action as the ``{tool, args}`` request the client runs.

    ``None`` (a plain question turn with no action) serializes to ``null``.
    """
    if action is None:
        return None
    return {"tool": action.tool, "args": dict(action.args)}


def turn_payload(turn: OnboardingTurn) -> dict[str, Any]:
    """The JSON body the ``/api/onboarding/converse`` route returns."""
    return {
        "reply": turn.reply,
        "action": action_payload(turn.action),
        "done": turn.done,
    }


__all__ = [
    "DEFAULT_TIMEOUT",
    "ENGINE_ENV",
    "GUIDE_AGENT",
    "ONBOARDING_ACTIONS",
    "RETRY_REPLY",
    "SCHEDULE_CADENCES",
    "THEME_ACTIONS",
    "OnboardingTurn",
    "action_payload",
    "build_prompt",
    "engine_from_env",
    "parse_turn",
    "prompt_relative_path",
    "render_system_prompt",
    "retry_turn",
    "run_turn",
    "turn_payload",
]
