"""Compression benchmark: raw vs built-in vs Headroom on recorded tool output.

Measures how much recorded grep, JSON, build-log, and test output each engine
removes without dropping required facts. The same payloads run through a raw
control, the built-in compactor, and Headroom. The report includes token
reduction, the effective engine, and required-fact retention.

Measurement rules (the same rules used by ``lib/benchmark.py`` and
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
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import compression_engine
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
    exit_code: int = 0
    required_facts: tuple[str, ...] = ()


_KIND_BY_SUFFIX = {".json": "json", ".log": "log", ".grep": "grep", ".txt": "log"}


def default_fixture_dir() -> Path:
    """Built-in compression fixtures inside the repo checkout."""
    return Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "compression"


def _load_quality_manifest(fixture_dir: Path) -> dict[str, tuple[int, tuple[str, ...]]]:
    path = fixture_dir / "quality-manifest.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("compression-bench: could not read quality manifest %s (%s)", path, exc)
        return {}
    if not isinstance(raw, Mapping):
        return {}
    manifest: dict[str, tuple[int, tuple[str, ...]]] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not isinstance(value, Mapping):
            continue
        exit_code = value.get("exit_code", 0)
        facts = value.get("required_facts", [])
        if not isinstance(exit_code, int) or not isinstance(facts, list):
            continue
        clean_facts = tuple(fact for fact in facts if isinstance(fact, str) and fact)
        manifest[name] = (exit_code, clean_facts)
    return manifest


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
    quality = _load_quality_manifest(fixture_dir)
    for path in sorted(fixture_dir.iterdir()):
        if not path.is_file():
            continue
        if path.name == "quality-manifest.json":
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
        exit_code, required_facts = quality.get(path.name, (0, ()))
        payloads.append(
            Payload(
                name=path.name,
                kind=kind,
                text=text,
                exit_code=exit_code,
                required_facts=required_facts,
            )
        )
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
    effective_engine: str
    note: str = ""
    required_facts: int = 0
    retained_facts: int = 0
    fact_recall: float | None = None
    quality_passed: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _not_run(
    engine: str,
    note: str,
    required_facts: Sequence[str] = (),
) -> EngineMeasure:
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
        effective_engine="not-run",
        note=note,
        required_facts=len(required_facts),
    )


def _fact_score(final: str, required_facts: Sequence[str]) -> tuple[int, float | None, bool | None]:
    if not required_facts:
        return 0, None, None
    retained = sum(fact in final for fact in required_facts)
    recall = round(retained / len(required_facts), 4)
    return retained, recall, retained == len(required_facts)


def _measure(
    engine: str,
    original: str,
    final: str,
    applied: bool,
    required_facts: Sequence[str] = (),
    effective_engine: str | None = None,
    note: str = "",
) -> EngineMeasure:
    ob = len(original.encode("utf-8"))
    fb = len(final.encode("utf-8"))
    ot, tok = count_tokens(original)
    ft, _ = count_tokens(final)
    retained, fact_recall, quality_passed = _fact_score(final, required_facts)
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
        effective_engine=effective_engine or engine,
        note=note,
        required_facts=len(required_facts),
        retained_facts=retained,
        fact_recall=fact_recall,
        quality_passed=quality_passed,
    )


def measure_raw(payload: Payload) -> EngineMeasure:
    """Measure the uncompressed control on one payload."""
    return _measure(
        "raw",
        payload.text,
        payload.text,
        applied=False,
        required_facts=payload.required_facts,
        effective_engine="raw",
    )


def measure_builtin(payload: Payload) -> EngineMeasure:
    """Run the built-in compactor through its real exit-status gate."""
    result = tool_compactor.compact_output(
        payload.text,
        tool_name="Bash",
        exit_code=payload.exit_code,
    )
    return _measure(
        "builtin",
        payload.text,
        result.text,
        result.applied,
        payload.required_facts,
        effective_engine="builtin" if result.reason == "compacted" else "raw",
        note=result.reason,
    )


def measure_headroom(payload: Payload, env: dict[str, str] | None = None) -> EngineMeasure:
    """Run headroom on a payload, or mark the arm ``not-run`` when unavailable.

    The real selector can use Headroom, fall back to the built-in compactor, or
    pass failed output through unchanged. The report records that effective
    path instead of attributing fallback output to Headroom.
    """
    if not headroom_engine.headroom_available(env):
        return _not_run(
            "headroom",
            "no Headroom compression path in this environment",
            payload.required_facts,
        )
    resolved = dict(env or {})
    resolved["ALFRED_COMPRESSION_ENGINE"] = "headroom"
    result = compression_engine.compact_output_via_engine(
        payload.text,
        tool_name="Bash",
        exit_code=payload.exit_code,
        env=resolved,
    )
    if result.reason == "compacted_headroom":
        effective_engine = "headroom"
        note = "headroom"
    elif result.reason == "compacted":
        effective_engine = "builtin"
        note = "built-in fallback"
    else:
        effective_engine = "raw"
        note = result.reason
    return _measure(
        "headroom",
        payload.text,
        result.text,
        result.applied,
        payload.required_facts,
        effective_engine=effective_engine,
        note=note,
    )


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------
@dataclass
class PayloadResult:
    payload: str
    kind: str
    raw: EngineMeasure
    builtin: EngineMeasure
    headroom: EngineMeasure

    def to_dict(self) -> dict[str, Any]:
        return {
            "payload": self.payload,
            "kind": self.kind,
            "raw": self.raw.to_dict(),
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


def _quality_pass_rate(measures: Sequence[EngineMeasure]) -> float | None:
    scored = [measure.quality_passed for measure in measures if measure.quality_passed is not None]
    if not scored:
        return None
    return round(sum(bool(value) for value in scored) / len(scored), 4)


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

    @property
    def raw_quality_pass_rate(self) -> float | None:
        return _quality_pass_rate([r.raw for r in self.results])

    @property
    def builtin_quality_pass_rate(self) -> float | None:
        return _quality_pass_rate([r.builtin for r in self.results])

    @property
    def headroom_quality_pass_rate(self) -> float | None:
        if not self.headroom_available:
            return None
        return _quality_pass_rate([r.headroom for r in self.results])

    @property
    def headroom_effective_payloads(self) -> int:
        return sum(r.headroom.effective_engine == "headroom" for r in self.results)

    @property
    def headroom_builtin_fallback_payloads(self) -> int:
        return sum(r.headroom.effective_engine == "builtin" for r in self.results)

    @property
    def headroom_passthrough_payloads(self) -> int:
        return sum(r.headroom.effective_engine == "raw" for r in self.results)

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
                "raw_quality_pass_rate": self.raw_quality_pass_rate,
                "builtin_quality_pass_rate": self.builtin_quality_pass_rate,
                "headroom_quality_pass_rate": self.headroom_quality_pass_rate,
                "headroom_effective_payloads": self.headroom_effective_payloads,
                "headroom_builtin_fallback_payloads": self.headroom_builtin_fallback_payloads,
                "headroom_passthrough_payloads": self.headroom_passthrough_payloads,
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
        raw = measure_raw(payload)
        builtin = measure_builtin(payload)
        headroom = measure_headroom(payload, env=env)
        if builtin.ran and builtin.tokenizer:
            tokenizer = builtin.tokenizer
        results.append(
            PayloadResult(
                payload=payload.name,
                kind=payload.kind,
                raw=raw,
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
    header = (
        f"  {'payload':<22} {'kind':<6} {'raw tok':<8} {'builtin tok':<12} "
        f"{'headroom tok':<13} {'headroom via':<13} {'retained facts'}"
    )
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))
    for r in report.results:
        raw = f"{r.raw.token_reduction * 100:.1f}%"
        b = f"{r.builtin.token_reduction * 100:.1f}%" if r.builtin.ran else "-"
        if r.headroom.ran:
            h = f"{r.headroom.token_reduction * 100:.1f}%"
        else:
            h = "not-run"
        if r.raw.quality_passed is None:
            quality = "-"
        else:
            raw_q = f"{r.raw.retained_facts}/{r.raw.required_facts}"
            builtin_q = f"{r.builtin.retained_facts}/{r.builtin.required_facts}"
            headroom_q = (
                f"{r.headroom.retained_facts}/{r.headroom.required_facts}"
                if r.headroom.ran
                else "not-run"
            )
            quality = f"raw {raw_q}, builtin {builtin_q}, headroom {headroom_q}"
        lines.append(
            f"  {r.payload:<22} {r.kind:<6} {raw:<8} {b:<12} {h:<13} "
            f"{r.headroom.effective_engine:<13} {quality}"
        )
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
    lines.append(
        "quality gate pass rate raw: "
        + _fmt(report.raw_quality_pass_rate)
        + "   builtin: "
        + _fmt(report.builtin_quality_pass_rate)
        + "   headroom: "
        + (_fmt(report.headroom_quality_pass_rate) if report.headroom_available else "not-run")
    )
    if report.headroom_available:
        lines.append(
            "headroom routing        headroom: "
            f"{report.headroom_effective_payloads}   builtin fallback: "
            f"{report.headroom_builtin_fallback_payloads}   raw passthrough: "
            f"{report.headroom_passthrough_payloads}"
        )
    lines.append("")
    lines.append("note: token reduction uses the labelled tokenizer above; byte reduction is")
    lines.append("exact. Only selected arms that ran are scored. The effective-engine column")
    lines.append("shows Headroom, built-in fallback, raw passthrough, or not-run.")
    lines.append("The quality gate requires every declared failure, path, line number, test")
    lines.append("count, and final command status to remain in the measured output.")
    return "\n".join(lines)


def render_report_json(report: CompressionReport) -> str:
    return json.dumps(report.to_dict(), indent=2, default=str)
