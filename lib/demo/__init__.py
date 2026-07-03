"""``alfred demo`` - the run-and-watch pipeline.

This package holds the engine-agnostic orchestration for the demo. It is
deliberately split so the loop can be unit-tested with a stubbed engine
(no real LLM in CI):

* :mod:`demo.orchestrator` - the plan / approve / build / review / fix /
  ship state machine. Pure Python. Takes an injectable ``engine`` callable
  and an ``events`` sink, so tests drive it with a fake engine and collect
  the emitted steps.
* :mod:`demo.sample_repo` - materializes the bundled ``examples/demo-repo``
  sample project into a throwaway temp dir and turns it into a real git
  repo, so the build step can run in a real worktree.
* :mod:`demo.presenter` - the terminal event sink: streams progress with the
  fleet's step vocabulary and drives the operator approval gate.

The runner (``bin/alfred-demo.py``) wires the real ``claude`` engine and the
terminal presenter together. Everything below the runner is importable and
side-effect free.
"""

from __future__ import annotations

from .orchestrator import (
    DEMO_STEPS,
    DemoAborted,
    DemoEngineError,
    DemoEvent,
    DemoResult,
    EngineCall,
    EngineOutcome,
    run_demo,
)
from .sample_repo import SAMPLE_REPO_DIR, materialize_sample_repo

__all__ = [
    "DEMO_STEPS",
    "SAMPLE_REPO_DIR",
    "DemoAborted",
    "DemoEngineError",
    "DemoEvent",
    "DemoResult",
    "EngineCall",
    "EngineOutcome",
    "materialize_sample_repo",
    "run_demo",
]
