"""Attach verification evidence to agent-authored pull requests.

Every agent PR should carry proof a non-author can check at a glance. This
module turns three signals into a single Markdown ``## Verification evidence``
block for the PR body:

1. Test evidence: a summary of the repo pre-push command that the runner
   already ran (pass/fail counts, duration, exit code). The runner used to
   discard that output; here it is captured and formatted.
2. Diff evidence: a files/lines summary plus the issue's acceptance criteria
   restated with a checked/unchecked self-assessment. The self-assessment is
   produced by the engine reviewing its own diff and is clearly labelled as
   such - it is a claim, not an independent verification.
3. Screenshot evidence (optional): when a repo declares a preview command,
   the runner starts it, captures before/after screenshots of a configured
   route, commits them under ``.alfred/evidence/<firing-id>/`` on the PR
   branch, and this block references them with relative links.

Design rules that keep the block trustworthy:

- Honest by construction. Evidence that could not be generated is rendered
  as ``not captured (<reason>)`` - never silently omitted, never fabricated.
- Gated. ``ALFRED_PR_EVIDENCE`` (default on) covers test evidence and the
  self-assessment. Screenshots are strictly opt-in per repo via config.
- Decoupled. This module does no subprocess work of its own for the runner
  path; the runner passes already-captured results in. The only shell-outs
  live behind :func:`capture_screenshots`, and every command is injectable so
  the tests never touch a real browser.

The public surface is small on purpose:

- :func:`build_evidence_block` - assemble the Markdown from typed inputs.
- :func:`evidence_enabled` - read the default-on env gate.
- :func:`parse_test_summary` - extract counts/duration from raw check output.
- :func:`load_preview_config` - read the per-repo screenshot config.
- :func:`capture_screenshots` - run the (injectable) screenshot command.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

RunCmd = Callable[..., subprocess.CompletedProcess]

# Default-on gate for test evidence + self-assessment. Screenshots stay opt-in
# behind per-repo config regardless of this flag.
EVIDENCE_ENV_VAR = "ALFRED_PR_EVIDENCE"

# Suggested default screenshot command, documented in docs/VERIFICATION.md. Not
# a hard dependency: the repo declares its own command, and this is only the
# fallback when the config gives a URL but no explicit command.
DEFAULT_SCREENSHOT_CMD = "npx --yes playwright screenshot --wait-for-timeout 1500 {url} {out}"

EVIDENCE_DIR_NAME = ".alfred/evidence"
EVIDENCE_HEADING = "## Verification evidence"

_NOT_CAPTURED = "_not captured_"


def evidence_enabled(env: dict[str, str] | None = None) -> bool:
    """Return whether test evidence + self-assessment should be attached.

    Default on. Set ``ALFRED_PR_EVIDENCE=0`` (or ``false``/``no``/``off``) to
    disable. Screenshots are governed separately by per-repo config.
    """
    source = os.environ if env is None else env
    raw = source.get(EVIDENCE_ENV_VAR)
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


# ---------------------------------------------------------------------------
# Typed inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TestEvidence:
    """Summary of the repo pre-push check the runner already executed."""

    # Not a pytest test class despite the ``Test`` prefix.
    __test__ = False

    ran: bool
    command: str = ""
    ok: bool = False
    reason: str = ""
    summary: str = ""
    duration_s: float | None = None
    exit_code: int | None = None


@dataclass(frozen=True)
class DiffStat:
    """Files/lines summary for the branch diff against the base."""

    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0
    files: tuple[str, ...] = ()


@dataclass(frozen=True)
class CriterionAssessment:
    """One acceptance criterion and the engine's self-assessment of it."""

    text: str
    met: bool | None  # True met, False not met, None undetermined
    note: str = ""


@dataclass(frozen=True)
class SelfAssessment:
    """The engine's own review of its diff against the acceptance criteria."""

    produced: bool
    reason: str = ""
    criteria: tuple[CriterionAssessment, ...] = ()
    overall_note: str = ""


