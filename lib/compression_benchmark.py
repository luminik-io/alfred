"""Compression benchmark: builtin #453 vs headroom on real tool-output payloads.

This answers one honest question: **on the kind of verbose output a firing
actually produces (grep dumps, JSON blobs, build logs), how much context does
each compression engine save?** It runs the *same* payloads through the
built-in #453 compactor and through headroom, and reports the token-reduction
ratio for each.

Honesty conventions (same spirit as ``lib/benchmark.py`` and
``lib/memory_benchmark.py``):

* **Only what it measures.** Reductions are computed from the actual compressed
  output, never estimated for an engine that did not run. When headroom is not
  installed in the test/host environment, its arm is reported as ``not-run``,
  not zero and not a guess.
* **Offline-testable.** The built-in arm is pure stdlib. Token counting prefers
  ``tiktoken`` when installed but falls back to a deterministic char/4 estimate
  and *labels which estimator produced the number*, so the harness runs and is
  unit-tested with no network and no optional dependency.
* **Byte reduction is exact; token reduction is labelled.** Byte counts are
  exact. Token counts carry the tokenizer name so a reader knows whether they
  are ``tiktoken`` truth or a documented estimate.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import headroom_engine
import tool_compactor

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Tokenizing (tiktoken if present, else a labelled deterministic estimate)
# --------------------------------------------------------------------------
def count_tokens(text: str) -> tuple[int, str]:
    """Return ``(token_count, estimator_name)`` for ``text``.

    Prefers ``tiktoken`` (cl100k_base) when installed; otherwise a deterministic
    ``ceil(len/4)`` estimate. The estimator name travels with the number so a
    report never presents an estimate as if it were exact.
    """
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text)), "tiktoken:cl100k_base"
    except Exception:
        n = len(text or "")
        return (n + 3) // 4, "estimate:chars/4"


def _ratio(original: int, final: int) -> float:
    """Reduction ratio in [0, 1]: fraction removed. 0.0 when nothing to reduce."""
    if original <= 0:
        return 0.0
    return round(max(0.0, 1.0 - (final / original)), 4)


# --------------------------------------------------------------------------
# Payload fixtures
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Payload:
    """One real tool-output sample fed to both engines."""

    name: str
    kind: str  # "grep" | "json" | "log" | other
    text: str


_KIND_BY_SUFFIX = {".json": "json", ".log": "log", ".grep": "grep", ".txt": "log"}


def default_fixture_dir() -> Path:
    """Built-in compression fixtures inside the repo checkout."""
    return Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "compression"


def load_payloads(fixture_dir: Path | None = None) -> list[Payload]:
    """Load payload files from a fixture dir, tolerating a missing dir/file.

    Kind is inferred from the file name: ``*.grep`` / ``*.json`` / ``*.log`` /
    ``*.txt``. A leading ``<kind>-`` in the stem also sets the kind (so
    ``grep-symbols.txt`` is a grep payload).
    """
    fixture_dir = fixture_dir or default_fixture_dir()
    payloads: list[Payload] = []
    if not fixture_dir.is_dir():
        logger.warning("compression-bench: fixture dir not found: %s", fixture_dir)
        return payloads
    for path in sorted(fixture_dir.iterdir()):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("compression-bench: could not read %s (%s)", path, exc)
            continue
        stem = path.stem
        kind = _KIND_BY_SUFFIX.get(path.suffix.lower(), "log")
        for prefix in ("grep", "json", "log"):
            if stem.startswith(prefix + "-"):
                kind = prefix
                break
        payloads.append(Payload(name=path.name, kind=kind, text=text))
    return payloads


# --------------------------------------------------------------------------
# Per-engine measurement
# --------------------------------------------------------------------------
@dataclass
class EngineMeasure:
    """One engine's result on one payload."""

    engine: str
    ran: bool
    applied: bool
    original_bytes: int
    final_bytes: int
    original_tokens: int
    final_tokens: int
    byte_reduction: float
    token_reduction: float
    tokenizer: str
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _not_run(engine: str, note: str) -> EngineMeasure:
    return EngineMeasure(
        engine=engine,
        ran=False,
        applied=False,
        original_bytes=0,
        final_bytes=0,
        original_tokens=0,
        final_tokens=0,
        byte_reduction=0.0,
        token_reduction=0.0,
        tokenizer="",
        note=note,
    )


def _measure(engine: str, original: str, final: str, applied: bool) -> EngineMeasure:
    ob = len(original.encode("utf-8"))
    fb = len(final.encode("utf-8"))
    ot, tok = count_tokens(original)
    ft, _ = count_tokens(final)
    return EngineMeasure(
        engine=engine,
        ran=True,
        applied=applied,
        original_bytes=ob,
        final_bytes=fb,
        original_tokens=ot,
        final_tokens=ft,
        byte_reduction=_ratio(ob, fb),
        token_reduction=_ratio(ot, ft),
        tokenizer=tok,
    )


def measure_builtin(payload: Payload) -> EngineMeasure:
    """Run the built-in #453 compactor on a confirmed-success payload."""
    result = tool_compactor.compact_output(payload.text, tool_name="Bash", exit_code=0)
    return _measure("builtin", payload.text, result.text, result.applied)


