"""Tests for the paired starter-skill benchmark."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import skill_benchmark as sb  # noqa: E402


def _write_fixture(root: Path) -> Path:
    fixture = root / "fixture"
    seed = fixture / "tasks" / "review-shell" / "seed"
    seed.mkdir(parents=True)
    (seed / "app.py").write_text("def run(value):\n    return value\n", encoding="utf-8")
    skill = root / "review-security" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: review-security\ndescription: Find unsafe shell use.\n---\n\nCheck shell input.\n",
        encoding="utf-8",
    )
    grader = fixture / "tasks" / "review-shell" / "grader.py"
    grader.write_text(
        "import json, pathlib, sys\n"
        "text=(pathlib.Path(sys.argv[1])/'answer.txt').read_text()\n"
        "print(json.dumps({'task_passed':'shell' in text,"
        "'regression':False,'review_findings':[] if 'allowlist' in text else ['missing fix']}))\n",
        encoding="utf-8",
    )
    (fixture / "suite.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tasks": [
                    {
                        "task_id": "review-shell",
                        "skill": "review-security",
                        "title": "Review shell input",
                        "prompt": "Review app.py and write answer.txt.",
                        "seed": "tasks/review-shell/seed",
                        "grader": "tasks/review-shell/grader.py",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return fixture


def test_load_fixture_keeps_graders_outside_agent_workspace(tmp_path: Path) -> None:
    fixture = _write_fixture(tmp_path)
    suite = sb.load_skill_suite(fixture, skills_root=tmp_path)

    assert suite.schema_version == 1
    assert suite.tasks[0].skill_name == "review-security"
    assert suite.tasks[0].seed_dir.name == "seed"
    assert suite.tasks[0].grader_path.name == "grader.py"
    assert suite.tasks[0].skill_path == tmp_path / "review-security" / "SKILL.md"
    assert not suite.tasks[0].grader_path.is_relative_to(suite.tasks[0].seed_dir)


def test_load_fixture_rejects_paths_outside_fixture(tmp_path: Path) -> None:
    fixture = _write_fixture(tmp_path)
    payload = json.loads((fixture / "suite.json").read_text())
    payload["tasks"][0]["seed"] = "../outside"
    (fixture / "suite.json").write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="inside the fixture"):
        sb.load_skill_suite(fixture, skills_root=tmp_path)


def test_run_skill_benchmark_uses_fresh_repo_per_arm_and_measures_prompt_bytes(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    suite = sb.load_skill_suite(fixture, skills_root=tmp_path)
    seen: list[tuple[str, str, bool, bool]] = []

    def runner(prompt: str, repo: Path, arm: str, task: sb.SkillTask) -> sb.AgentRun:
        answer = "shell risk"
        if arm == "skill":
            answer += " use an allowlist"
        (repo / "answer.txt").write_text(answer, encoding="utf-8")
        seen.append((arm, prompt, (repo / "app.py").exists(), (repo / ".git").is_dir()))
        return sb.AgentRun(
            exit_code=0,
            turns=2 if arm == "skill" else 3,
            tokens_in=100,
            tokens_out=20,
            elapsed_ms=25.0,
        )

    report = sb.run_skill_benchmark(suite, runner=runner, repetitions=1)

    assert [item.arm for item in report.results] == ["baseline", "skill"]
    assert all(item.task_passed for item in report.results)
    assert report.results[0].review_findings == 1
    assert report.results[1].review_findings == 0
    assert report.results[1].prompt_bytes > report.results[0].prompt_bytes
    assert seen[0][2] and seen[1][2]
    assert seen[0][3] and seen[1][3]
    assert "Check shell input" not in seen[0][1]
    assert "Check shell input" in seen[1][1]


def test_gate_requires_two_distinct_tasks_even_when_one_task_improves(tmp_path: Path) -> None:
    fixture = _write_fixture(tmp_path)
    suite = sb.load_skill_suite(fixture, skills_root=tmp_path)

    def runner(prompt: str, repo: Path, arm: str, task: sb.SkillTask) -> sb.AgentRun:
        (repo / "answer.txt").write_text(
            "shell risk use an allowlist" if arm == "skill" else "shell risk",
            encoding="utf-8",
        )
        return sb.AgentRun(exit_code=0, turns=1, elapsed_ms=1.0)

    report = sb.run_skill_benchmark(suite, runner=runner, repetitions=1)
    decision = report.decisions[0]
    assert decision.skill_name == "review-security"
    assert decision.eligible is False
    assert decision.evaluated_tasks == 1
    assert decision.pass_rate_delta == 0
    assert decision.review_findings_delta == -1


def test_gate_accepts_quality_gain_across_two_passing_tasks() -> None:
    def result(task_id: str, arm: str, findings: int) -> sb.SkillTaskResult:
        return sb.SkillTaskResult(
            task_id=task_id,
            skill_name="review-security",
            arm=arm,
            repetition=0,
            task_passed=arm == "skill",
            regression=False,
            review_findings=findings,
            review_finding_codes=tuple("finding" for _ in range(findings)),
            turns=1,
            prompt_bytes=100,
            tokens_in=10,
            cached_input_tokens=0,
            tokens_out=5,
            elapsed_ms=1.0,
            agent_exit_code=0,
        )

    decision = sb._decisions(
        [
            result("one", "baseline", 1),
            result("one", "skill", 0),
            result("two", "baseline", 1),
            result("two", "skill", 0),
        ]
    )[0]

    assert decision.evaluated_tasks == 2
    assert decision.eligible is True


def test_gate_rejects_skill_when_task_pass_rate_drops(tmp_path: Path) -> None:
    fixture = _write_fixture(tmp_path)
    suite = sb.load_skill_suite(fixture, skills_root=tmp_path)

    def runner(prompt: str, repo: Path, arm: str, task: sb.SkillTask) -> sb.AgentRun:
        (repo / "answer.txt").write_text(
            "shell risk" if arm == "baseline" else "no useful review",
            encoding="utf-8",
        )
        return sb.AgentRun(exit_code=0, turns=1, elapsed_ms=1.0)

    report = sb.run_skill_benchmark(suite, runner=runner, repetitions=1)
    assert report.decisions[0].eligible is False
    assert report.decisions[0].pass_rate_delta < 0


def test_gate_requires_every_skill_assisted_task_to_pass() -> None:
    def result(task_id: str, arm: str, passed: bool, findings: int) -> sb.SkillTaskResult:
        return sb.SkillTaskResult(
            task_id=task_id,
            skill_name="review-security",
            arm=arm,
            repetition=0,
            task_passed=passed,
            regression=False,
            review_findings=findings,
            review_finding_codes=tuple("finding" for _ in range(findings)),
            turns=1,
            prompt_bytes=100,
            tokens_in=10,
            cached_input_tokens=0,
            tokens_out=5,
            elapsed_ms=1.0,
            agent_exit_code=0,
        )

    decision = sb._decisions(
        [
            result("one", "baseline", False, 2),
            result("one", "skill", True, 0),
            result("two", "baseline", False, 2),
            result("two", "skill", False, 1),
        ]
    )[0]

    assert decision.pass_rate_delta > 0
    assert decision.eligible is False


def test_parse_codex_jsonl_sums_usage_and_turns() -> None:
    output = "\n".join(
        [
            json.dumps({"type": "turn.started"}),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 40,
                        "output_tokens": 20,
                    },
                }
            ),
            json.dumps({"type": "turn.started"}),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 50, "output_tokens": 10},
                }
            ),
        ]
    )

    usage = sb.parse_codex_jsonl(output)
    assert usage.turns == 2
    assert usage.tokens_in == 150
    assert usage.cached_input_tokens == 40
    assert usage.tokens_out == 30


def test_report_json_keeps_task_level_evidence(tmp_path: Path) -> None:
    fixture = _write_fixture(tmp_path)
    suite = sb.load_skill_suite(fixture, skills_root=tmp_path)

    def runner(prompt: str, repo: Path, arm: str, task: sb.SkillTask) -> sb.AgentRun:
        answer = "shell risk use an allowlist" if arm == "skill" else "shell risk"
        (repo / "answer.txt").write_text(answer, encoding="utf-8")
        return sb.AgentRun(exit_code=0, turns=1, elapsed_ms=2.0)

    report = sb.run_skill_benchmark(
        suite,
        runner=runner,
        repetitions=1,
        engine="test-agent",
        engine_version="test-agent 1.2.3",
        model="test-model",
    )
    payload = report.to_dict()

    assert payload["schema_version"] == 1
    assert payload["fixture_digest"] == sb.skill_suite_digest(suite)
    assert payload["engine"] == "test-agent"
    assert payload["engine_version"] == "test-agent 1.2.3"
    assert payload["model"] == "test-model"
    assert payload["gate"] == {
        "minimum_distinct_tasks": 2,
        "require_all_skill_tasks_passed": True,
        "allow_pass_rate_loss": False,
        "allow_regression_increase": False,
        "allow_review_finding_increase": False,
        "require_quality_gain": True,
    }
    assert payload["results"][0]["task_id"] == "review-shell"
    assert payload["results"][0]["prompt_bytes"] > 0
    assert payload["results"][0]["review_finding_codes"] == ["missing fix"]
    assert payload["decisions"][0]["evaluated_pairs"] == 1
    assert payload["decisions"][0]["evaluated_tasks"] == 1


def test_bundled_suite_covers_each_first_party_skill() -> None:
    suite = sb.load_skill_suite(
        sb.default_fixture_dir(),
        skills_root=ROOT / "skills" / "first_party",
    )
    assert {task.skill_name for task in suite.tasks} == {
        "add-observability",
        "changelog-and-release-notes",
        "migrate-dependency",
        "review-security",
        "spec-to-issues",
        "write-tests",
    }
    assert len({task.task_id for task in suite.tasks}) == len(suite.tasks) == 9
    assert sum(task.skill_name == "spec-to-issues" for task in suite.tasks) == 2
    assert sum(task.skill_name == "review-security" for task in suite.tasks) == 2
    assert sum(task.skill_name == "add-observability" for task in suite.tasks) == 2


def test_bundled_graders_do_not_count_harness_git_metadata_as_a_regression(
    tmp_path: Path,
) -> None:
    suite = sb.load_skill_suite(
        sb.default_fixture_dir(),
        skills_root=ROOT / "skills" / "first_party",
    )
    for task in suite.tasks:
        repo = tmp_path / task.task_id
        shutil.copytree(task.seed_dir, repo)
        sb._init_attempt_repo(repo)
        _, regression, _, _ = sb._grade(task, repo)
        assert regression is False, task.task_id


def test_write_tests_grader_accepts_behavior_coverage_in_an_existing_test_file(
    tmp_path: Path,
) -> None:
    suite = sb.load_skill_suite(
        sb.default_fixture_dir(),
        skills_root=ROOT / "skills" / "first_party",
    )
    task = next(task for task in suite.tasks if task.skill_name == "write-tests")
    repo = tmp_path / task.task_id
    shutil.copytree(task.seed_dir, repo)
    sb._init_attempt_repo(repo)
    (repo / "test_existing.py").write_text(
        """import unittest

