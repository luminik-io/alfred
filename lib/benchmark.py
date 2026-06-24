"""Reproducible benchmark harness for the autonomous fleet.

This module answers one operator question honestly: *is the fleet getting
better or worse at shipping code, and at what cost to my subscription
quota?* It does that by HARNESSING TELEMETRY THE FLEET ALREADY CAPTURES,
never by adding new instrumentation or fabricating numbers.

The four metric families, and where each number comes from:

* **Throughput** - PRs opened, time-to-first-PR. Read from the typed
  per-firing event log (``state/<codename>/events/<firing_id>.jsonl``):
  ``firing_started`` and ``pr_opened`` events carry the timestamps.
* **Quality** - merge rate, CI-pass-first-try proxy, review findings per
  PR, a human-edit-before-merge proxy. Read from the event log
  (``pr_opened`` / ``review_posted`` / ``checks_done`` / ``fix_pushed``).
* **Reliability** - firing success rate, fallback rate, self-heal
  (retry) rate, loop incidents. Read from the spend ledger
  (``successes_today`` / ``failures_today``) and the event log
  (``llm_fallback`` / ``error_loop_detected`` / ``branch_pushed`` after a
  retry).
* **Efficiency** - tokens per task (in / out / cache), cache-hit rate.
  Read from each assistant turn's ``message.usage`` block inside the
  stream-JSON transcript (the same field ``server/usage.py`` reads for
  the live dashboard), plus turns and cost from the spend ledger.

A benchmark *run* fires a FIXED task suite (see :data:`DEFAULT_SUITE`)
against a seed repo. The fleet's normal runner does the work and writes
its normal telemetry; this harness only DEFINES the suite and READS the
result back. That separation is what keeps the harness deterministic and
offline-testable: the reader has no LLM dependency at all, and the suite
definition is plain data.

Design contract:

* Pure stdlib. No dependency on ``agent_runner`` so it imports on a host
  that has not deployed the full runtime (same rule as ``transcripts``
  and ``metrics``).
* Tolerant reads. A missing file, a torn JSONL tail, an unparseable
  timestamp, or a firing with no PR is skipped, never raised. The
  operator runs this to learn *what shipped*, not to validate disk
  layout.
* Config-driven. The seed repo, the suite file, the state dir, and the
  quota plan are all overridable; nothing is hard-coded to one machine.
* Honest framing. Cost is expressed as a share of the operator's
  subscription quota (turns burned vs. plan budget), NOT as a
  dollar-per-PR figure, because subscription-backed Claude Code does not
  bill per token. See :func:`quota_cost_for_report`.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Top-level state-dir entries that are infrastructure trees, not agent
# codenames, so auto-discovery skips them. These names are RESERVED: an
# operator must not name an agent any of these, or its event log is invisible
# to the harness. Documented in docs/BENCHMARKS.md. Pass an explicit
# ``--codename`` to scan a directory regardless of this list.
RESERVED_CODENAMES = frozenset({"transcripts", "codex", "fleet", "engines"})


# --------------------------------------------------------------------------
# Fixed task suite
#
# The suite is the reproducible part: the SAME representative coding tasks,
# run against the SAME seed repo, every time. A run is comparable to a
# previous run only because the suite did not move. The bodies below are
# deliberately small and self-contained so a single firing can finish one.
# `acme-org/your-repo` is a placeholder seed; an operator points the
# harness at their own seed repo via `--seed-repo`.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class BenchmarkTask:
    """One fixed, representative coding task in the suite.

    ``task_id`` is the stable key used to correlate a firing back to the
    task it was meant to do (matched against the issue title / branch the
    runner used). ``kind`` groups tasks into the families an engineering
    team recognises. ``expect_pr`` records whether a healthy run of this
    task should end in an opened PR, so a run that produces none can be
    flagged honestly rather than scored as a silent pass.
    """

    task_id: str
    kind: str
    title: str
    description: str
    expect_pr: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# A small, representative suite. Each entry is the kind of bounded change a
# real engineering team hands a junior engineer or an agent: a focused fix,
# a small additive feature, a mechanical refactor, a test, a doc touch.
DEFAULT_SUITE: tuple[BenchmarkTask, ...] = (
    BenchmarkTask(
        task_id="fix-flaky-test",
        kind="fix",
        title="Fix a flaky test",
        description=(
            "A test fails intermittently because it depends on wall-clock "
            "ordering. Make it deterministic without weakening the assertion."
        ),
    ),
    BenchmarkTask(
        task_id="add-small-endpoint",
        kind="feature",
        title="Add a small read-only endpoint",
        description=(
            "Add a single GET endpoint that returns a health/status payload, "
            "with a handler, a route registration, and one unit test."
        ),
    ),
    BenchmarkTask(
        task_id="refactor-function",
        kind="refactor",
        title="Refactor a long function",
        description=(
            "Split one overly long function into two named helpers. Behaviour "
            "must not change; the existing tests must still pass."
        ),
    ),
    BenchmarkTask(
        task_id="add-unit-test",
        kind="test",
        title="Add a missing unit test",
        description=(
            "Add a unit test for an existing un-covered branch. No production "
            "code change beyond what the test needs."
        ),
    ),
    BenchmarkTask(
        task_id="tighten-validation",
        kind="fix",
        title="Tighten input validation",
        description=(
            "Reject an obviously invalid input that currently slips through, "
            "and cover the rejection with a test."
        ),
    ),
)


def load_suite(path: Path | None) -> tuple[BenchmarkTask, ...]:
    """Load the task suite from a JSON file, or return :data:`DEFAULT_SUITE`.

    The file is a JSON list of objects with the :class:`BenchmarkTask`
    fields. Unknown keys are ignored and missing optional keys take their
    default, so a hand-written suite file does not have to be exhaustive.
    A missing or unreadable file falls back to the built-in suite rather
    than raising: the harness should always have *a* suite to report
    against.
    """
    if path is None:
        return DEFAULT_SUITE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("could not load suite %s (%s); using default suite", path, exc)
        return DEFAULT_SUITE
    if not isinstance(raw, list):
        logger.warning("suite file %s is not a JSON list; using default suite", path)
        return DEFAULT_SUITE
    tasks: list[BenchmarkTask] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("task_id") or "").strip()
        if not task_id:
            continue
        tasks.append(
            BenchmarkTask(
                task_id=task_id,
                kind=str(item.get("kind") or "other"),
                title=str(item.get("title") or task_id),
                description=str(item.get("description") or ""),
                expect_pr=bool(item.get("expect_pr", True)),
            )
        )
    return tuple(tasks) if tasks else DEFAULT_SUITE


# --------------------------------------------------------------------------
# Per-transcript token usage extraction
#
# Each assistant turn in a `claude -p --output-format stream-json`
# transcript carries a `message.usage` object. This is the SAME field the
# live dashboard reads in `server/usage.py`; we sum it per firing so the
# efficiency family reports real tokens, not estimates.
# --------------------------------------------------------------------------


@dataclass
class TokenUsage:
    """Summed token counters for one firing (or a roll-up of firings)."""

    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0
    cache_creation: int = 0

    @property
    def cache_hit_rate(self) -> float:
        """Cached input tokens as a fraction of all input-side tokens.

        Denominator is fresh input + cache-creation + cache-read, i.e.
        every input-side token the model was billed-or-cached for. Zero
        when there was no input-side traffic at all, so an empty firing
        reports 0.0 rather than dividing by zero.
        """
        denom = self.tokens_in + self.cache_creation + self.cache_read
        if denom <= 0:
            return 0.0
        return self.cache_read / denom

    def add(self, other: TokenUsage) -> None:
        self.tokens_in += other.tokens_in
        self.tokens_out += other.tokens_out
        self.cache_read += other.cache_read
        self.cache_creation += other.cache_creation

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["cache_hit_rate"] = round(self.cache_hit_rate, 4)
        return data


def _coerce_int(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def extract_token_usage(text: str) -> TokenUsage:
    """Sum ``message.usage`` token counters across a stream-JSON transcript.

    Reads the four counters Claude Code emits per assistant turn:
    ``input_tokens``, ``output_tokens``, ``cache_creation_input_tokens``,
    ``cache_read_input_tokens``. Torn or non-JSON lines are skipped. A
    transcript with no usage blocks (older runtime, or a dry-run synthetic
    result) sums to an all-zero :class:`TokenUsage`, which is honest: we
    did not observe any token traffic.
    """
    usage = TokenUsage()
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        message = obj.get("message")
        if not isinstance(message, dict):
            continue
        block = message.get("usage")
        if not isinstance(block, dict):
            continue
        usage.tokens_in += _coerce_int(block.get("input_tokens"))
        usage.tokens_out += _coerce_int(block.get("output_tokens"))
        usage.cache_creation += _coerce_int(block.get("cache_creation_input_tokens"))
        usage.cache_read += _coerce_int(block.get("cache_read_input_tokens"))
    return usage


# --------------------------------------------------------------------------
# Per-firing event-log reading
#
# The typed event JSONL log (`state/<codename>/events/<firing_id>.jsonl`)
# is the throughput/quality/reliability spine. We read the small set of
# event types the four families need and tolerate everything else.
# --------------------------------------------------------------------------

_PR_OPENED = "pr_opened"
_FIRING_STARTED = "firing_started"
_LLM_FALLBACK = "llm_fallback"
_LOOP_DETECTED = "error_loop_detected"
_REVIEW_POSTED = "review_posted"
_FIX_PUSHED = "fix_pushed"
_CHECKS_DONE = "checks_done"
_BRANCH_PUSHED = "branch_pushed"


@dataclass
class FiringObservation:
    """One firing distilled to the signals the benchmark families need.

    Every field is derived from telemetry already on disk: the event log
    for the booleans/timestamps, the transcript for tokens. Nothing here
    is estimated.
    """

    firing_id: str
    codename: str
    started_at: datetime | None = None
    pr_opened_at: datetime | None = None
    opened_pr: bool = False
    had_fallback: bool = False
    loop_incident: bool = False
    review_findings: int = 0
    fix_pushes: int = 0
    checks_done: bool = False
    tokens: TokenUsage = field(default_factory=TokenUsage)

    @property
    def time_to_pr_seconds(self) -> float | None:
        """Wall-clock seconds from firing start to the first PR, or ``None``.

        ``None`` when either timestamp is missing or the PR landed before
        the recorded start (clock skew / torn log); a negative span is not
        a meaningful time-to-first-PR, so we drop it rather than report a
        nonsense figure.
        """
        if self.started_at is None or self.pr_opened_at is None:
            return None
        delta = (self.pr_opened_at - self.started_at).total_seconds()
        return delta if delta >= 0 else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "firing_id": self.firing_id,
            "codename": self.codename,
            "opened_pr": self.opened_pr,
            "time_to_pr_seconds": self.time_to_pr_seconds,
            "had_fallback": self.had_fallback,
            "loop_incident": self.loop_incident,
            "review_findings": self.review_findings,
            "fix_pushes": self.fix_pushes,
            "checks_done": self.checks_done,
            "tokens": self.tokens.to_dict(),
        }


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _event_type(obj: dict[str, Any]) -> str:
    """Resolve an event record's type across the log's field aliases.

    The serialized envelope keeps both a typed ``type`` and a legacy
    top-level ``event`` mirror, and the ``review_posted`` payload nests a
    ``findings`` count. We read ``type`` first, then ``event``.
    """
    return str(obj.get("type") or obj.get("event") or "")


def read_firing_events(path: Path) -> dict[str, Any]:
    """Read one firing's event JSONL into a small signal dict.

    Tolerant: a missing file or torn lines yield whatever was parseable.
    Returns the raw signals; :func:`observe_firing` folds them together
    with the transcript tokens.
    """
    signals: dict[str, Any] = {
        "started_at": None,
        "pr_opened_at": None,
        "opened_pr": False,
        "had_fallback": False,
        "loop_incident": False,
        "review_findings": 0,
        "fix_pushes": 0,
        "checks_done": False,
    }
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return signals
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        etype = _event_type(obj)
        ts = _parse_ts(obj.get("ts"))
        if etype == _FIRING_STARTED:
            if signals["started_at"] is None and ts is not None:
                signals["started_at"] = ts
        elif etype == _PR_OPENED:
            signals["opened_pr"] = True
            if signals["pr_opened_at"] is None and ts is not None:
                signals["pr_opened_at"] = ts
        elif etype == _LLM_FALLBACK:
            signals["had_fallback"] = True
        elif etype == _LOOP_DETECTED:
            signals["loop_incident"] = True
        elif etype == _REVIEW_POSTED:
            findings = obj.get("findings")
            if findings is None:
                payload = obj.get("payload")
                if isinstance(payload, dict):
                    findings = payload.get("findings")
            signals["review_findings"] += max(0, _coerce_int(findings))
        elif etype == _FIX_PUSHED:
            signals["fix_pushes"] += 1
        elif etype == _CHECKS_DONE:
            signals["checks_done"] = True
    return signals


def observe_firing(
    codename: str,
    firing_id: str,
    events_path: Path,
    transcript_path: Path | None,
) -> FiringObservation:
    """Fold one firing's event log + transcript into a :class:`FiringObservation`."""
    signals = read_firing_events(events_path)
    tokens = TokenUsage()
    if transcript_path is not None:
        try:
            tokens = extract_token_usage(
                transcript_path.read_text(encoding="utf-8", errors="replace")
            )
        except OSError:
            tokens = TokenUsage()
    return FiringObservation(
        firing_id=firing_id,
        codename=codename,
        started_at=signals["started_at"],
        pr_opened_at=signals["pr_opened_at"],
        opened_pr=bool(signals["opened_pr"]),
        had_fallback=bool(signals["had_fallback"]),
        loop_incident=bool(signals["loop_incident"]),
        review_findings=int(signals["review_findings"]),
        fix_pushes=int(signals["fix_pushes"]),
        checks_done=bool(signals["checks_done"]),
        tokens=tokens,
    )