def measure_headroom(payload: Payload, env: dict[str, str] | None = None) -> EngineMeasure:
    """Run headroom on a payload, or mark the arm ``not-run`` when unavailable.

    Only measures numbers headroom actually produced. When headroom is absent or
    declines (returns ``None``), the arm is honestly reported as not-run, never
    fabricated.
    """
    if not headroom_engine.headroom_available(env):
        return _not_run("headroom", "headroom not installed in this environment")
    compressed = headroom_engine.compress(payload.text, env=env)
    if compressed is None:
        return _not_run("headroom", "headroom declined this payload (no output)")
    return _measure("headroom", payload.text, compressed, applied=True)


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------
@dataclass
class PayloadResult:
    payload: str
    kind: str
    builtin: EngineMeasure
    headroom: EngineMeasure

    def to_dict(self) -> dict[str, Any]:
        return {
            "payload": self.payload,
            "kind": self.kind,
            "builtin": self.builtin.to_dict(),
            "headroom": self.headroom.to_dict(),
        }


def _mean_reduction(measures: Sequence[EngineMeasure], attr: str) -> float | None:
    # Average over EVERY payload the engine actually measured, not only the ones
    # it chose to compress. A payload the engine left untouched is a real 0%
    # reduction and must count, otherwise the mean is biased upward by silently
    # dropping the misses. Only ``not-run`` measures (the arm never executed,
    # e.g. headroom absent) are excluded, since there is no measurement to average.
    ran = [getattr(m, attr) for m in measures if m.ran]
    if not ran:
        return None
    return round(sum(ran) / len(ran), 4)


@dataclass
class CompressionReport:
    label: str
    generated_at: datetime
    results: list[PayloadResult]
    headroom_available: bool
    tokenizer: str

    @property
    def builtin_mean_token_reduction(self) -> float | None:
        return _mean_reduction([r.builtin for r in self.results], "token_reduction")

    @property
    def headroom_mean_token_reduction(self) -> float | None:
        return _mean_reduction([r.headroom for r in self.results], "token_reduction")

    @property
    def builtin_mean_byte_reduction(self) -> float | None:
        return _mean_reduction([r.builtin for r in self.results], "byte_reduction")

    @property
    def headroom_mean_byte_reduction(self) -> float | None:
        return _mean_reduction([r.headroom for r in self.results], "byte_reduction")

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "generated_at": self.generated_at.isoformat(),
            "headroom_available": self.headroom_available,
            "tokenizer": self.tokenizer,
            "aggregate": {
                "builtin_mean_token_reduction": self.builtin_mean_token_reduction,
                "headroom_mean_token_reduction": self.headroom_mean_token_reduction,
                "builtin_mean_byte_reduction": self.builtin_mean_byte_reduction,
                "headroom_mean_byte_reduction": self.headroom_mean_byte_reduction,
            },
            "results": [r.to_dict() for r in self.results],
        }


def run_compression_benchmark(
    payloads: Sequence[Payload],
    *,
    label: str = "run",
    env: dict[str, str] | None = None,
    now: datetime | None = None,
) -> CompressionReport:
    """Measure both engines over ``payloads`` and fold into a report."""
    results: list[PayloadResult] = []
    tokenizer = "estimate:chars/4"
    for payload in payloads:
        builtin = measure_builtin(payload)
        headroom = measure_headroom(payload, env=env)
        if builtin.ran and builtin.tokenizer:
            tokenizer = builtin.tokenizer
        results.append(
            PayloadResult(
                payload=payload.name,
                kind=payload.kind,
                builtin=builtin,
                headroom=headroom,
            )
        )
    return CompressionReport(
        label=label,
        generated_at=now or datetime.now(UTC),
        results=results,
        headroom_available=headroom_engine.headroom_available(env),
        tokenizer=tokenizer,
    )


def render_report_table(report: CompressionReport) -> str:
    ts = report.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []
    lines.append(f"alfred-benchmark compression - label={report.label!r} @ {ts}")
    lines.append(f"payloads: {len(report.results)}   tokenizer: {report.tokenizer}")
    lines.append(
        "headroom: " + ("available" if report.headroom_available else "NOT installed (arm not-run)")
    )
    lines.append("")
    header = f"  {'payload':<22} {'kind':<6} {'builtin tok':<12} {'headroom tok':<13}"
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))
    for r in report.results:
        b = f"{r.builtin.token_reduction * 100:.1f}%" if r.builtin.ran else "-"
        if r.headroom.ran:
            h = f"{r.headroom.token_reduction * 100:.1f}%"
        else:
            h = "not-run"
        lines.append(f"  {r.payload:<22} {r.kind:<6} {b:<12} {h:<13}")
    lines.append("")

    def _fmt(v: float | None) -> str:
        return "-" if v is None else f"{v * 100:.1f}%"

    lines.append(
        "mean token reduction   builtin: "
        + _fmt(report.builtin_mean_token_reduction)
        + "   headroom: "
        + (_fmt(report.headroom_mean_token_reduction) if report.headroom_available else "not-run")
    )
    lines.append(
        "mean byte reduction    builtin: "
        + _fmt(report.builtin_mean_byte_reduction)
        + "   headroom: "
        + (_fmt(report.headroom_mean_byte_reduction) if report.headroom_available else "not-run")
    )
    lines.append("")
    lines.append("note: token reduction uses the labelled tokenizer above; byte reduction is")
    lines.append("exact. Only engines that actually ran are scored - headroom is marked")
    lines.append("not-run when it is not installed, never zero and never a guess.")
    return "\n".join(lines)


def render_report_json(report: CompressionReport) -> str:
    return json.dumps(report.to_dict(), indent=2, default=str)
