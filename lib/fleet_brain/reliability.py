"""The reliability governor: failure patterns, promotion suggestions, doctor.

Read-only report paths over the ledger. ``list_failure_patterns`` groups
repeated failures and attaches a suggested operator action (via
:mod:`fleet_brain.classify`); ``suggest_memory_promotions`` scores reviewable
candidates that look safe to promote; ``reliability_report`` and ``doctor``
compose those into the operator-facing health views, and ``lesson_stats`` /
``health`` expose the cheap rollups the native memory API polls.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from .base import LedgerBase
from .classify import (
    _classify_failure_pattern,
    _failure_action_summary,
    _is_non_actionable_failure_pattern,
    _suggest_failure_action,
)
from .consolidate import _canonical_memory_body
from .serialize import _serialize
from .store import FailureEvent


class ReliabilityMixin(LedgerBase):
    """Operator-facing reliability + health reports, composed into ``FleetBrain``."""

    def list_failure_patterns(
        self,
        *,
        repo: str | None = None,
        codename: str | None = None,
        window_days: int = 7,
        min_count: int = 2,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Group repeated failures and attach a suggested operator action.

        This is the "reliability governor" read path. It does not mutate
        fleet state. The goal is to turn repeated Slack-style error noise
        into a small queue of concrete next actions.
        """
        cutoff = datetime.now(UTC) - timedelta(days=max(1, int(window_days)))
        grouped: dict[tuple[str, str, str, str], list[FailureEvent]] = {}
        for failure in self.list_failures(repo=repo, codename=codename, limit=500):
            if failure.created_at < cutoff:
                continue
            key = (
                failure.codename,
                failure.repo or "",
                failure.subtype or "unknown",
                failure.engine or "",
            )
            grouped.setdefault(key, []).append(failure)

        patterns: list[dict[str, Any]] = []
        threshold = max(1, int(min_count))
        for (agent, failure_repo, subtype, engine), rows in grouped.items():
            if len(rows) < threshold:
                continue
            rows.sort(key=lambda item: item.created_at)
            latest = rows[-1]
            if _is_non_actionable_failure_pattern(subtype, latest.summary):
                continue
            classification = _classify_failure_pattern(subtype, latest.summary)
            action = _suggest_failure_action(
                classification=classification,
                codename=agent,
                count=len(rows),
            )
            severity = "blocker" if action in {"pause_agent", "file_setup_issue"} else "warning"
            patterns.append(
                {
                    "key": "|".join([agent, failure_repo or "-", subtype, engine or "-"]),
                    "codename": agent,
                    "repo": failure_repo or None,
                    "subtype": subtype,
                    "engine": engine or None,
                    "count": len(rows),
                    "first_seen": rows[0].created_at.isoformat(),
                    "last_seen": latest.created_at.isoformat(),
                    "latest_summary": latest.summary,
                    "classification": classification,
                    "suggested_action": action,
                    "severity": severity,
                    "evidence_ids": [row.id for row in rows[-5:]],
                }
            )
        patterns.sort(
            key=lambda item: (
                item["severity"] != "blocker",
                -int(item["count"]),
                str(item["last_seen"]),
            )
        )
        return patterns[: max(1, min(int(limit), 100))]

    def suggest_memory_promotions(
        self,
        *,
        min_confidence: float = 0.75,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return reviewable candidates that look safe to promote.

        This is intentionally advisory. Alfred still keeps the human
        promotion step unless an operator explicitly scripts around it.
        """
        rows = self.list_memory_candidates(status="candidate", limit=500)
        suggestions: list[dict[str, Any]] = []
        trusted_bodies = {
            (lesson.repo, _canonical_memory_body(lesson.body)) for lesson in self.list_lessons()
        }
        for candidate in rows:
            canonical = _canonical_memory_body(candidate.body)
            if (candidate.repo, canonical) in trusted_bodies:
                continue
            score = float(candidate.confidence)
            reasons: list[str] = []
            if candidate.confidence >= min_confidence:
                reasons.append(f"confidence {candidate.confidence:.2f}")
            if candidate.evidence:
                score += 0.08
                reasons.append("has evidence")
            if candidate.tags:
                score += 0.03
                reasons.append("tagged")
            if candidate.severity in {"warning", "blocker"}:
                score += 0.04
                reasons.append(f"severity {candidate.severity}")
            if not reasons or score < min_confidence:
                continue
            suggestions.append(
                {
                    "candidate_id": candidate.id,
                    "codename": candidate.codename,
                    "repo": candidate.repo,
                    "body": candidate.body,
                    "score": round(min(score, 1.0), 3),
                    "reasons": reasons,
                }
            )
        suggestions.sort(key=lambda item: float(item["score"]), reverse=True)
        return suggestions[: max(1, min(int(limit), 100))]

    def reliability_report(
        self,
        *,
        window_days: int = 7,
        failure_min_count: int = 2,
        stale_worker_minutes: int = 60,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Return the operator-facing reliability governor report."""
        patterns = self.list_failure_patterns(
            window_days=window_days,
            min_count=failure_min_count,
            limit=limit,
        )
        stale_workers = self.list_stale_workers(max_age_minutes=stale_worker_minutes)
        promotions = self.suggest_memory_promotions(limit=limit)
        actions: list[dict[str, Any]] = []
        for pattern in patterns:
            actions.append(
                {
                    "kind": "failure_pattern",
                    "severity": pattern["severity"],
                    "action": pattern["suggested_action"],
                    "summary": _failure_action_summary(pattern),
                    "target": pattern["codename"],
                    "evidence": pattern["evidence_ids"],
                }
            )
        for worker in stale_workers[:limit]:
            actions.append(
                {
                    "kind": "stale_worker",
                    "severity": "warning",
                    "action": "inspect_worker",
                    "summary": (
                        f"{worker.codename} firing {worker.firing_id} has not "
                        f"sent a heartbeat recently"
                    ),
                    "target": worker.codename,
                    "evidence": [worker.id],
                }
            )
        if promotions:
            actions.append(
                {
                    "kind": "memory_promotion",
                    "severity": "info",
                    "action": "review_memory",
                    "summary": f"{len(promotions)} memory candidate(s) look promotable",
                    "target": None,
                    "evidence": [str(item["candidate_id"]) for item in promotions[:limit]],
                }
            )

        status = "ok"
        if any(item["severity"] == "blocker" for item in actions):
            status = "fail"
        elif actions:
            status = "warn"
        return {
            "status": status,
            "checked_at": datetime.now(UTC).isoformat(),
            "window_days": max(1, int(window_days)),
            "failure_min_count": max(1, int(failure_min_count)),
            "failure_patterns": patterns,
            "stale_workers": [_serialize(asdict(worker)) for worker in stale_workers[:limit]],
            "promotion_suggestions": promotions,
            "actions": actions,
        }

    def lesson_stats(self) -> dict[str, Any]:
        """Lesson-quality metrics for the operator (``alfred brain stats``).

        Cheap COUNT(*) rollups over the candidate ledger plus two derived rates.
        All counts come from a single store call so this is safe to poll:

          * ``states``: candidate counts by review state
            (candidate / validated / rejected / retired);
          * ``auto_promote_acceptance_rate``: of the candidates an auto-run has
            DECIDED, the fraction it saved. A judge decision is one of: saved
            (auto-validated), hard-rejected (auto-rejected), or HELD for a human
            (a duplicate, or scored below the bar) -- held rows are the judge's
            usual rejection, so they count as decided. ``None`` when the
            auto-promoter has decided nothing yet (no divide-by-zero);
          * ``judge_rejection_rate``: of those same auto-decided candidates, the
            fraction the judge/gate did NOT save (auto-rejected plus held).
            ``None`` when none decided;
          * ``held_for_review``: still-pending candidates an auto-run set aside
            for a human (also counted as judge rejections above).

        Recall hit counts are intentionally omitted: the ledger does not record
        per-recall hits, so surfacing them would need a new write path on the hot
        recall loop. The field is reserved (``recall_hits: None``) so the shape
        can grow without a breaking change.
        """
        raw = self.store.memory_candidate_stats()
        auto_validated = int(raw.get("auto_validated", 0))
        auto_rejected = int(raw.get("auto_rejected", 0))
        held = int(raw.get("held", 0))
        # The judge rejects almost entirely by HOLDING (a duplicate, or a lesson
        # below the confidence bar, is set aside for a human rather than stored
        # as status='rejected'). Count held rows as auto-decided rejections so
        # judge_rejection_rate reflects real judge behavior instead of staying
        # near zero.
        auto_rejections = auto_rejected + held
        auto_decided = auto_validated + auto_rejections
        acceptance: float | None = None
        rejection: float | None = None
        if auto_decided:
            acceptance = round(auto_validated / auto_decided, 4)
            rejection = round(auto_rejections / auto_decided, 4)
        return {
            "total": int(raw.get("total", 0)),
            "states": {
                "candidate": int(raw.get("candidate", 0)),
                "validated": int(raw.get("validated", 0)),
                "rejected": int(raw.get("rejected", 0)),
                "retired": int(raw.get("retired", 0)),
            },
            "auto_validated": auto_validated,
            "auto_rejected": auto_rejected,
            "auto_decided": auto_decided,
            "auto_promote_acceptance_rate": acceptance,
            "judge_rejection_rate": rejection,
            "held_for_review": int(raw.get("held", 0)),
            "recall_hits": None,
        }

    def health(self) -> dict[str, Any]:
        """Return a cheap liveness check for local API callers.

        ``doctor`` is the deeper operational report and can legitimately
        return warnings for a fresh install with no GitHub poll data or seed
        memories yet. ``health`` only answers whether the local ledger is
        reachable and schema-backed, which is what the native client's memory
        API needs before listing empty candidates/lessons on first run.
        """
        return {
            "ok": True,
            "status": "ok",
            "checked_at": datetime.now(UTC).isoformat(),
            "stats": self.stats(),
        }

    def doctor(self) -> dict[str, Any]:
        """Return a read-only health report for the memory store."""
        from .schema import SCHEMA_VERSION

        stats = self.stats()
        checks: list[dict[str, str]] = []

        def check(name: str, status: str, detail: str) -> None:
            checks.append({"name": name, "status": status, "detail": detail})

        check("schema", "ok", f"expected schema v{SCHEMA_VERSION}")
        open_candidates = stats.get("memory_candidates_open", 0)
        if open_candidates > 100:
            check("candidate_backlog", "fail", f"{open_candidates} candidates need review")
        elif open_candidates > 20:
            check("candidate_backlog", "warn", f"{open_candidates} candidates need review")
        else:
            check("candidate_backlog", "ok", f"{open_candidates} open candidates")

        recent_failures = self.list_failures(limit=20)
        blocker_failures = [F for F in recent_failures if F.severity == "blocker"]
        if blocker_failures:
            check("recent_failures", "fail", f"{len(blocker_failures)} blocker failure(s)")
        elif recent_failures:
            check("recent_failures", "warn", f"{len(recent_failures)} recorded failure(s)")
        else:
            check("recent_failures", "ok", "no recorded failures")

        stale_workers = self.list_stale_workers(max_age_minutes=60)
        if stale_workers:
            check("stale_workers", "warn", f"{len(stale_workers)} running worker(s) look stale")
        else:
            check("stale_workers", "ok", f"{stats.get('workers_running', 0)} running worker(s)")

        github_items = stats.get("github_items", 0)
        if github_items:
            check("github_poll", "ok", f"{github_items} cached GitHub issue/PR item(s)")
        else:
            check("github_poll", "warn", "no cached GitHub poll data yet")

        bundle_items = stats.get("bundle_items", 0)
        check("bundles", "ok", f"{bundle_items} cached bundle item(s)")

        suggestions = self.suggest_memory_promotions(limit=5)
        if suggestions:
            check("promotion_loop", "warn", f"{len(suggestions)} candidate(s) look promotable")
        else:
            check("promotion_loop", "ok", "no high-confidence candidates waiting")

        patterns = self.list_failure_patterns(limit=5)
        blocker_patterns = [p for p in patterns if p["severity"] == "blocker"]
        if blocker_patterns:
            check(
                "reliability_governor",
                "fail",
                f"{len(blocker_patterns)} repeated blocker failure pattern(s)",
            )
        elif patterns:
            check("reliability_governor", "warn", f"{len(patterns)} repeated pattern(s)")
        else:
            check("reliability_governor", "ok", "no repeated failure patterns")

        if stats.get("lessons", 0) == 0 and open_candidates == 0:
            check("recall_seed", "warn", "no trusted lessons or candidates yet")
        else:
            check("recall_seed", "ok", "memory has seed data")

        status = "ok"
        if any(c["status"] == "fail" for c in checks):
            status = "fail"
        elif any(c["status"] == "warn" for c in checks):
            status = "warn"
        return {
            "status": status,
            "checked_at": datetime.now(UTC).isoformat(),
            "stats": stats,
            "checks": checks,
        }