# --------------------------------------------------------------------------
# Discovery: events + matching transcripts under a state dir
# --------------------------------------------------------------------------


def _events_dir(state_dir: Path, codename: str) -> Path:
    return state_dir / codename / "events"


def _find_transcript(state_dir: Path, codename: str, firing_id: str) -> Path | None:
    """Locate the transcript matching a firing id under the month dirs.

    Transcripts live at ``transcripts/<codename>/<YYYY-MM>/<firing_id>.jsonl``;
    the event log does not record which month dir, so we scan. Returns the
    first match or ``None`` (a firing can have an event log but no
    transcript, e.g. it failed before any model turn).
    """
    root = state_dir / "transcripts" / codename
    if not root.is_dir():
        return None
    for month_dir in root.iterdir():
        if not month_dir.is_dir():
            continue
        candidate = month_dir / f"{firing_id}.jsonl"
        if candidate.is_file():
            return candidate
    return None


def discover_observations(
    state_dir: Path,
    codenames: list[str] | None = None,
) -> list[FiringObservation]:
    """Read every firing's event log (+ transcript) under ``state_dir``.

    ``codenames`` restricts the scan; ``None`` discovers every codename
    that has an ``events/`` directory. Firings with no event log are not
    observable here and are simply absent from the list.
    """
    if codenames is None:
        codenames = _discover_codenames_with_events(state_dir)
    observations: list[FiringObservation] = []
    for codename in codenames:
        events_dir = _events_dir(state_dir, codename)
        if not events_dir.is_dir():
            continue
        for events_path in sorted(events_dir.glob("*.jsonl")):
            firing_id = events_path.stem
            transcript = _find_transcript(state_dir, codename, firing_id)
            observations.append(observe_firing(codename, firing_id, events_path, transcript))
    return observations


