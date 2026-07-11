"""Bridge the durable goal ledger into the runtime (additive, guarded).

``lib/goals.py`` is a well-built on-disk ledger that, until now, no runner
consumed: the overnight fleet neither read an active goal before working nor
recorded attempts against one. This module is the bridge. It is deliberately
thin and read-mostly:

- ``active_goals_for_repo()`` selects the ACTIVE goals whose ``repos`` field
  matches a firing's repo (read-only).
- ``render_goal_context()`` turns those goals into one concise, clearly
  labeled standing-objective block.
- ``goal_context_block()`` is the convenience entry point a runner calls with
  the repo it is about to work: it returns the rendered block, or ``""`` when
  the feature is off or no goal applies.
- ``append_system_prompt_args()`` / ``prepend_to_prompt()`` surface that block
  to the two engines: Claude via the native ``--append-system-prompt`` flag,
  Codex (no hooks, no append flag) via prompt assembly.
- ``log_pr_event_for_repo()`` appends an ``attempted`` / ``evidence_added``
  event to every matching active goal when a firing opens a PR. This is
  additive audit only; it never changes a goal's status and never raises.

Design rules (mirror ``goals.py`` and ``alfred_hooks.py``):

- Stdlib only, so this imports cleanly under any ``python3`` (launchd, the
  bash CLI, the hook subprocess, the test suite) without the venv.
- Feature-guarded and fail-soft. The whole bridge is a no-op unless the
  operator opts in via ``ALFRED_GOAL_WIRING`` AND a matching active goal
  exists. Any error reading the ledger is swallowed and treated as "no
  active goal", so a broken or empty ledger can never regress a firing that
  works today.
- No control-flow changes for callers. Every public function either returns
  context text (empty when inapplicable) or performs best-effort logging.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import goals
from envflags import truthy

# Env knob that arms the whole bridge. OFF by default: a host that does not
# set this behaves exactly as it does today, regardless of ledger contents.
# Accepts the same truthy vocabulary as the rest of the runner.
GOAL_WIRING_ENV = "ALFRED_GOAL_WIRING"

# A standing objective should orient a firing, not drown its real task. Cap
# how many goals and how much per-field text we inject so the block stays a
# tight header rather than a second prompt.
_MAX_GOALS = 3
_MAX_LIST_ITEMS = 6
_MAX_FIELD_CHARS = 600

# Marker that opens the injected block. Tests and the Codex prepend path key
# off this so the standing objective is unambiguously labeled in transcripts.
CONTEXT_HEADER = "## Alfred standing objective (durable goal)"


def goal_wiring_enabled() -> bool:
    """True only when the operator armed the bridge via ``ALFRED_GOAL_WIRING``.

    Default OFF. This is the outer feature-guard: when it returns False every
    other entry point short-circuits to a no-op so the firing is byte-for-byte
    what it is today.
    """
    return truthy(os.environ.get(GOAL_WIRING_ENV))


def _normalize_repo(repo: str) -> str:
    """Canonicalize a repo token for matching.

    Goals may record a repo as a bare short name (``your-backend``), an
    ``owner/name`` slug, or even a URL fragment. Runners pass the short GH
    name. Lowercase, drop any owner prefix, and strip a trailing ``.git`` so
    the three spellings compare equal.
    """
    token = (repo or "").strip().lower()
    if not token:
        return ""
    # Keep only the final path segment (drops an ``owner/`` prefix or a URL).
    token = token.rstrip("/").split("/")[-1]
    if token.endswith(".git"):
        token = token[: -len(".git")]
    return token


def _goal_matches_repo(goal: goals.Goal, repo_norm: str) -> bool:
    """True when ``goal`` is scoped to ``repo_norm``.

    A goal with an empty ``repos`` list is treated as fleet-wide and matches
    every repo, mirroring how an operator would file a cross-cutting goal
    without naming a repo. A goal that names repos matches only when one of
    them normalizes to the firing's repo.
    """
    if not goal.repos:
        return True
    return any(_normalize_repo(r) == repo_norm for r in goal.repos)


def active_goals_for_repo(repo: str, *, root: Path | None = None) -> list[goals.Goal]:
    """Return the ACTIVE goals relevant to a firing on ``repo`` (read-only).

    Selection is intentionally narrow: only ``active`` goals (draft/paused/
    blocked/terminal goals never steer a firing) whose scope includes the
    repo. Returns ``[]`` when the feature is off, when ``repo`` is blank, or on
    any error reading the ledger, so callers can treat ``[]`` as "no-op". The
    ledger is never mutated here.
    """
    if not goal_wiring_enabled():
        return []
    repo_norm = _normalize_repo(repo)
    if not repo_norm:
        return []
    try:
        active = goals.list_goals(status=goals.ACTIVE, root=root)
    except Exception as e:  # pragma: no cover - defensive; ledger read is best-effort
        print(f"[goal_context] ledger read failed: {e}", file=sys.stderr)
        return []
    return [g for g in active if _goal_matches_repo(g, repo_norm)]


def _bullets(label: str, items: list[str]) -> list[str]:
    """Render a capped bulleted sub-list, or ``[]`` when there is nothing."""
    cleaned = [str(i).strip() for i in (items or []) if str(i).strip()]
    if not cleaned:
        return []
    lines = [f"{label}:"]
    for item in cleaned[:_MAX_LIST_ITEMS]:
        lines.append(f"  - {item[:_MAX_FIELD_CHARS]}")
    extra = len(cleaned) - _MAX_LIST_ITEMS
    if extra > 0:
        lines.append(f"  - (+{extra} more)")
    return lines


def _render_one(goal: goals.Goal) -> list[str]:
    """Render a single goal's operator contract as labeled lines."""
    lines = [f"- outcome: {goal.outcome.strip()[:_MAX_FIELD_CHARS]}"]
    lines.extend(f"  {ln}" for ln in _bullets("verification", goal.verification))
    lines.extend(f"  {ln}" for ln in _bullets("constraints", goal.constraints))
    lines.extend(f"  {ln}" for ln in _bullets("non-goals", goal.non_goals))
    lines.extend(f"  {ln}" for ln in _bullets("human gates (stop and ask)", goal.human_gates))
    if goal.iteration_policy.strip():
        lines.append(f"  iteration policy: {goal.iteration_policy.strip()[:_MAX_FIELD_CHARS]}")
    if goal.blocked_condition.strip():
        lines.append(f"  stop if: {goal.blocked_condition.strip()[:_MAX_FIELD_CHARS]}")
    lines.append(f"  goal id: {goal.id}")
    return lines


