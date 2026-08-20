#!/usr/bin/env python3
"""``alfred demo`` - the run-and-watch.

Watch the whole Alfred loop on a throwaway repo in one short run, using the
same Claude Code, Codex, OpenCode, or hybrid engine route as the fleet:

    plan  ->  approve  ->  build  ->  review (catches a planted bug)  ->  fix  ->  ship

No GitHub or Slack connection is required. The demo copies the bundled
``examples/demo-repo`` sample project (the ``textkit`` string library) into a
temp dir, makes it a real git repo, and runs a compressed pipeline of real
engine calls against it. It streams progress to the terminal, holds one
operator approval gate you press Enter on, and ends with a PR-style summary and
the measured run time.

If the selected engine CLI is missing, the demo prints an install pointer and
exits. If an engine call fails mid-run, it stops. It prints "shipped" only after
the local commit and verification pass.

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
# main lever on wall time is model choice. The plan step is a one-shot summary
# with no tool use and runs well on a small fast model. The review step is the
# whole point of the demo (it must catch the planted bug), and it drives several
# real Bash probes; a small fast model is measurably flaky at that agentic
# tool-use loop, so review keeps the default (stronger) model for a reliable
# catch. The code-editing steps (build, fix) keep the default model too, so the
# shipped change is reliable. Override the fast model with ALFRED_DEMO_FAST_MODEL,
# or set ALFRED_DEMO_MODEL to force one model everywhere.
_FAST_MODEL = os.environ.get("ALFRED_DEMO_FAST_MODEL", "haiku")


def _step_models() -> dict[str, str]:
    forced = os.environ.get("ALFRED_DEMO_MODEL")
    if forced:
        return dict.fromkeys(("plan", "build", "review", "fix"), forced)
    return {"plan": _FAST_MODEL}


_INSTALL_POINTER = (
    "Ready for the real fleet? See INSTALL.md to point Alfred at your own repos, "
    "then `alfred-init` to choose agents, repos, and your approval rules."
)

_STEP_TURNS = {"plan": 6, "review": 14, "build": 25, "fix": 20}
_WRITE_STEPS = frozenset({"build", "fix"})
_STEP_AGENTS = {
    "plan": "drake",
    "build": "lucius",
    "review": "ras-al-ghul",
    "fix": "lucius",
}
_ENGINE_BINARIES = {
    "claude": ("Claude Code CLI", "CLAUDE_BIN", "claude"),
    "codex": ("Codex CLI", "CODEX_BIN", "codex"),
    "opencode": ("OpenCode CLI", "OPENCODE_BIN", "opencode"),
}


def _engine_binary(engine: str) -> str:
    """Resolve one engine binary name, honoring its configured override."""
    _label, env_name, default = _ENGINE_BINARIES[engine]
    return os.environ.get(env_name, default)


def _preflight_engine(engine_mode: str, stream) -> bool:
    """Return True when the selected route has at least one installed CLI."""
    candidates = ("claude", "codex") if engine_mode == "hybrid" else (engine_mode,)
    if any(shutil.which(_engine_binary(engine)) for engine in candidates):
        return True
    labels = " or ".join(_ENGINE_BINARIES[engine][0] for engine in candidates)
    stream.write(
        f"\nalfred demo needs {labels} for the selected {engine_mode} route, "
        "and no matching CLI is on PATH.\n\n"
        "Install and authenticate one supported engine, then re-run the demo.\n"
        "See docs/ENGINE_ROUTING.md for the tested CLI contracts.\n"
    )
    return False


def _build_real_engine(*, verbose: bool, engine_mode: str):
    """Adapt the fleet engine facade into the demo Engine protocol."""
    # Imported lazily so `--help` and the missing-CLI path stay light.
    from agent_runner import invoke_agent_engine

    # Limit each step. Read-only steps need fewer turns than editing steps.
    def engine(call: EngineCall) -> EngineOutcome:
        allow_writes = call.step in _WRITE_STEPS
        result, engine_used = invoke_agent_engine(
            call.prompt,
            engine=engine_mode,
            agent=_STEP_AGENTS[call.step],
            firing_id=f"demo-{call.workdir.parent.name}-{call.step}",
            workdir=call.workdir,
            claude_allowed_tools=call.allowed_tools,
            timeout=call.timeout,
            claude_model=call.model,
            claude_max_turns=_STEP_TURNS.get(call.step),
            codex_sandbox="workspace-write" if allow_writes else "read-only",
            codex_approval_policy="never",
            opencode_allow_writes=allow_writes,
            hybrid_fallback_on_provider_failure=True,
        )
        text = (result.result_text or "").strip()
        if verbose:
            sys.stderr.write(f"[demo:{call.step}] engine={engine_used}\n")
            if result.error_message:
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
    parser.add_argument(
        "--engine",
        choices=("claude", "codex", "opencode", "hybrid"),
        default="hybrid",
        help="engine route for all demo steps (default hybrid)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    stream = sys.stdout

    if not _preflight_engine(args.engine, stream):
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

    engine = _build_real_engine(
        verbose=bool(os.environ.get("ALFRED_DEMO_VERBOSE")),
        engine_mode=args.engine,
    )

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
            # The review step did not flag the planted bug on this run.
            stream.write(
                "\nNote: the review pass did not flag the planted bug this run. "
                "The loop still shipped a reviewed change; re-run to see the catch.\n"
            )
    except DemoAborted:
        stream.write("\nDemo stopped at the approval gate. Nothing was changed.\n")
        exit_code = 0
    except DemoEngineError as exc:
        stream.write(
            f"\nalfred demo stopped at the {exc.step} step: {exc.message}\n"
            f"The demo did not ship. Check the selected {args.engine} route and try again.\n"
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
