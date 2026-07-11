"""Regression coverage for the lazy public Slack surface facade."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REMOVED_FLAT_MODULE = re.compile(
    r"(?<!test_)(?<!slack_surface[./])\bslack_(?:approval|control|trust|listener|format|intent|converse|"
    r"issue_bridge|thread_status|memory_candidates)\b"
)
SLACK_SURFACE_PATH = re.compile(r"\blib/slack_surface/[a-z_]+\.py\b")


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
    roots = (*ROOT.glob("*.md"), ROOT / "docs", ROOT / "site" / "src" / "content" / "docs")
    offenders: list[str] = []
    for root in roots:
        paths = [root] if root.is_file() else [*root.rglob("*.md"), *root.rglob("*.mdx")]
        for path in paths:
            if REMOVED_FLAT_MODULE.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_documented_slack_surface_module_paths_exist() -> None:
    roots = (*ROOT.glob("*.md"), ROOT / "docs", ROOT / "site" / "src" / "content" / "docs")
    missing: list[str] = []
    for root in roots:
        paths = [root] if root.is_file() else [*root.rglob("*.md"), *root.rglob("*.mdx")]
        for path in paths:
            text = path.read_text(encoding="utf-8")
            for match in SLACK_SURFACE_PATH.finditer(text):
                module_path = match.group(0)
                if not (ROOT / module_path).is_file():
                    missing.append(f"{path.relative_to(ROOT)}: {module_path}")
    assert missing == []
