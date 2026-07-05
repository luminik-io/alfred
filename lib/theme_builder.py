"""Conversational roster theme builder: name your agent team by talking to Alfred.

This module powers ``POST /api/theme-builder/converse``. Each call runs ONE
assistant turn: the model asks a short "what vibe?" question, then proposes a
full role-slug -> display-name mapping for the agent roster as a structured
``propose_theme`` action. The person tweaks and confirms in the existing custom
theme editor, and the CLIENT saves it via the existing ``POST /api/roster-theme``
(``theme: "custom"``). Nothing here saves: the model only PROPOSES.

Design notes:

* Turn-by-turn, one model invocation per HTTP call, routed through the same
  ``invoke_agent_engine`` dispatch (Claude / Codex / hybrid) the compose
  interrogator uses. The model is sandboxed read-only (Read/Grep/Glob) even
  though it never needs the repo; the request/execute split keeps it that way.
* UNTRUSTED INPUT: the person's chat is wrapped in the same hashed sentinel
  boundary the compose interrogator uses, so a "vibe" cannot inject
  instructions or forge a save.
* The roster CONTRACT (the exact role-slugs, their role labels, and the shipped
  Batman display names) is read server-side from ``roster_manifest.json`` via
  ``roster_theme_store.roster_contract_agents`` and seeded into the prompt, so
  the model names the agents the custom store can actually persist and never
  invents a codename.
* REUSE, not rebuild: message parsing, the untrusted-transcript boundary, action
  parsing/validation, JSON extraction, and the firing-id shape all come from
  ``compose_converse``. This module only adds the theme-specific prompt assembly,
  a draft-free turn type, and a light ``propose_theme`` args validator.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import compose_converse as cc
from roster_theme_store import RosterContractAgent, roster_contract_agents

# The engine driving the theme builder. Reuses the compose converse engine env so
# an operator configures one knob; ``ALFRED_THEME_BUILDER_ENGINE`` overrides it
# for the theme builder specifically. Empty means "no live session", the degrade
# path the caller turns into a 503 so the client falls back to the manual editor.
ENGINE_ENV = "ALFRED_THEME_BUILDER_ENGINE"

# The codename every theme-builder turn fires under, kept distinct from the
# compose interrogator so its transcripts and any cost show up separately.
BUILDER_AGENT = "theme-builder"

# One assistant turn per call; a single read-capable pass is plenty. The model
# never needs the repo, but we keep the same sandbox as the interrogator.
DEFAULT_TIMEOUT = 120
DEFAULT_MAX_TURNS = 4

# The prompt lives with the other engineering prompts and is loaded via
# ``load_prompt`` per the repo convention.
_PROMPT_RELATIVE = Path("prompts") / "theme-builder.md"

# The one action tool this flow proposes. The shared allowlist already includes
# ``propose_theme``; we constrain to it here so a stray onboarding action from a
# confused turn cannot leak into the theme surface.
PROPOSE_THEME_TOOL = "propose_theme"

# Bound the proposed maps so a runaway turn cannot smuggle an oversized blob.
# These mirror the roster-theme store's own limits so a proposal the client
# forwards to ``POST /api/roster-theme`` cannot be rejected purely on size.
MAX_PROPOSED_ENTRIES = 128
MAX_LABEL_LEN = 64


@dataclass(frozen=True)
class ThemeProposal:
    """A validated ``propose_theme`` payload: display names + optional role labels.

    ``custom_names`` maps role-slug (codename) -> display name; ``custom_roles``
    is an optional map of role-slug -> role label. Both are pre-shaped for the
    existing ``POST /api/roster-theme`` body, which re-validates every entry on
    write (this is a convenience/preview layer, not the trust boundary).
    """

    custom_names: dict[str, str]
    custom_roles: dict[str, str]

    def is_empty(self) -> bool:
        return not self.custom_names and not self.custom_roles


@dataclass(frozen=True)
class ThemeBuilderTurn:
    """The result of one theme-builder turn: a reply and an optional proposal."""

    reply: str
    proposal: ThemeProposal | None = None


def engine_from_env() -> str:
    """Resolve the engine driving the theme builder, or "" when none is set."""
    return (os.environ.get(ENGINE_ENV) or cc.converse_engine_from_env()).strip()


def prompt_relative_path() -> Path:
    return _PROMPT_RELATIVE


def render_system_prompt(
    *,
    prompt_path: Path,
    agents: Iterable[RosterContractAgent] | None = None,
    loader: Callable[..., str],
) -> str:
    """Render the theme-builder system prompt, seeding the roster contract.

    The ``${ROSTER_CONTRACT}`` placeholder is filled with a compact table of the
    role-slugs, their role labels, and the shipped Batman names so the model
    proposes names for the agents that actually exist. Rendered via
    ``load_prompt`` (one ``string.Template`` pass) so hostile transcript text,
    appended later, is never re-substituted.
    """
    roster = list(agents) if agents is not None else list(roster_contract_agents())
    return loader(
        prompt_path,
        extra_vars={"ROSTER_CONTRACT": build_roster_contract(roster)},
    )


def build_roster_contract(agents: Iterable[RosterContractAgent]) -> str:
    """Render the roster contract table for the prompt.

    One line per agent: the role-slug the proposal must key on, its plain role
    label, and the current Batman display name. Engineering roles are listed
    first so the model covers them before the ops agents.
    """
    lines = [
        "| role-slug | role | current name |",
        "| --- | --- | --- |",
    ]
    for agent in agents:
        lines.append(f"| `{agent.codename}` | {agent.role_label} | {agent.base_name} |")
    return "\n".join(lines)


def valid_codenames(agents: Iterable[RosterContractAgent] | None = None) -> frozenset[str]:
    """The set of role-slugs a proposal may name."""
    roster = list(agents) if agents is not None else list(roster_contract_agents())
    return frozenset(agent.codename for agent in roster)


def build_prompt(*, system_prompt: str, messages: Iterable[cc.ConverseMessage]) -> str:
    """Assemble the full single-turn prompt: system + untrusted transcript.

    Reuses ``compose_converse.format_untrusted_transcript`` so the person's chat
    is wrapped in the same hashed injection boundary. No structured draft is
    carried: the theme builder's only state is the proposal, which the model
    re-emits each turn as the person tweaks the vibe.
    """
    transcript = cc.format_untrusted_transcript(messages)
    return f"""{system_prompt}