def _discover_codenames_with_events(state_dir: Path) -> list[str]:
    if not state_dir.is_dir():
        return []
    out: list[str] = []
    for entry in state_dir.iterdir():
        if not entry.is_dir() or entry.name.startswith("_"):
            continue
        if entry.name in RESERVED_CODENAMES:
            if (entry / "events").is_dir():
                logger.debug(
                    "skipping reserved codename %r during auto-discovery; "
                    "pass --codename %s to include it",
                    entry.name,
                    entry.name,
                )
            continue
        if (entry / "events").is_dir():
            out.append(entry.name)
    return sorted(out)


# --------------------------------------------------------------------------
# Spend roll-up (reused from the same files `metrics.py` reads)
# --------------------------------------------------------------------------


@dataclass
class SpendRollup:
    """Roll-up of per-day spend files across the observed codenames."""

    firings: int = 0
    successes: int = 0
    failures: int = 0
    turns: int = 0
    cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def rollup_spend(state_dir: Path, codenames: list[str]) -> SpendRollup:
    """Sum every ``spend-*.json`` file for the given codenames.

    No date window: a benchmark run is a discrete event, so the operator
    points the harness at a state dir captured for that run (or filters by
    codename). Tolerant of missing dirs and unparseable files.
    """
    total = SpendRollup()
    for codename in codenames:
        agent_root = state_dir / codename
        if not agent_root.is_dir():
            continue
        for path in agent_root.glob("spend-*.json"):
            try:
                data = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            total.firings += _coerce_int(data.get("firings_today"))
            total.successes += _coerce_int(data.get("successes_today"))
            total.failures += _coerce_int(data.get("failures_today"))
            total.turns += _coerce_int(data.get("turns_today"))
            with contextlib.suppress(TypeError, ValueError):
                total.cost_usd += float(data.get("cost_usd_today") or 0.0)
    return total


