"""Lint-style guard: ``server/*`` reaches ``agent_runner`` only via the facade.

The 2026-07-11 engineering audit flagged the dashboard layer deep-importing
runtime internals (``agent_runner.metadata`` / ``agent_runner.paths`` /
``agent_runner.github``) straight from ``server/views.py``. Those imports now
live behind :mod:`server.runtime_facade`, the ONE module allowed to touch
``agent_runner`` internals.

This test greps every Python file under ``lib/server/`` and fails if any file
other than ``runtime_facade.py`` imports from ``agent_runner``. It is a
structural lint, not a behavior test: it keeps the boundary from silently
regressing when someone adds a new server route and reaches for a runtime
symbol directly. Prose mentions of ``agent_runner`` in comments/docstrings are
fine; only actual ``import`` statements are forbidden.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SERVER_DIR = REPO / "lib" / "server"
FACADE = SERVER_DIR / "runtime_facade.py"


def _server_python_files() -> list[Path]:
    return sorted(p for p in SERVER_DIR.rglob("*.py") if p != FACADE)


def _imports_agent_runner(source: str) -> bool:
    """True when ``source`` has an ``import`` statement pulling ``agent_runner``.

    Parses the module and inspects only ``import``/``from ... import`` nodes, so
    a docstring or comment that merely names ``agent_runner`` never trips the
    guard. Both ``import agent_runner`` and ``from agent_runner[...] import x``
    (including the deep ``from agent_runner.metadata import ...`` form) count.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "agent_runner" or alias.name.startswith("agent_runner."):
                    return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "agent_runner" or module.startswith("agent_runner."):
                return True
    return False


def test_server_does_not_deep_import_agent_runner() -> None:
    offenders = [
        str(path.relative_to(REPO))
        for path in _server_python_files()
        if _imports_agent_runner(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], (
        "server/* must reach agent_runner only through server.runtime_facade; "
        f"these files import agent_runner directly: {offenders}"
    )


def test_runtime_facade_is_the_single_boundary() -> None:
    # The facade itself is the one place the boundary is crossed. If it stops
    # importing agent_runner, either the facade was gutted or the runtime moved,
    # and this guard would be vacuously true, so pin the expectation explicitly.
    assert _imports_agent_runner(FACADE.read_text(encoding="utf-8"))
