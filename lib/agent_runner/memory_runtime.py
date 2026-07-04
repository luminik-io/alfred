"""Best-effort runtime wiring for Alfred's local memory layer."""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .result import ClaudeResult

_LOG = logging.getLogger(__name__)

BEGIN_MARKER = "ALFRED_MEMORY_REFLECTIONS_JSON"
END_MARKER = "END_ALFRED_MEMORY_REFLECTIONS_JSON"
_MEMORY_BLOCK_RE = re.compile(
    rf"(?:^|\n){BEGIN_MARKER}\s*(.*?)\s*{END_MARKER}(?:\n|$)",
    re.DOTALL,
)
_VALID_SEVERITIES = {"info", "warning", "blocker"}
_REFLECTION_MODES = {"direct", "candidate", "off"}


@dataclass(frozen=True)
class MemoryReflection:
    """One durable lesson parsed from an engine response."""

    body: str
    tags: tuple[str, ...] = ()
    severity: str = "info"


# Upper bound on the derived recall query. Long enough to carry the issue
# title plus a meaningful slice of the body into AMS semantic search, short
# enough that the query stays a focused topic signal rather than a wall of text.
_MEMORY_QUERY_MAX_CHARS = 240


def issue_memory_query(
    title: str | None,
    body: str | None = None,
    *,
    max_chars: int = _MEMORY_QUERY_MAX_CHARS,
) -> str | None:
    """Derive a bounded, whitespace-collapsed recall query from an issue.

    Combines the issue ``title`` with a leading slice of ``body`` so recalled
    lessons are relevant to the work actually being done, not just the
    repo/codename. Returns ``None`` when nothing usable is present so callers
    preserve the historical recency-only recall (never a worse default).
    """
    parts: list[str] = []
    for value in (title, body):
        collapsed = " ".join(str(value or "").split()).strip()
        if collapsed:
            parts.append(collapsed)
    if not parts:
        return None
    query = " ".join(parts)
    if max_chars > 0 and len(query) > max_chars:
        query = query[:max_chars].rstrip()
    return query or None


def load_runtime_memory(env: Mapping[str, str] | None = None):
    """Return the configured memory provider, or ``None`` on any failure."""
    try:
        from memory.config import load_provider

        return load_provider(env=env)
    except Exception:
        _LOG.exception("memory runtime: provider load failed")
        return None


_DEFAULT_RECALL_THRESHOLD = 0.0


def _recall_relevance_threshold(env: Mapping[str, str] | None = None) -> float:
    """Minimum AMS similarity a recalled lesson needs to be injected.

    Config-driven via ``ALFRED_MEMORY_RECALL_THRESHOLD`` (a similarity in
    ``[0, 1]``, higher is stricter). Default ``0.0`` preserves the historical
    "inject everything recall returned" behavior; raise it to suppress weakly
    related lessons. Lessons whose provider reports no score are never dropped
    by the threshold (the gate cannot judge them).
    """
    raw = (env or os.environ).get("ALFRED_MEMORY_RECALL_THRESHOLD")
    if raw is None or not str(raw).strip():
        return _DEFAULT_RECALL_THRESHOLD
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_RECALL_THRESHOLD
    return max(0.0, min(1.0, value))


_DEFAULT_INJECT_MAX_CHARS = 8000
_TRUNCATION_MARKER = "…(truncated)"


def _inject_max_chars(env: Mapping[str, str] | None = None) -> int:
    """Hard character budget for the formatted memory block prepended to a prompt.

    Config-driven via ``ALFRED_MEMORY_INJECT_MAX_CHARS`` (a positive integer of
    characters). Default ``8000`` mirrors ECC's ``ECC_SESSION_START_MAX_CHARS``
    so "memory on by default" can never silently balloon the run prompt and
    inflate the per-PR share of quota. Whole lessons are kept from the top
    (recall is ordered by relevance/recency) until the next would exceed the
    budget; a lone over-budget lesson is still injected but hard-truncated with
    a clear marker. A non-positive or unparseable value falls back to the
    default rather than disabling the cap.
    """
    raw = (env or os.environ).get("ALFRED_MEMORY_INJECT_MAX_CHARS")
    if raw is None or not str(raw).strip():
        return _DEFAULT_INJECT_MAX_CHARS
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return _DEFAULT_INJECT_MAX_CHARS
    if value <= 0:
        return _DEFAULT_INJECT_MAX_CHARS
    return value


def _normalized_body(body: str) -> str:
    """Whitespace- and case-folded body used as a dedup key."""
    return " ".join(str(body or "").split()).strip().casefold()


