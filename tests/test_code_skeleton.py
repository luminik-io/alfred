"""Tests for deterministic code skeleton projection in ``code_graph``."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from code_graph import (  # noqa: E402
    CODEGRAPH_SCHEMA,
    project_skeleton,
    skeleton_for_path,
)

_PY_SOURCE = '''"""Module doc."""
import os


class Widget:
    """A small widget."""

    def render(self, x):
        """Render it."""
        return x + 1


def helper(a, b):
    total = a + b
    return total
'''

_PY_SYMBOLS = [
    {"name": "Widget", "line": 5},
    {"name": "render", "line": 8},
    {"name": "helper", "line": 13},
]


def test_skeleton_keeps_signatures_and_elides_bodies() -> None:
    out = project_skeleton("pkg/widget.py", _PY_SOURCE, symbols=_PY_SYMBOLS)

    # Header names the file, language, and symbol count.
    assert out.splitlines()[0] == ("skeleton: pkg/widget.py (python) - 3 symbol(s), bodies elided")
    # Signatures are preserved verbatim.
    assert "class Widget:" in out
    assert "    def render(self, x):" in out
    assert "def helper(a, b):" in out
    # First docstring line kept for Python symbols.
    assert '    """A small widget."""' in out
    assert '        """Render it."""' in out
    # Bodies are elided with an explicit, indented marker.
    assert "        [body: 1 line(s) elided]" in out
    assert "    [body: 2 line(s) elided]" in out
    assert "[preamble: 2 line(s) elided]" in out


def test_skeleton_never_leaks_body_source() -> None:
    out = project_skeleton("pkg/widget.py", _PY_SOURCE, symbols=_PY_SYMBOLS)

    # Implementation lines must not appear in an orientation skeleton.
    assert "return x + 1" not in out
    assert "total = a + b" not in out
    assert "return total" not in out


def test_skeleton_is_deterministic() -> None:
    first = project_skeleton("pkg/widget.py", _PY_SOURCE, symbols=_PY_SYMBOLS)
    # Symbol order in the index must not change the output.
    shuffled = list(reversed(_PY_SYMBOLS))
    second = project_skeleton("pkg/widget.py", _PY_SOURCE, symbols=shuffled)
    assert first == second


def test_skeleton_without_symbols_uses_head_slice() -> None:
    source = "\n".join(f"line {i}" for i in range(1, 21))
    out = project_skeleton("data/config.txt", source, symbols=[], head_lines=5)

    assert "0 symbol(s)" in out
    assert "line 1" in out
    assert "line 5" in out
    assert "line 6" not in out
    assert "[... 15 more line(s) elided]" in out


def test_skeleton_handles_multiline_signature() -> None:
    source = "def wide(\n    a,\n    b,\n):\n    return a + b\n"
    out = project_skeleton("m.py", source, symbols=[{"name": "wide", "line": 1}])

    assert "def wide(" in out
    assert "    a," in out
    assert "):" in out
    assert "return a + b" not in out
    assert "[body: 1 line(s) elided]" in out


def test_skeleton_brace_language_stops_at_open_brace() -> None:
    source = "export function run(x: number): number {\n  return x * 2;\n}\n"
    out = project_skeleton(
        "src/run.ts",
        source,
        symbols=[{"name": "run", "line": 1}],
        language="typescript",
    )

    assert "export function run(x: number): number {" in out
    assert "return x * 2;" not in out
    assert "(typescript)" in out


def _code_map(repo_root: Path) -> dict:
    return {
        "generated_at": "2026-07-09T00:00:00Z",
        "repos": {
            "svc": {
                "head_sha": "deadbeef",
                "files": [
                    {
                        "path": "app/widget.py",
                        "language": "python",
                        "symbols": _PY_SYMBOLS,
                        "imports": ["os"],
                    }
                ],
                "edges": [],
            }
        },
    }


def test_skeleton_for_path_reads_source_and_reuses_index(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "widget.py").write_text(_PY_SOURCE, encoding="utf-8")

    result = skeleton_for_path(
        _code_map(tmp_path),
        repo="svc",
        path="app/widget.py",
        repo_root=tmp_path,
    )

    assert result["schema"] == CODEGRAPH_SCHEMA
    assert result["kind"] == "skeleton"
    assert result["match_status"] == "exact"
    assert result["matched_file"] == "app/widget.py"
    assert result["language"] == "python"
    assert result["symbol_count"] == 3
    assert "class Widget:" in result["skeleton"]
    assert "return total" not in result["skeleton"]


def test_skeleton_for_path_symbol_filter(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "widget.py").write_text(_PY_SOURCE, encoding="utf-8")

    result = skeleton_for_path(
        _code_map(tmp_path),
        repo="svc",
        path="app/widget.py",
        repo_root=tmp_path,
        symbol="helper",
    )

    assert result["symbol_count"] == 1
    assert "def helper(a, b):" in result["skeleton"]
    assert "class Widget:" not in result["skeleton"]


def test_skeleton_for_path_unmatched_path(tmp_path: Path) -> None:
    result = skeleton_for_path(
        _code_map(tmp_path),
        repo="svc",
        path="app/missing.py",
        repo_root=tmp_path,
    )

    assert result["match_status"] == "not_found"
    assert result["skeleton"] == ""
    assert result["reason"] == "not_found"


def test_skeleton_for_path_source_unavailable(tmp_path: Path) -> None:
    # File is in the index but not on disk under repo_root.
    result = skeleton_for_path(
        _code_map(tmp_path),
        repo="svc",
        path="app/widget.py",
        repo_root=tmp_path,
    )

    assert result["match_status"] == "exact"
    assert result["skeleton"] == ""
    assert result["reason"] == "source_unavailable"
