"""Tests for Alfred's runtime path expansion."""

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from runtime_home import expand_user_path  # noqa: E402


def test_named_user_uses_platform_expansion(monkeypatch) -> None:
    expected = Path("/Users/alice/bin/tool")
    monkeypatch.setattr(Path, "expanduser", lambda _path: expected)

    assert expand_user_path({}, "~alice/bin/tool") == expected
