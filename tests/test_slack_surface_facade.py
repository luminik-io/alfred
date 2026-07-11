"""Regression coverage for the lazy public Slack surface facade."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


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
