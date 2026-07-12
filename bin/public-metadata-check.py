#!/usr/bin/env python3
"""Reject private or noisy text before a public PR body becomes commit metadata."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_BODY_CHARS = 12_000
MAX_BODY_LINES = 180

_HOME_PATH = re.compile(
    r"(?:/(?:" + "Users" + r"|home)/|[A-Za-z]:[/\\]" + "Users" + r"[/\\])"
    r"(?P<account>[^/\\\s]+)[/\\]",
    re.IGNORECASE,
)
_GENERIC_ACCOUNTS = frozenset({"example", "runner", "shared", "user", "username"})
_LOCAL_WORKSPACE_PATH = re.compile(
    r"(?:/(?:tmp|workspace)(?:/|\b)|/private/tmp/|/var/folders/|"
    r"[A-Za-z]:[/\\](?:temp|tmp)[/\\])",
    re.IGNORECASE,
)
_RAW_OUTPUT = (
    re.compile(r"\.{20,}\s*\[\s*\d{1,3}%\]"),
    re.compile(r"(?m)^\s*(?:FAIL|ERROR)(?::\s*|\s+(?:tests?/|src/|\S+::))"),
    re.compile(r"(?m)^\s*[^\n:]+:\d+:\d+:\s+error\s+TS\d+"),
    re.compile(r"(?m)^\s*test result:\s+(?:ok|FAILED)\."),
    re.compile(r"(?m)^\s*running\s+\d+\s+tests?\s*$"),
    re.compile(r"(?m)^\s*Traceback \(most recent call last\):\s*$"),
)


def _contains_private_home_path(text: str) -> bool:
    for match in _HOME_PATH.finditer(text):
        account = match.group("account").strip("<>").lower()
        if account not in _GENERIC_ACCOUNTS:
            return True
    return False


def metadata_findings(title: str, body: str) -> list[str]:
    """Return public-safe finding labels without repeating matched content."""
    text = f"{title}\n{body}"
    findings: list[str] = []
    if _contains_private_home_path(text) or _LOCAL_WORKSPACE_PATH.search(text):
        findings.append("local filesystem path")
    if len(body) > MAX_BODY_CHARS or len(body.splitlines()) > MAX_BODY_LINES:
        findings.append("oversized PR description")
    if any(pattern.search(text) for pattern in _RAW_OUTPUT):
        findings.append("raw command, test, compiler, or stack output")
    return findings


def _existing_scrub_rejects(title: str, body: str) -> bool:
    """Run the repository scrub against metadata without printing the payload."""
    fd, raw_path = tempfile.mkstemp(prefix=".public-pr-metadata-", suffix=".md", dir=ROOT)
    path = Path(raw_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"# {title}\n\n{body}\n")
        result = subprocess.run(
            ["bash", str(ROOT / "bin" / "scrub-check.sh")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        return result.returncode != 0
    finally:
        path.unlink(missing_ok=True)


def main() -> int:
    title = os.environ.get("PR_TITLE", "")
    body = os.environ.get("PR_BODY", "")
    if not title.strip():
        print("public-metadata-check: PR_TITLE is required", file=sys.stderr)
        return 2

    findings = metadata_findings(title, body)
    if _existing_scrub_rejects(title, body):
        findings.append("blocked private identifier or secret")
    findings = list(dict.fromkeys(findings))
    if findings:
        for finding in findings:
            print(f"::error::Public PR metadata contains {finding}", file=sys.stderr)
        print("public-metadata-check: failed", file=sys.stderr)
        return 1
    print("public-metadata-check: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
