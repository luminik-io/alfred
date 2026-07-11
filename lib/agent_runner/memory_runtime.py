"""Best-effort runtime wiring for Alfred's local memory layer."""

from __future__ import annotations

import inspect
import json
import logging
import os
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import alfred_config

from . import memory_ranking
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
    """Return the configured memory provider, or ``None`` on a load failure.

    A genuine MISCONFIGURATION (:class:`MemoryProviderMisconfigured`, e.g. an
    invalid ``ALFRED_MEMORY_PG_TABLE_PREFIX``) is re-raised, never swallowed: a
    typo in a memory setting must surface rather than silently start the runner
    with recall memory disabled. Every other failure (an unavailable backend, a
    transient error) still degrades to ``None`` so optional memory never crashes
    a firing.
    """
    from memory.pgvector_provider import MemoryProviderMisconfigured

    try:
        from memory.config import load_provider

        return load_provider(env=env)
    except MemoryProviderMisconfigured:
        raise
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


_RECALL_OVERFETCH_FACTOR = 6
_RECALL_OVERFETCH_MIN_MARGIN = 12
_RECALL_OVERFETCH_MAX = 48


def _recall_fetch_limit(limit: int) -> int:
    """Candidate-pool size to recall before the injection reorders run.

    The injection reorders (the ops/codebase split, and rank/typed when armed)
    can only promote a codebase lesson into the injected page if that lesson was
    actually recalled. Fetching exactly ``limit`` rows means a repo whose top
    ``limit`` semantic hits are all ops lessons has no codebase row to promote,
    so the split silently fails exactly when ops lessons dominate recall. So pull
    a bounded larger candidate pool here; the ``pairs[:limit]`` slice and the
    character budget downstream still cap what is actually injected, and the
    output stays byte-identical when no reorder changes the order. Bounded above
    so a huge ``limit`` cannot pull an unbounded page from the provider.
    """
    if limit <= 0:
        return limit
    return min(
        max(limit * _RECALL_OVERFETCH_FACTOR, limit + _RECALL_OVERFETCH_MIN_MARGIN),
        _RECALL_OVERFETCH_MAX,
    )


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


def _recall_member(
    candidate,
    *,
    codename: str,
    repo: str,
    query: str | None,
    limit: int,
    anchor_refs: list[str] | None,
) -> list:
    """Call a chain member's ``recall``, threading ``anchor_refs`` when it accepts it.

    A member (or a third-party provider) written against the pre-Phase-2 protocol
    does not declare ``anchor_refs``; passing it would raise, so we only pass it
    when the member's ``recall`` accepts it. With no anchor refs the call is
    byte-identical to before.
    """
    if anchor_refs and _member_accepts_anchor_refs(candidate):
        return candidate.recall(
            codename=codename, repo=repo, query=query, limit=limit, anchor_refs=anchor_refs
        )
    return candidate.recall(codename=codename, repo=repo, query=query, limit=limit)


def _member_accepts_anchor_refs(candidate) -> bool:
    recall = getattr(candidate, "recall", None)
    if recall is None:
        return False
    try:
        params = inspect.signature(recall).parameters
    except (TypeError, ValueError):
        return False
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return True
    return "anchor_refs" in params


