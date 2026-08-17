"""Paired, task-level evaluation for Alfred's first-party skills.

The benchmark runs every task twice in a fresh copy of its seed repository:
once with the task prompt alone and once with the named skill instructions.
The agent never sees the task grader. A skill is eligible for the starter set
only when deterministic task evidence improves without a pass-rate or
regression-rate loss.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MINIMUM_DISTINCT_TASKS = 2


@dataclass(frozen=True)
class SkillTask:
    task_id: str
    skill_name: str
    title: str
    prompt: str
    seed_dir: Path
    grader_path: Path
    skill_path: Path


@dataclass(frozen=True)
class SkillSuite:
    schema_version: int
    fixture_dir: Path
    tasks: tuple[SkillTask, ...]


@dataclass(frozen=True)
class AgentRun:
    exit_code: int
    turns: int = 0
    tokens_in: int = 0
    cached_input_tokens: int = 0
    tokens_out: int = 0
    elapsed_ms: float = 0.0


@dataclass(frozen=True)
class SkillTaskResult:
    task_id: str
    skill_name: str
    arm: str
    repetition: int
    task_passed: bool
    regression: bool
    review_findings: int
    review_finding_codes: tuple[str, ...]
    turns: int
    prompt_bytes: int
    tokens_in: int
    cached_input_tokens: int
    tokens_out: int
    elapsed_ms: float
    agent_exit_code: int
    grader_error: str = ""


@dataclass(frozen=True)
class SkillDecision:
    skill_name: str
    evaluated_pairs: int
    evaluated_tasks: int
    baseline_pass_rate: float
    skill_pass_rate: float
    pass_rate_delta: float
    baseline_regression_rate: float
    skill_regression_rate: float
    regression_rate_delta: float
    baseline_review_findings: float
    skill_review_findings: float
    review_findings_delta: float
    eligible: bool


@dataclass(frozen=True)
class SkillBenchmarkReport:
    schema_version: int
    generated_at: datetime
    fixture_digest: str
    engine: str
    engine_version: str
    model: str | None
    repetitions: int
    results: tuple[SkillTaskResult, ...]
    decisions: tuple[SkillDecision, ...]

    def to_dict(self) -> dict[str, Any]:
        result_rows: list[dict[str, Any]] = []
        for result in self.results:
            row = asdict(result)
            row["review_finding_codes"] = list(result.review_finding_codes)
            result_rows.append(row)
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at.isoformat(),
            "fixture_digest": self.fixture_digest,
            "engine": self.engine,
            "engine_version": self.engine_version,
            "model": self.model,
            "gate": {
                "minimum_distinct_tasks": MINIMUM_DISTINCT_TASKS,
                "require_all_skill_tasks_passed": True,
                "allow_pass_rate_loss": False,
                "allow_regression_increase": False,
                "allow_review_finding_increase": False,
                "require_quality_gain": True,
            },
            "repetitions": self.repetitions,
            "results": result_rows,
            "decisions": [asdict(decision) for decision in self.decisions],
        }


AgentRunner = Callable[[str, Path, str, SkillTask], AgentRun]


def default_fixture_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "skill-benchmark"


def _inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"benchmark path must stay inside the fixture: {path}") from exc
    return resolved


def load_skill_suite(fixture_dir: Path, *, skills_root: Path) -> SkillSuite:
    """Load a fixed skill suite and validate every local path."""
    fixture = fixture_dir.resolve()
    payload = json.loads((fixture / "suite.json").read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("tasks"), list):
        raise ValueError("skill benchmark suite must contain a tasks list")
    schema_version = int(payload.get("schema_version", 0))
    if schema_version != 1:
        raise ValueError(f"unsupported skill benchmark schema: {schema_version}")
    tasks: list[SkillTask] = []
    for raw in payload["tasks"]:
        if not isinstance(raw, dict):
            raise ValueError("each skill benchmark task must be an object")
        task_id = str(raw.get("task_id") or "").strip()
        skill_name = str(raw.get("skill") or "").strip()
        prompt = str(raw.get("prompt") or "").strip()
        if not task_id or not skill_name or not prompt:
            raise ValueError("skill benchmark tasks require task_id, skill, and prompt")
        seed_dir = _inside(fixture / str(raw.get("seed") or ""), fixture)
        grader_path = _inside(fixture / str(raw.get("grader") or ""), fixture)
        skill_path = (skills_root / skill_name / "SKILL.md").resolve()
        if not seed_dir.is_dir():
            raise ValueError(f"seed directory does not exist: {seed_dir}")
        if not grader_path.is_file():
            raise ValueError(f"grader does not exist: {grader_path}")
        if not skill_path.is_file():
            raise ValueError(f"skill does not exist: {skill_path}")
        tasks.append(
            SkillTask(
                task_id=task_id,
                skill_name=skill_name,
                title=str(raw.get("title") or task_id),
                prompt=prompt,
                seed_dir=seed_dir,
                grader_path=grader_path,
                skill_path=skill_path,
            )
        )
    if not tasks:
        raise ValueError("skill benchmark suite has no tasks")
    return SkillSuite(schema_version=schema_version, fixture_dir=fixture, tasks=tuple(tasks))


def select_skill_suite(suite: SkillSuite, skill_names: Sequence[str]) -> SkillSuite:
    """Return a suite limited to named skills, preserving fixture order."""
    requested = {name.strip() for name in skill_names if name.strip()}
    available = {task.skill_name for task in suite.tasks}
    unknown = sorted(requested - available)
    if unknown:
        raise ValueError(f"unknown skill in benchmark suite: {', '.join(unknown)}")
    if not requested:
        return suite
    return SkillSuite(
        schema_version=suite.schema_version,
        fixture_dir=suite.fixture_dir,
        tasks=tuple(task for task in suite.tasks if task.skill_name in requested),
    )


def skill_suite_digest(suite: SkillSuite) -> str:
    """Hash the task contract, seed files, graders, and skill instructions."""
    digest = hashlib.sha256()
    digest.update(f"schema:{suite.schema_version}\n".encode())
    for task in suite.tasks:
        digest.update(
            json.dumps(
                {
                    "task_id": task.task_id,
                    "skill": task.skill_name,
                    "title": task.title,
                    "prompt": task.prompt,
                },
                sort_keys=True,
            ).encode()
        )
        for path in sorted(task.seed_dir.rglob("*")):
            if not path.is_file() or ".git" in path.relative_to(task.seed_dir).parts:
                continue
            digest.update(f"seed:{path.relative_to(task.seed_dir).as_posix()}\n".encode())
            digest.update(path.read_bytes())
        digest.update(b"grader\n")
        digest.update(task.grader_path.read_bytes())
        digest.update(b"skill\n")
        digest.update(task.skill_path.read_bytes())
    return digest.hexdigest()


def _prompt(task: SkillTask, arm: str) -> str:
    if arm == "baseline":
        return task.prompt
    skill_text = task.skill_path.read_text(encoding="utf-8")
    return (
        f"{task.prompt}\n\n"
        "Use these task instructions. Follow them only for this task.\n\n"
        f'<skill name="{task.skill_name}">\n{skill_text}\n</skill>'
    )


def _grade(task: SkillTask, repo: Path) -> tuple[bool, bool, tuple[str, ...], str]:
    try:
        proc = subprocess.run(
            [sys.executable, str(task.grader_path), str(repo)],
            cwd=str(task.grader_path.parent),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, True, ("grader did not complete",), type(exc).__name__
    if proc.returncode != 0:
        return False, True, ("grader exited with an error",), f"grader_exit_{proc.returncode}"
    try:
        payload = json.loads(proc.stdout)
        findings = payload.get("review_findings", [])
        if isinstance(findings, int):
            finding_codes = tuple("unspecified review finding" for _ in range(max(0, findings)))
        elif isinstance(findings, list):
            finding_codes = tuple(str(item) for item in findings)
        else:
            raise ValueError("review_findings must be a list or integer")
        return (
            bool(payload["task_passed"]),
            bool(payload["regression"]),
            finding_codes,
            "",
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        return False, True, ("grader returned invalid data",), type(exc).__name__


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _init_attempt_repo(repo: Path) -> None:
    """Create the local Git boundary required by coding-agent CLIs."""
    proc = subprocess.run(
        ["git", "init", "--quiet", "--template=", str(repo)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"could not initialize benchmark repository: {proc.stderr.strip()}")


def _decisions(results: Sequence[SkillTaskResult]) -> tuple[SkillDecision, ...]:
    decisions: list[SkillDecision] = []
    for skill_name in sorted({result.skill_name for result in results}):
        baseline = [r for r in results if r.skill_name == skill_name and r.arm == "baseline"]
        skill = [r for r in results if r.skill_name == skill_name and r.arm == "skill"]
        evaluated_pairs = min(len(baseline), len(skill))
        evaluated_tasks = len(
            {result.task_id for result in baseline} & {result.task_id for result in skill}
        )
        baseline_pass = _mean([float(r.task_passed) for r in baseline])
        skill_pass = _mean([float(r.task_passed) for r in skill])
        baseline_regression = _mean([float(r.regression) for r in baseline])
        skill_regression = _mean([float(r.regression) for r in skill])
        baseline_findings = _mean([float(r.review_findings) for r in baseline])
        skill_findings = _mean([float(r.review_findings) for r in skill])
        pass_delta = skill_pass - baseline_pass
        regression_delta = skill_regression - baseline_regression
        findings_delta = skill_findings - baseline_findings
        quality_gain = pass_delta > 0 or regression_delta < 0 or findings_delta < 0
        eligible = bool(
            evaluated_pairs
            and evaluated_tasks >= MINIMUM_DISTINCT_TASKS
            and skill_pass == 1.0
            and skill_pass >= baseline_pass
            and skill_regression <= baseline_regression
            and skill_findings <= baseline_findings
            and quality_gain
        )
        decisions.append(
            SkillDecision(
                skill_name=skill_name,
                evaluated_pairs=evaluated_pairs,
                evaluated_tasks=evaluated_tasks,
                baseline_pass_rate=baseline_pass,
                skill_pass_rate=skill_pass,
                pass_rate_delta=pass_delta,
                baseline_regression_rate=baseline_regression,
                skill_regression_rate=skill_regression,
                regression_rate_delta=regression_delta,
                baseline_review_findings=baseline_findings,
                skill_review_findings=skill_findings,
                review_findings_delta=findings_delta,
                eligible=eligible,
            )
        )
    return tuple(decisions)


def run_skill_benchmark(
    suite: SkillSuite,
    *,
    runner: AgentRunner,
    repetitions: int = 1,
    engine: str = "unspecified",
    engine_version: str = "",
    model: str | None = None,
) -> SkillBenchmarkReport:
    """Run paired skill/no-skill attempts and grade each fresh workspace."""
    if repetitions <= 0:
        raise ValueError("repetitions must be greater than zero")
    results: list[SkillTaskResult] = []
    for repetition in range(repetitions):
        arms = ("baseline", "skill") if repetition % 2 == 0 else ("skill", "baseline")
        for task in suite.tasks:
            for arm in arms:
                prompt = _prompt(task, arm)
                with tempfile.TemporaryDirectory(prefix="alfred-skill-bench-") as temp:
                    repo = Path(temp) / "repo"
                    shutil.copytree(task.seed_dir, repo)
                    _init_attempt_repo(repo)
                    run = runner(prompt, repo, arm, task)
                    task_passed, regression, finding_codes, grader_error = _grade(task, repo)
                if run.exit_code != 0:
                    finding_codes = (*finding_codes, f"agent exited with status {run.exit_code}")
                results.append(
                    SkillTaskResult(
                        task_id=task.task_id,
                        skill_name=task.skill_name,
                        arm=arm,
                        repetition=repetition,
                        task_passed=task_passed and run.exit_code == 0,
                        regression=regression or run.exit_code != 0,
                        review_findings=len(finding_codes),
                        review_finding_codes=finding_codes,
                        turns=run.turns,
                        prompt_bytes=len(prompt.encode("utf-8")),
                        tokens_in=run.tokens_in,
                        cached_input_tokens=run.cached_input_tokens,
                        tokens_out=run.tokens_out,
                        elapsed_ms=run.elapsed_ms,
                        agent_exit_code=run.exit_code,
                        grader_error=grader_error,
                    )
                )
    return SkillBenchmarkReport(
        schema_version=1,
        generated_at=datetime.now(UTC),
        fixture_digest=skill_suite_digest(suite),
        engine=engine,
        engine_version=engine_version,
        model=model,
        repetitions=repetitions,
        results=tuple(results),
        decisions=_decisions(results),
    )


def parse_codex_jsonl(output: str) -> AgentRun:
    """Read Codex JSONL usage without depending on session files."""
    turns = 0
    tokens_in = 0
    cached_input_tokens = 0
    tokens_out = 0
    for raw_line in output.splitlines():
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "turn.completed":
            continue
        turns += 1
        usage = event.get("usage")
        if not isinstance(usage, dict):
            continue
        tokens_in += int(usage.get("input_tokens") or 0)
        cached_input_tokens += int(usage.get("cached_input_tokens") or 0)
        tokens_out += int(usage.get("output_tokens") or 0)
    return AgentRun(
        exit_code=0,
        turns=turns,
        tokens_in=tokens_in,
        cached_input_tokens=cached_input_tokens,
        tokens_out=tokens_out,
    )


def make_codex_runner(
    *,
    model: str | None = None,
    timeout_s: int = 900,
) -> AgentRunner:
    """Return a local Codex runner with isolated configuration and writable seed repo."""
    executable = shutil.which("codex")
    if not executable:
        raise FileNotFoundError("codex executable not found")

    def run(prompt: str, repo: Path, arm: str, task: SkillTask) -> AgentRun:
        command = [
            executable,
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--sandbox",
            "workspace-write",
            "--cd",
            str(repo),
            "--json",
        ]
        if model:
            command.extend(["--model", model])
        command.append("-")
        started = time.perf_counter()
        try:
            proc = subprocess.run(
                command,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return AgentRun(exit_code=124, elapsed_ms=(time.perf_counter() - started) * 1000)
        except OSError:
            return AgentRun(exit_code=127, elapsed_ms=(time.perf_counter() - started) * 1000)
        parsed = parse_codex_jsonl(proc.stdout or "")
        return AgentRun(
            exit_code=proc.returncode,
            turns=parsed.turns,
            tokens_in=parsed.tokens_in,
            cached_input_tokens=parsed.cached_input_tokens,
            tokens_out=parsed.tokens_out,
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )

    return run


def codex_version() -> str:
    """Return the local Codex CLI version used for a benchmark report."""
    executable = shutil.which("codex")
    if not executable:
        raise FileNotFoundError("codex executable not found")
    proc = subprocess.run(
        [executable, "--version"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        return "unknown"
    return proc.stdout.strip() or "unknown"