# --------------------------------------------------------------------------
# The four metric families
# --------------------------------------------------------------------------


@dataclass
class ThroughputMetrics:
    prs_opened: int = 0
    firings: int = 0
    time_to_first_pr_seconds: float | None = None
    median_time_to_pr_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class QualityMetrics:
    prs_opened: int = 0
    prs_merged: int = 0
    merge_rate: float = 0.0
    ci_pass_first_try_rate: float = 0.0
    human_edit_before_merge_rate: float = 0.0
    review_findings_per_pr: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReliabilityMetrics:
    completed_firings: int = 0
    success_rate: float = 0.0
    fallback_rate: float = 0.0
    self_heal_rate: float = 0.0
    loop_incidents: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EfficiencyMetrics:
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    cache_hit_rate: float = 0.0
    turns: int = 0
    prs_opened: int = 0
    tokens_in_per_pr: float | None = None
    tokens_out_per_pr: float | None = None
    turns_per_pr: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


@dataclass
class BenchmarkReport:
    """The full self-benchmark snapshot.

    ``prs_merged`` is supplied by the caller (the merge state lives in the
    fleet brain / GitHub, not the per-firing event log), so the harness
    reads what it can observe locally and accepts the merge count as an
    input. ``label`` lets an operator tag a run ("before", "after",
    "v0.5.0") for honest before/after comparison.
    """

    label: str
    generated_at: datetime
    suite: tuple[BenchmarkTask, ...]
    throughput: ThroughputMetrics
    quality: QualityMetrics
    reliability: ReliabilityMetrics
    efficiency: EfficiencyMetrics
    spend: SpendRollup
    observations: list[FiringObservation]

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "generated_at": self.generated_at.isoformat(),
            "suite": [t.to_dict() for t in self.suite],
            "throughput": self.throughput.to_dict(),
            "quality": self.quality.to_dict(),
            "reliability": self.reliability.to_dict(),
            "efficiency": self.efficiency.to_dict(),
            "spend": self.spend.to_dict(),
            "observations": [o.to_dict() for o in self.observations],
        }


