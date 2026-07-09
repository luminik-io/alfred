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


class _FakeCompressResult:
    """Shape of the real headroom-ai ``CompressResult``: compressed messages
    plus savings metadata, NOT a bare string."""

    def __init__(self, messages, tokens_saved: int = 0, compression_ratio: float = 0.0) -> None:
        self.messages = messages
        self.tokens_saved = tokens_saved
        self.compression_ratio = compression_ratio


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
# CompressResult OBJECT unwrap (Codex P1): compress returns an object, not a str
# --------------------------------------------------------------------------
def test_extract_text_unwraps_compressresult_object() -> None:
    result = _FakeCompressResult(
        messages=[{"role": "user", "content": "SHRUNK-BODY"}],
        tokens_saved=1200,
        compression_ratio=0.8,
    )
    assert he._extract_text(result) == "SHRUNK-BODY"

    # Single-field compressed-text objects are unwrapped too.
    class _Obj:
        compressed = "single-field"

    assert he._extract_text(_Obj()) == "single-field"


def test_compress_library_unwraps_compressresult(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_compress(messages, **kwargs):
        # The real headroom returns a CompressResult, not a string.
        return _FakeCompressResult(
            messages=[{"role": "user", "content": "compressed-tool-output"}],
            tokens_saved=900,
            compression_ratio=0.75,
        )

    monkeypatch.setitem(sys.modules, "headroom", _fake_headroom(fake_compress))
    # Must read the compressed text out of the object, NOT fall back to builtin.
    assert he.compress("big blob " * 100, env={}) == "compressed-tool-output"


def test_compress_library_returns_none_only_when_truly_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A CompressResult with no usable text is the only case that yields None.
    monkeypatch.setitem(
        sys.modules, "headroom", _fake_headroom(lambda m, **k: _FakeCompressResult(messages=[]))
    )
    assert he.compress("payload", env={}) is None


# --------------------------------------------------------------------------
# Tool-output message (Codex P2): role is configurable, defaults to user
# --------------------------------------------------------------------------
def test_message_role_defaults_to_user(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_compress(messages, **kwargs):
        seen["role"] = messages[0]["role"]
        return "x"

    monkeypatch.setitem(sys.modules, "headroom", _fake_headroom(fake_compress))
    he.compress("y" * 10, env={})
    assert seen["role"] == "user"


def test_message_role_override(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_compress(messages, **kwargs):
        seen["role"] = messages[0]["role"]
        return "x"

    monkeypatch.setitem(sys.modules, "headroom", _fake_headroom(fake_compress))
    he.compress("y" * 10, env={"ALFRED_HEADROOM_MESSAGE_ROLE": "tool"})
    assert seen["role"] == "tool"


# --------------------------------------------------------------------------
# CLI compression uses shlex (Greptile P1): quoted args stay intact
# --------------------------------------------------------------------------
def test_compress_cli_shlex_splits_quoted_args(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "headroom", raising=False)
    monkeypatch.setattr(he, "_importable", lambda: False)
    monkeypatch.setattr(he.shutil, "which", lambda _n: "/usr/local/bin/headroom")
    captured: dict[str, object] = {}

    class _Proc:
        returncode = 0
        stdout = "compressed-cli"
        stderr = ""

    def fake_run(parts, **kwargs):
        captured["parts"] = parts
        return _Proc()

    monkeypatch.setattr(he.subprocess, "run", fake_run)
    env = {"ALFRED_HEADROOM_COMPRESS_CMD": '{bin} squeeze --mode "high json"'}
    out = he.compress("payload", env=env)
    assert out == "compressed-cli"
    # The quoted "high json" is ONE argument, not two.
    assert captured["parts"] == ["/usr/local/bin/headroom", "squeeze", "--mode", "high json"]


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


def test_autofetch_custom_command_shlex_quoting(monkeypatch: pytest.MonkeyPatch) -> None:
    # A quoted extras spec must survive as ONE token (Greptile P2).
    called: list[list[str]] = []
    monkeypatch.setattr(he.subprocess, "run", lambda cmd, **k: called.append(list(cmd)))
    env = {
        "ALFRED_HEADROOM_AUTOFETCH": "1",
        "ALFRED_HEADROOM_AUTOFETCH_CMD": 'pipx install "headroom-ai[all]"',
    }
    he.maybe_autofetch(env)
    assert called == [["pipx", "install", "headroom-ai[all]"]]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