def _recall_scored_lessons(
    provider,
    *,
    codename: str,
    repo: str,
    query: str | None,
    limit: int,
    anchor_refs: list[str] | None = None,
) -> list[tuple[object, float | None]] | None:
    """Return merged scored lessons across every chain member, or ``None``.

    Scored-capable members contribute ``(lesson, score)`` pairs from
    ``recall_scored``; members without scoring (e.g. FleetBrain) contribute
    ``(lesson, None)`` pairs from plain ``recall`` so their lessons are never
    dropped from the prompt. This mirrors :meth:`ChainedMemoryProvider.recall`,
    which merges every backend so freshly reviewed FleetBrain lessons still
    appear before a separate Redis sync has run.

    ``anchor_refs`` (Phase 2 code-grounding) is threaded to the plain-``recall``
    members that accept it, so an anchoring backend (e.g. FleetBrain) still
    surfaces file-linked lessons first even inside a Redis-scored chain. The AMS
    ``recall_scored`` path has no anchor index and is left unchanged.

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
                lessons = _recall_member(
                    candidate,
                    codename=codename,
                    repo=repo,
                    query=query,
                    limit=limit,
                    anchor_refs=anchor_refs,
                )
            except Exception:
                _LOG.exception("memory runtime: chain-member recall failed")
                continue
            merged.extend((lesson, None) for lesson in lessons)
    if not any_scored:
        return None
    return merged


def _gated_pairs(
    provider,
    *,
    codename: str,
    repo: str,
    query: str | None,
    limit: int,
    threshold: float,
    anchor_refs: list[str] | None = None,
    anchored_ids: set[str] | None = None,
) -> list[tuple[object, float | None]]:
    """Recall lessons, gate by relevance threshold, and dedupe by body.

    Returns ``(lesson, score)`` pairs, keeping each lesson's relevance score so
    the downstream ranking pass can fuse it with recency, ROI, and reuse. Prefers
    the scored recall path so the threshold can act on real AMS similarity. Falls
    back to plain ``recall`` (threshold inapplicable, dedup still applied) for
    providers without scores so existing behavior is never weakened.

    ``anchor_refs`` (Phase 2 code-grounding) is threaded through both paths so
    lessons anchored to the firing's files surface first.

    ``anchored_ids`` makes the body-dedup ANCHOR-AWARE: when the same lesson body
    is returned by both a scored provider (not anchored) and the local file store
    (anchored), a plain first-wins dedup would keep the scored copy and drop the
    anchored one, and the later id-based hoist could no longer recognize the
    survivor as anchored. So on a body tie an anchored copy wins over a
    non-anchored one. With ``anchored_ids`` empty (anchor recall off) this is a
    no-op and dedup keeps the first as before.
    """
    scored = _recall_scored_lessons(
        provider, codename=codename, repo=repo, query=query, limit=limit, anchor_refs=anchor_refs
    )
    if scored is None:
        lessons = _recall_member(
            provider,
            codename=codename,
            repo=repo,
            query=query,
            limit=limit,
            anchor_refs=anchor_refs,
        )
        pairs: list[tuple[object, float | None]] = [(lesson, None) for lesson in lessons]
    else:
        pairs = scored
    anchored = anchored_ids or set()
    out: list[tuple[object, float | None]] = []
    body_pos: dict[str, int] = {}
    for lesson, score in pairs:
        # A reported score below threshold is dropped; an absent score (None)
        # is always kept (the gate cannot judge it).
        if score is not None and score < threshold:
            continue
        key = _normalized_body(getattr(lesson, "body", ""))
        if not key:
            continue
        if key in body_pos:
            # Duplicate body. Anchor-aware: if this copy is anchored and the one
            # already kept is not, swap it in so the survivor is the anchored
            # lesson (which the hoist can then promote). Otherwise keep the first.
            idx = body_pos[key]
            kept_id = getattr(out[idx][0], "id", None)
            this_id = getattr(lesson, "id", None)
            if this_id in anchored and kept_id not in anchored:
                out[idx] = (lesson, score)
            continue
        body_pos[key] = len(out)
        out.append((lesson, score))
    return out


def _anchored_lesson_ids(
    provider,
    *,
    anchor_refs: list[str] | None,
    repo: str | None,
) -> set[str]:
    """Ids of the lessons anchored to any of ``anchor_refs``, across the chain.

    Asks each chain member that can answer anchor lookups (a provider with a
    ``lessons_for_anchor`` method, or one wrapping a FleetBrain that has it) which
    lessons are anchored to the requested refs. Used to hoist those lessons to
    the front of the merged/ranked result regardless of which member returned
    them or whether that member is scored. Best-effort: any lookup failure is
    logged and skipped, never raised.
    """
    refs = [r.strip() for r in (anchor_refs or []) if r and r.strip()]
    if not refs:
        return set()
    ids: set[str] = set()
    for member in _iter_chain_members(provider):
        lookup = getattr(member, "lessons_for_anchor", None)
        if lookup is None:
            brain = getattr(member, "brain", None)
            lookup = getattr(brain, "lessons_for_anchor", None)
        if lookup is None:
            continue
        for ref in refs:
            try:
                for lesson in lookup(anchor_ref=ref, repo=repo):
                    lesson_id = getattr(lesson, "id", None)
                    if lesson_id:
                        ids.add(lesson_id)
            except Exception:
                _LOG.exception("memory runtime: anchor lookup failed")
    return ids


def _hoist_anchored(
    pairs: list[tuple[object, float | None]],
    anchored_ids: set[str],
) -> list[tuple[object, float | None]]:
    """Move pairs whose lesson id is in ``anchored_ids`` to the front (stable).

    Preserves the relative order within each group, so the anchored lessons keep
    their merged/ranked order among themselves and the remainder keeps its own.
    De-dup is inherent: every pair is placed exactly once. A no-op when nothing
    matches.
    """
    if not anchored_ids:
        return pairs
    front: list[tuple[object, float | None]] = []
    rest: list[tuple[object, float | None]] = []
    for pair in pairs:
        if getattr(pair[0], "id", None) in anchored_ids:
            front.append(pair)
        else:
            rest.append(pair)
    return front + rest


def format_memory_context(
    provider,
    *,
    codename: str,
    repo: str,
    query: str | None = None,
    limit: int = 3,
    firing_id: str | None = None,
    anchor_refs: list[str] | None = None,
) -> str:
    """Return prompt-ready memory context, or an empty string.

    ``anchor_refs`` (Phase 2 code-grounding) surfaces lessons anchored to the
    firing's files first. A caller passes the file entities the firing is about
    (see :func:`with_memory_prompt`); with none it is a no-op.

    Recalled lessons are gated before injection: anything below the configured
    relevance threshold (``ALFRED_MEMORY_RECALL_THRESHOLD``) is dropped, and
    near-duplicate bodies are collapsed so the same lesson is never injected
    twice. This reuses the provider's own scoring rather than always injecting.

    Gated lessons then pass through the injection-quality pipeline
    (see :mod:`agent_runner.memory_ranking`), all OFF by default:

    * **Delta** (``ALFRED_MEMORY_DELTA``): with a ``firing_id``, a lesson already
      injected earlier in the same firing is dropped so the budget goes to fresh
      material on later turns.
    * **Rank** (``ALFRED_MEMORY_RANK``): the remaining lessons are ordered by a
      deterministic weighted score fusing relevance, severity/ROI, age-decayed
      recency, and reinforce-on-reuse, so the budget keeps the best lessons.

    The final formatted block is then bounded to a hard character budget
    (``ALFRED_MEMORY_INJECT_MAX_CHARS``, default ``8000``): the two header
    lines are always kept, and whole lessons are appended from the top
    (highest ranked first) only while they fit. Tail lessons that would blow the
    budget are dropped; if even the first lesson overflows on its own it is still
    injected but hard-truncated with a clear marker. If the cap is set below the
    header length itself, no block is injected at all (the empty string is
    returned) rather than emitting a header that exceeds the cap. With every knob
    at its default the output is byte-for-byte identical to the pre-ranking
    behavior.
    """
    if provider is None or getattr(provider, "name", "") == "null":
        return ""
    threshold = _recall_relevance_threshold()
    # Compute the anchored-id set ONCE, before dedup, so both the anchor-aware
    # body-dedup (an anchored copy wins a duplicate-body tie) and the later hoist
    # use the same set. Empty (and both a no-op) when no anchor_refs were passed.
    anchored_ids = (
        _anchored_lesson_ids(provider, anchor_refs=anchor_refs, repo=repo) if anchor_refs else set()
    )
    # Over-fetch a bounded candidate pool so the reorders below (ops/codebase
    # split, rank, typed) have codebase lessons to promote even when the top
    # ``limit`` semantic hits are all ops lessons. The ``pairs[:limit]`` slice and
    # the character budget still cap what is injected.
    fetch_limit = _recall_fetch_limit(limit)
    try:
        pairs = _gated_pairs(
            provider,
            codename=codename,
            repo=repo,
            query=query,
            limit=fetch_limit,
            threshold=threshold,
            anchor_refs=anchor_refs,
            anchored_ids=anchored_ids,
        )
    except Exception:
        _LOG.exception("memory runtime: recall failed")
        return ""
    if not pairs:
        return ""
    # Bind the durable reuse backend (Phase 3) from the provider so ranking reads
    # (and reinforce below writes) the reuse count that survives across firings.
    # A provider with no persisted reuse store leaves this None -> in-process
    # behaviour, unchanged. Only meaningful when ranking is armed.
    if memory_ranking.rank_enabled():
        memory_ranking.set_reuse_store(memory_ranking.reuse_store_for(provider))
    # Delta first so freed budget goes to fresh material, then rank so the
    # budget below keeps the best of what remains. Both are no-ops by default.
    # The reuse/delta state is process-global, so codename+repo scope every key
    # to keep unrelated firings from cross-contaminating each other.
    pairs = memory_ranking.apply_delta(pairs, firing_id, codename=codename, repo=repo)
    pairs = memory_ranking.rank_pairs(pairs, codename=codename, repo=repo)
    # Type-aware recall LAST (gated, off by default): when armed it lifts the
    # kinds that matter for editing code (conventions + fixes) ahead of passive
    # notes, with relevance/recency still ordering within a kind bucket.
    pairs = memory_ranking.apply_typed_recall(pairs)
    # Anchor hoist (only when anchor_refs were supplied): the whole point of
    # anchor recall is that file-linked lessons surface FIRST. In a scored chain
    # (e.g. Redis + FleetBrain) the scored member's generic hits are merged ahead
    # of the non-scored member's anchored lessons, so without this step the
    # anchored lessons lose their priority. Reuses the same ``anchored_ids`` the
    # anchor-aware dedup used, so a survivor swapped in for a duplicate body is
    # recognized here too. Works for any chain shape and is a no-op when no
    # anchor_refs were passed.
    if anchored_ids:
        pairs = _hoist_anchored(pairs, anchored_ids)
    # Ops/codebase split LAST (ON by default, ALFRED_MEMORY_INJECT_OPS to
    # disable): push Alfred-runtime lessons (provider quota, auth, engine
    # timeouts) below lessons about the underlying codebase so the injection
    # budget leads with what an engineer needs. Running it AFTER the hoist means
    # an ops lesson that happens to be file-anchored does NOT jump ahead of
    # codebase lessons and consume the slots this split reserves for them; the
    # stable sort keeps the hoisted codebase lessons first within the codebase
    # bucket and an anchored ops lesson first within the ops bucket. A pure
    # reorder, so no lesson is dropped, and a no-op when disabled (which leaves
    # the hoist as the final say, exactly as before).
    pairs = memory_ranking.deprioritize_ops(pairs)
    if not pairs:
        return ""
    header = [
        "Alfred memory for this codename and repo:",
        "Use these as hints only. Trust the repository code and current issue first.",
    ]
    lesson_lines: list[str] = []
    line_lessons: list[object] = []
    for idx, (lesson, _score) in enumerate(pairs[:limit], start=1):
        severity = "" if getattr(lesson, "severity", "info") == "info" else "!"
        tags = getattr(lesson, "tags", []) or []
        tag_text = f" [{', '.join(tags)}]" if tags else ""
        body = str(getattr(lesson, "body", "")).strip()
        if body:
            lesson_lines.append(f"{idx}. {severity}{tag_text} {body}".strip())
            line_lessons.append(lesson)
    budget = _inject_max_chars()
    lines = _apply_inject_budget(header, lesson_lines, budget)
    kept_lesson_count = max(0, len(lines) - len(header)) if lines else 0
    injected = line_lessons[:kept_lesson_count]
    if injected:
        # Reinforce the lessons that actually made it into the prompt and, for
        # delta, remember them against this firing so a later turn does not
        # re-inject them. Both are gated so the default path accumulates no
        # state at all (byte-identical, side-effect-free legacy behavior).
        if memory_ranking.rank_enabled():
            memory_ranking.record_reuse(injected, codename=codename, repo=repo)
        if firing_id and memory_ranking.delta_enabled():
            memory_ranking.record_injected(firing_id, injected, codename=codename, repo=repo)
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


_ANCHOR_RECALL_ENV = "ALFRED_MEMORY_ANCHOR_RECALL"


def anchor_recall_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether runtime anchor-grounded recall is armed (``ALFRED_MEMORY_ANCHOR_RECALL``).

    OFF by default, consistent with the rest of Phase 2: unless armed, the
    runtime never derives ``anchor_refs`` and recall is byte-identical to Phase
    1. When armed, the firing's available file context (its orientation paths)
    is passed to recall so lessons anchored to those files surface first.
    """
    raw = str((env or os.environ).get(_ANCHOR_RECALL_ENV, "")).strip().lower()
    return raw in {"1", "true", "yes", "on", "enabled"}


