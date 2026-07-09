#!/usr/bin/env python3
"""Tests for lib/compression_benchmark.py - the compression measurement arm.

Runs fully offline: the built-in arm is pure stdlib, and headroom is either
absent (marked not-run) or injected via a mock. No headroom-ai install and no
network are required, and no headroom numbers are ever fabricated.
"""

from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import compression_benchmark as cb  # noqa: E402
import pytest  # noqa: E402


def _payloads() -> list[cb.Payload]:
    payloads = cb.load_payloads()
    assert payloads, "built-in compression fixtures must exist"
    return payloads


# --------------------------------------------------------------------------
# Fixtures + tokenizer
# --------------------------------------------------------------------------
def test_fixtures_load_with_kinds() -> None:
    payloads = _payloads()
    kinds = {p.kind for p in payloads}
    # The three real tool-output shapes the task calls for.
    assert {"grep", "json", "log"} <= kinds


def test_count_tokens_labels_estimator() -> None:
    count, name = cb.count_tokens("hello world " * 100)
    assert count > 0
    assert name in ("tiktoken:cl100k_base", "estimate:chars/4")


def test_estimate_is_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force the offline estimate path and assert it is stable + labelled.
    monkeypatch.setitem(sys.modules, "tiktoken", None)
    a, na = cb.count_tokens("x" * 40)
    b, nb = cb.count_tokens("x" * 40)
    assert a == b == 10
    assert na == nb == "estimate:chars/4"


# --------------------------------------------------------------------------
# Built-in arm measures a real reduction
# --------------------------------------------------------------------------
def test_builtin_arm_reduces_tokens() -> None:
    for payload in _payloads():
        m = cb.measure_builtin(payload)
        assert m.ran
        assert m.applied, f"builtin should compact {payload.name}"
        assert m.final_bytes < m.original_bytes
        assert 0.0 < m.token_reduction <= 1.0
        assert m.tokenizer


# --------------------------------------------------------------------------
# headroom arm: not-run when absent, measured when present (mock)
# --------------------------------------------------------------------------
def test_headroom_marked_not_run_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cb.headroom_engine, "headroom_available", lambda env=None: False)
    report = cb.run_compression_benchmark(_payloads())
    assert report.headroom_available is False
    for r in report.results:
        assert r.headroom.ran is False
        assert "not installed" in r.headroom.note
        # Not-run means zeroed, explicitly flagged - never a fabricated ratio.
        assert r.headroom.token_reduction == 0.0
    assert report.headroom_mean_token_reduction is None


def test_headroom_arm_measured_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cb.headroom_engine, "headroom_available", lambda env=None: True)
    # A mock headroom that halves the text deterministically.
    monkeypatch.setattr(
        cb.headroom_engine,
        "compress",
        lambda text, **k: text[: len(text) // 2],
    )
    report = cb.run_compression_benchmark(_payloads())
    assert report.headroom_available is True
    for r in report.results:
        assert r.headroom.ran is True
        assert r.headroom.applied is True
        assert r.headroom.token_reduction > 0.0
    assert report.headroom_mean_token_reduction is not None
    assert report.builtin_mean_token_reduction is not None


def test_report_json_roundtrips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cb.headroom_engine, "headroom_available", lambda env=None: False)
    report = cb.run_compression_benchmark(_payloads(), label="before")
    payload = report.to_dict()
    assert payload["label"] == "before"
    assert payload["headroom_available"] is False
    assert "aggregate" in payload
    assert len(payload["results"]) == len(report.results)
    # Renders without raising.
    assert "compression" in cb.render_report_table(report)
    assert cb.render_report_json(report).startswith("{")


# --------------------------------------------------------------------------
# Aggregate averages over ALL measured payloads, incl. 0% (Codex P2)
# --------------------------------------------------------------------------
def test_aggregate_includes_zero_reduction_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cb.headroom_engine, "headroom_available", lambda env=None: False)
    big = cb.Payload(
        name="big.log",
        kind="log",
        text="\n".join(f"line {i} repeated content here" for i in range(600)) + "\n",
    )
    # Below the compactor's min-bytes floor: builtin leaves it untouched (0%).
    tiny = cb.Payload(name="tiny.txt", kind="log", text="ok\n")
    report = cb.run_compression_benchmark([big, tiny])

    tiny_m = next(r.builtin for r in report.results if r.payload == "tiny.txt")
    big_m = next(r.builtin for r in report.results if r.payload == "big.log")
    # The tiny payload was measured (ran) but not compacted (applied False) -> 0%.
    assert tiny_m.ran is True
    assert tiny_m.applied is False
    assert tiny_m.token_reduction == 0.0
    assert big_m.applied is True and big_m.token_reduction > 0.0

    # The mean averages over BOTH payloads, so the 0% miss pulls it down; it is
    # NOT the big payload's ratio alone.
    expected = round((big_m.token_reduction + 0.0) / 2, 4)
    assert report.builtin_mean_token_reduction == expected
    assert report.builtin_mean_token_reduction < big_m.token_reduction


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