def _iter_chain_members(provider) -> Iterator[Any]:
    """Yield each distinct member of ``provider``'s chain (or ``provider`` itself).

    A :class:`ChainedMemoryProvider` exposes a ``providers`` list; anything else
    is treated as a single-member chain. Duplicate object identities are skipped
    so the same sub-provider is never consulted twice.
    """
    seen: set[int] = set()
    candidates = getattr(provider, "providers", None)
    pool = candidates if isinstance(candidates, list) else [provider]
    for candidate in pool:
        if candidate is None or id(candidate) in seen:
            continue
        seen.add(id(candidate))
        yield candidate


def _recall_scored_lessons(
    provider,
    *,
    codename: str,
    repo: str,
    query: str | None,
    limit: int,
) -> list[tuple[object, float | None]] | None:
    """Return merged scored lessons across every chain member, or ``None``.

    Scored-capable members contribute ``(lesson, score)`` pairs from
    ``recall_scored``; members without scoring (e.g. FleetBrain) contribute
    ``(lesson, None)`` pairs from plain ``recall`` so their lessons are never
    dropped from the prompt. This mirrors :meth:`ChainedMemoryProvider.recall`,
    which merges every backend so freshly reviewed FleetBrain lessons still
    appear before a separate Redis sync has run.

    ``None`` is returned only when no member exposes ``recall_scored`` at all,
    signalling "fall back to the provider's own plain recall".
    """
    merged: list[tuple[object, float | None]] = []
    any_scored = False
    for candidate in _iter_chain_members(provider):
        if hasattr(candidate, "recall_scored"):
            any_scored = True
            try:
                scored = candidate.recall_scored(
                    codename=codename, repo=repo, query=query, limit=limit
                )
            except Exception:
                _LOG.exception("memory runtime: recall_scored failed")
                continue
            merged.extend(scored)
        else:
            try:
                lessons = candidate.recall(codename=codename, repo=repo, query=query, limit=limit)
            except Exception:
                _LOG.exception("memory runtime: chain-member recall failed")
                continue
            merged.extend((lesson, None) for lesson in lessons)
    if not any_scored:
        return None
    return merged


def _gated_lessons(
    provider,
    *,
    codename: str,
    repo: str,
    query: str | None,
    limit: int,
    threshold: float,
) -> list[object]:
    """Recall lessons, gate by relevance threshold, and dedupe by body.

    Prefers the scored recall path so the threshold can act on real AMS
    similarity. Falls back to plain ``recall`` (threshold inapplicable, dedup
    still applied) for providers without scores so existing behavior is never
    weakened.
    """
    scored = _recall_scored_lessons(
        provider, codename=codename, repo=repo, query=query, limit=limit
    )
    if scored is None:
        lessons = provider.recall(codename=codename, repo=repo, query=query, limit=limit)
        pairs: list[tuple[object, float | None]] = [(lesson, None) for lesson in lessons]
    else:
        pairs = scored
    out: list[object] = []
    seen_bodies: set[str] = set()
    for lesson, score in pairs:
        # A reported score below threshold is dropped; an absent score (None)
        # is always kept (the gate cannot judge it).
        if score is not None and score < threshold:
            continue
        key = _normalized_body(getattr(lesson, "body", ""))
        if not key or key in seen_bodies:
            continue
        seen_bodies.add(key)
        out.append(lesson)
    return out


def format_memory_context(
    provider,
    *,
    codename: str,
    repo: str,
    query: str | None = None,
    limit: int = 3,
) -> str:
    """Return prompt-ready memory context, or an empty string.

    Recalled lessons are gated before injection: anything below the configured
    relevance threshold (``ALFRED_MEMORY_RECALL_THRESHOLD``) is dropped, and
    near-duplicate bodies are collapsed so the same lesson is never injected
    twice. This reuses the provider's own scoring rather than always injecting.

    The final formatted block is then bounded to a hard character budget
    (``ALFRED_MEMORY_INJECT_MAX_CHARS``, default ``8000``): the two header
    lines are always kept, and whole lessons are appended from the top
    (highest relevance/recency first) only while they fit. Tail lessons that
    would blow the budget are dropped; if even the first lesson overflows on
    its own it is still injected but hard-truncated with a clear marker. If the
    cap is set below the header length itself, no block is injected at all (the
    empty string is returned) rather than emitting a header that exceeds the
    cap. Under budget the output is byte-for-byte identical to the pre-cap
    behavior.
    """
    if provider is None or getattr(provider, "name", "") == "null":
        return ""
    threshold = _recall_relevance_threshold()
    try:
        lessons = _gated_lessons(
            provider,
            codename=codename,
            repo=repo,
            query=query,
            limit=limit,
            threshold=threshold,
        )
    except Exception:
        _LOG.exception("memory runtime: recall failed")
        return ""
    if not lessons:
        return ""
    header = [
        "Alfred memory for this codename and repo:",
        "Use these as hints only. Trust the repository code and current issue first.",
    ]
    lesson_lines: list[str] = []
    for idx, lesson in enumerate(lessons[:limit], start=1):
        severity = "" if getattr(lesson, "severity", "info") == "info" else "!"
        tags = getattr(lesson, "tags", []) or []
        tag_text = f" [{', '.join(tags)}]" if tags else ""
        body = str(getattr(lesson, "body", "")).strip()
        if body:
            lesson_lines.append(f"{idx}. {severity}{tag_text} {body}".strip())
    budget = _inject_max_chars()
    lines = _apply_inject_budget(header, lesson_lines, budget)
    return "\n".join(lines).strip()


