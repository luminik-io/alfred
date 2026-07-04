#!/usr/bin/env python3
"""``alfred demo`` - the run-and-watch.

Watch the whole Alfred loop on a throwaway repo in one short run, with zero
configuration beyond an authenticated ``claude`` CLI:

    plan  ->  approve  ->  build  ->  review (catches a planted bug)  ->  fix  ->  ship

No GitHub, no Slack, no tokens. The demo copies the bundled ``examples/demo-repo``
sample project (the ``textkit`` string library) into a temp dir, makes it a real
git repo, and runs a compressed pipeline of REAL ``claude`` calls against it. It
streams progress to the terminal, holds one operator approval gate you press
Enter on, and ends with a PR-style summary and the measured run time.

It is honest by construction: if the ``claude`` CLI is missing it prints an
install pointer and exits; if an engine call fails mid-run it stops and says so.
It never prints a fake "shipped".

The heavy orchestration lives in ``lib/demo`` so it is unit-tested with a stubbed
engine; this file is the thin runner that wires the real engine and the terminal
presenter together.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Resolve lib/ relative to this script, matching bin/alfred: prefer the
# script's own checkout so a source run never imports a stale deployed lib.
_HERE = Path(__file__).resolve().parent
for _candidate in (
    Path(os.environ.get("ALFRED_HOME", "")) / "lib",
    _HERE.parent / "lib",
):
    _cp = str(_candidate)
    if _candidate.is_dir():
        while _cp in sys.path:
            sys.path.remove(_cp)
        sys.path.insert(0, _cp)

from demo import (  # noqa: E402  (import after sys.path shim)
    DemoAborted,
    DemoEngineError,
    EngineCall,
    EngineOutcome,
    materialize_sample_repo,
    run_demo,
)
from demo.presenter import Presenter  # noqa: E402

# Default per-engine-call ceiling. The whole demo targets a short run across
# four small, focused calls; this is the wall-clock guard per call.
_DEFAULT_STEP_TIMEOUT = 90

# The four steps are inherently sequential (each depends on the prior), so the
# main lever on wall time is model choice. The read-only reasoning steps (plan,
# review) run well on a small fast model; the code-editing steps (build, fix)
# keep the default (stronger) model so the shipped change is reliable. Override
# the fast model with ALFRED_DEMO_FAST_MODEL, or set ALFRED_DEMO_MODEL to force
# one model everywhere.
_FAST_MODEL = os.environ.get("ALFRED_DEMO_FAST_MODEL", "haiku")


def _step_models() -> dict[str, str]:
    forced = os.environ.get("ALFRED_DEMO_MODEL")
    if forced:
        return dict.fromkeys(("plan", "build", "review", "fix"), forced)
    return {"plan": _FAST_MODEL, "review": _FAST_MODEL}


_INSTALL_POINTER = (
    "Ready for the real fleet? See INSTALL.md to point Alfred at your own repos, "
    "then `alfred-init` to choose agents, repos, and your approval rules."
)


def _claude_bin() -> str:
    """Resolve the Claude CLI binary name, honoring CLAUDE_BIN."""
    return os.environ.get("CLAUDE_BIN", "claude")


def _preflight_claude(stream) -> bool:
    """Return True when the ``claude`` CLI is on PATH, else print a pointer."""
    if shutil.which(_claude_bin()):
        return True
    stream.write(
        "\nalfred demo needs the Claude Code CLI, and it is not on your PATH.\n\n"
        "Install it and authenticate once:\n"
        "  1. Install Claude Code: https://docs.claude.com/en/docs/claude-code\n"
        "  2. Run `claude` once and sign in with your Claude subscription.\n\n"
        "Then re-run `alfred demo`. No API key or token is required.\n"
    )
    return False


def _build_real_engine(*, verbose: bool):
    """Adapt the fleet's ``claude_invoke`` into the demo Engine protocol."""
    # Imported lazily so `--help` and the missing-CLI path stay light.
    from agent_runner import claude_invoke

    # Keep each step snappy: the read-only reasoning steps need only a couple
    # of turns; the code-editing steps a handful. This caps a step that would
    # otherwise wander, which is the main tail-latency risk in the run.
    _step_turns = {"plan": 6, "review": 6, "build": 25, "fix": 20}

    def engine(call: EngineCall) -> EngineOutcome:
        result = claude_invoke(
            call.prompt,
            workdir=call.workdir,
            allowed_tools=call.allowed_tools,
            timeout=call.timeout,
            model=call.model,
            max_turns=_step_turns.get(call.step),
        )
        text = (result.result_text or "").strip()
        if verbose and result.error_message:
            sys.stderr.write(f"[demo:{call.step}] engine note: {result.error_message}\n")
        return EngineOutcome(
            success=bool(result.success and text),
            text=text,
            error_message=result.error_message
            or (None if result.success else "engine returned an empty result"),
        )

    return engine


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alfred demo",
        description="Watch the Alfred team plan, build, catch a bug, and ship in one short run.",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="keep the throwaway demo repo instead of deleting it, and print its path",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=_DEFAULT_STEP_TIMEOUT,
        help=f"per-step engine wall-clock ceiling in seconds (default {_DEFAULT_STEP_TIMEOUT})",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="auto-approve the plan gate without waiting for Enter (for scripted runs)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    stream = sys.stdout

    if not _preflight_claude(stream):
        return 2

    presenter = Presenter.for_stream(stream)
    approve = (lambda _plan: True) if args.yes else presenter.approve

    tmp_root = Path(tempfile.mkdtemp(prefix="alfred-demo-"))
    workdir = tmp_root / "textkit"
    try:
        materialize_sample_repo(workdir)
    except (FileNotFoundError, RuntimeError) as exc:
        stream.write(f"\nalfred demo could not set up the sample repo: {exc}\n")
        shutil.rmtree(tmp_root, ignore_errors=True)
        return 1

    engine = _build_real_engine(verbose=bool(os.environ.get("ALFRED_DEMO_VERBOSE")))

    exit_code = 0
    try:
        result = run_demo(
            engine=engine,
            events=presenter.on_event,
            approve=approve,
            workdir=workdir,
            timeout=args.timeout,
            models=_step_models(),
        )
        stream.write("\n" + _INSTALL_POINTER + "\n")
        if not result.bug_caught:
            # Honest note: the review step did not flag the planted bug this run.
            stream.write(
                "\nNote: the review pass did not flag the planted bug this run. "
                "The loop still shipped a reviewed change; re-run to see the catch.\n"
            )
    except DemoAborted:
        stream.write("\nDemo stopped at the approval gate. Nothing was changed.\n")
        exit_code = 0
    except DemoEngineError as exc:
        stream.write(
            f"\nalfred demo stopped honestly at the {exc.step} step: {exc.message}\n"
            "No fake success. Check `claude` is authenticated and try again.\n"
        )
        exit_code = 1
    finally:
        if args.keep:
            stream.write(f"\nDemo repo kept at: {workdir}\n")
        else:
            shutil.rmtree(tmp_root, ignore_errors=True)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
