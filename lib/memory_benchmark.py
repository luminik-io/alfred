"""Memory A/B benchmark: does durable memory stop the fleet repeating a mistake?

This is the *coding-fleet memory* benchmark. It answers one question no
chat-recall benchmark (LongMemEval, LoCoMo) asks: **when a repo has already
taught the fleet a lesson, does memory stop the next firing from repeating the
mistake that lesson was about?** The headline metric is the
**repeated-mistake-rate**: over the N suite tasks that each re-tempt a
previously-learned mistake, how many does a memory-OFF run get wrong versus a
memory-ON run.

The design mirrors ``lib/benchmark.py`` and keeps its honesty conventions:

* **A/B pairs only.** The *same* task suite runs twice against the *same*
  seed repo and the *same* seeded lessons: once with memory ON (the operator's
  configured provider, seeded with the lessons the fleet "learned") and once
  with memory OFF (:class:`NullMemoryProvider`). The only variable is memory.
* **Explicit, non-fabricated denominators.** Every rate degrades to ``None``
  (never a guess) when there is nothing to divide by. ``N`` is reported
  alongside the headline, never a solo "memory is X% better".
* **Offline-testable harness.** The scoring core (:func:`build_report`,
  :func:`judge_solution`, retrieval maths) is pure stdlib and unit-tested with
  a fixture plus a deterministic stub solver: *no model runs, no network, no
  quota burns*. A real A/B swaps the stub for :func:`make_cli_engine_solver`,
  which fires an actual engine, without changing the harness or the metrics.

What "solving a task" means here is deliberately narrow and deterministic: a
solver returns candidate solution text; a solution is judged against the task's
declared ``mistake_markers`` (their presence means the known mistake was
repeated) and ``success_markers`` (their presence, with no mistake, means the
task was solved). This marker match is a plain regex, not an LLM judge, so the
score is reproducible. Marker fidelity is the honest limit of the check and is
called out as a caveat in ``docs/BENCHMARKS.md``.

Metrics, each with its denominator spelled out:

* ``repeated_mistake_rate`` (headline) = mistakes repeated / ``N`` eligible
  tasks (tasks flagged ``repeats_known_mistake``). ``None`` when ``N == 0``.
* ``task_success_rate`` = tasks solved / tasks attempted.
* ``tokens`` / ``turns`` = summed engine cost, and per-task figures.
* retrieval ``precision`` / ``recall`` of the *right* lesson = over the tasks
  that have a declared relevant lesson, how much of what memory recalled was
  the relevant lesson (precision) and how much of the relevant set was recalled
  (recall). Measured only on the memory-ON arm's recall step.

Design contract (same rules as ``benchmark`` and ``transcripts``):

* Pure stdlib in the scoring core. ``fleet_brain`` / ``memory`` (both in-tree,
  stdlib-only) are imported lazily by the *seeding* and *default recall*
  helpers, and ``agent_runner`` only by the real-engine solver, so the metric
  code imports and runs on a host that has not deployed the full runtime.
* Tolerant reads. A missing fixture file or a malformed entry is skipped, never
  raised, mirroring the base harness.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from benchmark import TokenUsage, extract_token_usage

if TYPE_CHECKING:
    from fleet_brain import Lesson

logger = logging.getLogger(__name__)

# Arm identifiers. The A/B has exactly these two arms; nothing else may be
# labelled an "arm" so a report's two sides are always this pair.
ARM_ON = "memory_on"
ARM_OFF = "memory_off"

# Codename/repo the fixture lessons are seeded under. A benchmark run fires one
# codename against one seed repo, so recall is scoped to this pair.
DEFAULT_BENCH_CODENAME = "mem-bench"
DEFAULT_BENCH_REPO = "acme-org/widgets"

# Default recall breadth per task. Matches the memory-runtime injection default
# (``format_memory_context(limit=3)``) so the benchmark recalls exactly as many
# lessons as a real firing would inject.
DEFAULT_RECALL_LIMIT = 3


# --------------------------------------------------------------------------
# Provider protocol (structural; the real memory providers satisfy it)
# --------------------------------------------------------------------------


@runtime_checkable
class RecallProvider(Protocol):
    """The slice of ``memory.MemoryProvider`` this benchmark depends on.

    Declared structurally so the scoring core never has to import the concrete
    provider package. ``FleetBrainProvider``, ``NullMemoryProvider`` and the
    chained provider all satisfy it.
    """

    name: str

    def recall(
        self,
        *,
        query: str | None = ...,
        codename: str | None = ...,
        repo: str | None = ...,
        limit: int = ...,
    ) -> list[Lesson]: ...


# --------------------------------------------------------------------------
# Fixture data model
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SeedLesson:
    """One lesson the fleet has already "learned" about the seed repo.

    These are seeded into the memory-ON provider before the A/B so the ON arm
    starts from a repo the fleet has real history with. ``lesson_id`` is the
    stable key the retrieval metric scores against.
    """

    lesson_id: str
    body: str
    tags: tuple[str, ...] = ()
    severity: str = "info"

    def to_dict(self) -> dict[str, Any]:
        return {
            "lesson_id": self.lesson_id,
            "body": self.body,
            "tags": list(self.tags),
            "severity": self.severity,
        }


@dataclass(frozen=True)
class MemTask:
    """One paired task: a bounded coding job that re-tempts a known mistake.

    A task is the memory analogue of :class:`benchmark.BenchmarkTask`. Beyond
    the change it asks for (``prompt``), it declares:

    * ``repeats_known_mistake`` -- whether this task counts toward ``N``, the
      headline denominator. A control task (``False``) still runs and scores
      success/cost but never inflates the repeated-mistake numerator/denominator.
    * ``mistake_markers`` / ``success_markers`` -- regexes matched
      case-insensitively against the solver's output. A mistake marker present
      means the known mistake was repeated; a success marker present with no
      mistake marker means the task was solved.
    * ``relevant_lesson_ids`` -- the seeded lesson(s) that, if recalled, should
      prevent the mistake. The retrieval metric scores recall against this set.
    * ``recall_query`` -- the focused topical query the benchmark issues to the
      memory provider for this task (a real firing derives a similar query from
      the issue). Falls back to title+prompt when empty.
    * ``lesson_signal`` -- a substring of the relevant lesson body. The offline
      stub solver treats "this signal reached the injected context" as "the
      model read the lesson", so the stub reacts to what was actually injected
      rather than to hidden state.
    * ``correct_solution`` / ``mistaken_solution`` -- reference outputs used
      *only* by the offline stub solver. Real engine runs ignore them.
    """

    task_id: str
    kind: str
    title: str
    prompt: str
    mistake_id: str
    relevant_lesson_ids: tuple[str, ...] = ()
    mistake_markers: tuple[str, ...] = ()
    success_markers: tuple[str, ...] = ()
    recall_query: str = ""
    lesson_signal: str = ""
    repeats_known_mistake: bool = True
    correct_solution: str = ""
    mistaken_solution: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value if str(v).strip())
    return ()


def _task_from_dict(item: dict[str, Any]) -> MemTask | None:
    task_id = str(item.get("task_id") or "").strip()
    if not task_id:
        return None
    return MemTask(
        task_id=task_id,
        kind=str(item.get("kind") or "other"),
        title=str(item.get("title") or task_id),
        prompt=str(item.get("prompt") or ""),
        mistake_id=str(item.get("mistake_id") or task_id),
        relevant_lesson_ids=_as_str_tuple(item.get("relevant_lesson_ids")),
        mistake_markers=_as_str_tuple(item.get("mistake_markers")),
        success_markers=_as_str_tuple(item.get("success_markers")),
        recall_query=str(item.get("recall_query") or ""),
        lesson_signal=str(item.get("lesson_signal") or ""),
        repeats_known_mistake=bool(item.get("repeats_known_mistake", True)),
        correct_solution=str(item.get("correct_solution") or ""),
        mistaken_solution=str(item.get("mistaken_solution") or ""),
    )


def _lesson_from_dict(item: dict[str, Any]) -> SeedLesson | None:
    lesson_id = str(item.get("lesson_id") or "").strip()
    if not lesson_id:
        return None
    return SeedLesson(
        lesson_id=lesson_id,
        body=str(item.get("body") or "").strip(),
        tags=_as_str_tuple(item.get("tags")),
        severity=str(item.get("severity") or "info"),
    )


@dataclass(frozen=True)
class Fixture:
    """A loaded mem-bench fixture: paired tasks plus the lessons to seed."""

    tasks: tuple[MemTask, ...]
    lessons: tuple[SeedLesson, ...]
    codename: str = DEFAULT_BENCH_CODENAME
    repo: str = DEFAULT_BENCH_REPO


def _load_json_list(path: Path) -> list[dict[str, Any]]:
    """Read a JSON list of objects, tolerating a missing/garbled file."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("mem-bench: could not read %s (%s)", path, exc)
        return []
    if not isinstance(raw, list):
        logger.warning("mem-bench: %s is not a JSON list", path)
        return []
    return [item for item in raw if isinstance(item, dict)]