## Conversation so far

{transcript}

Now produce your single JSON turn following the output contract exactly.
"""


def parse_proposal(
    raw: Any,
    *,
    valid: frozenset[str],
) -> ThemeProposal | None:
    """Validate a ``propose_theme`` action's args into a ``ThemeProposal``.

    Defensive by construction, mirroring ``compose_converse.parse_action``: it
    NEVER raises. Returns ``None`` (drop the proposal, keep the turn's reply) when
    the args carry no usable names. Unknown role-slugs, blank/over-long labels,
    and non-string values are dropped entry-by-entry rather than failing the
    whole proposal, so a mostly-good map still previews. Entry count is capped.

    ``args`` is expected to be ``{custom_names: {slug: name}, custom_roles?:
    {slug: label}}``. A bare ``names``/``roles`` alias is also accepted so a
    slightly-off model output still lands.
    """
    if not isinstance(raw, dict):
        return None
    names = _clean_slug_map(
        raw.get("custom_names") if raw.get("custom_names") is not None else raw.get("names"),
        valid=valid,
    )
    roles = _clean_slug_map(
        raw.get("custom_roles") if raw.get("custom_roles") is not None else raw.get("roles"),
        valid=valid,
    )
    if not names and not roles:
        return None
    return ThemeProposal(custom_names=names, custom_roles=roles)


def _clean_slug_map(value: Any, *, valid: frozenset[str]) -> dict[str, str]:
    """Keep only entries whose key is a known role-slug and value a clean label."""
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for key, raw in value.items():
        slug = str(key or "").strip().lower()
        # Slack/runtime codenames sometimes arrive dotted; key on the last segment.
        slug = (slug.split(".")[-1] or "").strip()
        if slug not in valid:
            continue
        label = _clean_label(raw)
        if label is None:
            continue
        out[slug] = label
        if len(out) >= MAX_PROPOSED_ENTRIES:
            break
    return out


def _clean_label(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split()).strip()
    if not text:
        return None
    return text[:MAX_LABEL_LEN]


def parse_turn(raw_text: str, *, valid: frozenset[str]) -> ThemeBuilderTurn | None:
    """Parse the model's JSON output into a ``ThemeBuilderTurn``.

    Returns ``None`` when the model did not return usable JSON so the caller can
    surface an honest error rather than a fabricated turn. A turn always carries a
    ``reply``; a ``propose_theme`` action, when present and valid, is attached as
    a ``ThemeProposal``. A malformed/unknown/non-propose action degrades to no
    proposal (the reply still stands), never raises.
    """
    obj = cc._extract_json_object(raw_text)
    if obj is None:
        return None
    reply = str(obj.get("reply") or "").strip()
    proposal = _proposal_from_obj(obj.get("action"), valid=valid)
    if not reply and proposal is None:
        # A turn with no reply and no proposal is useless; treat as a parse miss.
        return None
    return ThemeBuilderTurn(reply=reply, proposal=proposal)


def _proposal_from_obj(raw_action: Any, *, valid: frozenset[str]) -> ThemeProposal | None:
    """Extract a proposal from the turn's optional ``action`` block.

    Reuses ``compose_converse.parse_action`` for the allowlist + args bounds, then
    keeps only a ``propose_theme`` action and validates its args into a proposal.
    Any other allowlisted tool (or a malformed block) yields ``None``.
    """
    action = cc.parse_action(raw_action)
    if action is None or action.tool != PROPOSE_THEME_TOOL:
        return None
    return parse_proposal(action.args, valid=valid)


def run_turn(
    *,
    system_prompt: str,
    messages: Iterable[cc.ConverseMessage],
    engine: str,
    workdir: Path,
    valid_slugs: frozenset[str],
    timeout: int = DEFAULT_TIMEOUT,
    invoke: Callable[..., Any] | None = None,
    firing_id: str | None = None,
) -> ThemeBuilderTurn | None:
    """Run one theme-builder turn through the agent-engine dispatch.

    ``invoke`` defaults to ``agent_runner.invoke_agent_engine`` but is injected in
    tests so no live model call is made. Returns ``None`` when the engine failed
    or returned unparseable output, so the caller surfaces an honest error rather
    than a fabricated turn.
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
            agent=BUILDER_AGENT,
            firing_id=firing_id,
            workdir=workdir,
            claude_allowed_tools="Read,Grep,Glob",
            timeout=timeout,
            claude_max_turns=DEFAULT_MAX_TURNS,
            codex_timeout=timeout,
        )
    except Exception:
        return None
    if result is None:
        return None
    if not getattr(result, "success", False) or not getattr(result, "result_text", ""):
        return None
    return parse_turn(result.result_text, valid=valid_slugs)