@dataclass(frozen=True)
class PreviewConfig:
    """Per-repo screenshot configuration (opt-in)."""

    start_cmd: str = ""
    url: str = ""
    ready_regex: str = ""
    route: str = "/"
    screenshot_cmd: str = ""

    @property
    def enabled(self) -> bool:
        # A start command plus a URL is the minimum to attempt screenshots.
        return bool(self.start_cmd.strip() and self.url.strip())


@dataclass(frozen=True)
class ScreenshotEvidence:
    """Result of a before/after screenshot capture."""

    attempted: bool
    ok: bool = False
    reason: str = ""
    before_path: str = ""  # repo-relative
    after_path: str = ""  # repo-relative
    route: str = "/"


@dataclass
class EvidenceInputs:
    """Everything :func:`build_evidence_block` needs, all optional.

    ``include_core`` covers the ``ALFRED_PR_EVIDENCE``-gated tiers (tests,
    diff, self-assessment). Screenshots are governed independently by per-repo
    config, so a gate-off firing with a configured preview still produces a
    screenshots-only block instead of dropping the evidence on the floor.
    """

    test: TestEvidence | None = None
    diff: DiffStat | None = None
    assessment: SelfAssessment | None = None
    screenshots: ScreenshotEvidence | None = None
    firing_id: str = ""
    notes: list[str] = field(default_factory=list)
    include_core: bool = True


# ---------------------------------------------------------------------------
# Test evidence parsing
# ---------------------------------------------------------------------------

# pytest's summary line is a run of "N word" tokens between "=" rails, e.g.
# "===== 5 failed, 40 passed, 3 skipped in 9.1s =====". Match the "=" rail and
# the trailing duration with a lazy middle (no nested quantifiers, so no
# exponential backtracking on adversarial input) and let _COUNT_RE pull the
# counts out of that middle. Requiring the rail keeps a bare "18 passed"
# inside a jest table from being mistaken for pytest.
_PYTEST_LINE_RE = re.compile(r"=+\s(.*?)\sin\s+([\d.]+)s", re.IGNORECASE)
_COUNT_RE = re.compile(
    r"(\d+)\s+(passed|failed|skipped|error|errors|xfailed|xpassed)", re.IGNORECASE
)
# jest / vitest emit an explicit "Tests:" line.
_JEST_RE = re.compile(
    r"Tests:\s+(?:(\d+)\s+failed,\s+)?(?:(\d+)\s+skipped,\s+)?(\d+)\s+passed", re.IGNORECASE
)
_GRADLE_RE = re.compile(r"(\d+)\s+tests? completed(?:,\s*(\d+)\s+failed)?", re.IGNORECASE)
_BUILD_OK_RE = re.compile(r"BUILD SUCCESSFUL", re.IGNORECASE)


def parse_test_summary(stdout: str, stderr: str = "") -> str:
    """Extract a one-line human summary from raw check output.

    Best-effort across pytest, jest/vitest, and gradle. Returns an empty
    string when nothing recognisable is found; callers fall back to the exit
    code. Never raises.
    """
    blob = f"{stdout}\n{stderr}"

    # jest/vitest first: their "Tests:" line is unambiguous.
    jest = _JEST_RE.search(blob)
    if jest:
        failed, skipped, passed = jest.group(1), jest.group(2), jest.group(3)
        parts = []
        if failed:
            parts.append(f"{failed} failed")
        parts.append(f"{passed} passed")
        if skipped:
            parts.append(f"{skipped} skipped")
        return "jest/vitest: " + ", ".join(parts)

    pyt = _PYTEST_LINE_RE.search(blob)
    if pyt:
        counts = _COUNT_RE.findall(pyt.group(1))
        if counts:
            ordered = ", ".join(f"{n} {word.lower()}" for n, word in counts)
            return f"pytest: {ordered} in {pyt.group(2)}s"

    grad = _GRADLE_RE.search(blob)
    if grad:
        completed, failed = grad.group(1), grad.group(2)
        line = f"gradle: {completed} tests completed"
        if failed:
            line += f", {failed} failed"
        return line

    if _BUILD_OK_RE.search(blob):
        return "build successful"

    return ""