def load_fixture(
    fixture_dir: Path,
    *,
    codename: str = DEFAULT_BENCH_CODENAME,
    repo: str = DEFAULT_BENCH_REPO,
) -> Fixture:
    """Load ``tasks.json`` + ``lessons.json`` from a fixture directory.

    Tolerant like the base harness: a missing or malformed entry is skipped.
    Raises :class:`FileNotFoundError` only if the directory itself is absent,
    which is an operator error worth surfacing.
    """
    fixture_dir = Path(fixture_dir)
    if not fixture_dir.is_dir():
        raise FileNotFoundError(f"mem-bench fixture dir not found: {fixture_dir}")
    tasks = tuple(
        t
        for t in (_task_from_dict(i) for i in _load_json_list(fixture_dir / "tasks.json"))
        if t is not None
    )
    lessons = tuple(
        lesson
        for lesson in (_lesson_from_dict(i) for i in _load_json_list(fixture_dir / "lessons.json"))
        if lesson is not None
    )
    return Fixture(tasks=tasks, lessons=lessons, codename=codename, repo=repo)


# --------------------------------------------------------------------------
# Seeding the memory-ON provider
# --------------------------------------------------------------------------


def seed_fleet_provider(
    lessons: Sequence[SeedLesson],
    *,
    codename: str = DEFAULT_BENCH_CODENAME,
    repo: str = DEFAULT_BENCH_REPO,
    base_time: datetime | None = None,
) -> RecallProvider:
    """Return a real in-memory ``FleetBrainProvider`` seeded with ``lessons``.

    Uses an in-memory SQLite brain, so seeding touches no operator disk and no
    network: this is the honest local memory backend, exercised for real. Each
    lesson gets a strictly increasing ``created_at`` (one minute apart) so the
    recency-ordered backfill in local recall is deterministic across runs.

    Lazily imports the in-tree ``fleet_brain`` / ``memory`` modules so the
    scoring core stays import-light; both are stdlib-only.
    """
    from fleet_brain import FleetBrain
    from fleet_brain.store import SQLiteStore
    from memory.providers import FleetBrainProvider

    brain = FleetBrain(store=SQLiteStore(db_path=Path(":memory:")))
    moment = base_time or datetime(2026, 1, 1, tzinfo=UTC)
    for offset, lesson in enumerate(lessons):
        if not lesson.body:
            continue
        # Seed via the brain directly so each lesson keeps its stable fixture id
        # (the provider adapter's reflect() does not take lesson_id). The
        # retrieval metric scores recalled ids against the fixture ids.
        brain.reflect(
            codename=codename,
            repo=repo,
            body=lesson.body,
            tags=list(lesson.tags),
            severity=lesson.severity,  # type: ignore[arg-type]
            lesson_id=lesson.lesson_id,
            created_at=moment + timedelta(minutes=offset),
        )
    return FleetBrainProvider(brain=brain, name="fleet")


