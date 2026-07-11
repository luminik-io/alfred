"""The capture -> judge -> promote pipeline (memory doctrine).

This is the review loop that turns a staged ``MemoryCandidate`` into a trusted,
recall-able lesson written to the memory backend (Redis AMS / SQLite hybrid),
plus the reversal levers (revert an auto-promotion, retire a single lesson).

Behavior here is doctrine and preserved exactly: the AMS write happens FIRST
with no local fallback, the LLM judge is the primary save/skip decision (a light
structural pre-filter gates access to it, and the judge can only LOWER
confidence, never rescue), and every destructive lever confirms the recall
lesson is actually gone before it records the state change locally.
"""

from __future__ import annotations

import inspect
import logging
import re
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from .base import LedgerBase
from .config import (
    _auto_promote_switches_allow_learning,
    _env_float,
)
from .config import direct_auto_promote_env as _direct_auto_promote_env
from .store import _AUTO_HELD_MARKER, Lesson, MemoryCandidate

_LOG = logging.getLogger(__name__)

# Auto-promotion defaults. Every one is env-tunable so a deployment can tune
# the gate without a code change. Auto-promotion is ON by default when the flag
# is unset/blank or a recognized truthy value: the LLM judge is the primary
# save/skip decision, while ``ALFRED_AUTO_PROMOTE=0``, malformed nonblank
# values, and ``ALFRED_AUTO_PROMOTE_KILL=1`` fail closed.
# The threshold is a LIGHT pre-filter, not the decision: any evidenced
# candidate (candidates default to confidence 0.5) must reach the LLM judge,
# which makes the real save/skip call. Memory has to capture AND save
# autonomously via the model; a high bar that dumps observed lessons to a
# human queue just piles up and never gets reviewed.
AUTO_PROMOTE_DEFAULT_THRESHOLD = 0.5
AUTO_PROMOTE_DEFAULT_MAX_PER_RUN = 5
AUTO_PROMOTE_DEFAULT_MAX_JUDGE_CALLS = 25
# When the LLM judge is explicitly disabled, the structural confidence is the
# ONLY gate, so the low judge-era bar would auto-promote every evidenced
# default-confidence candidate with no review. Hold a conservative floor in
# that case (env-tunable) so heuristic-only promotion stays selective.
AUTO_PROMOTE_NO_JUDGE_THRESHOLD = 0.9

# Promoted lessons are written to Redis AMS under a deterministic id derived
# from the candidate. This makes the write idempotent (a re-promote upserts the
# same record) and lets the revert lever forget exactly the lesson it wrote.
_LESSON_MEMORY_ID_PREFIX = "lesson:memory_candidate:"

_PHASE2_REFLECT_KWARGS = ("kind", "provenance")


class MemoryPromotionError(RuntimeError):
    """Raised when a candidate could not be written to Redis AMS.

    The candidate is left untouched (still ``candidate``/pending) so it can be
    re-promoted on a later run. There is no silent local fallback: a promoted
    lesson lives in AMS or nowhere.
    """


def _lesson_memory_id(candidate_id: str) -> str:
    """Deterministic AMS memory id for a promoted candidate."""
    return f"{_LESSON_MEMORY_ID_PREFIX}{candidate_id}"


def candidate_id_from_lesson_id(lesson_id: str) -> str:
    """Recover the source candidate id from a promoted lesson's memory id.

    Recall lessons carry the deterministic ``lesson:memory_candidate:<id>``
    memory id ``promote_memory_candidate`` wrote, so the desktop undo affordance
    can retire a lesson it only knows by its recall id. A plain candidate id
    (no prefix) is returned unchanged, so callers may pass either form."""
    text = (lesson_id or "").strip()
    if text.startswith(_LESSON_MEMORY_ID_PREFIX):
        return text[len(_LESSON_MEMORY_ID_PREFIX) :]
    return text