# ---------------------------------------------------------------------------
# Acceptance criteria extraction
# ---------------------------------------------------------------------------

# Match Markdown task-list and bullet lines under an acceptance section.
_CHECKBOX_RE = re.compile(r"^\s*[-*]\s*\[( |x|X)\]\s+(.*\S)\s*$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*\S)\s*$")
_ACCEPTANCE_HEADING_RE = re.compile(
    r"^\s*#{1,6}\s*(acceptance\s+criteria|acceptance|success\s+criteria|"
    r"definition\s+of\s+done|requirements)\b",
    re.IGNORECASE,
)
_ANY_HEADING_RE = re.compile(r"^\s*#{1,6}\s+\S")


def extract_acceptance_criteria(issue_body: str, limit: int = 12) -> list[str]:
    """Pull acceptance-criteria bullet lines out of an issue body.

    Prefers an explicit ``## Acceptance criteria`` (or similar) section; when
    none exists, falls back to the first checkbox list anywhere in the body.
    Returns the raw criterion text with any checkbox marker stripped.
    """
    lines = issue_body.splitlines()
    section: list[str] = []
    in_section = False
    for line in lines:
        if _ACCEPTANCE_HEADING_RE.match(line):
            in_section = True
            continue
        if in_section and _ANY_HEADING_RE.match(line):
            break
        if in_section:
            section.append(line)

    criteria = _collect_bullets(section)
    if not criteria:
        # Fall back to any checkbox list in the whole body.
        criteria = _collect_bullets(lines, checkbox_only=True)
    return criteria[:limit]


def _collect_bullets(lines: Sequence[str], *, checkbox_only: bool = False) -> list[str]:
    out: list[str] = []
    for line in lines:
        cb = _CHECKBOX_RE.match(line)
        if cb:
            out.append(cb.group(2).strip())
            continue
        if checkbox_only:
            continue
        b = _BULLET_RE.match(line)
        if b:
            out.append(b.group(1).strip())
    return out


# ---------------------------------------------------------------------------
# Screenshot capture
# ---------------------------------------------------------------------------


def load_preview_config(raw: object) -> PreviewConfig:
    """Build a :class:`PreviewConfig` from a parsed TOML/dict fragment.

    Unknown keys are ignored; missing keys use safe defaults. Returns a
    disabled config for anything that is not a mapping.
    """
    if not isinstance(raw, dict):
        return PreviewConfig()
    return PreviewConfig(
        start_cmd=str(raw.get("start_cmd", "") or "").strip(),
        url=str(raw.get("url", "") or "").strip(),
        ready_regex=str(raw.get("ready_regex", "") or "").strip(),
        route=str(raw.get("route", "/") or "/").strip() or "/",
        screenshot_cmd=str(raw.get("screenshot_cmd", "") or "").strip(),
    )


def _format_screenshot_cmd(template: str, *, url: str, out: str) -> list[str]:
    """Build the screenshot argv, keeping substituted paths as single tokens.

    The template is split FIRST, then ``{url}``/``{out}`` are substituted into
    the resulting tokens. Substituting before the split would let an absolute
    worktree path with spaces (``/tmp/My Repo/...``) explode into multiple
    argv entries and point the shot at the wrong file.
    """
    import shlex

    return [token.replace("{url}", url).replace("{out}", out) for token in shlex.split(template)]