def null_provider() -> RecallProvider:
    """Return the memory-OFF provider (recalls nothing, injects nothing)."""
    from memory.providers import NullMemoryProvider

    return NullMemoryProvider()


# --------------------------------------------------------------------------
# Solver contract
# --------------------------------------------------------------------------


@dataclass
class SolveResult:
    """What a solver returns for one task attempt."""

    solution_text: str
    tokens: TokenUsage = field(default_factory=TokenUsage)
    turns: int = 0


# A solver turns (task, injected memory context, arm) into a candidate solution.
# The runner owns recall and context assembly; the solver only "does the work".
Solver = Callable[[MemTask, str, str], SolveResult]

RecallFn = Callable[[RecallProvider, MemTask, str, str, int], "list[Lesson]"]
InjectFn = Callable[[RecallProvider, MemTask, str, str, int], str]


def _default_query(task: MemTask) -> str | None:
    """Focused recall query for a task: its ``recall_query`` or issue-derived."""
    if task.recall_query.strip():
        return task.recall_query.strip()
    try:
        from agent_runner.memory_runtime import issue_memory_query

        return issue_memory_query(task.title, task.prompt)
    except Exception:
        # No runtime available: fall back to the title, still a valid query.
        collapsed = " ".join(task.title.split()).strip()
        return collapsed or None


