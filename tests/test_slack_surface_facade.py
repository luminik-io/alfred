"""Regression coverage for the lazy public Slack surface facade."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REMOVED_FLAT_MODULE = re.compile(
    r"\bslack_(?:approval|control|trust|listener|format|intent|converse|"
    r"issue_bridge|thread_status|memory_candidates)\b"
)


def test_slack_poster_facade_does_not_load_listener() -> None:
    script = f"""
import sys
sys.path.insert(0, {str(ROOT / "lib")!r})
from slack_surface import SlackPoster
assert SlackPoster.__module__ == 'slack_surface.posting'
assert 'slack_surface.listener' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_public_docs_do_not_reference_removed_flat_slack_modules() -> None:
    roots = (ROOT / "README.md", ROOT / "docs", ROOT / "site" / "src" / "content" / "docs")
    offenders: list[str] = []
    for root in roots:
        paths = [root] if root.is_file() else [*root.rglob("*.md"), *root.rglob("*.mdx")]
        for path in paths:
            if REMOVED_FLAT_MODULE.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