def build_report(
    observations: list[FiringObservation],
    spend: SpendRollup,
    *,
    label: str = "run",
    prs_merged: int = 0,
    suite: tuple[BenchmarkTask, ...] = DEFAULT_SUITE,
    now: datetime | None = None,
) -> BenchmarkReport:
    """Fold observations + spend into the four metric families.

    Every rate has an explicit, non-fabricated denominator and degrades to
    0.0 (or ``None`` for a missing time) when there is nothing to divide
    by, so an empty run reports honest zeros rather than NaN.

    Mapping, with the proxy each metric uses spelled out:

      throughput.prs_opened        count of firings that emitted pr_opened
      throughput.time_to_first_pr  the smallest observed start->pr span
      throughput.median_time_to_pr median of all observed spans
      quality.merge_rate           prs_merged / prs_opened (caller-supplied
                                   merged count; merge state is not local)
      quality.ci_pass_first_try    PRs whose firing reached checks_done with
                                   NO fix_pushed after the PR opened, over
                                   all PRs. Proxy: a clean first CI run is a
                                   firing that opened a PR, ran checks, and
                                   did not have to push a follow-up fix.
      quality.human_edit_before_merge a proxy: PRs that needed a fix_pushed
                                   (a post-open follow-up commit) before
                                   landing, over all PRs. Stands in for "a
                                   human had to edit before merge".
      quality.review_findings_per_pr total review findings / prs_opened.
      reliability.success_rate     successes / (successes + failures), the
                                   same completed-firing denominator the
                                   metrics CLI uses.
      reliability.fallback_rate    firings with an llm_fallback / firings.
      reliability.self_heal_rate   firings that pushed a branch AFTER a
                                   fallback or a recorded retry, over
                                   firings that had a fallback/loop signal.
                                   Proxy for "recovered without human help".
      reliability.loop_incidents   count of error_loop_detected events.
      efficiency.*                 summed message.usage tokens + ledger turns,
                                   divided by prs_opened for per-PR figures.
    """
    moment = now or datetime.now(UTC)

    firings_observed = len(observations)
    prs = [o for o in observations if o.opened_pr]
    prs_opened = len(prs)

    # Throughput -------------------------------------------------------
    spans = [s for s in (o.time_to_pr_seconds for o in prs) if s is not None]
    throughput = ThroughputMetrics(
        prs_opened=prs_opened,
        firings=firings_observed,
        time_to_first_pr_seconds=min(spans) if spans else None,
        median_time_to_pr_seconds=_median(spans),
    )

    # Quality ----------------------------------------------------------
    merged = max(0, min(prs_merged, prs_opened))
    clean_ci = sum(1 for o in prs if o.checks_done and o.fix_pushes == 0)
    needed_edit = sum(1 for o in prs if o.fix_pushes > 0)
    total_findings = sum(o.review_findings for o in prs)
    quality = QualityMetrics(
        prs_opened=prs_opened,
        prs_merged=merged,
        merge_rate=(merged / prs_opened) if prs_opened else 0.0,
        ci_pass_first_try_rate=(clean_ci / prs_opened) if prs_opened else 0.0,
        human_edit_before_merge_rate=(needed_edit / prs_opened) if prs_opened else 0.0,
        review_findings_per_pr=(total_findings / prs_opened) if prs_opened else 0.0,
    )

    # Reliability ------------------------------------------------------
    completed = spend.successes + spend.failures
    fallback_firings = sum(1 for o in observations if o.had_fallback)
    recoverable = [o for o in observations if o.had_fallback or o.loop_incident]
    healed = sum(1 for o in recoverable if o.opened_pr)
    reliability = ReliabilityMetrics(
        completed_firings=completed,
        success_rate=(spend.successes / completed) if completed else 0.0,
        fallback_rate=(fallback_firings / firings_observed) if firings_observed else 0.0,
        self_heal_rate=(healed / len(recoverable)) if recoverable else 0.0,
        loop_incidents=sum(1 for o in observations if o.loop_incident),
    )

    # Efficiency -------------------------------------------------------
    tokens = TokenUsage()
    for o in observations:
        tokens.add(o.tokens)
    efficiency = EfficiencyMetrics(
        tokens_in=tokens.tokens_in,
        tokens_out=tokens.tokens_out,
        cache_read=tokens.cache_read,
        cache_creation=tokens.cache_creation,
        cache_hit_rate=round(tokens.cache_hit_rate, 4),
        turns=spend.turns,
        prs_opened=prs_opened,
        tokens_in_per_pr=(tokens.tokens_in / prs_opened) if prs_opened else None,
        tokens_out_per_pr=(tokens.tokens_out / prs_opened) if prs_opened else None,
        turns_per_pr=(spend.turns / prs_opened) if prs_opened else None,
    )

    return BenchmarkReport(
        label=label,
        generated_at=moment,
        suite=suite,
        throughput=throughput,
        quality=quality,
        reliability=reliability,
        efficiency=efficiency,
        spend=spend,
        observations=observations,
    )


