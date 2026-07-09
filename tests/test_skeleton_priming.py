"""Tests for optional skeleton run-priming."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from agent_runner.skeleton_priming import (  # noqa: E402
    CONTEXT_HEADER,
    skeleton_priming_block,
    skeleton_priming_enabled,
)

_SOURCE = '''"""Widget module."""


def build(x):
    """Build a widget."""
    return x * 2
'''


def _code_map() -> dict:
    return {
        "repos": {
            "svc": {
                "files": [
                    {
                        "path": "app/widget.py",
                        "language": "python",
                        "symbols": [{"name": "build", "line": 4}],
                        "imports": [],
                    }
                ],
                "edges": [],
            }
        }
    }


def test_priming_off_by_default(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "widget.py").write_text(_SOURCE, encoding="utf-8")

    block = skeleton_priming_block(
        "svc",
        ["app/widget.py"],
        workdir=tmp_path,
        code_map=_code_map(),
        env={},
    )
    assert block == ""


def test_priming_renders_when_armed(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "widget.py").write_text(_SOURCE, encoding="utf-8")

    block = skeleton_priming_block(
        "svc",
        ["app/widget.py"],
        workdir=tmp_path,
        code_map=_code_map(),
        env={"ALFRED_SKELETON_PRIMING": "1"},
    )

    assert CONTEXT_HEADER in block
    assert "def build(x):" in block
    assert "bodies elided" in block
    # Orientation only: the body is never surfaced.
    assert "return x * 2" not in block


def test_priming_empty_when_no_paths(tmp_path: Path) -> None:
    assert (
        skeleton_priming_block("svc", [], workdir=tmp_path, env={"ALFRED_SKELETON_PRIMING": "1"})
        == ""
    )
    assert (
        skeleton_priming_block(
            "", ["app/widget.py"], workdir=tmp_path, env={"ALFRED_SKELETON_PRIMING": "1"}
        )
        == ""
    )


def test_skeleton_priming_enabled_env() -> None:
    assert skeleton_priming_enabled({}) is False
    assert skeleton_priming_enabled({"ALFRED_SKELETON_PRIMING": "1"}) is True
    assert skeleton_priming_enabled({"ALFRED_SKELETON_PRIMING": "off"}) is False
