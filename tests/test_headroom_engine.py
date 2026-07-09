#!/usr/bin/env python3
"""Tests for lib/headroom_engine.py - the optional headroom compression glue.

The whole point is that headroom is OPTIONAL: absent, everything is a clean
no-op; present (mocked here via an injected ``headroom`` module), it compresses.
No test requires headroom-ai to actually be installed.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import headroom_engine as he  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_autofetch() -> None:
    he._autofetch_attempted = False


def _fake_headroom(compress_impl) -> types.ModuleType:
    mod = types.ModuleType("headroom")
    mod.compress = compress_impl  # type: ignore[attr-defined]
    return mod


# --------------------------------------------------------------------------
# Detection / availability - the no-op-when-absent contract
# --------------------------------------------------------------------------
def test_absent_headroom_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "headroom", raising=False)
    monkeypatch.setattr(he, "_importable", lambda: False)
    monkeypatch.setattr(he.shutil, "which", lambda _n: None)
    assert he.headroom_available({}) is False
    # compress cleanly returns None (never raises) when absent.
    assert he.compress("some big output", env={}) is None


def test_importable_library_is_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "headroom", _fake_headroom(lambda m, **k: m))
    assert he.detect({}).source == "library"
    assert he.headroom_available({}) is True


def test_cli_only_available_only_with_compress_cmd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "headroom", raising=False)
    monkeypatch.setattr(he, "_importable", lambda: False)
    monkeypatch.setattr(he.shutil, "which", lambda _n: "/usr/local/bin/headroom")
    # A resolved binary alone is NOT "available" (no known blob-compress cmd)...
    assert he.headroom_available({}) is False
    # ...but is once the operator configures a compress command.
    assert he.headroom_available({"ALFRED_HEADROOM_COMPRESS_CMD": "{bin} squeeze"}) is True


def test_explicit_bin_override_resolves(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delitem(sys.modules, "headroom", raising=False)
    monkeypatch.setattr(he, "_importable", lambda: False)
    monkeypatch.setattr(he.shutil, "which", lambda _n: None)
    fake_bin = tmp_path / "headroom"
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(0o755)
    det = he.detect({"ALFRED_HEADROOM_BIN": str(fake_bin)})
    assert det.bin_path == str(fake_bin)
    assert det.source == "cli"


# --------------------------------------------------------------------------
# Library compression + defensive extraction
# --------------------------------------------------------------------------
def test_compress_library_string_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "headroom", _fake_headroom(lambda m, **k: "SHRUNK"))
    assert he.compress("big blob", env={}) == "SHRUNK"


def test_compress_library_message_list_result(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_compress(messages, **kwargs):
        return [{"role": "user", "content": "compressed-body"}]

    monkeypatch.setitem(sys.modules, "headroom", _fake_headroom(fake_compress))
    assert he.compress("big blob", env={}) == "compressed-body"


def test_compress_passes_model_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_compress(messages, model=None):
        seen["model"] = model
        return "ok"

    monkeypatch.setitem(sys.modules, "headroom", _fake_headroom(fake_compress))
    he.compress("x" * 10, env={"ALFRED_HEADROOM_MODEL": "claude-x"})
    assert seen["model"] == "claude-x"


def test_compress_survives_engine_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(messages, **kwargs):
        raise RuntimeError("headroom exploded")

    monkeypatch.setitem(sys.modules, "headroom", _fake_headroom(boom))
    # An engine crash degrades to None, never propagates.
    assert he.compress("payload", env={}) is None


def test_extract_text_shapes() -> None:
    assert he._extract_text("plain") == "plain"
    assert he._extract_text({"content": "c"}) == "c"
    assert he._extract_text({"text": "t"}) == "t"
    assert he._extract_text([{"content": "a"}, {"content": "b"}]) == "a\nb"
    assert he._extract_text(12345) is None
    assert he._extract_text([1, 2, 3]) is None


# --------------------------------------------------------------------------
# Autofetch is opt-in and never fires without the flag
# --------------------------------------------------------------------------
def test_autofetch_noop_without_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[list[str]] = []
    monkeypatch.setattr(he.subprocess, "run", lambda cmd, **k: called.append(cmd))
    assert he.maybe_autofetch({}) is False
    assert called == []


def test_autofetch_runs_once_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[list[str]] = []
    monkeypatch.setattr(he.subprocess, "run", lambda cmd, **k: called.append(list(cmd)))
    env = {"ALFRED_HEADROOM_AUTOFETCH": "1"}
    assert he.maybe_autofetch(env) is True
    # A second call in the same process does not re-install.
    assert he.maybe_autofetch(env) is False
    assert called == [["pipx", "install", "headroom-ai"]]


def test_autofetch_custom_command(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[list[str]] = []
    monkeypatch.setattr(he.subprocess, "run", lambda cmd, **k: called.append(list(cmd)))
    env = {
        "ALFRED_HEADROOM_AUTOFETCH": "1",
        "ALFRED_HEADROOM_AUTOFETCH_CMD": "uv pip install headroom-ai",
    }
    he.maybe_autofetch(env)
    assert called == [["uv", "pip", "install", "headroom-ai"]]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