def derive_anchor_refs(
    orientation_paths: Iterable[str] | None,
    *,
    repo: str | None,
) -> list[str]:
    """Build recall ``anchor_refs`` from a firing's file context. Deterministic.

    Recall runs BEFORE the agent edits anything, so the exact files-to-be-edited
    are not known yet. This uses the best signal available at prompt-build time:
    the firing's ``orientation_paths`` (the files it was told to look at). For
    each path it emits both the bare repo-relative path and the ``<repo>/<path>``
    form, so it matches lessons anchored under either convention. Returns an empty
    list when there is no file signal, in which case anchor recall is a no-op and
    the caller falls back to ordinary recall. It never invents a path.
    """
    refs: list[str] = []
    seen: set[str] = set()
    repo_slug = (repo or "").strip().strip("/")
    for raw in orientation_paths or []:
        path = str(raw or "").strip().lstrip("/")
        if not path:
            continue
        variants = [path]
        if repo_slug and not path.startswith(f"{repo_slug}/"):
            variants.append(f"{repo_slug}/{path}")
        for ref in variants:
            if ref in seen:
                continue
            seen.add(ref)
            refs.append(ref)
    return refs


def with_memory_prompt(
    prompt: str,
    provider,
    *,
    codename: str,
    repo: str | None,
    query: str | None = None,
    limit: int = 3,
    firing_id: str | None = None,
    repo_root: str | None = None,
    orientation_paths: Iterable[str] | None = None,
) -> str:
    """Prepend recall context and append reflection instructions when enabled.

    ``firing_id`` is optional and only used by the delta-injection pipeline
    (``ALFRED_MEMORY_DELTA``): passing it lets a later turn of the same firing
    skip lessons already injected on an earlier turn.

    ``repo_root`` is optional and only used by the repo-profile injector
    (``ALFRED_REPO_PROFILE``, off by default): when armed, a deterministic
    profile of the repo (manifest, package manager, verify commands, structure)
    is prepended as a convention-memory block so a headless firing does not have
    to rediscover the project's shape. It is independent of the recall provider,
    so it can orient a firing even when memory recall is empty.

    ``orientation_paths`` activates Phase 2 anchor-grounded recall
    (``ALFRED_MEMORY_ANCHOR_RECALL``, off by default). Recall happens before any
    edit, so the exact edit targets are unknown; the firing's orientation paths
    are the file signal available at prompt-build time. When the flag is armed
    and orientation paths are present, lessons anchored to those files surface
    first. With no orientation paths (or the flag off) this is a no-op, and the
    general path stays an explicit caller passing ``anchor_refs`` to
    :func:`format_memory_context` / a provider's ``recall``.
    """
    profile = _repo_profile_block(repo_root)
    if provider is None or not repo or getattr(provider, "name", "") == "null":
        if profile:
            return "\n\n".join([profile, prompt])
        return prompt
    anchor_refs = (
        derive_anchor_refs(orientation_paths, repo=repo) if anchor_recall_enabled() else None
    )
    context = format_memory_context(
        provider,
        codename=codename,
        repo=repo,
        query=query,
        limit=limit,
        firing_id=firing_id,
        anchor_refs=anchor_refs or None,
    )
    chunks = []
    if profile:
        chunks.append(profile)
    if context:
        chunks.append(context)
    chunks.append(prompt)
    chunks.append(memory_reflection_instructions())
    return "\n\n".join(chunks)


def _repo_profile_block(repo_root: str | None) -> str:
    """Best-effort deterministic repo-profile block (empty unless armed)."""
    if not repo_root:
        return ""
    try:
        from .repo_profile import repo_profile_block

        return repo_profile_block(repo_root)
    except Exception:
        _LOG.exception("memory runtime: repo profile build failed")
        return ""


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
    mode = (alfred_config.get_str("ALFRED_MEMORY_REFLECTION_MODE") or "").strip().lower()
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
