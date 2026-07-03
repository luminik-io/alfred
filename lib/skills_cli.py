"""Shared CLI for the curated skill packs: list / install / installed.

One implementation, two entry points:

* ``bin/alfred skills ...`` (source checkout and deployed runtime) delegates
  its parsed args here.
* ``alfred-os skills ...`` (the wheel's console script, ``alfred_os_cli``)
  forwards raw argv to :func:`run`, so an installed wheel exposes the same
  workflow without shipping ``bin/alfred``.

All manifest/install logic lives in :mod:`skill_packs`; this module is only
argument handling and printing, so it stays dependency-free and both entry
points behave identically.
"""

from __future__ import annotations

import argparse
import json
import sys

import skill_packs

__all__ = ["cmd_install", "cmd_installed", "cmd_list", "run"]

_MANIFEST_HINT = (
    "skills manifest not found: {path}\n"
    "From a source checkout this is skills/packs.toml. On a deployed runtime, "
    "re-run deploy.sh so skills/ is copied into $ALFRED_HOME next to lib/."
)


def _load_packs() -> list[skill_packs.Pack]:
    """Load the manifest, translating a missing file into a friendly error."""
    try:
        return skill_packs.load_manifest()
    except FileNotFoundError:
        print(_MANIFEST_HINT.format(path=skill_packs.manifest_path()), file=sys.stderr)
        raise SystemExit(1) from None


def cmd_list(*, role: str | None = None, as_json: bool = False) -> int:
    packs = _load_packs()
    if role:
        packs = [p for p in packs if role in p.roles]
    installed = skill_packs.installed_packs(packs)
    if as_json:
        print(
            json.dumps(
                [
                    {
                        "name": p.name,
                        "summary": p.summary,
                        "source": p.source,
                        "license": p.license,
                        "install": p.install,
                        "roles": list(p.roles),
                        "opt_in": p.opt_in,
                        "installed": p.name in installed,
                    }
                    for p in packs
                ],
                indent=2,
            )
        )
        return 0
    if not packs:
        print("No packs match." if role else "No packs configured.")
        return 0
    print(f"Curated skill packs (skills dir: {skill_packs.default_skills_dir()})")
    for p in packs:
        mark = "installed" if p.name in installed else "available"
        shape = p.install + (" opt-in" if p.opt_in else "")
        print(f"  [{mark:<9}] {p.name:<28} {p.license:<11} {shape}")
        print(f"              {p.summary}")
        if p.roles:
            print(f"              roles: {', '.join(p.roles)}")
    return 0


def cmd_installed(*, as_json: bool = False) -> int:
    packs = _load_packs()
    installed = skill_packs.installed_packs(packs)
    names = sorted(installed)
    skills_dir = skill_packs.default_skills_dir()
    if as_json:
        print(json.dumps({"skills_dir": str(skills_dir), "installed": names}, indent=2))
        return 0
    if not names:
        print(f"No packs installed under {skills_dir}.")
        return 0
    print(f"Installed under {skills_dir}:")
    for name in names:
        print(f"  {name}")
    return 0


def cmd_install(pack_name: str, *, yes: bool = False, dry_run: bool = False) -> int:
    packs = _load_packs()
    by_name = {p.name: p for p in packs}
    target = by_name.get(pack_name)
    if target is None:
        print(f"Unknown pack {pack_name!r}. Run `alfred skills list`.", file=sys.stderr)
        return 2
    if target.is_fetch and not yes and not dry_run:
        print(
            f"Pack {target.name!r} is reference-install: it runs a network command:\n"
            f"  {target.fetch_cmd}\n"
            f"Re-run with --yes to proceed (or --dry-run to preview).",
            file=sys.stderr,
        )
        return 1
    try:
        result = skill_packs.install_pack(target, dry_run=dry_run)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"Install failed: {exc}", file=sys.stderr)
        return 1
    verb = "would install" if result.dry_run else "installed"
    if result.fetched:
        print(f"{verb} {result.pack} (fetch) -> {result.dest}")
        print(f"  command: {result.fetched}")
    else:
        print(f"{verb} {result.pack} (vendored) -> {result.dest}")
    if target.attribution:
        print(f"  license: {target.license} | {target.attribution}")
    return 0


def build_parser(prog: str = "alfred skills") -> argparse.ArgumentParser:
    """Standalone parser for the ``skills`` verbs (used by the wheel CLI)."""
    parser = argparse.ArgumentParser(prog=prog, description="manage curated skill packs")
    sub = parser.add_subparsers(dest="skills_command", required=True)

    p_list = sub.add_parser("list", help="list curated skill packs")
    p_list.add_argument("--role", help="only packs recommended for this agent role")
    p_list.add_argument("--json", action="store_true")

    p_install = sub.add_parser("install", help="install one pack into the skills dir")
    p_install.add_argument("pack", help="pack name (see `list`)")
    p_install.add_argument(
        "--yes",
        action="store_true",
        help="confirm running a reference-install pack's network fetch command",
    )
    p_install.add_argument(
        "--dry-run", action="store_true", help="show what would happen without writing or fetching"
    )

    p_installed = sub.add_parser("installed", help="list installed packs")
    p_installed.add_argument("--json", action="store_true")
    return parser


def dispatch(args: argparse.Namespace) -> int:
    """Route a parsed namespace (from :func:`build_parser` or ``bin/alfred``)."""
    command = getattr(args, "skills_command", None)
    if command == "list":
        return cmd_list(
            role=getattr(args, "role", None), as_json=bool(getattr(args, "json", False))
        )
    if command == "installed":
        return cmd_installed(as_json=bool(getattr(args, "json", False)))
    if command == "install":
        return cmd_install(
            args.pack,
            yes=bool(getattr(args, "yes", False)),
            dry_run=bool(getattr(args, "dry_run", False)),
        )
    print("Usage: alfred skills {list|install <pack>|installed}", file=sys.stderr)
    return 2


def run(argv: list[str] | None = None) -> int:
    """Entry point for ``alfred-os skills ...`` (raw argv, no outer parser)."""
    args = build_parser(prog="alfred-os skills").parse_args(argv)
    return dispatch(args)
