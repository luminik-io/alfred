"""LLM auto-extraction of fleet-brain lessons from a firing outcome.

This is the highest-leverage half of gated auto-build memory: at the end
of a firing, distill the run's outcome/transcript into structured lessons
``{lesson, confidence, severity, evidence}`` using the fleet's existing
frontier-CLI dispatch (``claude``/``codex``), then feed them to
``FleetBrain.propose_memory`` so they enter the same dedup-on-write +
confidence-gate pipeline as every other candidate.

Design constraints (all binding):

  * OFF BY DEFAULT. ``extract_and_propose`` is a no-op unless
    ``ALFRED_MEMORY_EXTRACT`` is armed. When off it touches nothing.
  * No new model surface. We borrow ``agent_runner.claude_invoke`` (the
    same dispatch every firing uses). It is imported lazily so importing
    this module never drags in the heavy runner, and so the package works
    on a brain-only host.
  * Mockable. The CLI call is an injected ``invoker`` callable, so tests
    pass a stub and never spawn a real ``claude``/``codex`` process.
  * Fail-soft. Any extraction failure (CLI down, unparseable output,
    bad JSON) degrades to "no lessons" and never raises into the firing.
    A poisoned/garbage extraction can at worst create a low-confidence
    candidate that the gate keeps in the manual queue.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

# An invoker takes the extraction prompt and returns the model's raw text
# (the JSON payload we asked for), or None on any failure. This is the
# single seam tests stub out.
Invoker = Callable[[str], str | None]

_VALID_SEVERITY = {"info", "warning", "blocker"}

_EXTRACTION_PROMPT = """\
You are distilling durable engineering lessons from one autonomous-agent
firing so a fleet of coding agents can avoid repeating mistakes.

Return ONLY a JSON array (no prose, no code fence). Each element:
  {{"lesson": str, "confidence": float 0..1, "severity":
    "info"|"warning"|"blocker", "evidence": [str, ...]}}

Rules:
  - Emit a lesson ONLY if it is concrete, reusable, and supported by what
    actually happened in this firing. Prefer zero lessons over a guess.
  - "confidence" is how sure you are the lesson is true AND durable.
    Be conservative: one-off or speculative observations are <= 0.6.
  - "evidence" cites specifics from the firing (commands, errors, file
    paths, PR/issue refs). No evidence => do not emit the lesson.
  - At most 3 lessons. Deduplicate.

Firing context:
agent: {agent}
repo: {repo}
outcome: {outcome}

Firing transcript / outcome detail (may be truncated):
{detail}
"""

# Cap the detail we send so a giant transcript cannot blow the prompt or
# the cost budget. The tail tends to carry the outcome (errors, final
# diff), so we keep the tail.
_MAX_DETAIL_CHARS = 12_000


def extract_enabled(env: Mapping[str, str] | None = None) -> bool:
    src = env if env is not None else os.environ
    return str(src.get("ALFRED_MEMORY_EXTRACT", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _truncate_tail(text: str, limit: int = _MAX_DETAIL_CHARS) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return "...[truncated]...\n" + text[-limit:]


def build_prompt(*, agent: str, repo: str, outcome: str, detail: str) -> str:
    return _EXTRACTION_PROMPT.format(
        agent=agent or "unknown",
        repo=repo or "global",
        outcome=outcome or "unknown",
        detail=_truncate_tail(detail),
    )


def _parse_lessons(raw: str | None) -> list[dict[str, Any]]:
    """Parse the model's JSON array of lessons, tolerantly.

    Strips an optional ```json fence, finds the outermost array, and keeps
    only well-shaped entries. Returns [] on any problem so a malformed
    extraction is simply dropped."""
    if not raw or not raw.strip():
        return []
    text = raw.strip()
    # Strip a fenced block if the model wrapped the JSON.
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # Fall back to the outermost [...] if there is leading/trailing prose.
    if not text.startswith("["):
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []

    out: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        lesson = str(item.get("lesson") or "").strip()
        if not lesson:
            continue
        try:
            confidence = float(item.get("confidence"))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))
        severity = str(item.get("severity") or "info").strip().lower()
        if severity not in _VALID_SEVERITY:
            severity = "info"
        ev_raw = item.get("evidence") or []
        evidence = (
            [str(e).strip() for e in ev_raw if str(e).strip()] if isinstance(ev_raw, list) else []
        )
        if not evidence:
            # No evidence => the gate would never auto-promote it anyway,
            # and an evidence-free lesson is exactly what we must not trust.
            continue
        out.append(
            {
                "lesson": lesson,
                "confidence": confidence,
                "severity": severity,
                "evidence": evidence,
            }
        )
    return out[:3]


def _default_invoker() -> Invoker:
    """Resolve the real CLI invoker lazily (claude -p, read-only).

    Imported lazily so this module stays importable on a brain-only host
    and so tests that inject a stub never import the heavy runner."""

    def _invoke(prompt: str) -> str | None:
        try:
            from agent_runner import claude_invoke
        except Exception:
            return None
        try:
            result = claude_invoke(
                prompt,
                workdir=Path(os.environ.get("ALFRED_HOME", ".")),
                # Read-only distillation: no tools needed.
                allowed_tools="",
                max_turns=1,
                timeout=int(os.environ.get("ALFRED_MEMORY_EXTRACT_TIMEOUT", "180")),
            )
        except Exception:
            return None
        if not getattr(result, "success", False):
            return None
        return getattr(result, "result_text", None)

    return _invoke


def extract_and_propose(
    brain: Any,
    *,
    agent: str,
    repo: str | None,
    outcome: str,
    detail: str,
    firing_id: str | None = None,
    invoker: Invoker | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Distill lessons from a firing and queue them as candidates.

    No-op (returns ``{"enabled": False, ...}``) unless
    ``ALFRED_MEMORY_EXTRACT`` is armed. Otherwise it builds the prompt,
    calls ``invoker`` (defaulting to the real ``claude -p`` dispatch),
    parses the JSON contract, and routes each lesson through
    ``brain.propose_memory``, which applies dedup-on-write and the
    confidence gate. The model's self-reported ``confidence`` flows through
    as the gate input; ``severity`` is preserved on the candidate.

    Returns a summary dict (safe to log). Never raises."""
    summary: dict[str, Any] = {
        "enabled": extract_enabled(env),
        "extracted": 0,
        "proposed": [],
    }
    if not summary["enabled"]:
        return summary

    invoke = invoker or _default_invoker()
    prompt = build_prompt(agent=agent, repo=repo or "global", outcome=outcome, detail=detail)
    try:
        raw = invoke(prompt)
    except Exception:
        raw = None
    lessons = _parse_lessons(raw)
    summary["extracted"] = len(lessons)

    for lesson in lessons:
        evidence = [
            {"kind": "llm_extraction", "firing_id": firing_id, "detail": ev}
            for ev in lesson["evidence"]
        ]
        try:
            candidate_id = brain.propose_memory(
                agent=agent,
                repo=repo,
                topic=f"firing:{firing_id}" if firing_id else "firing-lesson",
                body=lesson["lesson"],
                evidence=evidence,
                source="llm-extraction",
                confidence=lesson["confidence"],
                severity=lesson["severity"],
            )
        except Exception:
            continue
        summary["proposed"].append(candidate_id)
    return summary