def default_recall_fn(
    provider: RecallProvider,
    task: MemTask,
    codename: str,
    repo: str,
    limit: int,
) -> list[Lesson]:
    """Recall the candidate lessons for a task from the provider.

    These are the lessons the memory layer surfaced for this task; the
    retrieval metric scores them and the injector formats them. Returns ``[]``
    on any provider error so one flaky backend never breaks a run.
    """
    try:
        return provider.recall(
            query=_default_query(task), codename=codename, repo=repo, limit=limit
        )
    except Exception:
        logger.exception("mem-bench: recall failed for task %s", task.task_id)
        return []


def default_inject_fn(
    provider: RecallProvider,
    task: MemTask,
    codename: str,
    repo: str,
    limit: int,
) -> str:
    """Build the prompt-ready memory block via the real injection primitive.

    Reuses ``agent_runner.memory_runtime.format_memory_context`` -- the exact
    path a live firing uses to inject memory -- so the benchmark measures the
    real mechanism, not a re-implementation. Returns ``""`` when the runtime is
    unavailable or the provider recalls nothing.
    """
    try:
        from agent_runner.memory_runtime import format_memory_context

        return format_memory_context(
            provider, codename=codename, repo=repo, query=_default_query(task), limit=limit
        )
    except Exception:
        logger.exception("mem-bench: inject failed for task %s", task.task_id)
        return ""


# --------------------------------------------------------------------------
# Offline stub solver (deterministic; no model)
# --------------------------------------------------------------------------


def make_stub_solver() -> Solver:
    """A deterministic solver that reacts to the injected memory context.

    It simulates a model that follows a lesson only if the lesson's guidance
    actually reached its prompt: when ``task.lesson_signal`` appears in the
    injected context, it returns the task's ``correct_solution``; otherwise it
    returns the ``mistaken_solution``. That ties the stub to what the *real*
    injection path put in the prompt, so the offline A/B exercises recall +
    injection for real and stubs only the engine. It burns no quota and makes no
    network call.
    """

    def solve(task: MemTask, memory_context: str, arm: str) -> SolveResult:
        signal = task.lesson_signal.strip()
        followed = bool(signal) and signal.lower() in memory_context.lower()
        text = task.correct_solution if followed else task.mistaken_solution
        # A small, fixed synthetic cost so cost metrics have non-zero, arm-equal
        # values to report (the stub is not a cost model; a real run measures
        # true tokens/turns from the transcript).
        return SolveResult(
            solution_text=text,
            tokens=TokenUsage(tokens_in=1000, tokens_out=200),
            turns=5,
        )

    return solve


# --------------------------------------------------------------------------
# Real-engine solver (fires an actual engine; not exercised by unit tests)
# --------------------------------------------------------------------------


def make_cli_engine_solver(
    *,
    engine: str = "claude",
    model: str | None = None,
    cwd: Path | None = None,
    timeout_s: int = 900,
    extra_args: Sequence[str] = (),
) -> Solver:
    """Build a solver that fires a real ``claude``/``codex`` CLI for each task.

    This is the "run it for real" path: it prepends the injected memory context
    to the task prompt, shells the engine in ``--output-format stream-json``,
    captures the final assistant text as the candidate solution, and extracts
    true token usage from the transcript with :func:`benchmark.extract_token_usage`.
    It is intentionally *not* covered by unit tests -- exercising it needs a live
    model and burns real quota -- but it lets ``alfred benchmark memory`` produce
    a genuine memory-ON vs memory-OFF result. Any engine failure yields an empty
    solution rather than raising, so one bad task does not abort the run.
    """

    def solve(task: MemTask, memory_context: str, arm: str) -> SolveResult:
        prompt = f"{memory_context}\n\n{task.prompt}".strip() if memory_context else task.prompt
        cmd = [engine, "-p", prompt, "--output-format", "stream-json", "--verbose"]
        if model:
            cmd += ["--model", model]
        cmd += list(extra_args)
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(cwd) if cwd else None,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            logger.exception("mem-bench: engine %s failed for task %s", engine, task.task_id)
            return SolveResult(solution_text="")
        stdout = proc.stdout or ""
        return SolveResult(
            solution_text=_final_assistant_text(stdout),
            tokens=extract_token_usage(stdout),
            turns=_num_turns(stdout),
        )

    return solve


