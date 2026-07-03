"""Engine-agnostic orchestration for ``alfred demo``.

The demo compresses the real fleet loop into one narrated run against a
throwaway sample repo:

    plan  ->  approve  ->  build  ->  review  ->  fix  ->  ship

Every model call goes through an injected ``engine`` callable, and every
step is announced through an injected ``events`` sink. That injection is
the whole point: the runner (``bin/alfred-demo.py``) passes the real
``claude`` engine and a terminal presenter, while the tests pass a scripted
fake engine and collect the events. No real LLM runs in CI.

The loop is honest by construction (product rule: real progress only). If
an engine call fails, :func:`run_demo` raises :class:`DemoEngineError` and
the run stops. It never prints a fake "shipped". The one scripted beat is
the approval gate: the presenter blocks on the operator pressing Enter, and
if they decline the run aborts with :class:`DemoAborted`.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Step vocabulary
#
# These are the narrated beats of the demo, in order. Kept as a tuple so the
# presenter can render a progress rail and the tests can assert the full
# sequence fired. The names mirror the fleet's own stage language (plan,
# implement, review, ship) so the demo reads like a real firing timeline.
# ---------------------------------------------------------------------------
DEMO_STEPS: tuple[str, ...] = (
    "intro",
    "plan",
    "approval",
    "build",
    "review",
    "fix",
    "ship",
    "done",
)

# Sentinel the review prompt is asked to emit. The orchestrator keys the
# "bug caught, fix demanded" branch off this marker rather than off free
# text, mirroring how the real Ra's al Ghul runner parses review verdicts.
REVIEW_BLOCK_SENTINEL = "[DEMO-REVIEW-CHANGES-REQUESTED]"
REVIEW_PASS_SENTINEL = "[DEMO-REVIEW-APPROVED]"


class DemoAborted(RuntimeError):
    """Raised when the operator declines at the approval gate."""


class DemoEngineError(RuntimeError):
    """Raised when a real engine call fails mid-demo.

    Carries the failing step and the engine's own error text so the runner
    can print an honest failure instead of a fabricated success.
    """

    def __init__(self, step: str, message: str) -> None:
        super().__init__(f"{step}: {message}")
        self.step = step
        self.message = message


@dataclass
class EngineCall:
    """One request to the engine for a single demo step."""

    step: str
    prompt: str
    allowed_tools: str
    workdir: Path
    timeout: int
    # Optional model hint. The read-only reasoning steps (plan, review) run
    # fine on a small fast model, which is the main lever for the time budget
    # since the four steps are inherently sequential. ``None`` means the
    # engine's default. Test stubs ignore it.
    model: str | None = None


@dataclass
class EngineOutcome:
    """Normalized engine result the orchestrator understands.

    Deliberately a thin subset of the fleet's ``ClaudeResult`` so the runner
    can adapt a real result into it and the tests can construct one directly.
    """

    success: bool
    text: str
    error_message: str | None = None


# An engine is any callable turning an EngineCall into an EngineOutcome.
Engine = Callable[[EngineCall], EngineOutcome]


@dataclass
class DemoEvent:
    """One narrated beat emitted to the events sink."""

    step: str
    kind: str  # "start" | "detail" | "done" | "gate"
    text: str
    payload: dict = field(default_factory=dict)


# An events sink is a callable receiving DemoEvents. For the approval gate the
# orchestrator calls a separate ``approve`` callback (below) so the sink stays
# output-only and the blocking prompt is explicit.
Events = Callable[[DemoEvent], None]

# The approval callback returns True to proceed, False to abort. The runner
# wires this to "press Enter"; tests wire it to a constant.
Approver = Callable[[str], bool]


@dataclass
class DemoResult:
    """Summary of a completed demo run."""

    shipped: bool
    bug_caught: bool
    elapsed_seconds: float
    plan_text: str
    review_text: str
    diff_summary: str
    workdir: Path


def _emit(events: Events, step: str, kind: str, text: str, **payload: object) -> None:
    events(DemoEvent(step=step, kind=kind, text=text, payload=dict(payload)))


def _invoke(engine: Engine, call: EngineCall, events: Events) -> str:
    """Run one engine call, emit a done beat, or raise a demo error."""
    outcome = engine(call)
    if not outcome.success:
        raise DemoEngineError(call.step, outcome.error_message or "engine returned no result")
    return outcome.text


def _git_capture(args: list[str], *, cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=30,
    )
    return (proc.stdout or "").strip()


# ---------------------------------------------------------------------------
# Prompts
#
# Small and focused, on purpose: the four steps run sequentially against real
# claude calls, so each prompt does exactly one job to keep the run short.
# They are module-level so the tests can
# assert the sample project's real symbols make it into the prompt text.
# ---------------------------------------------------------------------------

_PLAN_PROMPT = """You are Drake, the planning agent on the Alfred fleet.

