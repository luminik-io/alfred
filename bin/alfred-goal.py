#!/usr/bin/env python3
"""alfred goal - operator-facing CLI for the durable goal ledger.

Alfred is the source of truth for durable goals; engine-local ``/goal``
modes are only execution hints. This thin entrypoint wraps ``lib/goals.py``
so Slack, the native client, and the CLI all read and write the same
ledger under ``$ALFRED_HOME/state/goals/<goal_id>/``.

Subcommands (dispatched from ``alfred goal <cmd>``):

  alfred goal create <outcome> [--verification V ...] [--constraint C ...]
        [--non-goal N ...] [--iteration-policy TEXT] [--human-gate G ...]
        [--blocked-condition TEXT] [--owner WHO] [--repo R ...]
        [--source REF ...] [--id GOAL_ID] [--json]
        File a new goal in ``draft``. Prints the assigned goal id.

  alfred goal list [--status draft|active|blocked|paused|achieved|cleared]
        [--json]
        List goals, newest-filed last, optionally filtered by status.

  alfred goal status <goal_id> [--events] [--json]
        Show one goal's contract and current status; --events appends the
        full audit trail.

  alfred goal pause <goal_id> [--reason TEXT]
  alfred goal approve <goal_id> [--reason TEXT]
  alfred goal activate <goal_id> [--reason TEXT]
  alfred goal resume <goal_id> [--reason TEXT]
  alfred goal clear <goal_id> [--reason TEXT]
        Lifecycle moves through the validated state machine.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for candidate in (
    _HERE.parent / "lib",
    Path(os.environ.get("ALFRED_HOME", "")) / "lib",
):
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import goals  # noqa: E402


def _goal_to_summary(g: goals.Goal) -> dict:
    """Compact dict for --json output and list rows."""
    return {
        "id": g.id,
        "status": g.status,
        "outcome": g.outcome,
        "owner": g.owner,
        "repos": g.repos,
        "created_at": g.created_at,
        "updated_at": g.updated_at,
    }


def cmd_create(args: argparse.Namespace) -> int:
    try:
        g = goals.create(
            args.outcome,
            verification=args.verification,
            constraints=args.constraint,
            non_goals=args.non_goal,
            iteration_policy=args.iteration_policy or "",
            human_gates=args.human_gate,
            blocked_condition=args.blocked_condition or "",
            owner=args.owner or "",
            repos=args.repo,
            source_refs=args.source,
            goal_id=args.id,
        )
    except (goals.GoalExists, ValueError) as e:
        print(f"alfred goal: {e}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(g.to_dict(), indent=2))
    else:
        print(f"created goal {g.id} (status: {g.status})")
        print(f"  outcome: {g.outcome}")
        print(f"  approve/start with: alfred goal approve {g.id}")
        print(f"  manage with: alfred goal <pause|resume|clear> {g.id}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    items = goals.list_goals(status=args.status)
    if args.json:
        print(json.dumps([_goal_to_summary(g) for g in items], indent=2))
        return 0
    if not items:
        scope = f" with status {args.status!r}" if args.status else ""
        print(f"no goals{scope}.")
        return 0
    fmt = "{:<10} {:<44} {}"
    print(fmt.format("status", "id", "outcome"))
    print(fmt.format("-" * 10, "-" * 44, "-" * 7))
    for g in items:
        outcome = g.outcome if len(g.outcome) <= 60 else g.outcome[:57] + "..."
        print(fmt.format(g.status, g.id, outcome))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    try:
        g = goals.get(args.goal_id)
    except (goals.GoalNotFound, ValueError) as e:
        print(f"alfred goal: {e}", file=sys.stderr)
        return 1
    if args.json:
        out = g.to_dict()
        if args.events:
            out["events"] = goals.read_events(g.id)
        print(json.dumps(out, indent=2))
        return 0
    print(f"goal {g.id}")
    print(f"  status:            {g.status}")
    print(f"  outcome:           {g.outcome}")
    if g.owner:
        print(f"  owner:             {g.owner}")
    if g.repos:
        print(f"  repos:             {', '.join(g.repos)}")
    if g.verification:
        print(f"  verification:      {', '.join(g.verification)}")
    if g.constraints:
        print(f"  constraints:       {', '.join(g.constraints)}")
    if g.non_goals:
        print(f"  non-goals:         {', '.join(g.non_goals)}")
    if g.iteration_policy:
        print(f"  iteration policy:  {g.iteration_policy}")
    if g.human_gates:
        print(f"  human gates:       {', '.join(g.human_gates)}")
    if g.blocked_condition:
        print(f"  blocked condition: {g.blocked_condition}")
    if g.source_refs:
        print(f"  source refs:       {', '.join(g.source_refs)}")
    print(f"  created:           {g.created_at}")
    print(f"  updated:           {g.updated_at}")
    if args.events:
        events = goals.read_events(g.id)
        print(f"  events ({len(events)}):")
        for ev in events:
            extra = {k: v for k, v in ev.items() if k not in ("ts", "goal_id", "event")}
            suffix = f"  {extra}" if extra else ""
            print(f"    {ev.get('ts', '?')}  {ev.get('event', '?')}{suffix}")
    return 0


def _lifecycle(args: argparse.Namespace, fn) -> int:
    fields = {}
    if getattr(args, "reason", None):
        fields["reason"] = args.reason
    try:
        g = fn(args.goal_id, **fields)
    except goals.GoalNotFound as e:
        print(f"alfred goal: {e}", file=sys.stderr)
        return 1
    except (goals.InvalidTransition, ValueError) as e:
        print(f"alfred goal: {e}", file=sys.stderr)
        return 2
    print(f"goal {g.id} -> {g.status}")
    return 0


def cmd_pause(args: argparse.Namespace) -> int:
    return _lifecycle(args, goals.pause)


def cmd_approve(args: argparse.Namespace) -> int:
    return _lifecycle(args, goals.approve)


def cmd_resume(args: argparse.Namespace) -> int:
    return _lifecycle(args, goals.resume)


def cmd_clear(args: argparse.Namespace) -> int:
    return _lifecycle(args, goals.clear)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="alfred goal", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("create", help="file a new draft goal")
    pc.add_argument("outcome", help="what must be true when Alfred is done")
    pc.add_argument(
        "--verification", action="append", metavar="V", help="evidence that proves done"
    )
    pc.add_argument("--constraint", action="append", metavar="C")
    pc.add_argument("--non-goal", action="append", metavar="N", dest="non_goal")
    pc.add_argument("--iteration-policy", dest="iteration_policy")
    pc.add_argument("--human-gate", action="append", metavar="G", dest="human_gate")
    pc.add_argument("--blocked-condition", dest="blocked_condition")
    pc.add_argument("--owner")
    pc.add_argument("--repo", action="append", metavar="R")
    pc.add_argument("--source", action="append", metavar="REF", help="source Slack/GitHub ref")
    pc.add_argument("--id", help="force a specific goal id (errors on collision)")
    pc.add_argument("--json", action="store_true")
    pc.set_defaults(func=cmd_create)

    pl = sub.add_parser("list", help="list goals")
    pl.add_argument("--status", choices=sorted(goals.STATUSES))
    pl.add_argument("--json", action="store_true")
    pl.set_defaults(func=cmd_list)

    ps = sub.add_parser("status", help="show one goal")
    ps.add_argument("goal_id")
    ps.add_argument("--events", action="store_true", help="include the audit trail")
    ps.add_argument("--json", action="store_true")
    ps.set_defaults(func=cmd_status)

    for name, fn in (
        ("approve", cmd_approve),
        ("activate", cmd_approve),
        ("pause", cmd_pause),
        ("resume", cmd_resume),
        ("clear", cmd_clear),
    ):
        pp = sub.add_parser(name, help=f"{name} a goal")
        pp.add_argument("goal_id")
        pp.add_argument("--reason", help="recorded in the audit trail")
        pp.set_defaults(func=fn)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