def render_goal_context(goal_list: list[goals.Goal]) -> str:
    """Render selected goals into one concise standing-objective block.

    Returns ``""`` for an empty list so callers can concatenate the result
    unconditionally. The block is clearly labeled as a standing objective that
    frames (does not replace) the firing's immediate task.
    """
    if not goal_list:
        return ""
    lines = [
        CONTEXT_HEADER,
        "",
        "This firing runs under a durable, operator-owned goal from Alfred's "
        "ledger. Treat it as the standing objective that frames your immediate "
        "task: honor its constraints and non-goals, aim your work at its "
        "outcome, and stop for a human at any listed human gate. It does not "
        "replace the specific task below.",
        "",
    ]
    for goal in goal_list[:_MAX_GOALS]:
        lines.extend(_render_one(goal))
    return "\n".join(lines).rstrip() + "\n"


def goal_context_block(repo: str, *, root: Path | None = None) -> str:
    """Selection + render in one call. ``""`` when there is no active goal.

    This is the single entry point a runner uses to obtain the standing
    objective for the repo it is about to work. Empty string is the no-op
    signal (feature off, no matching goal, or ledger error).
    """
    return render_goal_context(active_goals_for_repo(repo, root=root))


def append_system_prompt_args(repo: str, *, root: Path | None = None) -> list[str]:
    """Claude ``--append-system-prompt`` args for the repo's standing objective.

    Returns ``["--append-system-prompt", <block>]`` when an active goal
    applies, else ``[]`` so the command line is unchanged for firings without
    a goal. Native flag, so the objective rides Claude's system prompt rather
    than being string-concatenated into the user prompt.
    """
    block = goal_context_block(repo, root=root)
    if not block:
        return []
    return ["--append-system-prompt", block]


def prepend_to_prompt(prompt: str, repo: str, *, root: Path | None = None) -> str:
    """Prepend the standing objective to a prompt (the Codex injection path).

    Codex ``exec`` has no hooks and no ``--append-system-prompt``; the only
    channel is the prompt itself. Returns ``prompt`` unchanged when there is no
    active goal, so non-goal Codex firings are untouched.
    """
    block = goal_context_block(repo, root=root)
    if not block:
        return prompt
    return f"{block}\n{prompt}"


def log_pr_event_for_repo(
    repo: str,
    *,
    event: str = goals.EVENT_ATTEMPTED,
    root: Path | None = None,
    **fields: object,
) -> list[str]:
    """Append a lifecycle event to every active goal matching ``repo``.

    Called from a runner's result handling when a firing opens or advances a
    PR tied to a goal. Purely additive audit: it appends ``attempted`` (or the
    given ``event``, e.g. ``evidence_added``) to ``events.jsonl`` and never
    changes a goal's status, never gates, and never raises. Returns the ids it
    logged against (``[]`` when the feature is off, no goal matches, or the
    write fails) for the caller's own event stream.

    The convenience wrappers (``goals.add_attempt`` / ``goals.add_evidence``)
    are used so a terminal goal is skipped rather than corrupted; any
    per-goal failure is swallowed so one bad goal never aborts the firing.
    """
    if not goal_wiring_enabled():
        return []
    try:
        matched = active_goals_for_repo(repo, root=root)
    except Exception:  # pragma: no cover - active_goals_for_repo already guards
        return []
    logged: list[str] = []
    for goal in matched:
        try:
            if event == goals.EVENT_EVIDENCE_ADDED:
                goals.add_evidence(goal.id, root=root, **fields)
            else:
                goals.add_attempt(goal.id, root=root, **fields)
            logged.append(goal.id)
        except Exception as e:  # never let goal logging break a real firing
            print(
                f"[goal_context] event-log for goal {goal.id} failed: {e}",
                file=sys.stderr,
            )
    return logged


__all__ = [
    "CONTEXT_HEADER",
    "GOAL_WIRING_ENV",
    "active_goals_for_repo",
    "append_system_prompt_args",
    "goal_context_block",
    "goal_wiring_enabled",
    "log_pr_event_for_repo",
    "prepend_to_prompt",
    "render_goal_context",
]
