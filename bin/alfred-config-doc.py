#!/usr/bin/env python3
"""Regenerate operator config docs from the central registry.

Single source of truth is ``lib/alfred_config.py``. This script emits two
generated artifacts and is the only supported way to change them:

* ``.env.example``  - operator-facing vars, grouped by category, with the
  registered default shown as a commented line.
* ``docs/CONFIG.md`` - the full reference table, including internal vars.

Usage::

    bin/alfred-config-doc.py           # rewrite both files in place
    bin/alfred-config-doc.py --check   # exit 1 if either file is stale

The ``--check`` mode is CI-friendly: it proves the committed docs still match
the registry without touching the tree.
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))

import alfred_config as cfg  # noqa: E402 - path set above

ENV_EXAMPLE = REPO_ROOT / ".env.example"
CONFIG_MD = REPO_ROOT / "docs" / "CONFIG.md"

# Vars that must stay UNCOMMENTED in .env.example because install.sh rewrites
# the line in place with ``s|^VAR=.*|VAR=...|`` when seeding a fresh
# ``$ALFRED_HOME/.env``. A commented ``# VAR=`` line would not match, so the
# operator's prompted value would be silently dropped. Keep this set in sync
# with the sed substitutions in install.sh.
REQUIRED_UNCOMMENTED = frozenset(
    {
        "GH_ORG",
        "OPERATOR_NAME",
        "OPERATOR_EMAIL",
        "ALFRED_HOME",
        "WORKSPACE_ROOT",
    }
)

CATEGORY_TITLES: dict[str, str] = {
    "runtime": "Runtime, paths, and repo scope",
    "memory": "Memory providers and code intelligence",
    "engine": "Engine selection, quotas, retries",
    "batteries": "Context batteries (governor, read-delta, digests)",
    "compression": "Context compression (headroom, condenser)",
    "slack": "Slack transport, converse, and bridge",
    "server": "Serve API and status cache",
    "scheduler": "Scheduler, cleanup, disk guardian, backup",
    "telemetry": "Proof telemetry",
    "agents": "Per-agent caps, repos, engines",
    "ops": "Ops integrations (E2E, ops-watch, scrub)",
    "onboarding": "Onboarding and theming",
    "internal": "Internal and process-set",
}

_HEADER = """\
# Alfred operator config example.
#
# GENERATED FILE - do not edit by hand.
# Regenerate with `bin/alfred-config-doc.py` after changing the registry in
# `lib/alfred_config.py`. `bin/alfred-config-doc.py --check` fails CI on drift.
#
# Copy to $ALFRED_HOME/.env (install.sh does this on a fresh setup), fill in
# the values you need, and every scheduler-spawned agent loads it through
# bin/agent-launch at firing time.
#
# This file is parsed as dotenv-style key/value pairs. Lines are commented
# with their registered default; uncomment and edit the ones you want to set.
"""


def _wrap_comment(text: str) -> list[str]:
    return [f"# {line}" for line in textwrap.wrap(text, width=74)] or ["#"]


def _value(var: cfg.ConfigVar) -> str:
    if var.kind == "secret":
        return ""
    return var.default or ""


def render_env_example() -> str:
    lines: list[str] = [_HEADER.rstrip("\n")]
    for category in cfg.CATEGORIES:
        group = [v for v in cfg.operator_vars() if v.category == category]
        if not group:
            continue
        title = CATEGORY_TITLES.get(category, category.title())
        lines.append("")
        lines.append("# " + "-" * 70)
        lines.append(f"# {title}")
        lines.append("# " + "-" * 70)
        for var in group:
            lines.append("")
            lines.extend(_wrap_comment(var.description))
            assignment = f"{var.name}={_value(var)}"
            if var.name in REQUIRED_UNCOMMENTED:
                lines.append(assignment)
            else:
                lines.append(f"# {assignment}")
    return "\n".join(lines) + "\n"


def render_config_md() -> str:
    total = len(cfg.all_vars())
    operator = len(cfg.operator_vars())
    lines: list[str] = [
        "<!-- GENERATED FILE - do not edit by hand.",
        "     Regenerate with `bin/alfred-config-doc.py` after changing",
        "     `lib/alfred_config.py`. `--check` fails CI on drift. -->",
        "",
        "# Alfred configuration reference",
        "",
        "Every environment variable Alfred reads is declared once in the typed",
        "registry at `lib/alfred_config.py`. This page is generated from it.",
        "",
        f"- Declared variables: **{total}**",
        f"- Operator-facing (in `.env.example`): **{operator}**",
        f"- Internal / experimental: **{total - operator}**",
        "",
        "Operator-facing vars also appear, with their defaults, in",
        "`.env.example`. Internal vars are listed here for completeness; they",
        "are experimental, deep-tuning, or set by Alfred itself at runtime.",
        "",
    ]
    for category in cfg.CATEGORIES:
        group = [v for v in cfg.all_vars() if v.category == category]
        if not group:
            continue
        title = CATEGORY_TITLES.get(category, category.title())
        lines.append(f"## {title}")
        lines.append("")
        lines.append("| Variable | Type | Default | Scope | Description |")
        lines.append("| --- | --- | --- | --- | --- |")
        for var in group:
            default = "" if var.default is None else f"`{var.default}`"
            scope = "operator" if var.operator else "internal"
            kind = var.kind
            if var.choices:
                kind = f"{kind} ({'/'.join(var.choices)})"
            desc = var.description.replace("|", "\\|")
            lines.append(f"| `{var.name}` | {kind} | {default} | {scope} | {desc} |")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the generated files are stale (do not rewrite).",
    )
    args = parser.parse_args()

    outputs = {
        ENV_EXAMPLE: render_env_example(),
        CONFIG_MD: render_config_md(),
    }

    if args.check:
        stale = []
        for path, content in outputs.items():
            current = path.read_text(encoding="utf-8") if path.exists() else ""
            if current != content:
                stale.append(path)
        if stale:
            names = ", ".join(str(p.relative_to(REPO_ROOT)) for p in stale)
            print(
                f"config docs are stale: {names}\nrun bin/alfred-config-doc.py to regenerate.",
                file=sys.stderr,
            )
            return 1
        print("config docs up to date.")
        return 0

    CONFIG_MD.parent.mkdir(parents=True, exist_ok=True)
    for path, content in outputs.items():
        path.write_text(content, encoding="utf-8")
        print(f"wrote {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
