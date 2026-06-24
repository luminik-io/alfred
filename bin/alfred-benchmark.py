#!/usr/bin/env python3
"""``alfred-benchmark``: reproducible self-benchmark from existing telemetry.

Runs a FIXED task suite's results back out of the telemetry the fleet
already captures and reports the four metric families an engineering team
cares about: throughput, quality, reliability, efficiency. Plus a
subscription-quota cost table (% of plan budget per PR), never $/PR.

It reads three on-disk state trees under ``$ALFRED_STATE_DIR`` (default
``$ALFRED_HOME/state``, default ``~/.alfred/state``):

* ``<codename>/spend-YYYY-MM-DD.json``         per-day spend ledger
* ``<codename>/events/<firing_id>.jsonl``      typed per-firing event log
* ``transcripts/<codename>/<YYYY-MM>/*.jsonl`` stream-JSON transcripts

It performs NO LLM calls and writes nothing under the state tree; it only
reads. To produce a fresh data point, fire the suite with the normal
runner (``--show-suite`` / ``--write-suite`` emit the fixed tasks), then
run ``alfred benchmark report`` to read the result. See docs/BENCHMARKS.md.

Subcommands:
  report        Read telemetry and print the benchmark (default).
  show-suite    Print the fixed task suite (text or --json).
  write-suite   Write the default suite to a file (for editing/seeding).

Exit codes:
  0 success
  1 user error (bad args, unknown codename)
  2 system error (state dir missing or unreadable)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Make ``lib/`` importable whether this script runs from the repo checkout
# or from ``$ALFRED_HOME/bin``.
_HERE = Path(__file__).resolve().parent
for candidate in (
    _HERE.parent / "lib",
    Path(os.environ.get("ALFRED_HOME", "")) / "lib",
):
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from benchmark import (  # noqa: E402
    DEFAULT_SUITE,
    BenchmarkReport,
    BenchmarkTask,
    load_suite,
    quota_cost_for_report,
    run_report,
)
from transcripts import default_state_dir  # noqa: E402

logger = logging.getLogger("alfred-benchmark")


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _fmt_secs(value: float | None) -> str:
    if value is None:
        return "-"
    if value < 90:
        return f"{value:.0f}s"
    return f"{value / 60:.1f}m"


def _fmt_num(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:,.0f}"


# --------------------------------------------------------------------------
# Renderers
# --------------------------------------------------------------------------


def render_report_table(report: BenchmarkReport) -> str:
    ts = report.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []
    lines.append(f"alfred-benchmark - label={report.label!r} @ {ts}")
    lines.append(
        f"suite: {len(report.suite)} tasks   firings observed: {report.throughput.firings}"
    )
    lines.append("")

    tp = report.throughput
    lines.append("Throughput")
    lines.append(f"  PRs opened ............... {tp.prs_opened}")
    lines.append(f"  time to first PR ......... {_fmt_secs(tp.time_to_first_pr_seconds)}")
    lines.append(f"  median time to PR ........ {_fmt_secs(tp.median_time_to_pr_seconds)}")
    lines.append("")

    q = report.quality
    lines.append("Quality")
    lines.append(f"  PRs merged ............... {q.prs_merged} / {q.prs_opened}")
    lines.append(f"  merge rate ............... {_fmt_pct(q.merge_rate)}")
    lines.append(f"  CI pass first try ........ {_fmt_pct(q.ci_pass_first_try_rate)}")
    lines.append(f"  human-edit before merge .. {_fmt_pct(q.human_edit_before_merge_rate)}")
    lines.append(f"  review findings per PR ... {q.review_findings_per_pr:.2f}")
    lines.append("")

    r = report.reliability
    lines.append("Reliability")
    lines.append(
        f"  success rate ............. {_fmt_pct(r.success_rate)} ({r.completed_firings} completed)"
    )
    lines.append(f"  fallback rate ............ {_fmt_pct(r.fallback_rate)}")
    lines.append(f"  self-heal rate ........... {_fmt_pct(r.self_heal_rate)}")
    lines.append(f"  loop incidents ........... {r.loop_incidents}")
    lines.append("")

    e = report.efficiency
    lines.append("Efficiency (tokens)")
    lines.append(f"  tokens in ................ {e.tokens_in:,}")
    lines.append(f"  tokens out ............... {e.tokens_out:,}")
    lines.append(f"  cache read ............... {e.cache_read:,}")
    lines.append(f"  cache creation ........... {e.cache_creation:,}")
    lines.append(f"  cache hit rate ........... {_fmt_pct(e.cache_hit_rate)}")
    lines.append(f"  turns .................... {e.turns:,}")
    lines.append(f"  tokens in per PR ......... {_fmt_num(e.tokens_in_per_pr)}")
    lines.append(f"  turns per PR ............. {_fmt_num(e.turns_per_pr)}")
    lines.append("")

    lines.append("Cost as a share of subscription quota (turns per PR / daily plan budget)")
    header = f"  {'plan':<16} {'daily turns':<12} {'turns/PR':<10} {'% quota/PR'}"
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))
    for row in quota_cost_for_report(report):
        pct = "-" if row.pct_quota_per_pr is None else f"{row.pct_quota_per_pr:.2f}%"
        tpp = "-" if row.turns_per_pr is None else f"{row.turns_per_pr:.1f}"
        lines.append(f"  {row.plan:<16} {row.daily_turn_budget:<12,} {tpp:<10} {pct}")
    lines.append("")
    lines.append("note: % quota is a sizing estimate from the turn-burn budgets in")
    lines.append("docs/CLAUDE_CODE.md, not a provider billing guarantee. This is a")
    lines.append("SELF-benchmark (honest absolutes + before/after), not a 'beats X' claim.")
    return "\n".join(lines)


def render_report_json(report: BenchmarkReport) -> str:
    payload = report.to_dict()
    payload["quota_cost"] = [row.to_dict() for row in quota_cost_for_report(report)]
    return json.dumps(payload, indent=2, default=str)


def render_suite_table(suite: tuple[BenchmarkTask, ...]) -> str:
    lines = [f"alfred-benchmark fixed task suite ({len(suite)} tasks)", ""]
    lines.append(f"{'task_id':<20} {'kind':<10} {'title'}")
    lines.append("-" * 60)
    for task in suite:
        lines.append(f"{task.task_id:<20} {task.kind:<10} {task.title}")
    return "\n".join(lines)


def render_suite_json(suite: tuple[BenchmarkTask, ...]) -> str:
    return json.dumps([t.to_dict() for t in suite], indent=2)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="alfred-benchmark",
        description="Reproducible self-benchmark from existing fleet telemetry.",
    )
    sub = p.add_subparsers(dest="command")

    rep = sub.add_parser("report", help="read telemetry and print the benchmark (default)")
    rep.add_argument("--label", default="run", help="tag for this run, e.g. before/after/v0.5.0")
    rep.add_argument("--codename", action="append", help="restrict to one codename (repeatable)")
    rep.add_argument(
        "--prs-merged",
        type=int,
        default=0,
        help="merged-PR count for the run (merge state is not in local telemetry)",
    )
    rep.add_argument("--suite-file", type=Path, default=None, help="custom suite JSON file")
    rep.add_argument("--state-dir", type=Path, default=None, help="override state directory")
    rep.add_argument("--json", action="store_true", dest="json_out", help="machine-readable JSON")
    rep.add_argument("--verbose", "-v", action="store_true", help="debug logging")

    show = sub.add_parser("show-suite", help="print the fixed task suite")
    show.add_argument("--suite-file", type=Path, default=None, help="custom suite JSON file")
    show.add_argument("--json", action="store_true", dest="json_out", help="machine-readable JSON")

    write = sub.add_parser("write-suite", help="write the default suite to a file for editing")
    write.add_argument("path", type=Path, help="destination JSON file")
    write.add_argument("--force", action="store_true", help="overwrite an existing file")

    # Top-level mirrors so `alfred-benchmark --json` (no subcommand) works as report.
    p.add_argument("--label", default="run", help=argparse.SUPPRESS)
    p.add_argument("--codename", action="append", help=argparse.SUPPRESS)
    p.add_argument("--prs-merged", type=int, default=0, help=argparse.SUPPRESS)
    p.add_argument("--suite-file", type=Path, default=None, help=argparse.SUPPRESS)
    p.add_argument("--state-dir", type=Path, default=None, help=argparse.SUPPRESS)
    p.add_argument("--json", action="store_true", dest="json_out", help=argparse.SUPPRESS)
    p.add_argument("--verbose", "-v", action="store_true", help=argparse.SUPPRESS)
    return p


def _cmd_report(args: argparse.Namespace) -> int:
    state_dir = args.state_dir or default_state_dir()
    if not state_dir.exists():
        print(
            f"alfred-benchmark: state directory {state_dir} does not exist. "
            "Set ALFRED_STATE_DIR or run the suite at least once.",
            file=sys.stderr,
        )
        return 2
    suite = load_suite(args.suite_file)
    report = run_report(
        state_dir,
        label=args.label,
        codenames=args.codename,
        prs_merged=args.prs_merged,
        suite=suite,
    )
    if args.json_out:
        print(render_report_json(report))
    else:
        print(render_report_table(report))
    return 0


def _cmd_show_suite(args: argparse.Namespace) -> int:
    suite = load_suite(args.suite_file)
    print(render_suite_json(suite) if args.json_out else render_suite_table(suite))
    return 0


def _cmd_write_suite(args: argparse.Namespace) -> int:
    path: Path = args.path
    if path.exists() and not args.force:
        print(
            f"alfred-benchmark: {path} exists; pass --force to overwrite.",
            file=sys.stderr,
        )
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_suite_json(DEFAULT_SUITE) + "\n", encoding="utf-8")
    print(f"wrote {len(DEFAULT_SUITE)}-task suite to {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.WARNING,
        format="%(name)s: %(message)s",
    )
    command = args.command or "report"
    if command == "show-suite":
        return _cmd_show_suite(args)
    if command == "write-suite":
        return _cmd_write_suite(args)
    return _cmd_report(args)


if __name__ == "__main__":
    sys.exit(main())