def _reflect_accepts_phase2_kwargs(lesson_writer: Any) -> bool:
    """Whether ``lesson_writer.reflect`` declares the Phase 2 ``kind``/``provenance``.

    Used so ``promote_memory_candidate`` passes the typed/provenance kwargs only
    to a writer that accepts them, rather than passing them unconditionally and
    retrying on a ``TypeError`` (which would mask a real ``TypeError`` raised
    inside a writer that DOES accept the kwargs). A writer whose ``reflect``
    accepts ``**kwargs`` is treated as accepting them. On any introspection
    failure we conservatively return ``False`` (call the Phase 1 contract), which
    never loses a promotion, only the typed metadata.
    """
    reflect = getattr(lesson_writer, "reflect", None)
    if reflect is None:
        return False
    try:
        params = inspect.signature(reflect).parameters
    except (TypeError, ValueError):
        return False
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return True
    return all(name in params for name in _PHASE2_REFLECT_KWARGS)


def _auto_dedup_key(body: str) -> str:
    """Normalize a candidate body to a conflict key.

    The OSS ledger has no precomputed ``dedup_hash`` column, so derive a stable
    key from the body: lowercased with collapsed whitespace. Two pending
    candidates that normalize to the same key are treated as a conflict (two
    unreviewed versions of one lesson) and both are left for a human."""
    return re.sub(r"\s+", " ", (body or "").strip().lower())