def _final_assistant_text(stream_json: str) -> str:
    """Concatenate assistant text from a stream-JSON transcript.

    Reads the ``result`` line's ``result`` field when present (the engine's
    final answer), else joins assistant text blocks. Torn lines are skipped.
    """
    result_text = ""
    assistant_chunks: list[str] = []
    for raw in stream_json.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("type") == "result" and isinstance(obj.get("result"), str):
            result_text = obj["result"]
        message = obj.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and isinstance(block.get("text"), str):
                        assistant_chunks.append(block["text"])
    return result_text or "\n".join(assistant_chunks)


def _num_turns(stream_json: str) -> int:
    for raw in reversed(stream_json.splitlines()):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict) and obj.get("type") == "result":
            try:
                return int(obj.get("num_turns") or 0)
            except (TypeError, ValueError):
                return 0
    return 0


# --------------------------------------------------------------------------
# Judging one solution (deterministic marker match)
# --------------------------------------------------------------------------


def _matches_any(patterns: Sequence[str], text: str) -> bool:
    for pat in patterns:
        if not pat:
            continue
        try:
            if re.search(pat, text, re.IGNORECASE):
                return True
        except re.error:
            # A malformed marker falls back to a literal substring test rather
            # than crashing the whole run.
            if pat.lower() in text.lower():
                return True
    return False


def judge_solution(task: MemTask, solution_text: str) -> tuple[bool, bool]:
    """Return ``(made_mistake, succeeded)`` for one solution.

    ``made_mistake`` is ``True`` when any ``mistake_marker`` matches the output.
    ``succeeded`` is ``True`` only when a ``success_marker`` matches *and* no
    mistake marker did -- a solution that both fixes the intent and re-commits
    the known mistake is not a success. Both are plain regex matches, so the
    verdict is reproducible; there is no model in the loop.
    """
    made_mistake = _matches_any(task.mistake_markers, solution_text)
    hit_success = _matches_any(task.success_markers, solution_text)
    succeeded = hit_success and not made_mistake
    return made_mistake, succeeded


# --------------------------------------------------------------------------
# Attempts + the runner
# --------------------------------------------------------------------------


@dataclass
class TaskAttempt:
    """One task solved under one arm, distilled to what the metrics need."""

    task_id: str
    arm: str
    made_mistake: bool
    succeeded: bool
    recalled_lesson_ids: tuple[str, ...]
    turns: int
    tokens: TokenUsage = field(default_factory=TokenUsage)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "arm": self.arm,
            "made_mistake": self.made_mistake,
            "succeeded": self.succeeded,
            "recalled_lesson_ids": list(self.recalled_lesson_ids),
            "turns": self.turns,
            "tokens": self.tokens.to_dict(),
        }


def run_arm(
    suite: Sequence[MemTask],
    *,
    arm: str,
    provider: RecallProvider,
    solver: Solver,
    codename: str = DEFAULT_BENCH_CODENAME,
    repo: str = DEFAULT_BENCH_REPO,
    limit: int = DEFAULT_RECALL_LIMIT,
    recall_fn: RecallFn | None = None,
    inject_fn: InjectFn | None = None,
) -> list[TaskAttempt]:
    """Run one arm of the A/B: recall + inject + solve + judge, per task.

    On the ``memory_on`` arm, lessons are recalled and the injected context is
    built from the *real* injection primitive. On ``memory_off`` no recall or
    injection happens at all (empty context, no recalled ids), so the arm is a
    genuine no-memory control rather than memory-with-an-empty-store.
    """
    _recall = recall_fn or default_recall_fn
    _inject = inject_fn or default_inject_fn
    attempts: list[TaskAttempt] = []
    for task in suite:
        if arm == ARM_ON:
            lessons = _recall(provider, task, codename, repo, limit)
            recalled_ids = tuple(
                str(getattr(le, "id", "")) for le in lessons if getattr(le, "id", "")
            )
            context = _inject(provider, task, codename, repo, limit)
        else:
            recalled_ids = ()
            context = ""
        result = solver(task, context, arm)
        made_mistake, succeeded = judge_solution(task, result.solution_text)
        attempts.append(
            TaskAttempt(
                task_id=task.task_id,
                arm=arm,
                made_mistake=made_mistake,
                succeeded=succeeded,
                recalled_lesson_ids=recalled_ids,
                turns=result.turns,
                tokens=result.tokens,
            )
        )
    return attempts


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


