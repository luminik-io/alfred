"""AI-slop detector: scan a directory tree for vocabulary, phrases, and
patterns that mark text as LLM-authored.

This module is the scanner library. It is stateless and stdlib-only.
The CLI (``bin/slop-detector.py``) and any scheduled wrapper (e.g.
``bin/curator.py``) import from here.

Design boundaries

- **Single responsibility.** This file owns *scanning*: load a rule pack,
  walk a directory, return a structured ``ScanReport``. It does not
  print, post to Slack, or touch state files. The CLI handles all I/O.
- **Open/closed.** Rules live in JSON. To add a new banned word you edit
  the rule pack, not this module. Rule types are ``word``, ``phrase``,
  ``regex``, and ``pattern`` (``pattern`` is a soft alias for ``regex``).
- **Dependency inversion.** The public entry point ``scan_path`` accepts
  a fully constructed ``RulePack`` dataclass; tests inject in-memory
  packs without writing JSON to disk.
- **No hidden state.** No env vars, no globals beyond the constants the
  rule pack itself provides. The same inputs always produce the same
  ``ScanReport``.

Code regions excluded from matching

Code fences (``` ... ```), inline backticks, HTML comments, and JSX
comments are stripped before matching so an intentional example ("we
never say 'leverage'") does not flag itself. Stripping replaces the
region with whitespace of the SAME length, preserving newlines, so the
line numbers reported against the cleaned text still map 1:1 to the
original file. (A substring-based exclusion would miss when the same
banned word appears both in a fenced example and in real prose
elsewhere in the file.)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Rule-type literals. ``pattern`` is a soft alias for ``regex`` so rule
# packs can label rhythmic / structural rules separately from raw regex.
RULE_TYPES = ("word", "phrase", "regex", "pattern")

# Default code-region strippers. Order matters: fences before inline
# backticks so a fenced block does not get partially matched by the
# inline pattern.
_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]+`")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_JSX_COMMENT = re.compile(r"\{/\*.*?\*/\}", re.DOTALL)


# --------------------------------------------------------------------------
# Dataclasses
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    """A single banned-vocab rule, compiled and ready to match."""

    id: str
    type: str
    severity: str
    value: str
    reason: str
    pattern: re.Pattern[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "severity": self.severity,
            "value": self.value,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RulePack:
    """A loaded, compiled set of rules plus scan configuration."""

    name: str
    version: str
    description: str
    severities: tuple[str, ...]
    skip_dirs: frozenset[str]
    include_globs: tuple[str, ...]
    rules: tuple[Rule, ...]


@dataclass(frozen=True)
class Finding:
    """One match in one file at one line."""

    path: str
    line: int
    severity: str
    rule_id: str
    match: str
    snippet: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScanReport:
    """Aggregated scan output, suitable for JSON or markdown rendering."""

    root: str
    rule_pack: str
    rule_pack_version: str
    scanned_files: int
    total_findings: int
    by_severity: dict[str, int] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "rule_pack": self.rule_pack,
            "rule_pack_version": self.rule_pack_version,
            "scanned_files": self.scanned_files,
            "total_findings": self.total_findings,
            "by_severity": dict(self.by_severity),
            "findings": [f.to_dict() for f in self.findings],
        }


# --------------------------------------------------------------------------
# Rule loading / compilation
# --------------------------------------------------------------------------


class RuleLoadError(ValueError):
    """Raised when a rule pack JSON file is malformed."""


def _compile_rule(raw: dict[str, Any]) -> Rule:
    rule_id = raw.get("id")
    rule_type = raw.get("type")
    severity = raw.get("severity")
    value = raw.get("value")
    reason = raw.get("reason", "")

    if not isinstance(rule_id, str) or not rule_id:
        raise RuleLoadError(f"rule missing 'id': {raw!r}")
    if rule_type not in RULE_TYPES:
        raise RuleLoadError(
            f"rule {rule_id!r}: type must be one of {RULE_TYPES}, got {rule_type!r}"
        )
    if not isinstance(severity, str) or not severity:
        raise RuleLoadError(f"rule {rule_id!r}: missing 'severity'")
    if not isinstance(value, str) or not value:
        raise RuleLoadError(f"rule {rule_id!r}: missing 'value'")

    flags = re.IGNORECASE
    if rule_type == "word":
        # \b boundaries; allow common inflections by escaping the
        # operator-supplied word and relying on the rule pack to list
        # each inflection it cares about (predictable, no surprise
        # over-matches).
        compiled = re.compile(rf"\b{re.escape(value)}\b", flags)
    elif rule_type == "phrase":
        # Treat internal whitespace as flexible so "your stack" matches
        # "your  stack" but not "your-stack". Punctuation-bounded.
        parts = [re.escape(p) for p in value.split()]
        compiled = re.compile(r"\b" + r"\s+".join(parts) + r"\b", flags)
    else:
        # regex / pattern: trust the rule pack but compile defensively.
        try:
            compiled = re.compile(value, flags)
        except re.error as exc:
            raise RuleLoadError(f"rule {rule_id!r}: invalid regex: {exc}") from exc

    return Rule(
        id=rule_id,
        type=rule_type,
        severity=severity,
        value=value,
        reason=reason if isinstance(reason, str) else "",
        pattern=compiled,
    )


def load_rule_pack(path: Path) -> RulePack:
    """Load and compile a rule pack from a JSON file."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuleLoadError(f"cannot read rule pack {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuleLoadError(f"rule pack {path} is not valid JSON: {exc}") from exc
    return _rule_pack_from_dict(raw)


def rule_pack_from_dict(data: dict[str, Any]) -> RulePack:
    """Public constructor for in-memory rule packs (used by tests)."""
    return _rule_pack_from_dict(data)


def _rule_pack_from_dict(data: dict[str, Any]) -> RulePack:
    if not isinstance(data, dict):
        raise RuleLoadError("rule pack root must be a JSON object")
    raw_rules = data.get("rules", [])
    if not isinstance(raw_rules, list):
        raise RuleLoadError("'rules' must be a list")
    compiled = tuple(_compile_rule(r) for r in raw_rules)

    severities = data.get("severities") or ["DRIFT", "CAUTION", "TYPO"]
    skip_dirs = data.get("skip_dirs") or [".git", "node_modules", ".cache"]
    include_globs = data.get("include_globs") or ["*.md", "*.mdx", "*.html"]

    return RulePack(
        name=str(data.get("name", "custom")),
        version=str(data.get("version", "0.0.0")),
        description=str(data.get("description", "")),
        severities=tuple(severities),
        skip_dirs=frozenset(skip_dirs),
        include_globs=tuple(include_globs),
        rules=compiled,
    )


# --------------------------------------------------------------------------
# Code-region stripping
# --------------------------------------------------------------------------


def _blank_preserving(match: re.Match[str]) -> str:
    """Replace match with same-length whitespace, preserving newlines."""
    return "".join(c if c == "\n" else " " for c in match.group(0))


def strip_code_regions(text: str) -> str:
    """Replace code fences, inline code, and comments with offset-preserving
    whitespace. Length and newlines unchanged, so line numbers reported
    against the cleaned text still map to the original file."""
    out = _CODE_FENCE.sub(_blank_preserving, text)
    out = _INLINE_CODE.sub(_blank_preserving, out)
    out = _HTML_COMMENT.sub(_blank_preserving, out)
    out = _JSX_COMMENT.sub(_blank_preserving, out)
    return out


# --------------------------------------------------------------------------
# File walking + scanning
# --------------------------------------------------------------------------


def iter_target_files(root: Path, pack: RulePack) -> list[Path]:
    """Walk ``root`` and return files matching the rule pack's include
    globs, with skip_dirs pruned."""
    out: list[Path] = []
    if not root.exists() or not root.is_dir():
        return out
    skip = pack.skip_dirs
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in skip for part in path.relative_to(root).parts):
            continue
        if not any(path.match(g) for g in pack.include_globs):
            continue
        out.append(path)
    return out