def capture_screenshots(
    worktree: Path,
    config: PreviewConfig,
    firing_id: str,
    *,
    run_cmd: RunCmd = subprocess.run,
    popen: Callable[..., object] = subprocess.Popen,
    server_boot_wait_s: float = 8.0,
    shot_timeout_s: int = 90,
    sleep: Callable[[float], None] | None = None,
    has_base_screenshot: bool = False,
) -> ScreenshotEvidence:
    """Capture a screenshot of the configured route on the PR branch.

    The runner is expected to call this once for the "after" state; a
    before-image is optional and captured by the caller from the base branch.
    The preview server is started through the injectable ``popen`` (must be
    Popen-like: non-blocking, with ``terminate``); the screenshot command runs
    through ``run_cmd`` (``subprocess.run`` signature). Tests inject both plus
    a no-op ``sleep`` so nothing real ever launches.

    Readiness is a fixed ``server_boot_wait_s`` grace period in v1. The
    ``ready_regex`` config key is reserved for output-polling readiness; until
    that lands, a server that is not up in time yields a failed shot, which is
    reported honestly rather than papered over.

    Failure is always reported as ``ScreenshotEvidence(ok=False, reason=...)``;
    this function never raises for an environment problem.
    """
    if not config.enabled:
        return ScreenshotEvidence(attempted=False, reason="preview command not configured")

    route = config.route or "/"
    url = config.url.rstrip("/") + ("" if route == "/" else route)
    rel_dir = f"{EVIDENCE_DIR_NAME}/{firing_id or 'unknown'}"
    out_dir = worktree / rel_dir
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return ScreenshotEvidence(
            attempted=True, ok=False, reason=f"cannot create evidence dir: {exc}", route=route
        )

    after_rel = f"{rel_dir}/after.png"
    template = config.screenshot_cmd or DEFAULT_SCREENSHOT_CMD
    shot_cmd = _format_screenshot_cmd(template, url=url, out=str(worktree / after_rel))
    do_sleep = sleep if sleep is not None else _default_sleep

    proc = None
    try:
        # Start the preview server without blocking; give it a fixed grace
        # period to come up.
        import shlex

        proc = popen(
            shlex.split(config.start_cmd),
            cwd=str(worktree),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        do_sleep(server_boot_wait_s)

        shot = run_cmd(
            shot_cmd,
            cwd=str(worktree),
            timeout=shot_timeout_s,
            capture_output=True,
            text=True,
        )
        rc = getattr(shot, "returncode", 1)
        if rc != 0 or not (worktree / after_rel).exists():
            detail = (getattr(shot, "stderr", "") or "").strip()[:200]
            return ScreenshotEvidence(
                attempted=True,
                ok=False,
                reason=f"screenshot command failed (exit {rc}) {detail}".strip(),
                route=route,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        return ScreenshotEvidence(
            attempted=True, ok=False, reason=f"{exc.__class__.__name__}: {exc}", route=route
        )
    finally:
        _terminate(proc)

    before_rel = f"{rel_dir}/before.png"
    return ScreenshotEvidence(
        attempted=True,
        ok=True,
        before_path=before_rel if has_base_screenshot else "",
        after_path=after_rel,
        route=route,
    )


def _default_sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)


def _terminate(proc: object) -> None:
    """Tear down the preview server and everything it spawned.

    ``start_cmd`` values like ``npm run dev`` are wrappers that fork the real
    server, so terminating only the parent leaves the child holding the port.
    The server is started with ``start_new_session=True``, which makes its pid
    the process-group id; signal the whole group (TERM, then KILL after a
    short grace period). Fakes without a real pid fall back to their own
    ``terminate``/``kill`` methods.
    """
    if proc is None:
        return
    pid = getattr(proc, "pid", None)
    if isinstance(pid, int) and pid > 0:
        import signal

        try:
            os.killpg(pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
        else:
            import contextlib

            wait_fn = getattr(proc, "wait", None)
            if callable(wait_fn):
                try:
                    wait_fn(timeout=5)
                    return
                except (subprocess.TimeoutExpired, OSError):
                    pass
            with contextlib.suppress(OSError, ProcessLookupError):
                os.killpg(pid, signal.SIGKILL)
            return
    for method in ("terminate", "kill"):
        fn = getattr(proc, method, None)
        if callable(fn):
            try:
                fn()
                return
            except (OSError, ProcessLookupError):
                continue


# ---------------------------------------------------------------------------
# Markdown assembly
# ---------------------------------------------------------------------------


def _render_test(test: TestEvidence | None) -> list[str]:
    lines = ["### Tests"]
    if test is None:
        lines.append(f"- {_NOT_CAPTURED} (evidence gate off or no result available)")
        return lines
    if not test.ran:
        reason = test.reason or "no pre-push command configured for this repo"
        lines.append(f"- {_NOT_CAPTURED} ({reason})")
        return lines
    status = "passed" if test.ok else "FAILED"
    summary = test.summary or (f"exit {test.exit_code}" if test.exit_code is not None else "")
    detail = f" - {summary}" if summary else ""
    lines.append(f"- Pre-push checks {status}{detail}")
    if test.command:
        lines.append(f"- Command: `{_one_line(test.command)}`")
    if test.duration_s is not None:
        lines.append(f"- Duration: {test.duration_s:.1f}s")
    if not test.ok and test.reason:
        lines.append(f"- Reason: {_one_line(test.reason)}")
    return lines


def _render_diff(diff: DiffStat | None) -> list[str]:
    lines = ["### Diff"]
    if diff is None:
        lines.append(f"- {_NOT_CAPTURED} (diff stat unavailable)")
        return lines
    lines.append(
        f"- {diff.files_changed} file(s) changed, +{diff.insertions} / -{diff.deletions} lines"
    )
    if diff.files:
        shown = list(diff.files[:10])
        for name in shown:
            lines.append(f"  - `{name}`")
        if len(diff.files) > len(shown):
            lines.append(f"  - ... and {len(diff.files) - len(shown)} more")
    return lines


def _render_assessment(assessment: SelfAssessment | None) -> list[str]:
    lines = ["### Acceptance criteria (engine self-assessment)"]
    lines.append(
        "> Self-reported by the implementing engine reviewing its own diff. "
        "This is a claim to check, not an independent verification."
    )
    if assessment is None or not assessment.produced:
        reason = (assessment.reason if assessment else "") or "self-assessment not produced"
        lines.append(f"- {_NOT_CAPTURED} ({reason})")
        return lines
    if not assessment.criteria:
        lines.append(f"- {_NOT_CAPTURED} (no acceptance criteria found in the issue)")
        return lines
    for crit in assessment.criteria:
        if crit.met is True:
            box = "[x]"
        elif crit.met is False:
            box = "[ ]"
        else:
            box = "[?]"
        note = f" - {_one_line(crit.note)}" if crit.note else ""
        lines.append(f"- {box} {_one_line(crit.text)}{note}")
    if assessment.overall_note:
        lines.append("")
        lines.append(f"{_one_line(assessment.overall_note)}")
    return lines


def _render_screenshots(shots: ScreenshotEvidence | None) -> list[str] | None:
    # Screenshots are opt-in. When never attempted, omit the section entirely
    # rather than adding noise - absence here is not dishonest because the
    # feature simply was not requested for this repo.
    if shots is None or not shots.attempted:
        return None
    lines = ["### Screenshots"]
    lines.append(f"- Route: `{shots.route}`")
    if not shots.ok:
        reason = shots.reason or "capture failed"
        lines.append(f"- {_NOT_CAPTURED} ({_one_line(reason)})")
        return lines
    if shots.before_path:
        lines.append(f"- Before: [`{shots.before_path}`]({shots.before_path})")
    else:
        lines.append("- Before: _not captured (no base-branch baseline)_")
    lines.append(f"- After: [`{shots.after_path}`]({shots.after_path})")
    return lines


def build_evidence_block(inputs: EvidenceInputs) -> str:
    """Assemble the ``## Verification evidence`` Markdown block.

    Returns a string with the heading; each included subsection is honest
    about missing data. Screenshots appear only when a capture was attempted.
    With ``include_core=False`` (operator turned ``ALFRED_PR_EVIDENCE`` off)
    the gated tiers are omitted as a disabled feature, not as missing
    evidence; a screenshots-only block remains when a capture ran. Returns an
    empty string when nothing at all is included.
    """
    blocks: list[list[str]] = []
    if inputs.include_core:
        blocks.extend(
            [
                _render_test(inputs.test),
                _render_diff(inputs.diff),
                _render_assessment(inputs.assessment),
            ]
        )
    shots = _render_screenshots(inputs.screenshots)
    if shots is not None:
        blocks.append(shots)
    if not blocks and not inputs.notes:
        return ""

    out: list[str] = [EVIDENCE_HEADING, ""]
    for i, block in enumerate(blocks):
        out.extend(block)
        if i != len(blocks) - 1:
            out.append("")

    for note in inputs.notes:
        out.append("")
        out.append(f"_{_one_line(note)}_")

    return "\n".join(out).rstrip() + "\n"


def parse_assessment_response(text: str, criteria: Sequence[str]) -> SelfAssessment:
    """Parse the engine's self-assessment reply into a :class:`SelfAssessment`.

    The engine is asked to emit a JSON object shaped like::

        {"criteria": [{"index": 0, "met": true, "note": "..."}],
         "overall": "..."}

    Parsing is tolerant: a JSON object embedded in prose is extracted, unknown
    indices are ignored, and any criterion the engine did not judge is kept
    with ``met=None`` so the block stays honest about what was assessed. When
    no usable JSON is found, returns ``produced=False`` with a reason.
    """
    crit_list = [c for c in criteria if c.strip()]
    if not crit_list:
        return SelfAssessment(produced=True, criteria=())

    payload = _extract_json_object(text)
    if payload is None:
        return SelfAssessment(
            produced=False,
            reason="engine did not return a parseable self-assessment",
            criteria=tuple(CriterionAssessment(text=c, met=None) for c in crit_list),
        )

    by_index: dict[int, dict] = {}
    raw_items = payload.get("criteria")
    if isinstance(raw_items, list):
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            idx = item.get("index")
            if isinstance(idx, bool) or not isinstance(idx, int):
                continue
            by_index[idx] = item

    assessed: list[CriterionAssessment] = []
    for i, crit_text in enumerate(crit_list):
        item = by_index.get(i)
        if item is None:
            assessed.append(CriterionAssessment(text=crit_text, met=None))
            continue
        met = item.get("met")
        met_val: bool | None
        if isinstance(met, bool):
            met_val = met
        else:
            met_val = None
        note = str(item.get("note", "") or "")
        assessed.append(CriterionAssessment(text=crit_text, met=met_val, note=note))

    overall = payload.get("overall")
    overall_note = str(overall or "") if not isinstance(overall, (dict, list)) else ""
    return SelfAssessment(
        produced=True,
        criteria=tuple(assessed),
        overall_note=overall_note,
    )


def _extract_json_object(text: str) -> dict | None:
    import json

    if not text:
        return None
    # Fast path: the whole string is JSON.
    stripped = text.strip()
    for candidate in (stripped, _first_brace_block(stripped)):
        if not candidate:
            continue
        try:
            obj = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _first_brace_block(text: str) -> str:
    start = text.find("{")
    if start == -1:
        return ""
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return ""


def assessment_prompt(diff_text: str, criteria: Sequence[str], max_diff_chars: int = 30000) -> str:
    """Build the prompt asking an engine to self-assess its diff.

    Kept here so the runner and tests share one wording. The prompt is
    explicit that the engine must be honest and cite the diff.
    """
    numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(criteria))
    truncated = diff_text[:max_diff_chars]
    if len(diff_text) > max_diff_chars:
        truncated += "\n... (diff truncated)"
    return (
        "You just implemented a change. Review YOUR OWN diff against the "
        "acceptance criteria below and report honestly which criteria the diff "
        "actually satisfies. Do not claim a criterion is met unless the diff "
        "shows it. If you cannot tell from the diff, mark it unmet.\n\n"
        "Reply with ONLY a JSON object, no prose, shaped exactly like:\n"
        '{"criteria": [{"index": 0, "met": true, "note": "one short reason"}], '
        '"overall": "one sentence"}\n\n'
        f"Acceptance criteria:\n{numbered}\n\n"
        f"Diff:\n```diff\n{truncated}\n```\n"
    )


def _one_line(text: str, limit: int = 400) -> str:
    collapsed = " ".join((text or "").split())
    if len(collapsed) > limit:
        return collapsed[: limit - 1].rstrip() + "…"
    return collapsed
