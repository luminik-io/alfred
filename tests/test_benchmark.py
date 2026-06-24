"""Tests for lib/benchmark.py and bin/alfred-benchmark.py.

The harness reader is pure: it never calls an LLM and never touches the
network or the real disk outside ``tmp_path``. These tests build a
synthetic state tree (spend ledger + typed event logs + stream-JSON
transcripts with ``message.usage`` blocks), then assert the four metric
families and the subscription-quota framing. A "fired suite" here is
entirely mocked telemetry: no model runs, no quota burns.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "lib"))

import benchmark  # noqa: E402

# --------------------------------------------------------------------------
# Synthetic-telemetry builders (mirror what a real firing leaves on disk)
# --------------------------------------------------------------------------


def _write_spend(state_dir: Path, codename: str, day: str, **kw) -> Path:
    path = state_dir / codename / f"spend-{day}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "firings_today": kw.get("firings", 0),
        "successes_today": kw.get("successes", 0),
        "failures_today": kw.get("failures", 0),
        "turns_today": kw.get("turns", 0),
        "cost_usd_today": kw.get("cost", 0.0),
    }
    path.write_text(json.dumps(payload))
    return path


def _write_events(state_dir: Path, codename: str, firing_id: str, events: list[dict]) -> Path:
    path = state_dir / codename / "events" / f"{firing_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return path


def _write_transcript(
    state_dir: Path,
    codename: str,
    firing_id: str,
    usages: list[dict],
) -> Path:
    month = datetime.now(UTC).strftime("%Y-%m")
    path = state_dir / "transcripts" / codename / month / f"{firing_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        {"type": "assistant", "message": {"role": "assistant", "usage": usage}} for usage in usages
    ]
    lines.append({"type": "result", "subtype": "success", "num_turns": 3})
    path.write_text("\n".join(json.dumps(e) for e in lines) + "\n", encoding="utf-8")
    return path


def _usage(inp: int, out: int, cache_create: int = 0, cache_read: int = 0) -> dict:
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_creation_input_tokens": cache_create,
        "cache_read_input_tokens": cache_read,
    }


def _ts(hour: int, minute: int) -> str:
    return f"2026-06-22T{hour:02d}:{minute:02d}:00Z"


# --------------------------------------------------------------------------
# Suite
# --------------------------------------------------------------------------


def test_default_suite_is_non_empty_and_typed():
    assert len(benchmark.DEFAULT_SUITE) >= 3
    kinds = {t.kind for t in benchmark.DEFAULT_SUITE}
    assert {"fix", "feature", "refactor"} <= kinds


def test_load_suite_defaults_when_path_none():
    assert benchmark.load_suite(None) == benchmark.DEFAULT_SUITE


def test_load_suite_reads_custom_file(tmp_path: Path):
    path = tmp_path / "suite.json"
    path.write_text(json.dumps([{"task_id": "t1", "kind": "fix", "title": "T1"}]))
    suite = benchmark.load_suite(path)
    assert len(suite) == 1
    assert suite[0].task_id == "t1"
    assert suite[0].expect_pr is True


def test_load_suite_falls_back_on_garbage(tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text("{not json")
    assert benchmark.load_suite(path) == benchmark.DEFAULT_SUITE


def test_load_suite_skips_entries_without_task_id(tmp_path: Path):
    path = tmp_path / "suite.json"
    path.write_text(json.dumps([{"kind": "fix"}, {"task_id": "ok", "kind": "fix"}]))
    suite = benchmark.load_suite(path)
    assert [t.task_id for t in suite] == ["ok"]


# --------------------------------------------------------------------------
# Token extraction
# --------------------------------------------------------------------------


def test_extract_token_usage_sums_all_turns():
    text = "\n".join(
        json.dumps({"type": "assistant", "message": {"usage": u}})
        for u in (_usage(100, 20, 5, 50), _usage(200, 40, 0, 150))
    )
    usage = benchmark.extract_token_usage(text)
    assert usage.tokens_in == 300
    assert usage.tokens_out == 60
    assert usage.cache_creation == 5
    assert usage.cache_read == 200


def test_extract_token_usage_cache_hit_rate():
    usage = benchmark.TokenUsage(tokens_in=100, cache_creation=0, cache_read=300)
    # 300 / (100 + 0 + 300) == 0.75
    assert usage.cache_hit_rate == pytest.approx(0.75)


def test_extract_token_usage_zero_when_empty():
    assert benchmark.extract_token_usage("").to_dict()["tokens_in"] == 0
    assert benchmark.TokenUsage().cache_hit_rate == 0.0


def test_extract_token_usage_tolerates_torn_lines():
    text = '{"type":"assistant","message":{"usage":{"input_tokens":10}}}\n{bad json\n'
    usage = benchmark.extract_token_usage(text)
    assert usage.tokens_in == 10


# --------------------------------------------------------------------------
# Event reading + per-firing observation
# --------------------------------------------------------------------------


def test_read_firing_events_extracts_signals(tmp_path: Path):
    path = _write_events(
        tmp_path,
        "lucius",
        "F1",
        [
            {"type": "firing_started", "ts": _ts(10, 0)},
            {"type": "llm_fallback", "ts": _ts(10, 1)},
            {"type": "review_posted", "ts": _ts(10, 5), "findings": 2},
            {"type": "checks_done", "ts": _ts(10, 6)},
            {"type": "pr_opened", "ts": _ts(10, 8)},
        ],
    )
    signals = benchmark.read_firing_events(path)
    assert signals["opened_pr"] is True
    assert signals["had_fallback"] is True
    assert signals["review_findings"] == 2
    assert signals["checks_done"] is True


def test_observe_firing_time_to_pr(tmp_path: Path):
    events = _write_events(
        tmp_path,
        "lucius",
        "F2",
        [
            {"type": "firing_started", "ts": _ts(10, 0)},
            {"type": "pr_opened", "ts": _ts(10, 10)},
        ],
    )
    transcript = _write_transcript(tmp_path, "lucius", "F2", [_usage(100, 50, 0, 100)])
    obs = benchmark.observe_firing("lucius", "F2", events, transcript)
    assert obs.opened_pr is True
    assert obs.time_to_pr_seconds == 600  # 10 minutes
    assert obs.tokens.tokens_in == 100


def test_observe_firing_negative_span_is_dropped(tmp_path: Path):
    events = _write_events(
        tmp_path,
        "lucius",
        "F3",
        [
            {"type": "firing_started", "ts": _ts(10, 10)},
            {"type": "pr_opened", "ts": _ts(10, 0)},
        ],
    )
    obs = benchmark.observe_firing("lucius", "F3", events, None)
    assert obs.time_to_pr_seconds is None


# --------------------------------------------------------------------------
# Discovery + full report
# --------------------------------------------------------------------------


def _seed_two_pr_run(state_dir: Path) -> None:
    """One clean PR firing + one PR firing that needed a fix push."""
    today = datetime.now().strftime("%Y-%m-%d")
    _write_spend(
        state_dir, "lucius", today, firings=3, successes=2, failures=1, turns=120, cost=0.0
    )
    # Clean PR: checks done, no follow-up fix.
    _write_events(
        state_dir,
        "lucius",
        "clean",
        [
            {"type": "firing_started", "ts": _ts(9, 0)},
            {"type": "pr_opened", "ts": _ts(9, 5)},
            {"type": "checks_done", "ts": _ts(9, 6)},
            {"type": "review_posted", "ts": _ts(9, 7), "findings": 1},
        ],
    )
    _write_transcript(state_dir, "lucius", "clean", [_usage(1000, 200, 100, 800)])
    # PR that needed a human-edit proxy (fix_pushed) and a fallback.
    _write_events(
        state_dir,
        "lucius",
        "messy",
        [
            {"type": "firing_started", "ts": _ts(10, 0)},
            {"type": "llm_fallback", "ts": _ts(10, 1)},
            {"type": "pr_opened", "ts": _ts(10, 12)},
            {"type": "fix_pushed", "ts": _ts(10, 20)},
            {"type": "review_posted", "ts": _ts(10, 22), "findings": 3},
        ],
    )
    _write_transcript(state_dir, "lucius", "messy", [_usage(2000, 400, 0, 1000)])
    # A no-PR firing (loop incident, never opened a PR).
    _write_events(
        state_dir,
        "lucius",
        "stuck",
        [
            {"type": "firing_started", "ts": _ts(11, 0)},
            {"type": "error_loop_detected", "ts": _ts(11, 3)},
        ],
    )


def test_discover_observations_finds_all_firings(tmp_path: Path):
    _seed_two_pr_run(tmp_path)
    obs = benchmark.discover_observations(tmp_path)
    assert {o.firing_id for o in obs} == {"clean", "messy", "stuck"}


def test_build_report_four_families(tmp_path: Path):
    _seed_two_pr_run(tmp_path)
    report = benchmark.run_report(tmp_path, label="after", prs_merged=1)

    # Throughput: 2 PRs opened, first PR span = 5 minutes (clean).
    assert report.throughput.prs_opened == 2
    assert report.throughput.firings == 3
    assert report.throughput.time_to_first_pr_seconds == 300

    # Quality: 1 merged / 2 opened; clean CI on the one with checks + no fix.
    assert report.quality.prs_merged == 1
    assert report.quality.merge_rate == pytest.approx(0.5)
    assert report.quality.ci_pass_first_try_rate == pytest.approx(0.5)
    assert report.quality.human_edit_before_merge_rate == pytest.approx(0.5)
    assert report.quality.review_findings_per_pr == pytest.approx(2.0)  # (1+3)/2

    # Reliability: success rate from ledger (2 / (2+1)).
    assert report.reliability.success_rate == pytest.approx(2 / 3)
    assert report.reliability.fallback_rate == pytest.approx(1 / 3)
    assert report.reliability.loop_incidents == 1
    # self-heal: recoverable firings = messy(fallback)+stuck(loop)=2; healed
    # (opened a PR) = messy only = 1 -> 0.5
    assert report.reliability.self_heal_rate == pytest.approx(0.5)

    # Efficiency: summed tokens across the two transcripts.
    assert report.efficiency.tokens_in == 3000
    assert report.efficiency.tokens_out == 600
    assert report.efficiency.cache_read == 1800
    assert report.efficiency.turns == 120
    assert report.efficiency.turns_per_pr == pytest.approx(60.0)


def test_build_report_empty_state_is_honest_zeros(tmp_path: Path):
    report = benchmark.run_report(tmp_path)
    assert report.throughput.prs_opened == 0
    assert report.quality.merge_rate == 0.0
    assert report.reliability.success_rate == 0.0
    assert report.efficiency.turns_per_pr is None
    assert report.throughput.time_to_first_pr_seconds is None


def test_review_findings_per_pr_excludes_firings_without_a_pr(tmp_path: Path):
    """Findings from a review-only firing (no PR) must not inflate the rate.

    Regression: ``total_findings`` once summed over every observation while
    the denominator counted only PRs, so a firing that posted a review but
    never opened a PR (e.g. a loop-detected run) inflated findings-per-PR
    with work no PR ever carried.
    """
    _seed_two_pr_run(tmp_path)
    # The 'stuck' firing posts a review but still never opens a PR. Its
    # findings must be excluded from the numerator (denominator = 2 PRs).
    _write_events(
        tmp_path,
        "lucius",
        "stuck",
        [
            {"type": "firing_started", "ts": _ts(11, 0)},
            {"type": "review_posted", "ts": _ts(11, 2), "findings": 50},
            {"type": "error_loop_detected", "ts": _ts(11, 3)},
        ],
    )
    report = benchmark.run_report(tmp_path, prs_merged=1)
    assert report.throughput.prs_opened == 2
    # (1 from clean + 3 from messy) / 2 PRs = 2.0; the 50 from 'stuck' is dropped.
    assert report.quality.review_findings_per_pr == pytest.approx(2.0)


def test_merge_rate_never_exceeds_one(tmp_path: Path):
    _seed_two_pr_run(tmp_path)
    # Caller over-reports merges; harness clamps to opened.
    report = benchmark.run_report(tmp_path, prs_merged=99)
    assert report.quality.prs_merged == 2
    assert report.quality.merge_rate == pytest.approx(1.0)


# --------------------------------------------------------------------------
# Quota-cost framing
# --------------------------------------------------------------------------


def test_quota_cost_uses_turns_per_pr(tmp_path: Path):
    _seed_two_pr_run(tmp_path)
    report = benchmark.run_report(tmp_path)
    rows = benchmark.quota_cost_for_report(report)
    by_plan = {r.plan: r for r in rows}
    assert "claude_pro" in by_plan
    # turns_per_pr = 60; claude_pro budget = 2000 -> 3.0%
    assert by_plan["claude_pro"].turns_per_pr == pytest.approx(60.0)
    assert by_plan["claude_pro"].pct_quota_per_pr == pytest.approx(3.0)


def test_quota_cost_none_when_no_pr(tmp_path: Path):
    report = benchmark.run_report(tmp_path)
    rows = benchmark.quota_cost_for_report(report)
    assert all(r.pct_quota_per_pr is None for r in rows)


def test_plan_budgets_env_override():
    env = {"ALFRED_BENCHMARK_TURN_BUDGET_CLAUDE_PRO": "500"}
    budgets = benchmark.plan_daily_turn_budgets(env)
    assert budgets["claude_pro"] == 500


def test_plan_budgets_ignore_bad_override():
    env = {"ALFRED_BENCHMARK_TURN_BUDGET_CLAUDE_PRO": "notanumber"}
    budgets = benchmark.plan_daily_turn_budgets(env)
    assert budgets["claude_pro"] == benchmark.DEFAULT_PLAN_DAILY_TURN_BUDGET["claude_pro"]


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _load_cli():
    spec = importlib.util.spec_from_file_location(
        "alfred_benchmark_cli", str(REPO_ROOT / "bin" / "alfred-benchmark.py")
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_report_table(tmp_path: Path, capsys):
    _seed_two_pr_run(tmp_path)
    cli = _load_cli()
    rc = cli.main(["report", "--state-dir", str(tmp_path), "--label", "after", "--prs-merged", "1"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "alfred-benchmark" in out
    assert "Throughput" in out
    assert "Reliability" in out
    assert "% quota/PR" in out
    assert "SELF-benchmark" in out


def test_cli_report_json(tmp_path: Path, capsys):
    _seed_two_pr_run(tmp_path)
    cli = _load_cli()
    rc = cli.main(["report", "--state-dir", str(tmp_path), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["throughput"]["prs_opened"] == 2
    assert "quota_cost" in payload
    assert payload["efficiency"]["turns"] == 120


def test_cli_report_is_default_subcommand(tmp_path: Path, capsys):
    _seed_two_pr_run(tmp_path)
    cli = _load_cli()
    rc = cli.main(["--state-dir", str(tmp_path), "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["throughput"]["prs_opened"] == 2


def test_cli_missing_state_dir_exit_2(tmp_path: Path, capsys):
    cli = _load_cli()
    rc = cli.main(["report", "--state-dir", str(tmp_path / "missing")])
    assert rc == 2
    assert "does not exist" in capsys.readouterr().err


def test_cli_show_suite(capsys):
    cli = _load_cli()
    rc = cli.main(["show-suite"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "fix-flaky-test" in out


def test_cli_write_suite_round_trip(tmp_path: Path, capsys):
    cli = _load_cli()
    dest = tmp_path / "suite.json"
    rc = cli.main(["write-suite", str(dest)])
    assert rc == 0
    assert dest.exists()
    loaded = benchmark.load_suite(dest)
    assert loaded == benchmark.DEFAULT_SUITE


def test_cli_write_suite_refuses_overwrite(tmp_path: Path, capsys):
    cli = _load_cli()
    dest = tmp_path / "suite.json"
    dest.write_text("existing")
    rc = cli.main(["write-suite", str(dest)])
    assert rc == 1
    assert "exists" in capsys.readouterr().err
