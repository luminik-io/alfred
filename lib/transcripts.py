"""Transcript reader for stream-JSON firing logs.

Standalone module - no dependency on ``agent_runner`` so it can be imported
on hosts that haven't deployed the full runtime.

Path layout (under ``$ALFRED_HOME/state``, default ``~/.alfred/state``):

    transcripts/<codename>/<YYYY-MM>/<firing_id>.jsonl
    codex/<codename>/<YYYY-MM>/<firing_id>.stdout.txt

Every consumer passes an explicit ``state_dir`` so tests can inject a tmp
path; nothing in this module reads ``$ALFRED_HOME`` directly.

The stream-JSON shape is the one emitted by ``claude -p --output-format
stream-json``: a sequence of newline-separated JSON objects with
``type ∈ {"system", "user", "assistant", "result"}``. We summarise it by
counting tool-use blocks, recording file paths and Bash commands, and
extracting the trailing ``result`` event.

Pure stdlib. Operator runs this with the system Python.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# State directory resolution
# --------------------------------------------------------------------------


def default_state_dir() -> Path:
    """Resolve the operator's state directory.

    Priority order:
      1. ``ALFRED_STATE_DIR`` (explicit override).
      2. ``ALFRED_HOME``/state.
      3. ``~/.alfred/state``.

    The returned path is not required to exist; callers that need a
    populated tree should check ``exists()`` themselves.
    """
    import os

    explicit = os.environ.get("ALFRED_STATE_DIR")
    if explicit:
        return Path(explicit).expanduser()
    alfred_home = os.environ.get("ALFRED_HOME")
    if alfred_home:
        return Path(alfred_home).expanduser() / "state"
    return Path.home() / ".alfred" / "state"


def transcripts_root(state_dir: Path) -> Path:
    return state_dir / "transcripts"


# --------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------


@dataclass
class FiringResult:
    """The trailing ``result`` event from a stream-JSON transcript."""

    subtype: str | None = None
    raw_subtype: str | None = None
    num_turns: int | None = None
    total_cost_usd: float | None = None
    session_id: str | None = None
    stop_reason: str | None = None
    is_error: bool = False
    api_error_status: int | str | None = None
    result_text: str | None = None
    error_message: str | None = None

    def to_summary_dict(self) -> dict[str, Any]:
        """Return the safe, machine-readable result summary.

        The raw transcript remains available through explicit JSONL passthrough
        commands. Summary views intentionally avoid provider payload text and
        session identifiers because those are easy to archive accidentally.
        """
        return {
            "subtype": self.subtype,
            "raw_subtype": self.raw_subtype,
            "num_turns": self.num_turns,
            "total_cost_usd": self.total_cost_usd,
            "stop_reason": self.stop_reason,
            "is_error": self.is_error,
            "api_error_status": self.api_error_status,
        }


@dataclass
class TranscriptSummary:
    """Aggregate view of one firing transcript."""

    path: str
    tool_calls_total: int = 0
    tool_calls_by_name: dict[str, int] = field(default_factory=dict)
    bash_commands: list[str] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    files_edited: list[str] = field(default_factory=list)
    files_written: list[str] = field(default_factory=list)
    skills_invoked: list[str] = field(default_factory=list)
    result: FiringResult | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["result"] = self.result.to_summary_dict() if self.result else None
        return data


@dataclass
class FiringRef:
    """Pointer to a firing transcript on disk plus its codename."""

    codename: str
    firing_id: str
    path: Path
    mtime: float

    @property
    def timestamp(self) -> datetime:
        return datetime.fromtimestamp(self.mtime, tz=UTC)


# --------------------------------------------------------------------------
# Listing transcripts
# --------------------------------------------------------------------------


def list_firings(state_dir: Path, codename: str) -> list[FiringRef]:
    """Return all firing transcripts for ``codename``, newest first."""
    root = transcripts_root(state_dir) / codename
    if not root.is_dir():
        return []
    out: list[FiringRef] = []
    for month_dir in root.iterdir():
        if not month_dir.is_dir():
            continue
        for path in month_dir.glob("*.jsonl"):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                logger.debug("skipping unreadable transcript %s", path)
                continue
            out.append(
                FiringRef(
                    codename=codename,
                    firing_id=path.stem,
                    path=path,
                    mtime=mtime,
                )
            )
    out.sort(key=lambda r: r.mtime, reverse=True)
    return out


def find_firing(state_dir: Path, codename: str, firing_id: str) -> FiringRef | None:
    """Return the firing record matching ``firing_id`` or ``None``."""
    for ref in list_firings(state_dir, codename):
        if ref.firing_id == firing_id:
            return ref
    return None


def list_codenames(state_dir: Path) -> list[str]:
    """Return codenames that have at least one transcript directory."""
    root = transcripts_root(state_dir)
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


# --------------------------------------------------------------------------
# Summarisation
# --------------------------------------------------------------------------


def transcript_summary(path: Path) -> TranscriptSummary:
    """Summarise a stream-JSON transcript file.

    Returns an empty summary if the file is missing or unreadable. JSON
    decode errors on individual lines are skipped silently - the
    stream-JSON format guarantees well-formed lines but interrupted
    writes can produce torn tails.
    """
    summary = TranscriptSummary(path=str(path))
    if not path.exists():
        return summary
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        logger.debug("could not read transcript %s", path)
        return summary

    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue
        try:
            obj = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue

        if obj.get("type") == "result" or ("subtype" in obj and "num_turns" in obj):
            summary.result = _result_from_event(obj)
            continue

        message = obj.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue

        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            _record_tool_use(summary, block)

    return summary


def _record_tool_use(summary: TranscriptSummary, block: dict[str, Any]) -> None:
    """Update ``summary`` in place with one tool_use block."""
    name = block.get("name") or ""
    if not name:
        return
    summary.tool_calls_total += 1
    summary.tool_calls_by_name[name] = summary.tool_calls_by_name.get(name, 0) + 1
    inp = block.get("input") or {}
    if not isinstance(inp, dict):
        return

    if name == "Bash":
        cmd = (inp.get("command") or "")[:200]
        if cmd:
            summary.bash_commands.append(cmd)
    elif name == "Read":
        file_path = inp.get("file_path") or ""
        if file_path:
            summary.files_read.append(file_path)
    elif name == "Edit":
        file_path = inp.get("file_path") or ""
        if file_path:
            summary.files_edited.append(file_path)
    elif name == "Write":
        file_path = inp.get("file_path") or ""
        if file_path:
            summary.files_written.append(file_path)
    elif name == "Skill":
        skill = inp.get("skill") or ""
        if skill:
            summary.skills_invoked.append(skill)


# --------------------------------------------------------------------------
# Codex artifact helpers
# --------------------------------------------------------------------------


_CODEX_RATE_LIMIT_RE = re.compile(
    r"rate.?limit|usage.?limit|quota|\b429\b|too.?many.?requests",
    re.IGNORECASE,
)
_RESULT_RATE_LIMIT_RE = re.compile(
    r"\b(?:HTTP\s*)?429\b|\btoo many requests\b|\bquota exceeded\b"
    r"|\brate limit(?:ed| exceeded| reached)?\b"
    r"|\brate_limit(?:ed|_error|_exceeded)?\b"
    r"|\brate-limit(?:ed| exceeded| reached| error| hit)\b"
    r"|API Error[^\n]{0,120}\brate-limit\b"
    r"|\bdisabled Claude subscription access\b"
    r"|\bClaude subscription access for Claude Code\b"
    r"|\bsubscription access.{0,40}Claude Code\b",
    re.IGNORECASE,
)

_RESULT_AUTH_RE = re.compile(
    r"authentication_(?:error|failed)|failed to authenticate|invalid authentication credentials"
    r"|\bAPI Error:\s*401\b|\b401\b[^\n]{0,120}authentication"
    r"|not logged in|please run /login",
    re.IGNORECASE,
)
_RESULT_BUDGET_RE = re.compile(
    r"\b(?:you(?:'re| are) out of extra usage|you(?:'ve| have) hit your usage limit)\b"
    r"|\bout of extra usage\b",
    re.IGNORECASE,
)
_RESULT_OVERLOAD_RE = re.compile(
    r'"type"\s*:\s*"error"[^\n]{0,400}?"type"\s*:\s*"overloaded_error"'
    r"|(?m:^API Error[^\n]{0,400}overloaded_error)"
    r"|\bHTTP\s*529\b"
    r"|\b529\b\s*[:.\-]\s*(?:overloaded|too\s+many\s+requests)"
    r'|"type"\s*:\s*"error"[^\n]{0,400}?[Bb]edrock[^\n]{0,400}?throttl(?:ing|ed)'
    r'|"type"\s*:\s*"error"[^\n]{0,400}?throttl(?:ing|ed)[^\n]{0,400}?[Bb]edrock',
    re.IGNORECASE,
)


def _result_from_event(obj: dict[str, Any]) -> FiringResult:
    raw_subtype = obj.get("subtype")
    result_text = str(obj.get("result") or "")
    error_message = _first_text(obj, "error_message", "errorMessage", "error")
    api_error_status = obj.get("api_error_status")
    return FiringResult(
        subtype=_effective_result_subtype(obj),
        raw_subtype=raw_subtype,
        num_turns=obj.get("num_turns"),
        total_cost_usd=obj.get("total_cost_usd"),
        session_id=obj.get("session_id"),
        stop_reason=obj.get("stop_reason"),
        is_error=_truthy(obj.get("is_error")),
        api_error_status=api_error_status,
        result_text=result_text or None,
        error_message=error_message,
    )


def _effective_result_subtype(obj: dict[str, Any]) -> str | None:
    raw = obj.get("subtype")
    raw_str = str(raw or "")
    is_error = _truthy(obj.get("is_error"))
    api_error_status = obj.get("api_error_status")
    has_api_error_status = api_error_status is not None and str(api_error_status).strip() != ""
    stop_reason = str(obj.get("stop_reason") or "")
    text = _result_error_text(obj)
    strict_text = _result_strict_error_text(obj)
    rate_limit_text = text if is_error else strict_text
    has_provider_marker = bool(
        has_api_error_status
        or _RESULT_BUDGET_RE.search(text)
        or _RESULT_AUTH_RE.search(text)
        or _RESULT_OVERLOAD_RE.search(text)
        or _RESULT_RATE_LIMIT_RE.search(rate_limit_text)
    )

    if raw_str.startswith("error_"):
        return raw_str
    if stop_reason in {"aborted", "error"}:
        return raw
    if not is_error and not has_provider_marker:
        return raw
    if _RESULT_BUDGET_RE.search(text):
        return "error_budget"
    if _status_is(obj.get("api_error_status"), 401) or _RESULT_AUTH_RE.search(text):
        return "error_authentication"
    if _status_is(obj.get("api_error_status"), 529) or _RESULT_OVERLOAD_RE.search(text):
        return "error_overloaded"
    if _status_is(obj.get("api_error_status"), 429) or _RESULT_RATE_LIMIT_RE.search(
        rate_limit_text
    ):
        return "error_rate_limit"
    return "error_api"


def _result_error_text(obj: dict[str, Any]) -> str:
    text = "\n".join(
        part
        for part in (
            str(obj.get("result") or ""),
            _first_text(obj, "error_message", "errorMessage", "error") or "",
            str(obj.get("api_error_status") or ""),
        )
        if part
    )
    return text.replace("\u2018", "'").replace("\u2019", "'")


def _result_strict_error_text(obj: dict[str, Any]) -> str:
    text = "\n".join(
        part
        for part in (
            _first_text(obj, "error_message", "errorMessage", "error") or "",
            str(obj.get("api_error_status") or ""),
        )
        if part
    )
    return text.replace("\u2018", "'").replace("\u2019", "'")


def _first_text(obj: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = obj.get(key)
        if value:
            return str(value)
    return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _status_is(value: Any, code: int) -> bool:
    try:
        return int(str(value).strip()) == code
    except (TypeError, ValueError):
        return False


def extract_codex_tokens(text: str) -> int:
    """Parse the ``tokens used\\nN`` summary block from a Codex stdout dump.

    Returns 0 when no token summary is present. Codex prints the summary
    only when the run completed cleanly; rate-limited or aborted runs
    have no token line, which is reflected as zero.
    """
    lines = [line.strip() for line in (text or "").splitlines()]
    for index, line in enumerate(lines):
        if line == "tokens used" and index + 1 < len(lines):
            raw = lines[index + 1].replace(",", "")
            if raw.isdigit():
                return int(raw)
    return 0


def extract_codex_session_id(text: str) -> str | None:
    """Pull the ``session id:`` line out of a Codex stdout dump."""
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("session id:"):
            return stripped.split(":", 1)[1].strip() or None
    return None


def codex_run_hit_rate_limit(text: str) -> bool:
    """Return True when the Codex stdout body contains rate-limit signals."""
    return bool(_CODEX_RATE_LIMIT_RE.search(text or ""))


# --------------------------------------------------------------------------
# Pretty-printing helpers used by the CLI layer
# --------------------------------------------------------------------------


def render_firing_jsonl(path: Path) -> list[str]:
    """Decode a stream-JSON transcript into a list of human-readable lines.

    Returns the raw lines a caller would print one-per-line. The
    rendering is intentionally compact - full payloads are clipped so
    operators can scan a firing without scrolling pages of tool output.
    """
    lines: list[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("could not read transcript %s: %s", path, exc)
        return lines

    for raw in text.splitlines():
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        rendered = _render_event(obj)
        if rendered:
            lines.append(rendered)
    return lines


def _render_event(obj: dict[str, Any]) -> str | None:
    t = obj.get("type") or obj.get("event_type") or "?"
    if t == "system":
        return f"[system] {obj.get('subtype') or ''}".rstrip()
    if t == "user":
        return _render_user(obj)
    if t == "assistant":
        return _render_assistant(obj)
    if t == "result" or ("subtype" in obj and "num_turns" in obj):
        result = _result_from_event(obj)
        cost = obj.get("total_cost_usd") or 0
        try:
            cost_str = f"${float(cost):.4f}"
        except (TypeError, ValueError):
            cost_str = "$?"
        detail = ""
        if result.raw_subtype and result.raw_subtype != result.subtype:
            detail += f" raw_subtype={result.raw_subtype}"
        if result.is_error:
            detail += " is_error=true"
        if result.api_error_status is not None:
            detail += f" api_error_status={result.api_error_status}"
        return (
            f"[result] subtype={result.subtype} turns={obj.get('num_turns')} "
            f"cost={cost_str} stop_reason={obj.get('stop_reason')}{detail}"
        )
    return None


def _render_user(obj: dict[str, Any]) -> str | None:
    content = (obj.get("message") or {}).get("content") or ""
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                body = block.get("content") or ""
                if isinstance(body, list):
                    body = " ".join(
                        (b.get("text", "") if isinstance(b, dict) else str(b)) for b in body
                    )
                snippet = (str(body) or "").replace("\n", " ")[:120]
                parts.append(f"[tool_result] {snippet}")
        return "\n".join(parts) if parts else None
    snippet = str(content).replace("\n", " ")[:120]
    if not snippet.strip():
        return None
    return f"[user] {snippet}"


def _render_assistant(obj: dict[str, Any]) -> str | None:
    content = (obj.get("message") or {}).get("content") or []
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        bt = block.get("type")
        if bt == "text":
            snippet = (block.get("text") or "").replace("\n", " ")[:160]
            if snippet.strip():
                parts.append(f"[assistant] {snippet}")
        elif bt == "tool_use":
            parts.append(_render_tool_use_event(block))
    return "\n".join(parts) if parts else None


def _render_tool_use_event(block: dict[str, Any]) -> str:
    name = block.get("name") or "?"
    inp = block.get("input") or {}
    if not isinstance(inp, dict):
        return f"[tool_use {name}] (no input)"
    if name == "Bash":
        return f"[tool_use Bash] $ {(inp.get('command') or '')[:160]}"
    if name in {"Read", "Edit", "Write"}:
        return f"[tool_use {name}] {inp.get('file_path') or ''}"
    if name == "Skill":
        return f"[tool_use Skill] /{inp.get('skill') or '?'}"
    return f"[tool_use {name}] {json.dumps(inp)[:140]}"
