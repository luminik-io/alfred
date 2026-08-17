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


def test_quality_fixtures_declare_required_facts_and_exit_status() -> None:
    payloads = _payloads()
    quality = [payload for payload in payloads if payload.required_facts]

    assert {payload.name for payload in quality} == {
        "data.json",
        "grep-symbols.txt",
        "log-build.txt",
        "quality-failure.txt",
    }
    failure = next(payload for payload in quality if payload.exit_code != 0)
    assert "tests/test_orders.py:42" in failure.required_facts
    assert "1 failed, 12 passed" in failure.required_facts
    assert "exit code 1" in failure.required_facts


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
        if payload.exit_code == 0:
            assert m.applied, f"builtin should compact {payload.name}"
            assert m.final_bytes < m.original_bytes
            assert 0.0 < m.token_reduction <= 1.0
        else:
            assert m.applied is False
            assert m.final_bytes == m.original_bytes
            assert m.token_reduction == 0.0
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
        assert "no Headroom compression path" in r.headroom.note
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
        if next(p for p in _payloads() if p.name == r.payload).exit_code == 0:
            assert r.headroom.applied is True
            assert r.headroom.token_reduction > 0.0
        else:
            assert r.headroom.applied is False
            assert r.headroom.token_reduction == 0.0
    assert report.headroom_mean_token_reduction is not None
    assert report.builtin_mean_token_reduction is not None


def test_report_scores_raw_builtin_and_headroom_fact_retention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = cb.Payload(
        name="failure.log",
        kind="log",
        text=("progress\n" * 900)
        + "FAILED tests/test_orders.py:42 AssertionError: expected 200, got 500\n"
        + "1 failed, 12 passed\ncommand exit code 1\n",
        exit_code=1,
        required_facts=(
            "FAILED",
            "tests/test_orders.py:42",
            "AssertionError: expected 200, got 500",
            "1 failed, 12 passed",
            "exit code 1",
        ),
    )
    monkeypatch.setattr(cb.headroom_engine, "headroom_available", lambda env=None: True)
    compress_calls: list[str] = []
    monkeypatch.setattr(
        cb.headroom_engine,
        "compress",
        lambda text, **kwargs: compress_calls.append(text) or "hidden",
    )

    report = cb.run_compression_benchmark([payload])
    result = report.results[0]

    assert result.raw.quality_passed is True
    assert result.raw.effective_engine == "raw"
    assert result.raw.fact_recall == 1.0
    assert result.builtin.quality_passed is True
    assert result.builtin.effective_engine == "raw"
    assert result.builtin.fact_recall == 1.0
    assert result.headroom.quality_passed is True
    assert result.headroom.effective_engine == "raw"
    assert result.headroom.fact_recall == 1.0
    assert result.headroom.applied is False
    assert compress_calls == []
    assert report.raw_quality_pass_rate == 1.0
    assert report.builtin_quality_pass_rate == 1.0
    assert report.headroom_quality_pass_rate == 1.0


def test_headroom_report_marks_builtin_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = cb.Payload(name="large.log", kind="log", text="same output\n" * 900)
    monkeypatch.setattr(cb.headroom_engine, "headroom_available", lambda env=None: True)
    monkeypatch.setattr(cb.headroom_engine, "compress", lambda text, **kwargs: None)

    report = cb.run_compression_benchmark([payload])
    result = report.results[0].headroom

    assert result.ran is True
    assert result.applied is True
    assert result.effective_engine == "builtin"
    assert result.note == "built-in fallback"
    assert report.headroom_effective_payloads == 0
    assert report.headroom_builtin_fallback_payloads == 1
    assert report.headroom_passthrough_payloads == 0


def test_quality_gate_fails_when_a_compressor_drops_a_required_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = cb.Payload(
        name="success.log",
        kind="log",
        text=("progress\n" * 900)
        + "tests/test_api.py:17\n24 passed, 2 skipped\ncommand exit code 0\n",
        exit_code=0,
        required_facts=("tests/test_api.py:17", "24 passed, 2 skipped", "exit code 0"),
    )
    monkeypatch.setattr(cb.headroom_engine, "headroom_available", lambda env=None: True)
    monkeypatch.setattr(
        cb.headroom_engine,
        "compress",
        lambda text, **kwargs: "24 passed, 2 skipped\ncommand exit code 0\n",
    )

    report = cb.run_compression_benchmark([payload])
    headroom = report.results[0].headroom

    assert headroom.retained_facts == 2
    assert headroom.required_facts == 3
    assert headroom.fact_recall == pytest.approx(2 / 3, abs=0.0001)
    assert headroom.quality_passed is False
    assert report.headroom_quality_pass_rate == 0.0


def test_report_json_roundtrips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cb.headroom_engine, "headroom_available", lambda env=None: False)
    report = cb.run_compression_benchmark(_payloads(), label="before")
    payload = report.to_dict()
    assert payload["label"] == "before"
    assert payload["headroom_available"] is False
    assert "aggregate" in payload
    assert "raw_quality_pass_rate" in payload["aggregate"]
    assert "builtin_quality_pass_rate" in payload["aggregate"]
    assert "headroom_quality_pass_rate" in payload["aggregate"]
    assert "headroom_effective_payloads" in payload["aggregate"]
    assert "headroom_builtin_fallback_payloads" in payload["aggregate"]
    assert "headroom_passthrough_payloads" in payload["aggregate"]
    assert len(payload["results"]) == len(report.results)
    assert "raw" in payload["results"][0]
    # Renders without raising.
    table = cb.render_report_table(report)
    assert "compression" in table
    assert "raw tok" in table
    assert "headroom via" in table
    assert "quality gate pass rate" in table
    assert "retained facts" in table
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