def proposal_payload(proposal: ThemeProposal | None) -> dict[str, Any] | None:
    """Serialize a proposal as the ``propose_theme`` action the client executes.

    Shaped as ``{tool, args:{custom_names, custom_roles}}`` so the desktop can
    forward ``args`` straight to ``POST /api/roster-theme`` with ``theme:
    "custom"`` after the person confirms. ``None`` (a plain question turn with no
    proposal) serializes to ``null``.
    """
    if proposal is None or proposal.is_empty():
        return None
    return {
        "tool": PROPOSE_THEME_TOOL,
        "args": {
            "custom_names": dict(proposal.custom_names),
            "custom_roles": dict(proposal.custom_roles),
        },
    }


def turn_payload(turn: ThemeBuilderTurn) -> dict[str, Any]:
    """The JSON body the ``/api/theme-builder/converse`` route returns."""
    return {
        "reply": turn.reply,
        "action": proposal_payload(turn.proposal),
    }


__all__ = [
    "BUILDER_AGENT",
    "ENGINE_ENV",
    "PROPOSE_THEME_TOOL",
    "ThemeBuilderTurn",
    "ThemeProposal",
    "build_prompt",
    "build_roster_contract",
    "engine_from_env",
    "parse_proposal",
    "parse_turn",
    "prompt_relative_path",
    "proposal_payload",
    "render_system_prompt",
    "run_turn",
    "turn_payload",
    "valid_codenames",
]