def run_report(
    state_dir: Path,
    *,
    label: str = "run",
    codenames: list[str] | None = None,
    prs_merged: int = 0,
    suite: tuple[BenchmarkTask, ...] = DEFAULT_SUITE,
    now: datetime | None = None,
) -> BenchmarkReport:
    """Top-level reader: discover observations + spend and build the report.

    This is the offline, deterministic entry point. It performs NO LLM
    calls and writes nothing; it only reads the telemetry a fleet run
    already left on disk. That is what lets the harness's own unit tests
    run green against a synthetic state tree with no model and no network.
    """
    observations = discover_observations(state_dir, codenames)
    observed_codenames = codenames or sorted({o.codename for o in observations})
    spend = rollup_spend(state_dir, observed_codenames)
    return build_report(
        observations,
        spend,
        label=label,
        prs_merged=prs_merged,
        suite=suite,
        now=now,
    )


# --------------------------------------------------------------------------
# Subscription-quota cost framing
#
# Cost is reported as a SHARE OF SUBSCRIPTION QUOTA, never $/PR.
# Subscription-backed Claude Code draws from a shared usage pool, not
# per-token API billing (see docs/CLAUDE_CODE.md "Cost model"). The honest
# unit an operator cares about is "what fraction of my Claude Max / Codex
# Pro budget does one PR cost". The turn budgets below reuse the empirical
# turn-burn numbers from docs/CLAUDE_CODE.md and are config-overridable.
# --------------------------------------------------------------------------