class PromotionMixin(LedgerBase):
    """The capture -> judge -> promote review loop, composed into ``FleetBrain``."""

    def promote_memory_candidate(
        self,
        candidate_id: str,
        *,
        reviewer: str = "operator",
        review_note: str = "",
        reviewed_at: datetime | None = None,
        lesson_writer: Any | None = None,
        env: Mapping[str, str] | None = None,
    ) -> Lesson:
        """Promote a candidate into a trusted lesson, written to Redis AMS.

        The candidate review queue, dedup index, and operational state stay in
        the local FleetBrain ledger; only the promoted LESSON moves, and it is
        written to Redis AMS, the semantic-recall backend.

        The AMS write happens FIRST and there is no local fallback: the
        candidate is flipped to ``validated`` only after the lesson is durably
        in AMS, so an unreachable AMS leaves the candidate ``candidate``
        (pending) and re-promotable rather than silently losing it. On an AMS
        write failure this raises :class:`MemoryPromotionError`.

        ``lesson_writer`` is the AMS provider (``reflect``-shaped, accepting a
        ``memory_id``); tests inject a stub. When omitted it is built from env.
        """
        candidate = self.store.get_memory_candidate(candidate_id)
        if candidate is None:
            raise ValueError(f"promote_memory_candidate: unknown candidate {candidate_id!r}")
        if candidate.status != "candidate":
            raise ValueError(
                f"promote_memory_candidate: candidate {candidate_id!r} is {candidate.status}"
            )

        # AMS write FIRST. No local fallback: if this fails the candidate stays
        # pending (no store update) and is re-promotable on a later run. Provider
        # CONSTRUCTION is inside the try too, so a bad AMS env value surfaces as a
        # retryable MemoryPromotionError (candidate stays pending, batch counts an
        # ams_write_error) rather than a raw exception / CLI traceback.
        try:
            if lesson_writer is None:
                lesson_writer = self._lesson_provider(env=env)
            if lesson_writer is None:
                # Runtime memory is disabled (ALFRED_MEMORY_PROVIDERS=null or
                # empty): there is no recall store to write to, so promotion is a
                # no-op. Leave the candidate pending rather than flipping it to
                # validated with no durable lesson behind it.
                raise MemoryPromotionError(
                    "promote_memory_candidate: runtime memory is disabled "
                    "(no lesson writer configured); nothing was written."
                )
            reflect_kwargs: dict[str, Any] = {
                "codename": candidate.codename,
                "repo": candidate.repo,
                "body": candidate.body,
                "tags": candidate.tags,
                "firing_id": candidate.source_firing_id,
                "severity": candidate.severity,
                "memory_id": _lesson_memory_id(candidate.id),
            }
            # Phase 2: pass the typed ``kind`` and ``provenance`` ONLY when the
            # writer's reflect actually declares them. We inspect the signature
            # up front rather than catching a TypeError from the call: a writer
            # that DOES accept the kwargs but raises TypeError internally (a real
            # bug) must surface as a MemoryPromotionError, not be silently
            # retried without the Phase 2 kwargs and mask the fault.
            if _reflect_accepts_phase2_kwargs(lesson_writer):
                reflect_kwargs["kind"] = candidate.kind
                reflect_kwargs["provenance"] = candidate.source_firing_id
            lesson = lesson_writer.reflect(**reflect_kwargs)
        except MemoryPromotionError:
            # Already the retryable, candidate-stays-pending signal; do not
            # re-wrap it (that would bury the disabled-memory message).
            raise
        except Exception as exc:
            _LOG.exception(
                "promote_memory_candidate: AMS lesson write failed for "
                "candidate %s; leaving it pending",
                candidate_id,
            )
            raise MemoryPromotionError(
                f"promote_memory_candidate: AMS write failed for {candidate_id!r}"
            ) from exc

        # Lesson is durable in AMS -> flip the candidate to validated.
        self.store.update_memory_candidate(
            replace(
                candidate,
                status="validated",
                reviewed_at=reviewed_at or datetime.now(UTC),
                reviewed_by=reviewer.strip() or "operator",
                review_note=review_note.strip() or None,
                promoted_lesson_id=lesson.id,
            )
        )
        return lesson

    def reject_memory_candidate(
        self,
        candidate_id: str,
        *,
        reviewer: str = "operator",
        review_note: str = "",
        reviewed_at: datetime | None = None,
    ) -> MemoryCandidate:
        """Reject a candidate so it remains auditable but never enters recall."""
        candidate = self.store.get_memory_candidate(candidate_id)
        if candidate is None:
            raise ValueError(f"reject_memory_candidate: unknown candidate {candidate_id!r}")
        if candidate.status != "candidate":
            raise ValueError(
                f"reject_memory_candidate: candidate {candidate_id!r} is {candidate.status}"
            )
        updated = replace(
            candidate,
            status="rejected",
            reviewed_at=reviewed_at or datetime.now(UTC),
            reviewed_by=reviewer.strip() or "operator",
            review_note=review_note.strip() or None,
        )
        return self.store.update_memory_candidate(updated)

    def auto_promote_enabled(self, env: Mapping[str, str] | None = None) -> bool:
        """True unless explicitly disabled or kill-switched.

        Memory should learn autonomously: evidenced candidates reach the LLM
        judge by default and the judge decides whether to save. Operators can
        set ``ALFRED_AUTO_PROMOTE=0`` for a normal opt-out; malformed nonblank
        values fail closed too. ``ALFRED_AUTO_PROMOTE_KILL=1`` wins over
        everything so a bad batch can be halted without editing the rest of the
        deployment config."""
        env_src = self._auto_promote_env(env)
        return _auto_promote_switches_allow_learning(env_src)

    def _auto_promote_env(self, env: Mapping[str, str] | None = None) -> Mapping[str, str]:
        if env is not None:
            return env
        if self._env is not None:
            return self._env
        return _direct_auto_promote_env()

    def hold_candidate_for_review(
        self, candidate_id: str, *, note: str = ""
    ) -> MemoryCandidate | None:
        """Set a candidate aside for a human without promoting or rejecting it.

        The row keeps status ``candidate`` (so it stays in the review queue and
        the dedup index) but its review_note is stamped with the held marker so
        later auto-promote runs skip it. Returns None if the candidate is gone
        or already left the candidate state."""
        candidate = self.store.get_memory_candidate(candidate_id)
        if candidate is None or candidate.status != "candidate":
            return None
        held = f"{_AUTO_HELD_MARKER} {note}".strip()
        return self.store.update_memory_candidate(
            replace(
                candidate,
                reviewed_at=datetime.now(UTC),
                reviewed_by="auto",
                review_note=held[:500],
            )
        )

    def auto_promote_candidates(
        self,
        *,
        threshold: float | None = None,
        max_per_run: int | None = None,
        reviewer: str = "auto",
        judge: Any | None = None,
        env: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Promote high-confidence, corroborated, non-conflicting candidates.

        Structural gate (every condition must hold):

          * the opt-out flag is not off and the kill-switch is off
            (``auto_promote_enabled``); otherwise this is a NO-OP that touches
            nothing and the manual queue is unchanged;
          * the candidate is still ``candidate`` and not already held for a
            human on a prior run;
          * the candidate carries evidence (no bare assertion auto-enters
            recall);
          * it does not conflict with another pending candidate that normalizes
            to the same body (two unreviewed versions => leave both for a
            human);
          * ``confidence >= threshold`` (default 0.5, env-tunable) -- a light
            pre-filter so any evidenced candidate reaches the judge, which is
            the real save/skip decision (autonomous LLM-driven capture+save).

        LLM judge (additive, default ON, gated behind
        ``ALFRED_AUTO_PROMOTE_LLM_JUDGE``): for each candidate that clears the
        structural gate, an LLM is asked whether the lesson is safe to save.
        The verdict shapes the outcome:

          * ``changes_agent_behavior`` => still AUTO-SAVED like any other safe
            verdict (the judge decides; the save is reversible), just recorded
            with a distinct note and counted under ``auto_saved_behavior_change``
            so the audit trail flags it. It no longer holds for a human;
          * ``is_duplicate``           => held for a human (dedup owns merging);
          * the judge confidence is taken as the LOWER of itself and the
            structural confidence (never a rescue), and a candidate that falls
            below the bar after that is held for a human;
          * FAIL-SOFT: any LLM error/timeout/parse/empty judgment leaves the
            candidate PENDING. A candidate is NEVER auto-saved on a failed or
            empty judgment, only on an explicit verdict that also clears the
            threshold. With the judge disabled, the heuristic alone gates.

        Promotions are capped per run (``max_per_run``) and recorded with
        ``reviewer="auto"`` so the whole batch stays auditable. ``judge`` is an
        injectable ``str -> str|None`` seam; tests pass a stub so no real model
        process is spawned. Returns a summary dict (always safe to log)."""
        env_src = self._auto_promote_env(env)
        summary: dict[str, Any] = {
            "enabled": self.auto_promote_enabled(env_src),
            "judge_enabled": False,
            "threshold": None,
            "cap": None,
            "considered": 0,
            "promoted": [],
            "skipped_low_confidence": 0,
            "skipped_no_evidence": 0,
            "skipped_conflict": 0,
            "skipped_duplicate": 0,
            "skipped_flagged": 0,
            "auto_saved_behavior_change": 0,
            # Kept at 0 for back-compat: behavior-changing verdicts are now
            # auto-saved (counted under ``auto_saved_behavior_change``) rather
            # than held, so nothing increments this any more.
            "flagged_behavior_change": 0,
            "held_low_confidence": 0,
            "judge_errors": 0,
            "judge_calls": 0,
            "judge_budget_exhausted": False,
            "ams_write_errors": 0,
        }
        if not summary["enabled"]:
            # No-op when explicitly disabled: do not even read the queue.
            return summary

        from memory_judge import judge_candidate, judge_enabled

        use_judge = judge_enabled(env_src)
        summary["judge_enabled"] = use_judge

        bar = (
            float(threshold)
            if threshold is not None
            else _env_float(
                "ALFRED_AUTO_PROMOTE_THRESHOLD", AUTO_PROMOTE_DEFAULT_THRESHOLD, env_src
            )
        )
        # The low default bar only makes sense because the LLM judge is the
        # real decider. With the judge off, raise the bar to a conservative
        # floor so default-confidence candidates are not blindly promoted with
        # no model or human review.
        if not use_judge:
            bar = max(
                bar,
                _env_float(
                    "ALFRED_AUTO_PROMOTE_NO_JUDGE_THRESHOLD",
                    AUTO_PROMOTE_NO_JUDGE_THRESHOLD,
                    env_src,
                ),
            )
        cap = (
            int(max_per_run)
            if max_per_run is not None
            else int(
                _env_float(
                    "ALFRED_AUTO_PROMOTE_MAX_PER_RUN",
                    AUTO_PROMOTE_DEFAULT_MAX_PER_RUN,
                    env_src,
                )
            )
        )
        # Per-run judge-call budget. The promotion ``cap`` only limits successful
        # promotions, but a rejected/duplicate/flagged row still costs a judge
        # call, so judging is bounded by this instead. Never below the promotion
        # cap (you must be able to judge enough to fill it).
        max_judge_calls = max(
            cap,
            int(
                _env_float(
                    "ALFRED_AUTO_PROMOTE_MAX_JUDGE_CALLS",
                    AUTO_PROMOTE_DEFAULT_MAX_JUDGE_CALLS,
                    env_src,
                )
            ),
        )
        summary["threshold"] = bar
        summary["cap"] = cap
        summary["max_judge_calls"] = max_judge_calls
        judge_calls = 0

        candidates = self.list_memory_candidates(status="candidate", limit=500)
        summary["considered"] = len(candidates)
        # Count normalized bodies so genuine conflicts (>1 unreviewed version)
        # are left for a human.
        seen: dict[str, int] = {}
        for cand in candidates:
            key = _auto_dedup_key(cand.body)
            seen[key] = seen.get(key, 0) + 1
        conflict_keys = {key for key, count in seen.items() if count > 1}

        promoted = 0
        for candidate in candidates:
            if promoted >= cap:
                break
            if (candidate.review_note or "").startswith(_AUTO_HELD_MARKER):
                # Already held for a human on a prior run; never reprocess.
                summary["skipped_flagged"] += 1
                continue
            if not (candidate.evidence or "").strip():
                summary["skipped_no_evidence"] += 1
                continue
            if _auto_dedup_key(candidate.body) in conflict_keys:
                summary["skipped_conflict"] += 1
                continue
            try:
                confidence = float(candidate.confidence)
            except (TypeError, ValueError):
                confidence = 0.0

            # Structural confidence is a prerequisite, and the judge can only
            # LOWER it (never rescue), so a below-bar candidate can never pass.
            # Skip it BEFORE spending a judge call so a queue of newer
            # low-confidence rows cannot exhaust the budget and starve older
            # promotable candidates.
            if confidence < bar:
                summary["skipped_low_confidence"] += 1
                continue

            note = f"auto-promoted (confidence={confidence:.3f} >= {bar:.3f})"
            if use_judge:
                if judge_calls >= max_judge_calls:
                    # Spent the per-run judge budget. Stop here so the run stays
                    # bounded; remaining rows are picked up next run.
                    summary["judge_budget_exhausted"] = True
                    break
                judge_calls += 1
                verdict = judge_candidate(
                    topic=(candidate.body or "").split("\n", 1)[0][:200],
                    body=candidate.body or "",
                    evidence=candidate.evidence or "",
                    judge=judge,
                    env=env_src,
                )
                if verdict is None:
                    # FAIL-SOFT: a failed/empty/unparseable judgment must NEVER
                    # auto-promote. Leave the candidate pending for the human.
                    summary["judge_errors"] += 1
                    continue
                if verdict.is_duplicate:
                    # Hold (not reject): a rejected row drops out of the dedup
                    # index, so the next harvest would re-propose, re-create, and
                    # re-judge the same lesson. Held keeps it in the index while
                    # keeping it out of the re-judge loop.
                    self.hold_candidate_for_review(
                        candidate.id,
                        note=f"LLM judge: duplicate {verdict.rationale}".strip(),
                    )
                    summary["skipped_duplicate"] += 1
                    continue
                # Safe verdict. Take the LOWER of structural and judge
                # confidence so a high judge score can never lift a candidate
                # that failed the structural bar.
                confidence = min(confidence, verdict.confidence)
                if confidence < bar:
                    # The judge lowered confidence under the bar. Unlike a purely
                    # structural skip (which leaves the row pending for the next
                    # run), this row was JUDGED and is HELD for a human, so count
                    # it as a hold, not a transient low-confidence skip.
                    self.hold_candidate_for_review(
                        candidate.id,
                        note=(f"LLM judge confidence {confidence:.3f} < {bar:.3f}"),
                    )
                    summary["held_low_confidence"] += 1
                    continue
                if verdict.changes_agent_behavior:
                    # Behavior-changing but otherwise safe and above the bar:
                    # AUTO-SAVE it (the judge decided; every auto-save is
                    # reversible) with a distinct note so the audit trail flags
                    # it. Counted separately from ordinary saves.
                    summary["auto_saved_behavior_change"] += 1
                    note = (
                        f"auto-saved (behavior-changing; structural + LLM judge "
                        f"confidence={confidence:.3f} >= {bar:.3f})"
                    )
                else:
                    note = (
                        f"auto-promoted (structural + LLM judge "
                        f"confidence={confidence:.3f} >= {bar:.3f})"
                    )

            try:
                self.promote_memory_candidate(
                    candidate.id,
                    reviewer=reviewer,
                    review_note=note,
                    env=env_src,
                )
            except ValueError:
                # The candidate changed under us (already promoted/rejected by a
                # concurrent reviewer). Skip without counting it.
                continue
            except MemoryPromotionError:
                # The AMS write failed: the candidate is left pending (no local
                # fallback, no silent loss) and will be retried on a later run.
                summary["ams_write_errors"] = summary.get("ams_write_errors", 0) + 1
                continue
            promoted += 1
            summary["promoted"].append(candidate.id)

        summary["judge_calls"] = judge_calls
        return summary

    def revert_auto_promotions(
        self,
        *,
        reviewer: str = "auto-revert",
        note: str = "",
        lesson_forgetter: Any | None = None,
    ) -> list[str]:
        """Forget every auto-promoted lesson from Redis AMS and reopen it.

        The reversal lever the auto-promotion guardrails promise: forgets each
        auto-promoted lesson from Redis AMS (the promoted-lesson backend) and
        flips its candidate back to ``candidate`` so the operator can
        re-review. Auto-promotions are the validated candidates the auto-promoter
        wrote (``reviewed_by == "auto"`` with a recorded ``promoted_lesson_id``).

        A candidate is reopened ONLY once its lesson is actually forgotten from
        AMS: if the forget fails (a transient outage, or forgetting disabled
        server-side) the candidate is left validated and logged, so the local
        ledger never claims a revert while the lesson is still live in AMS
        recall. The sweep paginates (reverting flips a candidate out of the
        validated set) so it drains more than one page. ``lesson_forgetter`` is
        the AMS provider; tests inject a stub. Returns the candidate ids that
        were actually reverted.
        """
        reverted: list[str] = []
        forget_failed: set[str] = set()
        if lesson_forgetter is None:
            lesson_forgetter = self._lesson_provider()
        if lesson_forgetter is None:
            # Memory disabled (ALFRED_MEMORY_PROVIDERS=null): there is no store to
            # forget from, so there is nothing to revert. Controlled no-op rather
            # than crashing on a None forgetter or reopening candidates whose
            # lessons were never actually removed.
            _LOG.debug("revert_auto_promotions: runtime memory disabled; no-op")
            return []
        # Phase 1: enumerate EVERY validated auto-promotion via offset paging.
        # Reading is non-mutating, so offsets stay stable and a newest page full
        # of human-reviewed or undeletable rows cannot hide older auto-promotions
        # (the bug a "loop until the set shrinks" approach had).
        targets: list[MemoryCandidate] = []
        page = 500
        offset = 0
        while True:
            batch = self.list_memory_candidates(status="validated", limit=page, offset=offset)
            targets.extend(
                cand
                for cand in batch
                if cand.reviewed_by == "auto" and cand.promoted_lesson_id is not None
            )
            if len(batch) < page:
                break
            offset += page
        # Phase 2: forget then reopen each, reopening ONLY when the lesson is
        # actually gone so the ledger never records a revert while the lesson is
        # still live in AMS recall.
        for candidate in targets:
            cid = candidate.id
            # A non-None forgetter is guaranteed by the disabled-memory guard
            # above, so a failed forget is a real outage, not "memory off".
            try:
                forgotten = bool(lesson_forgetter.forget_lesson(_lesson_memory_id(cid)))
            except Exception:
                _LOG.exception(
                    "revert_auto_promotions: AMS forget failed for candidate %s",
                    cid,
                )
                forgotten = False
            if not forgotten:
                forget_failed.add(cid)
                continue
            self.store.update_memory_candidate(
                replace(
                    candidate,
                    status="candidate",
                    reviewed_at=datetime.now(UTC),
                    reviewed_by=reviewer.strip() or "auto-revert",
                    review_note=note.strip() or None,
                    promoted_lesson_id=None,
                )
            )
            reverted.append(cid)
        if forget_failed:
            _LOG.warning(
                "revert_auto_promotions: left %d candidate(s) validated because the "
                "AMS lesson could not be forgotten: %s",
                len(forget_failed),
                ", ".join(sorted(forget_failed)),
            )
        return reverted

    def retire_memory_candidate(
        self,
        candidate_id: str,
        *,
        reviewer: str = "operator",
        note: str = "",
        lesson_forgetter: Any | None = None,
    ) -> MemoryCandidate | None:
        """Undo a single promoted lesson: forget it from AMS and retire the row.

        The per-lesson counterpart to ``revert_auto_promotions``. It powers the
        desktop "undo / retire" affordance so a bad auto-promotion is one click
        to walk back, without opening the whole batch. Accepts either a raw
        candidate id or the ``lesson:memory_candidate:<id>`` recall id a lesson
        surfaces under (see ``candidate_id_from_lesson_id``), so the client can
        retire a lesson it only knows by its recall id.

        The AMS forget happens FIRST and the row is flipped to ``retired`` ONLY
        once the lesson is actually gone, mirroring ``revert_auto_promotions`` /
        ``consolidate_lessons``: the ledger never records a retire while the
        lesson is still live in recall. Returns the updated candidate, or ``None``
        if the candidate is unknown or was never a promoted (``validated``)
        lesson. Raises :class:`MemoryPromotionError` if the AMS forget fails, so
        the caller can surface a retryable error rather than silently claiming a
        retire that did not happen.
        """
        cid = candidate_id_from_lesson_id(candidate_id)
        candidate = self.store.get_memory_candidate(cid)
        if candidate is None or candidate.status != "validated":
            return None
        lesson_id = candidate.promoted_lesson_id or _lesson_memory_id(candidate.id)
        forgetter = lesson_forgetter
        if forgetter is None:
            forgetter = self._lesson_provider()
        if forgetter is None:
            # Memory disabled (ALFRED_MEMORY_PROVIDERS=null): there is no store to
            # forget the lesson from, so the retire cannot be confirmed. Raise the
            # same controlled signal as a forget failure (candidate stays
            # validated) instead of crashing on None or silently retiring a lesson
            # that may still be live if memory is re-enabled.
            raise MemoryPromotionError(
                "retire_memory_candidate: runtime memory is disabled "
                "(no lesson forgetter); nothing was retired."
            )
        try:
            forgotten = bool(forgetter.forget_lesson(lesson_id))
        except Exception as exc:
            _LOG.exception(
                "retire_memory_candidate: AMS forget failed for candidate %s",
                candidate.id,
            )
            raise MemoryPromotionError(
                f"retire_memory_candidate: AMS forget failed for {candidate.id!r}"
            ) from exc
        if not forgotten:
            # The lesson is still live in AMS recall; do NOT record a retire.
            raise MemoryPromotionError(
                f"retire_memory_candidate: AMS forget returned false for {candidate.id!r}"
            )
        return self.store.update_memory_candidate(
            replace(
                candidate,
                status="retired",
                reviewed_at=datetime.now(UTC),
                reviewed_by=reviewer.strip() or "operator",
                review_note=note.strip() or None,
                promoted_lesson_id=None,
            )
        )