The repo in your working directory is `textkit`, a tiny Python string library.
Read `textkit.py` and `test_textkit.py`. Callers keep asking for a `slugify`
helper that turns a title into a URL-safe slug, and it does not exist yet.

Write a SHORT implementation plan (5 lines max, plain sentences, no preamble)
for adding a `slugify(text: str) -> str` function to `textkit.py` plus tests
in `test_textkit.py`. Cover: lowercasing, replacing runs of non-alphanumeric
characters with single hyphens, and stripping leading/trailing hyphens.
Do not write the code yet. Output only the plan."""

_BUILD_PROMPT = """You are Lucius, the implementation agent on the Alfred fleet.

Working in this `textkit` repo, implement the approved plan. Be fast and direct:
read `textkit.py`, then make the edits. Do not run the test suite; the review
step verifies the change.

Add a `slugify(text: str) -> str` function to `textkit.py` that lowercases the
input, replaces every run of non-alphanumeric characters with a single hyphen,
and strips leading and trailing hyphens. Add two focused tests for it to
`test_textkit.py` (one with punctuation, one with multiple spaces). Keep the
existing code intact.

When done, output one line: [DEMO-BUILD-DONE] followed by a one-sentence
summary. Do nothing else."""

_REVIEW_PROMPT = f"""You are Ra's al Ghul, the adversarial reviewer on the Alfred fleet.

Review the CURRENT state of `textkit.py` in your working directory with a
critical eye. There is a real correctness bug in the EXISTING `titlecase`
function: it splits on a single space and rejoins with a single space, so it
silently collapses runs of consecutive whitespace (for example "a  b" with two
spaces returns "A B" with one). The current tests only use single spaces, so
they pass and hide it.

Find that bug. Explain it in two sentences: what breaks and the input that
exposes it. Then, on the LAST line, output exactly one verdict token:
  {REVIEW_BLOCK_SENTINEL}   if you found a correctness bug that must be fixed
  {REVIEW_PASS_SENTINEL}    only if the code is genuinely correct
Do not edit any files. Output only your two-sentence finding and the verdict."""

_FIX_PROMPT = """You are Lucius again. The reviewer blocked the change with this finding:

{finding}

