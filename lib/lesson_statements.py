"""Plain-language one-liners for lesson candidates.

A memory candidate harvested from a failure pattern carries machine-shaped
fields: an ``agent`` column, a ``topic`` key like
``failure-pattern:bane||llm-error_rate_limit|codex-fallback`` (agent, repo,
subtype, engine joined by ``|``), and an ``evidence`` array whose first entry
holds the ``count``. The native client's Lessons surface must read as a plain
sentence a non-developer understands, not a raw key.

``lesson_statement`` builds that sentence from the structured fields:

    "Bane keeps hitting rate limits on codex-fallback (seen 4 times)."

It is pure (no I/O) and defensive: any missing field is simply dropped from the
sentence rather than rendered as a literal ``unknown``. ``_candidate_to_api``
calls it so every candidate row carries a ``statement`` for the client.
"""

from __future__ import annotations

import json
from typing import Any

_PATTERN_PREFIX = "failure-pattern:"

# Human phrasings for the common failure subtypes. The match is on a normalized
# subtype (lowercased, ``_``/``-`` collapsed to spaces); the first substring hit
# wins, so ``llm-error_rate_limit`` reads as "rate limits".
_SUBTYPE_PHRASES: tuple[tuple[str, str], ...] = (
    ("rate limit", "rate limits"),
    ("context overflow", "context-window overflows"),
    ("context window", "context-window overflows"),
    ("timeout", "timeouts"),
    ("timed out", "timeouts"),
    ("auth", "authentication errors"),
    ("permission", "permission errors"),
    ("network", "network errors"),
    ("llm error", "model errors"),
    ("merge conflict", "merge conflicts"),
    ("test failure", "test failures"),
    ("lint", "lint failures"),
)


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", " ").replace("-", " ")


def _placeholder(value: Any) -> bool:
    """True for empty or sentinel values that carry no real information."""
    text = str(value or "").strip().lower()
    return text in {"", "-", "unknown", "none", "null", "global", "operator"}


def _pretty_agent(agent: Any) -> str:
    text = str(agent or "").strip()
    if not text or _placeholder(text):
        return "An agent"
    return text[0].upper() + text[1:]


def _phrase_subtype(subtype: Any) -> str:
    normalized = _normalize(subtype)
    if not normalized:
        return "repeated failures"
    for needle, phrase in _SUBTYPE_PHRASES:
        if needle in normalized:
            return phrase
    # Fall back to the cleaned subtype itself ("schema drift").
    return f"{normalized} failures"


def parse_pattern_key(topic: Any) -> dict[str, str]:
    """Split a ``failure-pattern:agent|repo|subtype|engine`` topic key.

    Returns a dict with ``agent``, ``repo``, ``subtype``, ``engine`` keys
    (empty strings for missing positions). A topic that is not a failure
    pattern key yields all-empty fields.
    """
    text = str(topic or "").strip()
    if not text.startswith(_PATTERN_PREFIX):
        return {"agent": "", "repo": "", "subtype": "", "engine": ""}
    body = text[len(_PATTERN_PREFIX) :]
    parts = body.split("|")
    parts += [""] * (4 - len(parts))
    return {
        "agent": parts[0].strip(),
        "repo": parts[1].strip(),
        "subtype": parts[2].strip(),
        "engine": parts[3].strip(),
    }


def _count_from_evidence(evidence: Any) -> int | None:
    """Pull the failure ``count`` from the candidate's evidence payload.

    Evidence is a JSON array (or its already-parsed list); the first
    ``failure_pattern`` entry carries the count. Tolerates a JSON string, a
    list, or a single dict; returns None when no usable count is present.
    """
    items: Any = evidence
    if isinstance(evidence, str):
        try:
            items = json.loads(evidence)
        except (ValueError, TypeError):
            return None
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        raw = item.get("count")
        if raw is None:
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def lesson_statement(
    *,
    agent: Any = None,
    topic: Any = None,
    subtype: Any = None,
    engine: Any = None,
    count: Any = None,
    evidence: Any = None,
    body: Any = None,
) -> str:
    """Build a plain one-line statement for a lesson candidate.

    Structured fields win when supplied directly; otherwise they are parsed
    from the ``topic`` failure-pattern key and the ``evidence`` payload. When
    no structured signal is available, falls back to the candidate ``body``
    (already a readable sentence for harvested patterns).
    """
    parsed = parse_pattern_key(topic)
    agent_val = agent if not _placeholder(agent) else parsed["agent"]
    subtype_val = subtype if not _placeholder(subtype) else parsed["subtype"]
    engine_val = engine if not _placeholder(engine) else parsed["engine"]

    # A failure-pattern statement needs a real subtype. A candidate with only an
    # agent (a non-pattern lesson, e.g. a Slack-captured planning note) is NOT a
    # failure pattern, so it falls back to its already-readable body.
    if _placeholder(subtype_val):
        body_text = str(body or "").strip()
        if body_text:
            return body_text
        return "The fleet noticed something worth keeping."

    who = _pretty_agent(agent_val)
    what = _phrase_subtype(subtype_val)
    sentence = f"{who} keeps hitting {what}"

    if not _placeholder(engine_val):
        sentence += f" on {engine_val}"

    count_val: int | None
    if count is None:
        count_val = _count_from_evidence(evidence)
    else:
        try:
            count_val = int(count)
        except (TypeError, ValueError):
            count_val = None
    if count_val and count_val > 0:
        times = "time" if count_val == 1 else "times"
        sentence += f" (seen {count_val} {times})"

    return sentence + "."