def scan_file(path: Path, pack: RulePack, root: Path) -> list[Finding]:
    """Return findings for one file. Empty list if clean or unreadable."""
    findings: list[Finding] = []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("slop-detector: cannot read %s: %s", path, exc)
        return findings
    cleaned = strip_code_regions(raw)
    raw_lines = raw.splitlines()
    rel = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)

    for rule in pack.rules:
        for m in rule.pattern.finditer(cleaned):
            line_no = cleaned.count("\n", 0, m.start()) + 1
            line_text = raw_lines[line_no - 1] if 0 < line_no <= len(raw_lines) else ""
            findings.append(
                Finding(
                    path=rel,
                    line=line_no,
                    severity=rule.severity,
                    rule_id=rule.id,
                    match=m.group(0),
                    snippet=line_text.strip()[:200],
                    reason=rule.reason,
                )
            )
    return findings


def scan_path(root: Path, pack: RulePack) -> ScanReport:
    """Top-level entry: scan a directory and return a ``ScanReport``.

    Findings are sorted by (path, line, rule_id) so the report is
    deterministic across runs.
    """
    files = iter_target_files(root, pack)
    findings: list[Finding] = []
    for file_path in files:
        findings.extend(scan_file(file_path, pack, root))

    findings.sort(key=lambda f: (f.path, f.line, f.rule_id))
    by_severity: dict[str, int] = {}
    for finding in findings:
        by_severity[finding.severity] = by_severity.get(finding.severity, 0) + 1

    return ScanReport(
        root=str(root),
        rule_pack=pack.name,
        rule_pack_version=pack.version,
        scanned_files=len(files),
        total_findings=len(findings),
        by_severity=by_severity,
        findings=findings,
    )


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def render_markdown(report: ScanReport, max_findings: int | None = None) -> str:
    """Render a ``ScanReport`` as a human-readable markdown report."""
    lines: list[str] = []
    lines.append(f"# AI-slop scan: `{report.root}`")
    lines.append("")
    lines.append(
        f"Rule pack: **{report.rule_pack}** v{report.rule_pack_version}. "
        f"Scanned {report.scanned_files} file"
        f"{'s' if report.scanned_files != 1 else ''}. "
        f"Total findings: **{report.total_findings}**."
    )

    if report.total_findings == 0:
        lines.append("")
        lines.append("Clean. No slop detected.")
        return "\n".join(lines) + "\n"

    lines.append("")
    lines.append("## By severity")
    lines.append("")
    for sev in sorted(report.by_severity):
        lines.append(f"- **{sev}**: {report.by_severity[sev]}")

    lines.append("")
    lines.append("## Findings")
    lines.append("")
    shown = report.findings if max_findings is None else report.findings[:max_findings]
    for f in shown:
        lines.append(f"- `{f.path}:{f.line}` [{f.severity}] **{f.rule_id}**: `{f.match}`")
        if f.snippet:
            lines.append(f"  > {f.snippet}")
        if f.reason:
            lines.append(f"  ({f.reason})")
    if max_findings is not None and report.total_findings > max_findings:
        lines.append("")
        lines.append(
            f"...and {report.total_findings - max_findings} more. "
            f"Re-run with `--report json` for the full list."
        )
    return "\n".join(lines) + "\n"


def render_json(report: ScanReport) -> str:
    """Render a ``ScanReport`` as deterministic, indented JSON."""
    return json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n"


# --------------------------------------------------------------------------
# Default-rule-pack discovery
# --------------------------------------------------------------------------


def default_rule_pack_path() -> Path:
    """Locate the bundled default rule pack.

    Lookup order:
      1. ``ALFRED_SLOP_RULES`` env var (handled by the CLI, not here).
      2. ``examples/slop-rules.json`` relative to this file's repo root.

    The CLI passes the resolved path; this helper is for embedders.
    """
    # lib/slop_detector.py -> repo root is one level up.
    repo_root = Path(__file__).resolve().parent.parent
    return repo_root / "examples" / "slop-rules.json"