def _apply_inject_budget(header: list[str], lesson_lines: list[str], budget: int) -> list[str]:
    """Bound ``header + lesson_lines`` to ``budget`` total characters.

    Returns the lines to inject, or an empty list meaning "inject nothing".

    The block is ALL OR NOTHING: it is either the full two-line header plus at
    least one (possibly hard-truncated) lesson, all within ``budget``, or it is
    empty. A partial or lone-header block is never emitted. If the full header
    plus even a minimal truncated lesson cannot fit the budget, ``[]`` is
    returned up front.

    ``budget`` is also an ABSOLUTE ceiling: the returned lines, joined with
    newlines, are guaranteed to be at most ``budget`` characters for every value
    of ``budget`` (down to ``1``). Whole lessons are kept from the top only while
    the running ``len("\\n".join(lines))`` stays within ``budget``; the first
    line that would exceed it and every line after are dropped. If the single
    highest-priority lesson still overflows the remaining room it is
    hard-truncated (with :data:`_TRUNCATION_MARKER`). A final pop-until-fits
    backstop then enforces the ceiling belt-and-suspenders.
    """

    def joined_len(lines: list[str]) -> int:
        return len("\n".join(lines))

    # All-or-nothing gate: unless the FULL header plus a minimal truncated lesson
    # (one joining newline + the marker) fits, inject nothing. This forbids a
    # partial/lone-header block at any sub-header or barely-above-header budget.
    if joined_len(header) + 1 + len(_TRUNCATION_MARKER) > budget:
        return []
    kept = list(header)
    for line in lesson_lines:
        if joined_len([*kept, line]) <= budget:
            kept.append(line)
            continue
        # This lesson does not fit whole. If no lesson has been added yet, inject
        # the top lesson truncated to the remaining room so at least one lesson is
        # always present (the up-front gate guarantees room for the marker plus
        # the joining newline; ``body_room`` may be 0, giving a marker-only line).
        if len(kept) == len(header):
            remaining = budget - joined_len(kept) - 1  # -1 for the joining "\n"
            body_room = remaining - len(_TRUNCATION_MARKER)
            kept.append(line[:body_room].rstrip() + _TRUNCATION_MARKER)
        break
    # Absolute backstop: no value of the budget may be exceeded. Pop trailing
    # lines until the block fits (or nothing is left).
    while kept and joined_len(kept) > budget:
        kept.pop()
    # The block is all-or-nothing: either the full header plus a lesson, or empty.
    # If the backstop reduced it to header-only (or less), inject nothing.
    if len(kept) <= len(header):
        return []
    return kept


def memory_reflection_instructions() -> str:
    """Prompt appendix that lets a firing file durable lessons."""
    return f"""If this firing learned a durable repo convention, recurring bug pattern, or operator preference, append this optional block at the very end of your final message:
{BEGIN_MARKER}
[
  {{"body": "Short durable lesson for next time.", "tags": ["repo-convention"], "severity": "info"}}
]
{END_MARKER}

Only include durable lessons. Do not include secrets, tokens, customer data, stack traces with private values, or facts that are already obvious from nearby code."""


def with_memory_prompt(
    prompt: str,
    provider,
    *,
    codename: str,
    repo: str | None,
    query: str | None = None,
    limit: int = 3,
) -> str:
    """Prepend recall context and append reflection instructions when enabled."""
    if provider is None or not repo or getattr(provider, "name", "") == "null":
        return prompt
    context = format_memory_context(
        provider,
        codename=codename,
        repo=repo,
        query=query,
        limit=limit,
    )
    chunks = []
    if context:
        chunks.append(context)
    chunks.append(prompt)
    chunks.append(memory_reflection_instructions())
    return "\n\n".join(chunks)