# Per-plan daily turn budgets, sized from the empirical turn-burn numbers in
# docs/CLAUDE_CODE.md: a continuous fleet (Lucius alone) averages 2000-3500
# turns/day and exceeds Pro in a day once several codenames fire. These are
# SIZING ESTIMATES for the % framing, not provider billing guarantees;
# Anthropic/OpenAI own the real reset behaviour. Override via env.
DEFAULT_PLAN_DAILY_TURN_BUDGET: dict[str, int] = {
    "claude_pro": 2_000,
    "claude_max_5x": 10_000,
    "claude_max_20x": 40_000,
    "codex_pro": 4_000,
}

_PLAN_BUDGET_ENV_PREFIX = "ALFRED_BENCHMARK_TURN_BUDGET_"


def plan_daily_turn_budgets(
    env: dict[str, str] | None = None,
) -> dict[str, int]:
    """Return the per-plan daily turn budgets, env-overridable.

    For each known plan, ``ALFRED_BENCHMARK_TURN_BUDGET_<PLAN>`` (upper-case,
    e.g. ``ALFRED_BENCHMARK_TURN_BUDGET_CLAUDE_MAX_5X``) overrides the
    default. A non-numeric or non-positive override is ignored so a typo
    cannot zero a budget and divide-by-zero the framing.
    """
    source = env if env is not None else os.environ
    budgets = dict(DEFAULT_PLAN_DAILY_TURN_BUDGET)
    for plan in list(budgets):
        raw = source.get(f"{_PLAN_BUDGET_ENV_PREFIX}{plan.upper()}")
        if raw is None:
            continue
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            continue
        if value > 0:
            budgets[plan] = value
    return budgets


@dataclass
class QuotaCostRow:
    """One plan's quota-cost framing for a benchmark run."""

    plan: str
    daily_turn_budget: int
    turns_per_pr: float | None
    pct_quota_per_pr: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def quota_cost_for_report(
    report: BenchmarkReport,
    *,
    env: dict[str, str] | None = None,
) -> list[QuotaCostRow]:
    """Frame the run's per-PR turn cost as a share of each plan's quota.

    ``pct_quota_per_pr`` is ``turns_per_pr / daily_turn_budget * 100``: the
    fraction of a plan's daily turn budget one merged-or-opened PR consumes.
    ``None`` when the run opened no PR (no per-PR figure to frame). This is
    the SUBSCRIPTION framing the launch strategy asks for: "% of your Claude
    Max / Codex Pro quota per PR", not dollars per PR.
    """
    budgets = plan_daily_turn_budgets(env)
    turns_per_pr = report.efficiency.turns_per_pr
    rows: list[QuotaCostRow] = []
    for plan, budget in budgets.items():
        pct: float | None = None
        if turns_per_pr is not None and budget > 0:
            pct = round(turns_per_pr / budget * 100, 2)
        rows.append(
            QuotaCostRow(
                plan=plan,
                daily_turn_budget=budget,
                turns_per_pr=round(turns_per_pr, 1) if turns_per_pr is not None else None,
                pct_quota_per_pr=pct,
            )
        )
    return rows