@dataclass
class RetrievalMetrics:
    """Retrieval precision/recall of the *right* lesson, over tasks that have one.

    Denominators are explicit and coherent -- both are summed only over the
    tasks that declare a relevant lesson:

    * ``recall`` = relevant lessons recalled / relevant lessons total. ``None``
      only when no task declares a relevant lesson (nothing to recall).
    * ``precision`` = relevant lessons recalled / all lessons recalled for those
      tasks. ``None`` when nothing was recalled at all (memory-OFF, or an empty
      store), because there is no retrieved set to be precise about.
    """

    tasks_with_relevant: int = 0
    relevant_total: int = 0
    recalled_total: int = 0
    recalled_relevant: int = 0
    precision: float | None = None
    recall: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MemoryArmMetrics:
    """One arm's metrics. Every rate carries the ``N`` it was divided by."""

    arm: str
    tasks: int = 0
    mistake_eligible: int = 0
    mistakes_repeated: int = 0
    repeated_mistake_rate: float | None = None
    succeeded: int = 0
    task_success_rate: float | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    turns: int = 0
    tokens_in_per_task: float | None = None
    turns_per_task: float | None = None
    retrieval: RetrievalMetrics = field(default_factory=RetrievalMetrics)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data


def _retrieval_metrics(
    attempts: Sequence[TaskAttempt],
    tasks_by_id: dict[str, MemTask],
) -> RetrievalMetrics:
    tasks_with_relevant = 0
    relevant_total = 0
    recalled_total = 0
    recalled_relevant = 0
    for att in attempts:
        task = tasks_by_id.get(att.task_id)
        if task is None:
            continue
        relevant = set(task.relevant_lesson_ids)
        if not relevant:
            continue
        recalled = set(att.recalled_lesson_ids)
        tasks_with_relevant += 1
        relevant_total += len(relevant)
        recalled_total += len(recalled)
        recalled_relevant += len(relevant & recalled)
    recall = (recalled_relevant / relevant_total) if relevant_total else None
    precision = (recalled_relevant / recalled_total) if recalled_total else None
    return RetrievalMetrics(
        tasks_with_relevant=tasks_with_relevant,
        relevant_total=relevant_total,
        recalled_total=recalled_total,
        recalled_relevant=recalled_relevant,
        precision=precision,
        recall=recall,
    )


def build_arm_metrics(
    arm: str,
    attempts: Sequence[TaskAttempt],
    suite: Sequence[MemTask],
) -> MemoryArmMetrics:
    """Fold one arm's attempts into its metrics, with explicit denominators."""
    tasks_by_id = {t.task_id: t for t in suite}
    tasks = len(attempts)
    eligible = [
        a
        for a in attempts
        if (t := tasks_by_id.get(a.task_id)) is not None and t.repeats_known_mistake
    ]
    mistake_eligible = len(eligible)
    mistakes_repeated = sum(1 for a in eligible if a.made_mistake)
    succeeded = sum(1 for a in attempts if a.succeeded)
    tokens = TokenUsage()
    turns = 0
    for a in attempts:
        tokens.add(a.tokens)
        turns += a.turns
    return MemoryArmMetrics(
        arm=arm,
        tasks=tasks,
        mistake_eligible=mistake_eligible,
        mistakes_repeated=mistakes_repeated,
        repeated_mistake_rate=(
            (mistakes_repeated / mistake_eligible) if mistake_eligible else None
        ),
        succeeded=succeeded,
        task_success_rate=((succeeded / tasks) if tasks else None),
        tokens_in=tokens.tokens_in,
        tokens_out=tokens.tokens_out,
        turns=turns,
        tokens_in_per_task=(round(tokens.tokens_in / tasks, 1) if tasks else None),
        turns_per_task=(round(turns / tasks, 2) if tasks else None),
        retrieval=_retrieval_metrics(attempts, tasks_by_id),
    )


