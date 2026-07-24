"""Live operational grounding for Alfred's conversational surfaces.

Alfred's conversation (Slack mentions / DMs and the desktop Ask) should answer
questions a colleague would answer: "what's the fleet doing?", "why did lucius
fail on #1038?", "what did you ship today?". The spec-interrogator prompt already
grounds a turn in each repo's ``CLAUDE.md`` and a code map, but that is the wrong
grounding for a status question. This module assembles a bounded, honest snapshot
of *live fleet state* so a conversation turn can answer those questions from real
data instead of guessing.

The snapshot is read through the SAME read-only :class:`FleetReader` the desktop
client uses (``lib/server/reader.py``), so the conversation reflects exactly what
the Fleet view shows. Nothing here mutates state, launches an agent, or touches
the network; it only reads and formats.

Design contract (mirrors ``compose_converse.build_repo_grounding``):

- **Reader-injected and pure.** :func:`build_operational_grounding` takes a
  ``FleetReader`` and returns text. Tests pass a stub reader; no filesystem or
  runtime is required.
- **Bounded.** Agent rows and firing rows are capped so a large fleet or a long
  history never blows up the prompt. Summaries are trimmed to one line.
- **Degrades to empty.** Any reader failure yields an empty string, so a missing
  or briefly inconsistent runtime never breaks the conversation; the turn simply
  loses its live grounding and still answers from the repo grounding.
- **No em-dashes** in any operator-facing string (fleet rule).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Protocol

from envflags import FALSY_VALUES, truthy

# Bounds. All overridable by the caller, but the defaults keep the grounding
# block small enough to sit inside a single-turn prompt without crowding out the
# repo grounding or the conversation transcript.
DEFAULT_AGENT_LIMIT = 24
DEFAULT_FIRINGS_LIMIT = 12
# One-line trim for any free-text summary pulled from a firing or an agent row.
MAX_SUMMARY_CHARS = 160

ENV_ENABLED = "ALFRED_CONVERSE_OPERATIONAL_GROUNDING"


class OperationalReader(Protocol):
    """The read-only fleet subset the grounding needs.

    ``lib/server/reader.py``'s ``FilesystemReader`` satisfies this natively; a
    test passes a stub with the same two methods. Both are best-effort: a raising
    reader degrades to an empty grounding rather than breaking the turn.
    """

    def list_agents(self) -> list[Any]: ...

    def list_recent_firings(self, *, limit: int = 50, codename: str | None = None) -> list[Any]: ...


def operational_grounding_enabled() -> bool:
    """True unless the operator explicitly turns operational grounding off.

    Conversation is Alfred's default surface and a colleague answer needs live
    state, so this is ON by default. ``ALFRED_CONVERSE_OPERATIONAL_GROUNDING=0``
    (also ``false`` / ``off``) disables it, e.g. to shave prompt size or when the
    runtime state is known-stale.
    """
    raw = (os.environ.get(ENV_ENABLED) or "").strip().lower()
    if not raw:
        return True
    if truthy(raw):
        return True
    if raw in FALSY_VALUES:
        return False
    return False


def build_operational_grounding(
    reader: OperationalReader | None,
    *,
    agent_limit: int = DEFAULT_AGENT_LIMIT,
    firings_limit: int = DEFAULT_FIRINGS_LIMIT,
) -> str:
    """Assemble a bounded snapshot of live fleet state as prompt grounding.

    Returns a Markdown block describing each agent's at-a-glance state and the
    most recent firings (with any classified error cause), or an empty string
    when the reader is absent, disabled, or raises. The text is advisory context
    for a conversation turn, never an instruction the model must follow.
    """
    if reader is None or not operational_grounding_enabled():
        return ""
    try:
        agents = list(reader.list_agents())
    except Exception:
        agents = []
    try:
        firings = list(reader.list_recent_firings(limit=max(1, firings_limit)))
    except Exception:
        firings = []
    if not agents and not firings:
        return ""

    sections: list[str] = []
    agent_block = _render_agents(agents, limit=agent_limit)
    if agent_block:
        sections.append(agent_block)
    firing_block = _render_firings(firings, limit=firings_limit)
    if firing_block:
        sections.append(firing_block)
    if not sections:
        return ""
    return "\n\n".join(sections)


def build_engine_grounding(
    engines: Iterable[Mapping[str, Any]],
    *,
    conversation_engine: str,
) -> str:
    """Render the live CLI inventory without guessing model names.

    Setup owns engine detection, so conversation surfaces should use its result
    when answering capability questions. The selected conversation route is a
    separate fact: hybrid means Claude first with Codex fallback, while an
    ready CLI may still be available to scheduled roles even when this turn
    uses the other engine.
    """
    ready: list[str] = []
    seen: set[str] = set()
    for engine in engines:
        if not engine.get("ready"):
            continue
        name = str(engine.get("name") or "").strip().lower()
        if not name or name in seen:
            continue
        seen.add(name)
        ready.append(str(engine.get("display_name") or name))
    if not ready:
        return ""

    route = (conversation_engine or "").strip().lower()
    route_label = {
        "hybrid": "hybrid (Claude Code first, Codex fallback)",
        "claude": "Claude Code",
        "codex": "Codex",
    }.get(route, route or "not reported")
    available = ", ".join(ready)
    return (
        "### Coding engines (live)\n\n"
        f"- Compatible, signed in, and available to Alfred: {available}.\n"
        f"- This conversation route: {route_label}.\n"
        "- Scheduled roles may choose any ready engine independently. "
        "Do not invent a model name or version that is not listed here."
    )


def _render_agents(agents: list[Any], *, limit: int) -> str:
    rows: list[str] = []
    for agent in agents[: max(0, limit)]:
        codename = _attr_str(agent, "codename")
        if not codename:
            continue
        status = _attr_str(agent, "status") or "unknown"
        paused = bool(getattr(agent, "paused", False))
        state = "paused" if paused else status
        name = _attr_str(agent, "display_name") or codename
        role = _attr_str(agent, "role_title")
        today = _attr_int(agent, "firings_today")
        summary = _trim(_attr_str(agent, "last_summary"))
        label = f"{name}" if name == codename else f"{name} ({codename})"
        if role:
            label = f"{label}, {role}"
        parts = [f"- {label}: {state}"]
        if today:
            parts.append(f"{today} run{'s' if today != 1 else ''} today")
        line = ", ".join(parts)
        if summary:
            line = f"{line}. Last: {summary}"
        rows.append(line)
    if not rows:
        return ""
    header = "### Fleet status (live)"
    note = (
        "Each agent's current state, read from the runtime. `paused` means an "
        "operator paused it; `error` means its last run failed."
    )
    return f"{header}\n\n{note}\n\n" + "\n".join(rows)


def _render_firings(firings: list[Any], *, limit: int) -> str:
    rows: list[str] = []
    for firing in firings[: max(0, limit)]:
        firing_id = _attr_str(firing, "firing_id")
        codename = _attr_str(firing, "codename") or "unknown"
        status = _attr_str(firing, "status") or "unknown"
        when = _attr_str(firing, "ended_at") or _attr_str(firing, "started_at")
        summary = _trim(_attr_str(firing, "summary"))
        error = _firing_error(firing)
        head = f"- {codename} [{status}]"
        if firing_id:
            head = f"{head} {firing_id}"
        if when:
            head = f"{head} at {when}"
        line = head
        detail = error or summary
        if detail:
            line = f"{line}: {detail}"
        rows.append(line)
    if not rows:
        return ""
    header = "### Recent firings (most recent first)"
    note = (
        "The fleet's recent runs. Use these to answer why a run failed, what an "
        "agent last did, or what shipped. If a firing is not listed here, say you "
        "do not have it rather than guessing."
    )
    return f"{header}\n\n{note}\n\n" + "\n".join(rows)


def _firing_error(firing: Any) -> str:
    """Pull a classified error cause off a firing's timeline, if present."""
    timeline = getattr(firing, "timeline", None)
    if timeline is None:
        return ""
    cause = _attr_str(timeline, "error")
    if cause:
        return _trim(cause)
    headline = _attr_str(timeline, "headline")
    severity = _attr_str(timeline, "severity")
    if severity == "error" and headline:
        return _trim(headline)
    return ""


