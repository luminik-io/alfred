#!/usr/bin/env python3
"""Curator - scheduled wrapper around the slop-detector.

Curator is the opt-in codename that fires the slop-detector on a
schedule and posts findings to Slack. The detector itself
(``lib/slop_detector.py`` + ``bin/slop-detector.py``) is fully
standalone and has zero scheduler / Slack coupling; this file is the
glue that adds those.

Configuration (all env vars, 12-factor):

    ALFRED_SLOP_TARGET_PATH   directory to scan (required)
    ALFRED_SLOP_RULES         rule pack JSON (default: bundled pack)
    ALFRED_CURATOR_MAX_ITEMS  cap on findings shown in Slack (default 8)

Schedule

Add to your ``launchd/agents.conf`` (weekly is a sensible default; the
detector is cheap but the report is noisy if posted daily):

    my.fleet.curator   curator.py   interval:604800   no   my.fleet.curator   AI-slop weekly

Curator never modifies any file. It is read-only.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Resolve ALFRED_HOME's lib/ first (where the runtime installs
# agent_runner), then fall back to the repo's own lib/ so this script
# also works from a fresh checkout.
_HERE = Path(__file__).resolve().parent
_REPO_LIB = _HERE.parent / "lib"
sys.path.insert(0, (os.environ.get("ALFRED_HOME") or os.path.expanduser("~/.alfred")) + "/lib")
if str(_REPO_LIB) not in sys.path:
    sys.path.insert(0, str(_REPO_LIB))

from agent_runner import (  # noqa: E402
    EventLog,
    PreflightFailed,
    PreflightSpec,
    SpendState,
    doctor_mode,
    preflight,
    slack_post,
    with_lock,
)
from slop_detector import (  # noqa: E402
    RuleLoadError,
    default_rule_pack_path,
    load_rule_pack,
    scan_path,
)

AGENT = os.environ.get("AGENT_CODENAME", "curator")
PREFLIGHT = PreflightSpec(agent=AGENT)

TARGET_PATH = os.environ.get("ALFRED_SLOP_TARGET_PATH", "")
RULES_PATH = os.environ.get("ALFRED_SLOP_RULES", "")
MAX_ITEMS = int(os.environ.get("ALFRED_CURATOR_MAX_ITEMS", "8"))


def format_slack(report_dict: dict, max_items: int) -> str:
    total = report_dict["total_findings"]
    if total == 0:
        return f"[{AGENT}] {report_dict['root']}: clean. No slop detected."
    lines = [
        f"[{AGENT}] AI-slop weekly: *{total}* finding{'s' if total != 1 else ''}",
        f"  root: `{report_dict['root']}`",
        f"  rule pack: `{report_dict['rule_pack']}` v{report_dict['rule_pack_version']}",
    ]
    by_sev = report_dict["by_severity"]
    for sev in sorted(by_sev):
        lines.append(f"  {sev}: {by_sev[sev]}")
    findings = report_dict["findings"]
    if findings:
        lines.append("\n  *Samples:*")
        for f in findings[:max_items]:
            lines.append(
                f"    `{f['path']}:{f['line']}` [{f['severity']}] {f['rule_id']}: `{f['match']}`"
            )
        if total > max_items:
            lines.append(f"    ...and {total - max_items} more.")
    return "\n".join(lines)


def main() -> int:
    with_lock(AGENT)

    try:
        preflight(PREFLIGHT)
    except PreflightFailed:
        return 0

    if doctor_mode():
        print(f"[{AGENT.upper()}-DOCTOR-OK]")
        return 0

    if not TARGET_PATH:
        print(f"[{AGENT.upper()}-IDLE] set ALFRED_SLOP_TARGET_PATH to enable")
        return 0

    target = Path(TARGET_PATH).expanduser().resolve()
    if not target.is_dir():
        print(f"[{AGENT.upper()}-IDLE] target {target} is not a directory")
        return 0

    rules_path = Path(RULES_PATH).expanduser().resolve() if RULES_PATH else default_rule_pack_path()
    try:
        pack = load_rule_pack(rules_path)
    except RuleLoadError as exc:
        print(f"[{AGENT.upper()}-ERROR] rule pack: {exc}", file=sys.stderr)
        return 0

    events = EventLog(agent=AGENT)
    events.emit("firing_started", target=str(target), rules=str(rules_path))

    spend = SpendState(AGENT)
    report = scan_path(target, pack)
    spend.increment(firings_today=1, hits_today=report.total_findings)

    payload = report.to_dict()
    msg = format_slack(payload, MAX_ITEMS)
    print(msg)
    slack_post(msg)

    events.emit(
        "firing_completed",
        total=report.total_findings,
        by_severity=json.dumps(report.by_severity, sort_keys=True),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
