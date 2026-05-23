#!/usr/bin/env python3
"""alfred slop-detect: scan a directory for AI-slop vocabulary.

A lightweight, standalone CLI. No scheduler, no Slack, no state files,
no AWS. Reads a JSON rule pack, walks a path, prints findings as
markdown or JSON. Suitable for local runs, pre-commit hooks, and CI
gates (with ``--fail-on-match``).

Exit codes:

- ``0``  clean (no findings, or no findings above --min-severity)
- ``1``  findings present and ``--fail-on-match`` was supplied
- ``2``  system error (bad rule pack, unreadable path, etc.)

The scheduled wrapper that posts to Slack lives in ``bin/curator.py``.

Usage::

    alfred slop-detect [--path <dir>] [--rules <json>]
                       [--report md|json] [--fail-on-match]
                       [--min-severity <name>] [--max-findings <n>]

Environment::

    ALFRED_SLOP_TARGET_PATH   default for --path
    ALFRED_SLOP_RULES         default for --rules
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Resolve the lib/ dir relative to this script so the CLI runs from a
# fresh clone without installing the package. Mirrors the pattern other
# bin/ codenames use.
_BIN_DIR = Path(__file__).resolve().parent
_LIB_DIR = _BIN_DIR.parent / "lib"
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

from slop_detector import (  # noqa: E402
    RuleLoadError,
    default_rule_pack_path,
    load_rule_pack,
    render_json,
    render_markdown,
    scan_path,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="alfred slop-detect",
        description=(
            "Scan a directory tree for AI-slop vocabulary, phrases, and "
            "patterns. Exits non-zero with --fail-on-match for CI use."
        ),
    )
    p.add_argument(
        "--path",
        default=os.environ.get("ALFRED_SLOP_TARGET_PATH", "."),
        help=("Directory to scan (default: $ALFRED_SLOP_TARGET_PATH or current directory)."),
    )
    p.add_argument(
        "--rules",
        default=os.environ.get("ALFRED_SLOP_RULES"),
        help=(
            "Path to a JSON rule pack (default: $ALFRED_SLOP_RULES, falling "
            "back to the bundled examples/slop-rules.json)."
        ),
    )
    p.add_argument(
        "--report",
        choices=("md", "json"),
        default="md",
        help="Output format (default: md).",
    )
    p.add_argument(
        "--fail-on-match",
        action="store_true",
        help=(
            "Exit 1 if any finding at or above --min-severity is reported. "
            "Use this in CI to block AI-slop from landing."
        ),
    )
    p.add_argument(
        "--min-severity",
        default=None,
        help=(
            "Only count findings with this severity or higher (severity "
            "order is taken from the rule pack's 'severities' list, with "
            "the first entry being most severe). Default: all severities "
            "count."
        ),
    )
    p.add_argument(
        "--max-findings",
        type=int,
        default=None,
        help="Markdown report: cap the number of itemized findings (JSON is unaffected).",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress logging output. Findings still print to stdout.",
    )
    return p


def _resolve_rules_path(arg: str | None) -> Path:
    if arg:
        return Path(arg).expanduser().resolve()
    return default_rule_pack_path()


def _count_blocking(
    report_dict_by_severity: dict[str, int],
    severities_order: tuple[str, ...],
    min_severity: str | None,
) -> int:
    """Count findings at or above min_severity.

    ``severities_order`` is the rule pack's ``severities`` tuple, most
    severe first. If ``min_severity`` is None or not in the list, count
    everything.
    """
    if not min_severity:
        return sum(report_dict_by_severity.values())
    if min_severity not in severities_order:
        return sum(report_dict_by_severity.values())
    cutoff = severities_order.index(min_severity)
    return sum(
        n
        for sev, n in report_dict_by_severity.items()
        if sev in severities_order and severities_order.index(sev) <= cutoff
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(message)s",
    )
    log = logging.getLogger("slop-detect")

    target = Path(args.path).expanduser().resolve()
    if not target.exists():
        log.error("path does not exist: %s", target)
        return 2
    if not target.is_dir():
        log.error("path is not a directory: %s", target)
        return 2

    rules_path = _resolve_rules_path(args.rules)
    try:
        pack = load_rule_pack(rules_path)
    except RuleLoadError as exc:
        log.error("rule pack error: %s", exc)
        return 2

    report = scan_path(target, pack)

    if args.report == "json":
        sys.stdout.write(render_json(report))
    else:
        sys.stdout.write(render_markdown(report, max_findings=args.max_findings))

    if args.fail_on_match:
        blocking = _count_blocking(report.by_severity, pack.severities, args.min_severity)
        if blocking > 0:
            log.warning(
                "slop-detect: %d finding%s at/above %s; failing as requested",
                blocking,
                "s" if blocking != 1 else "",
                args.min_severity or "any-severity",
            )
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