def _attr_str(obj: Any, name: str) -> str:
    value = getattr(obj, name, None)
    if value is None:
        return ""
    return str(value).strip()


def _attr_int(obj: Any, name: str) -> int:
    value = getattr(obj, name, 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _trim(text: str) -> str:
    text = " ".join((text or "").split())
    if len(text) <= MAX_SUMMARY_CHARS:
        return text
    return text[: MAX_SUMMARY_CHARS - 1].rstrip() + "…"


def default_operational_reader_factory() -> Callable[[], OperationalReader | None]:
    """Return a factory that builds the runtime ``FilesystemReader`` lazily.

    Deferred import keeps this module light: importing ``converse_grounding``
    never drags in the server reader until a grounding is actually requested.
    Returns ``None`` on any import/construction failure so the caller degrades to
    no operational grounding.
    """

    def _factory() -> OperationalReader | None:
        try:
            from server.reader import FilesystemReader

            return FilesystemReader()
        except Exception:
            return None

    return _factory


__all__ = [
    "DEFAULT_AGENT_LIMIT",
    "DEFAULT_FIRINGS_LIMIT",
    "ENV_ENABLED",
    "OperationalReader",
    "build_engine_grounding",
    "build_operational_grounding",
    "default_operational_reader_factory",
    "operational_grounding_enabled",
]
