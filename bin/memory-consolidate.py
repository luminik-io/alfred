#!/usr/bin/env python3
"""Scheduled memory consolidation/decay pass (off by default).

Shells out to ``alfred brain consolidate``, which is a NO-OP unless the operator
has armed ``ALFRED_MEMORY_CONSOLIDATE`` in ``$ALFRED_HOME/.env``. The pass is
conservative and invalidate-not-delete: it forgets stale promoted lessons from
Redis AMS and collapses exact-duplicate auto-promoted lessons, flipping the
local candidate rows to ``retired`` (never destroying the audit history).
Scheduling it disarmed is a safe no-op.

A nonzero exit is reserved for a real failure (the subprocess crashed, timed
out, returned invalid JSON, or an AMS forget failed) so a scheduled failure is
never silent.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
for candidate in (HERE.parent / "lib", Path(os.environ.get("ALFRED_HOME", "")) / "lib"):
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


def _brain_script() -> Path:
    return HERE / "alfred-brain.py"


def _doctor_mode() -> bool:
    return str(os.environ.get("ALFRED_DOCTOR", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _run_consolidate(args: argparse.Namespace) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(_brain_script()),
        "consolidate",
        "--stale-days",
        str(args.stale_days),
        "--json",
    ]
    if args.dry_run:
        cmd.append("--dry-run")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ.copy(),
    )
    try:
        stdout, stderr = proc.communicate(timeout=args.timeout)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        stdout, stderr = proc.communicate()
        detail = _short((stderr or stdout or "").strip(), 500)
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(
            f"memory consolidate timed out after {args.timeout}s{suffix}"
        ) from exc

    # rc 1 WITH a valid JSON summary means "AMS forget failed" (real, but the
    # payload is still useful). A crash BEFORE any JSON is printed must surface
    # as a hard failure: an empty stdout with a nonzero returncode is a child
    # crash, not a disabled no-op, so do NOT default it to `{}` (which would let
    # the wrapper report a false "disabled/no-op" and skip the failure path).
    if not (stdout or "").strip():
        if proc.returncode != 0:
            detail = _short((stderr or "consolidate produced no output").strip(), 500)
            raise RuntimeError(f"memory consolidate failed (rc={proc.returncode}): {detail}")
        # rc 0 with no output: nothing to do (still surface as a non-crash empty).
        return {"_returncode": 0}
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        detail = _short((stderr or stdout or "consolidate failed").strip(), 500)
        raise RuntimeError(f"memory consolidate returned invalid JSON: {detail}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("memory consolidate returned a non-object payload")
    payload["_returncode"] = proc.returncode
    return payload


def _short(value: str, limit: int) -> str:
    text = " ".join(value.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "..."


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stale-days", type=int, default=180)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print raw JSON payload.")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--slack", dest="slack", action="store_true", default=True)
    parser.add_argument("--no-slack", dest="slack", action="store_false")
    return parser


def _post_slack(message: str, *, severity: str = "info") -> bool:
    try:
        from agent_runner import slack_post
    except Exception as exc:
        print(f"[memory-consolidate] Slack unavailable: {exc}", file=sys.stderr)
        return False
    return bool(slack_post(message, severity=severity))


def main(argv: list[str] | None = None) -> int:
    # doctor.sh probes every scheduled agent for this sentinel; without it a
    # healthy wrapper is reported as a crash.
    if _doctor_mode():
        print("[MEMORY-CONSOLIDATE-DOCTOR-OK]")
        return 0

    args = build_parser().parse_args(argv)
    try:
        payload = _run_consolidate(args)
    except Exception as exc:
        message = f"*Alfred memory consolidate failed*\n\n```{_short(str(exc), 900)}```"
        if args.slack:
            _post_slack(message, severity="warn")
        print(f"memory-consolidate: {exc}", file=sys.stderr)
        return 1

    ams_failed = int(payload.get("ams_forget_failed") or 0)
    returncode = int(payload.get("_returncode") or 0)

    if args.slack and ams_failed:
        _post_slack(
            "*Alfred memory consolidate*\n\n"
            f"Left {ams_failed} lesson(s) live because the AMS forget failed; "
            "they will be retried on the next pass.",
            severity="warn",
        )

    if args.json:
        printable = {k: v for k, v in payload.items() if k != "_returncode"}
        print(json.dumps(printable, indent=2, sort_keys=True))
    elif not payload.get("enabled"):
        print("memory-consolidate: disabled (ALFRED_MEMORY_CONSOLIDATE off); no-op")
    else:
        print(
            "memory-consolidate: "
            f"enabled=true dry_run={bool(payload.get('dry_run'))} "
            f"decayed={int(payload.get('decayed') or 0)} "
            f"merged={int(payload.get('merged') or 0)} "
            f"ams_forget_attempted={int(payload.get('ams_forget_attempted') or 0)} "
            f"ams_forgotten={int(payload.get('ams_forgotten') or 0)} "
            f"ams_forget_failed={ams_failed}"
        )
    # Propagate a real failure (AMS forget failure surfaces as rc 1 from the
    # brain CLI) so the scheduler records it rather than swallowing it.
    return 1 if (ams_failed or returncode) else 0


if __name__ == "__main__":
    raise SystemExit(main())