@dataclass
class MemoryABReport:
    """The full memory A/B snapshot: both arms, the delta, and every attempt."""

    label: str
    generated_at: datetime
    codename: str
    repo: str
    suite: tuple[MemTask, ...]
    memory_on: MemoryArmMetrics
    memory_off: MemoryArmMetrics
    attempts: list[TaskAttempt]
    solver_kind: str = "stub"

    @property
    def repeated_mistake_rate_delta(self) -> float | None:
        """``off - on``: how much memory reduced the repeated-mistake-rate.

        Positive means memory-ON repeated fewer known mistakes (memory helped);
        ``None`` when either arm has no eligible tasks to rate.
        """
        on = self.memory_on.repeated_mistake_rate
        off = self.memory_off.repeated_mistake_rate
        if on is None or off is None:
            return None
        return round(off - on, 4)

    @property
    def success_rate_delta(self) -> float | None:
        on = self.memory_on.task_success_rate
        off = self.memory_off.task_success_rate
        if on is None or off is None:
            return None
        return round(on - off, 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "generated_at": self.generated_at.isoformat(),
            "codename": self.codename,
            "repo": self.repo,
            "solver_kind": self.solver_kind,
            "suite": [t.to_dict() for t in self.suite],
            "memory_on": self.memory_on.to_dict(),
            "memory_off": self.memory_off.to_dict(),
            "delta": {
                "repeated_mistake_rate": self.repeated_mistake_rate_delta,
                "task_success_rate": self.success_rate_delta,
            },
            "attempts": [a.to_dict() for a in self.attempts],
        }


def build_report(
    suite: Sequence[MemTask],
    on_attempts: Sequence[TaskAttempt],
    off_attempts: Sequence[TaskAttempt],
    *,
    label: str = "run",
    codename: str = DEFAULT_BENCH_CODENAME,
    repo: str = DEFAULT_BENCH_REPO,
    solver_kind: str = "stub",
    now: datetime | None = None,
) -> MemoryABReport:
    """Fold the two arms' attempts into a full A/B report."""
    return MemoryABReport(
        label=label,
        generated_at=now or datetime.now(UTC),
        codename=codename,
        repo=repo,
        suite=tuple(suite),
        memory_on=build_arm_metrics(ARM_ON, on_attempts, suite),
        memory_off=build_arm_metrics(ARM_OFF, off_attempts, suite),
        attempts=[*on_attempts, *off_attempts],
        solver_kind=solver_kind,
    )


def run_memory_ab(
    fixture: Fixture,
    *,
    solver: Solver,
    on_provider: RecallProvider | None = None,
    off_provider: RecallProvider | None = None,
    label: str = "run",
    limit: int = DEFAULT_RECALL_LIMIT,
    solver_kind: str = "stub",
    recall_fn: RecallFn | None = None,
    inject_fn: InjectFn | None = None,
    now: datetime | None = None,
) -> MemoryABReport:
    """Run the full A/B: seed memory ON, keep memory OFF null, score the delta.

    The *only* difference between the arms is the memory provider. ``solver`` is
    the pluggable engine: :func:`make_stub_solver` for the offline, deterministic
    harness, or :func:`make_cli_engine_solver` for a real run. Both arms use the
    same suite, seed repo, recall query and limit, so the delta is attributable
    to memory alone.
    """
    on = on_provider or seed_fleet_provider(
        fixture.lessons, codename=fixture.codename, repo=fixture.repo
    )
    off = off_provider or null_provider()
    on_attempts = run_arm(
        fixture.tasks,
        arm=ARM_ON,
        provider=on,
        solver=solver,
        codename=fixture.codename,
        repo=fixture.repo,
        limit=limit,
        recall_fn=recall_fn,
        inject_fn=inject_fn,
    )
    off_attempts = run_arm(
        fixture.tasks,
        arm=ARM_OFF,
        provider=off,
        solver=solver,
        codename=fixture.codename,
        repo=fixture.repo,
        limit=limit,
        recall_fn=recall_fn,
        inject_fn=inject_fn,
    )
    return build_report(
        fixture.tasks,
        on_attempts,
        off_attempts,
        label=label,
        codename=fixture.codename,
        repo=fixture.repo,
        solver_kind=solver_kind,
        now=now,
    )


def default_fixture_dir() -> Path:
    """Location of the built-in mem-bench fixture inside the repo checkout."""
    return Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "mem-bench"
