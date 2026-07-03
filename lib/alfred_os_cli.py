"""Small console entry point for installed alfred-os wheels."""

from __future__ import annotations

import argparse
import importlib.metadata
import sys
from pathlib import Path

import agent_runner


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)

    # `alfred-os skills ...` is the curated skill-pack workflow. Routed before
    # the flag parser so the wheel exposes the same list/install/installed
    # verbs as `alfred skills` (the manifest and vendored tree ship in the
    # wheel via force-include). Imported lazily to keep the bare version
    # printout dependency-free.
    if args_list and args_list[0] == "skills":
        import skills_cli

        return skills_cli.run(args_list[1:])

    parser = argparse.ArgumentParser(
        prog="alfred-os",
        description="alfred-os wheel utilities (use `alfred-os skills --help` for skill packs)",
    )
    parser.add_argument(
        "--paths",
        action="store_true",
        help="print the installed agent_runner module path",
    )
    args = parser.parse_args(args_list)

    if args.paths:
        print(Path(agent_runner.__file__).resolve())
        return 0

    version = importlib.metadata.version("alfred-os")
    print(f"alfred-os {version}")
    print("agent_runner:", Path(agent_runner.__file__).resolve())
    return 0