def parse_memory_reflections(text: str) -> list[MemoryReflection]:
    """Parse all memory-reflection blocks from ``text``."""
    reflections: list[MemoryReflection] = []
    for match in _MEMORY_BLOCK_RE.finditer(text or ""):
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payload = [payload]
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            body = str(item.get("body") or "").strip()
            if not body:
                continue
            raw_tags = item.get("tags") or []
            tags: tuple[str, ...] = ()
            if isinstance(raw_tags, list):
                tags = tuple(str(tag).strip() for tag in raw_tags if str(tag).strip())
            severity = str(item.get("severity") or "info").strip().lower()
            if severity not in _VALID_SEVERITIES:
                severity = "info"
            reflections.append(MemoryReflection(body=body, tags=tags, severity=severity))
    return reflections


def strip_memory_reflections(text: str) -> str:
    """Remove machine-readable memory blocks from user-facing result text."""
    return _MEMORY_BLOCK_RE.sub("\n", text or "").strip()


def _iter_writable_memory_providers(provider) -> Iterator[object]:
    providers = getattr(provider, "providers", None)
    if isinstance(providers, list):
        yield from providers
    elif provider is not None:
        yield provider


def record_reflections(
    provider,
    reflections: Iterable[MemoryReflection],
    *,
    codename: str,
    repo: str,
    firing_id: str,
) -> int:
    """Persist parsed lessons. Returns the count written."""
    if provider is None:
        return 0
    # Default changed from direct lesson writes to reviewable candidates so
    # engine-generated memories never enter recall without operator review.
    mode = os.environ.get("ALFRED_MEMORY_REFLECTION_MODE", "candidate").strip().lower()
    if mode not in _REFLECTION_MODES:
        mode = "candidate"
    if mode == "off":
        return 0
    written = 0
    dropped = 0
    for reflection in reflections:
        try:
            if mode == "candidate":
                stored = False
                for candidate in _iter_writable_memory_providers(provider):
                    brain = getattr(candidate, "brain", None)
                    if brain is None or not hasattr(brain, "propose_memory"):
                        continue
                    brain.propose_memory(
                        codename=codename,
                        repo=repo,
                        body=reflection.body,
                        tags=reflection.tags,
                        severity=reflection.severity,
                        source="engine-reflection",
                        source_firing_id=firing_id,
                        confidence=0.6,
                    )
                    stored = True
                    break
                if not stored:
                    raise NotImplementedError("no candidate-capable memory provider")
            else:
                provider.reflect(
                    codename=codename,
                    repo=repo,
                    body=reflection.body,
                    tags=reflection.tags,
                    severity=reflection.severity,
                    firing_id=firing_id,
                )
            written += 1
        except NotImplementedError:
            # No writable/candidate-capable provider took the lesson. This is a
            # real drop (the firing's learning is lost), not an expected
            # fallthrough, so it must not vanish silently.
            dropped += 1
        except Exception:
            dropped += 1
            _LOG.exception("memory runtime: reflect failed")
    if dropped:
        # One summary line per firing so a misconfigured chain (no writable
        # memory provider) is visible in logs instead of silently discarding
        # every lesson a firing learned.
        _LOG.warning(
            "memory runtime: dropped %d of %d reflection(s) for %s/%s firing %s "
            "(mode=%s): no provider accepted the write",
            dropped,
            written + dropped,
            codename,
            repo,
            firing_id,
            mode,
        )
    return written


def record_firing(
    provider,
    *,
    codename: str,
    repo: str,
    firing_id: str,
    result: ClaudeResult,
    engine_used: str,
) -> bool:
    """Best-effort write of the firing audit row into fleet-brain."""
    status = "ok" if result.success else "blocked"
    if result.subtype in {"error_max_turns", "error_timeout", "parse-failed"}:
        status = "partial"
    summary = f"engine={engine_used} subtype={result.subtype} turns={result.num_turns}"
    for candidate in _iter_writable_memory_providers(provider):
        brain = getattr(candidate, "brain", None)
        if brain is None or not hasattr(brain, "firing_log"):
            continue
        try:
            brain.firing_log(
                firing_id=firing_id,
                codename=codename,
                repo=repo,
                status=status,
                summary=summary,
                cost_cents=round(result.cost_usd * 100),
                sentinel=result.subtype,
                finished_at=datetime.now(UTC),
            )
            if status != "ok" and hasattr(brain, "record_failure"):
                try:
                    brain.record_failure(
                        codename=codename,
                        repo=repo,
                        firing_id=firing_id,
                        subtype=result.subtype,
                        summary=summary,
                        engine=engine_used,
                        severity="warning",
                    )
                except Exception:
                    _LOG.exception("memory runtime: record_failure failed")
            return True
        except Exception:
            _LOG.exception("memory runtime: firing_log failed")
    return False