from limits import normalize_limit


class LimitTests(unittest.TestCase):
    def test_default(self):
        self.assertEqual(normalize_limit(None), 10)

    def test_bounds(self):
        self.assertEqual(normalize_limit(1), 1)
        self.assertEqual(normalize_limit(100), 100)

    def test_invalid_values(self):
        for value in (0, -1, 101, '10', True):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_limit(value)
""",
        encoding="utf-8",
    )

    task_passed, regression, finding_codes, grader_error = sb._grade(task, repo)

    assert task_passed is True
    assert regression is False
    assert finding_codes == ()
    assert grader_error == ""


def test_spec_grader_accepts_an_issues_wrapper_with_coverage_metadata(tmp_path: Path) -> None:
    suite = sb.load_skill_suite(
        sb.default_fixture_dir(),
        skills_root=ROOT / "skills" / "first_party",
    )
    task = next(task for task in suite.tasks if task.skill_name == "spec-to-issues")
    repo = tmp_path / task.task_id
    shutil.copytree(task.seed_dir, repo)
    sb._init_attempt_repo(repo)
    (repo / "issues.json").write_text(
        json.dumps(
            {
                "issues": [
                    {
                        "title": "feat(health): add service health endpoint",
                        "repo": "acme/service-api",
                        "labels": ["agent:implement"],
                        "acceptance_criteria": ["GET /v1/health returns 200 with status ok."],
                        "test": "Test the unauthenticated response.",
                        "out_of_scope": ["Metrics", "Authentication middleware"],
                    }
                ],
                "coverage": ["All acceptance criteria map to the issue."],
            }
        ),
        encoding="utf-8",
    )

    task_passed, regression, finding_codes, grader_error = sb._grade(task, repo)

    assert task_passed is True
    assert regression is False
    assert finding_codes == ()
    assert grader_error == ""


def test_security_grader_accepts_verify_as_an_authorization_fix(tmp_path: Path) -> None:
    suite = sb.load_skill_suite(
        sb.default_fixture_dir(),
        skills_root=ROOT / "skills" / "first_party",
    )
    task = next(task for task in suite.tasks if task.task_id == "review-report-boundaries")
    repo = tmp_path / task.task_id
    shutil.copytree(task.seed_dir, repo)
    sb._init_attempt_repo(repo)
    common = {"file": "reports.py", "severity": "P1"}
    rows = [
        {
            **common,
            "line": 9,
            "lens": "injection",
            "risk": "The SQL query accepts raw input.",
            "fix": "Use a parameterized SQL query.",
        },
        {
            **common,
            "line": 13,
            "lens": "authorization",
            "risk": "A user can delete a report from another account.",
            "fix": "Verify that the user may act on the account before deletion.",
        },
        {
            **common,
            "line": 17,
            "lens": "secret handling",
            "risk": "The archive token is written to a log.",
            "fix": "Remove the token from the log fields.",
        },
        {
            **common,
            "line": 22,
            "lens": "ssrf",
            "risk": "The callback URL can reach an internal host.",
            "fix": "Allowlist callback hosts and block private addresses.",
        },
    ]
    (repo / "security-review.json").write_text(
        json.dumps({"findings": rows}),
        encoding="utf-8",
    )

    task_passed, regression, finding_codes, grader_error = sb._grade(task, repo)

    assert task_passed is True
    assert regression is False
    assert finding_codes == ()
    assert grader_error == ""


def _load_cli():
    spec = importlib.util.spec_from_file_location(
        "alfred_benchmark_skill_cli", ROOT / "bin" / "alfred-benchmark.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_skills_show_suite_is_offline_json(capsys: pytest.CaptureFixture[str]) -> None:
    cli = _load_cli()
    rc = cli.main(["skills", "--show-suite", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["schema_version"] == 1
    assert len(payload["tasks"]) == 9
    assert all("prompt" not in task for task in payload["tasks"])


def test_cli_skills_show_suite_filters_by_skill(capsys: pytest.CaptureFixture[str]) -> None:
    cli = _load_cli()
    rc = cli.main(["skills", "--show-suite", "--skill", "review-security", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert len(payload["tasks"]) == 2
    assert {task["skill"] for task in payload["tasks"]} == {"review-security"}


def test_cli_skills_rejects_nonpositive_repetitions(capsys: pytest.CaptureFixture[str]) -> None:
    cli = _load_cli()
    rc = cli.main(["skills", "--repetitions", "0"])
    assert rc == 2
    assert "greater than zero" in capsys.readouterr().err


def test_select_skill_suite_keeps_only_requested_skills() -> None:
    suite = sb.load_skill_suite(
        sb.default_fixture_dir(),
        skills_root=ROOT / "skills" / "first_party",
    )

    selected = sb.select_skill_suite(suite, ("write-tests", "review-security"))

    assert {task.skill_name for task in selected.tasks} == {
        "write-tests",
        "review-security",
    }
    with pytest.raises(ValueError, match="unknown skill"):
        sb.select_skill_suite(suite, ("does-not-exist",))