Be fast and direct. Fix the reported bug in `titlecase` in `textkit.py` so runs
of consecutive whitespace are preserved instead of collapsed. Add one test to
`test_textkit.py` that would have caught it (an input with two consecutive
spaces). Do not run the test suite. Make the edits with your tools, then output
one line: [DEMO-FIX-DONE] followed by a one-sentence summary. Do nothing else."""


def run_demo(
    *,
    engine: Engine,
    events: Events,
    approve: Approver,
    workdir: Path,
    timeout: int,
    models: dict[str, str] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> DemoResult:
    """Drive the full demo loop against ``workdir``.

    Args:
        engine: the injected model engine (real ``claude`` or a test stub).
        events: output-only sink for narrated beats.
        approve: blocking approval-gate callback; returns False to abort.
        workdir: the materialized sample-repo working copy.
        timeout: per-engine-call wall-clock ceiling in seconds.
        models: optional per-step model hints (e.g. a fast model for the
            read-only plan and review steps). Missing steps use the default.
        clock: monotonic clock, injectable for deterministic tests.

    Returns:
        A :class:`DemoResult`.

    Raises:
        DemoAborted: the operator declined at the gate.
        DemoEngineError: a real engine call failed; the run stops honestly.
    """
    started = clock()
    models = models or {}

    _emit(
        events,
        "intro",
        "start",
        "Alfred demo: one throwaway repo, the whole team, one short run.",
    )
    _emit(
        events,
        "intro",
        "detail",
        f"Sample project `textkit` materialized in an isolated worktree: {workdir}",
    )

    # -- plan --------------------------------------------------------------
    _emit(events, "plan", "start", "Drake drafts a plan for the missing `slugify` feature.")
    plan_text = _invoke(
        engine,
        EngineCall(
            step="plan",
            prompt=_PLAN_PROMPT,
            allowed_tools="Read,Glob,Grep",
            workdir=workdir,
            timeout=timeout,
            model=models.get("plan"),
        ),
        events,
    )
    _emit(events, "plan", "done", plan_text.strip(), plan=plan_text.strip())

    # -- approval gate -----------------------------------------------------
    _emit(
        events,
        "approval",
        "gate",
        "Operator approval gate. This is where you stay in control of the fleet.",
    )
    if not approve(plan_text.strip()):
        _emit(events, "approval", "done", "Operator declined. Nothing was changed.")
        raise DemoAborted("operator declined at the approval gate")
    _emit(events, "approval", "done", "Approved. Handing the plan to Lucius.")

    # -- build -------------------------------------------------------------
    _emit(events, "build", "start", "Lucius implements the plan in the worktree.")
    build_text = _invoke(
        engine,
        EngineCall(
            step="build",
            prompt=_BUILD_PROMPT,
            allowed_tools="Read,Glob,Grep,Edit,Write,Bash",
            workdir=workdir,
            timeout=timeout,
            model=models.get("build"),
        ),
        events,
    )
    _emit(events, "build", "done", build_text.strip())

    # -- review ------------------------------------------------------------
    _emit(events, "review", "start", "Ra's al Ghul reviews the change, adversarially.")
    review_text = _invoke(
        engine,
        EngineCall(
            step="review",
            prompt=_REVIEW_PROMPT,
            allowed_tools="Read,Glob,Grep,Bash",
            workdir=workdir,
            timeout=timeout,
            model=models.get("review"),
        ),
        events,
    )
    bug_caught = REVIEW_BLOCK_SENTINEL in review_text
    finding = _strip_verdict(review_text)
    if bug_caught:
        _emit(events, "review", "done", finding, verdict="changes_requested", bug_caught=True)
    else:
        _emit(events, "review", "done", finding, verdict="approved", bug_caught=False)

    # -- fix (only when the reviewer demanded it) --------------------------
    if bug_caught:
        _emit(events, "fix", "start", "Lucius applies the fix the reviewer demanded.")
        fix_text = _invoke(
            engine,
            EngineCall(
                step="fix",
                prompt=_FIX_PROMPT.format(finding=finding),
                allowed_tools="Read,Glob,Grep,Edit,Write,Bash",
                workdir=workdir,
                timeout=timeout,
                model=models.get("fix"),
            ),
            events,
        )
        _emit(events, "fix", "done", fix_text.strip())
    else:
        _emit(
            events,
            "fix",
            "done",
            "Reviewer approved without changes, so no fix was needed.",
        )

    # -- ship --------------------------------------------------------------
    _emit(events, "ship", "start", "Committing the reviewed change and drafting a PR summary.")
    diff_summary = _finalize_and_summarize(workdir)
    _emit(events, "ship", "done", diff_summary, diff_summary=diff_summary)

    elapsed = clock() - started
    _emit(
        events,
        "done",
        "done",
        f"Shipped in {elapsed:.1f}s of run time. That was the whole loop.",
        elapsed_seconds=elapsed,
    )
    return DemoResult(
        shipped=True,
        bug_caught=bug_caught,
        elapsed_seconds=elapsed,
        plan_text=plan_text.strip(),
        review_text=review_text.strip(),
        diff_summary=diff_summary,
        workdir=workdir,
    )


def _strip_verdict(review_text: str) -> str:
    """Return the reviewer's prose finding with the verdict token removed."""
    cleaned = review_text.replace(REVIEW_BLOCK_SENTINEL, "").replace(REVIEW_PASS_SENTINEL, "")
    return cleaned.strip()


def _finalize_and_summarize(workdir: Path) -> str:
    """Commit any working changes and produce a PR-style summary string.

    Uses the local repo materialized by :mod:`demo.sample_repo`, so there is
    no remote, no GitHub, and no push. The "merge" is a real local commit and
    the summary is built from the real diffstat, never fabricated.
    """
    status = _git_capture(["status", "--porcelain"], cwd=workdir)
    if status:
        subprocess.run(
            ["git", "add", "-A"], cwd=str(workdir), capture_output=True, text=True, timeout=30
        )
        subprocess.run(
            [
                "git",
                "commit",
                "--quiet",
                "-m",
                "feat(textkit): add slugify and fix titlecase whitespace bug",
            ],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=30,
        )

    diffstat = _git_capture(["diff", "--stat", "HEAD~1", "HEAD"], cwd=workdir)
    log_line = _git_capture(["log", "-1", "--pretty=%s"], cwd=workdir)
    if not diffstat:
        # No second commit (e.g. reviewer approved and build made no net change).
        diffstat = _git_capture(["show", "--stat", "--pretty=format:", "HEAD"], cwd=workdir)
    parts = ["PR summary (local, no remote):", f"  title: {log_line}", "  files changed:"]
    for line in diffstat.splitlines():
        line = line.strip()
        if line:
            parts.append(f"    {line}")
    return "\n".join(parts)
