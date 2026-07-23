"""Isolated local-checkout discovery for the setup repository picker."""

from __future__ import annotations

import json
import sys
from typing import Any

from .setup import _repo_picker_local_paths_sync


def _run(payload: dict[str, Any]) -> list[dict[str, Any]]:
    repos = payload.get("repos")
    selected = payload.get("selected")
    env = payload.get("env")
    deadline = payload.get("deadline")
    if not isinstance(repos, list) or not all(isinstance(repo, str) for repo in repos):
        raise ValueError("repos must be a list of strings")
    if not isinstance(selected, list) or not all(isinstance(repo, str) for repo in selected):
        raise ValueError("selected must be a list of strings")
    if not isinstance(env, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in env.items()
    ):
        raise ValueError("env must be a string map")
    if not isinstance(deadline, int | float):
        raise ValueError("deadline must be numeric")
    return _repo_picker_local_paths_sync(repos, set(selected), env, deadline=float(deadline))


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        json.dump(_run(payload), sys.stdout)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
