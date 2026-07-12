#!/usr/bin/env python3
"""Evaluate exact-head external reviews for one GitHub pull request."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from merge_gate import collect_snapshot, evaluate_external_review_gate  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, help="GitHub repository as owner/name")
    parser.add_argument("--pr", required=True, type=int, help="Pull request number")
    return parser


def main() -> int:
    args = _parser().parse_args()
    snapshot = collect_snapshot(args.repo, args.pr, collect_external_reviews=True)
    decision = evaluate_external_review_gate(snapshot)
    print(
        json.dumps(
            {
                "passed": decision.mergeable,
                "headSha": decision.head_sha,
                "conditions": [
                    {
                        "key": condition.key,
                        "passed": condition.passed,
                        "detail": condition.detail,
                    }
                    for condition in decision.conditions
                ],
            }
        )
    )
    return 0 if decision.mergeable else 1


if __name__ == "__main__":
    raise SystemExit(main())
