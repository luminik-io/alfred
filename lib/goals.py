"""Durable goal ledger - Alfred as the source of truth for goals.

Alfred should treat substantial work as a durable goal, not a loose
prompt. A goal is the operator-owned contract that says what must become
true, how Alfred proves it, which boundaries stay intact, and when Alfred
must stop for human input. This module is the on-disk ledger that backs
that contract. The CLI writes through this module now; Slack, the native
client, planner, and evaluator should use it as they wire goal-awareness in.

Layout under ``$ALFRED_HOME/state/goals/<goal_id>/``:

- ``goal.json``    - the goal entity: id, status, outcome, verification,
  constraints, non_goals, iteration_policy, human_gates, blocked_condition,
  owner, repos, source refs, created_at, updated_at.
- ``events.jsonl`` - append-only audit trail of lifecycle events
  (created, clarified, approved, started, attempted, evidence_added,
  paused, resumed, blocked, achieved, cleared).

Design notes:

- Stdlib only. No third-party imports. This must run from launchd, the
  bash CLI, the native client server, and the test suite without any
  install step.
- The goals root is resolved dynamically from
  ``agent_runner_paths.STATE_ROOT`` on every call rather than captured at
  import time. The test suite (and the native client) monkeypatch
  ``STATE_ROOT`` onto a tmp dir; resolving lazily means those patches take
  effect without re-importing this module. Callers can also pass an
  explicit ``root`` to any function to bypass the global entirely.
- The id scheme is deterministic: a slug of the outcome plus a short
  content hash of the outcome text. No ``Date.now`` / ``random`` reliance,
  so a given outcome maps to a stable id. Collisions (same outcome filed
  twice) are resolved by appending ``-2``, ``-3``, ... using the on-disk
  directory listing, never a random suffix.
- Writes are atomic (tmp + ``os.replace``) so a crashed write never leaves
  a half-written ``goal.json`` behind. ``events.jsonl`` is append-only and
  best-effort: a broken event log must never wedge a goal mutation.
- The lifecycle state machine mirrors ``labels.Transition``: a frozen
  ``Transition`` dataclass plus a transition table, with
  ``is_legal_transition`` / ``legal_transitions`` predicates. Illegal
  ``set_status`` moves raise ``InvalidTransition``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_runner import paths as agent_runner_paths

# ---------------------------------------------------------------------------
# Status constants + lifecycle state machine (mirrors labels.Transition).
# ---------------------------------------------------------------------------
DRAFT = "draft"
"""Goal exists but is not yet approved or safe to start. Initial status."""

ACTIVE = "active"
"""Goal is approved and Alfred may work it."""

BLOCKED = "blocked"
"""Stop condition met; Alfred cannot continue responsibly without a human."""

PAUSED = "paused"
"""Operator paused the goal. No work happens until resumed."""

ACHIEVED = "achieved"
"""Evidence proves the outcome is true. Terminal."""

CLEARED = "cleared"
"""Operator abandoned the goal. Terminal."""

STATUSES: frozenset[str] = frozenset({DRAFT, ACTIVE, BLOCKED, PAUSED, ACHIEVED, CLEARED})

TERMINAL_STATUSES: frozenset[str] = frozenset({ACHIEVED, CLEARED})


@dataclass(frozen=True)
class Transition:
    """A legal lifecycle move.

    ``trigger`` names the event that drives the move (operator command or
    runtime signal). It is documentation, not enforcement: the value of the
    table is which (src, dst) pairs are legal.
    """

    src: str
    dst: str
    trigger: str


_TRANSITIONS: tuple[Transition, ...] = (
    Transition(DRAFT, ACTIVE, "approved or safe to start"),
    Transition(DRAFT, CLEARED, "operator clears a draft"),
    Transition(ACTIVE, BLOCKED, "stop condition met"),
    Transition(ACTIVE, PAUSED, "operator pauses"),
    Transition(ACTIVE, ACHIEVED, "evidence proves done"),
    Transition(ACTIVE, CLEARED, "operator clears an active goal"),
    Transition(BLOCKED, ACTIVE, "operator unblocks"),
    Transition(BLOCKED, PAUSED, "operator pauses a blocked goal"),
    Transition(BLOCKED, CLEARED, "operator clears a blocked goal"),
    Transition(PAUSED, ACTIVE, "operator resumes"),
    Transition(PAUSED, CLEARED, "operator clears a paused goal"),
)


def all_transitions() -> tuple[Transition, ...]:
    """Return the full transition table (for docs / introspection)."""
    return _TRANSITIONS


def legal_transitions(src: str) -> tuple[Transition, ...]:
    """Return every legal transition from ``src``."""
    return tuple(t for t in _TRANSITIONS if t.src == src)


def is_legal_transition(src: str, dst: str) -> bool:
    """True if ``src -> dst`` is a documented lifecycle move."""
    return any(t.src == src and t.dst == dst for t in _TRANSITIONS)


class GoalError(Exception):
    """Base class for goal-ledger errors."""


class GoalNotFound(GoalError):
    """Raised when a goal id has no ``goal.json`` on disk."""


class GoalExists(GoalError):
    """Raised when ``create`` would clobber an existing goal id."""


class InvalidTransition(GoalError):
    """Raised when a status change is not a documented lifecycle move."""


# ---------------------------------------------------------------------------
# Event names - the append-only audit vocabulary.
# ---------------------------------------------------------------------------
EVENT_CREATED = "created"
EVENT_CLARIFIED = "clarified"
EVENT_APPROVED = "approved"
EVENT_STARTED = "started"
EVENT_ATTEMPTED = "attempted"
EVENT_EVIDENCE_ADDED = "evidence_added"
EVENT_PAUSED = "paused"
EVENT_RESUMED = "resumed"
EVENT_BLOCKED = "blocked"
EVENT_ACHIEVED = "achieved"
EVENT_CLEARED = "cleared"

EVENT_NAMES: frozenset[str] = frozenset(
    {
        EVENT_CREATED,
        EVENT_CLARIFIED,
        EVENT_APPROVED,
        EVENT_STARTED,
        EVENT_ATTEMPTED,
        EVENT_EVIDENCE_ADDED,
        EVENT_PAUSED,
        EVENT_RESUMED,
        EVENT_BLOCKED,
        EVENT_ACHIEVED,
        EVENT_CLEARED,
    }
)

# Which lifecycle event a status transition emits, so set_status and the
# convenience wrappers stay in sync without a second source of truth.
_STATUS_EVENT: dict[str, str] = {
    ACTIVE: EVENT_APPROVED,
    BLOCKED: EVENT_BLOCKED,
    PAUSED: EVENT_PAUSED,
    ACHIEVED: EVENT_ACHIEVED,
    CLEARED: EVENT_CLEARED,
}


# ---------------------------------------------------------------------------
# Time + id helpers.
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    """UTC timestamp with millisecond precision, matching EventLog."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


_SLUG_RE = re.compile(r"[^a-z0-9]+")
_GOAL_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,94}[a-z0-9])?$")


def slugify(text: str, *, max_len: int = 48) -> str:
    """Lowercase, hyphenated slug of ``text``.

    Collapses any run of non-alphanumerics to a single hyphen, trims
    leading/trailing hyphens, and caps the length. Returns ``"goal"`` when
    the input has no usable characters so an id is always well-formed.
    """
    slug = _SLUG_RE.sub("-", text.strip().lower()).strip("-")
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")
    return slug or "goal"


def _content_hash(text: str) -> str:
    """Short, stable hex digest of ``text``. Deterministic across runs."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def make_goal_id(outcome: str) -> str:
    """Deterministic base id for a goal from its outcome text.

    ``<slug-of-outcome>-<8-char-sha256>``. No clock or RNG, so the same
    outcome always yields the same base id. ``create`` disambiguates true
    collisions (same outcome filed more than once) with a numeric suffix.
    """
    return f"{slugify(outcome)}-{_content_hash(outcome)}"


def validate_goal_id(goal_id: str) -> str:
    """Return a canonical goal id or raise before it can become a path."""
    gid = str(goal_id).strip() if goal_id is not None else ""
    if not _GOAL_ID_RE.fullmatch(gid):
        raise ValueError(
            "goal_id must be a lowercase slug of 1-96 chars using only "
            "a-z, 0-9, and interior hyphens"
        )
    return gid


# ---------------------------------------------------------------------------
# Path resolution - lazy so STATE_ROOT monkeypatching works in tests.
# ---------------------------------------------------------------------------
def goals_root(root: Path | None = None) -> Path:
    """Return the goals ledger root directory.

    ``root`` overrides everything when given (used by callers that manage
    their own state tree, including the test suite). Otherwise resolve
    ``STATE_ROOT`` *at call time* so a monkeypatched STATE_ROOT is honored.

    The attribute is read through ``sys.modules`` rather than the bound
    ``agent_runner_paths`` reference so that a test (or any consumer) that
    reloads ``agent_runner.paths`` still resolves against the live module.
    Without this, ``goals`` could keep a stale module object whose
    STATE_ROOT differs from the one the conftest/tests patch, and the
    ledger would silently fall back to the operator's real
    ``~/.alfred/state``.
    """
    if root is not None:
        return Path(root)
    paths_mod = sys.modules.get("agent_runner.paths", agent_runner_paths)
    return Path(paths_mod.STATE_ROOT) / "goals"


def _goal_dir(goal_id: str, root: Path | None = None) -> Path:
    return goals_root(root) / validate_goal_id(goal_id)


def _goal_json_path(goal_id: str, root: Path | None = None) -> Path:
    return _goal_dir(goal_id, root) / "goal.json"


def _events_path(goal_id: str, root: Path | None = None) -> Path:
    return _goal_dir(goal_id, root) / "events.jsonl"


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """tmp + os.replace atomic write. Leaves no half-written file on crash."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        tmp.replace(path)
    finally:
        if tmp.exists():
            with contextlib.suppress(OSError):
                tmp.unlink()


# ---------------------------------------------------------------------------
# Goal entity.
# ---------------------------------------------------------------------------
@dataclass
class Goal:
    """An operator-owned durable goal.

    The fields map one-to-one onto ``goal.json``. Lists/dicts default to
    empty so a goal created from a bare outcome is still well-formed and
    round-trips cleanly through ``to_dict`` / ``from_dict``.
    """

    id: str
    outcome: str
    status: str = DRAFT
    verification: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    non_goals: list[str] = field(default_factory=list)
    iteration_policy: str = ""
    human_gates: list[str] = field(default_factory=list)
    blocked_condition: str = ""
    owner: str = ""
    repos: list[str] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Goal:
        """Build a Goal from a (possibly partial) dict.

        Unknown keys are dropped and missing keys fall back to dataclass
        defaults, so an older or hand-edited ``goal.json`` still loads.
        ``id`` and ``outcome`` remain required because without them the
        record is not a recoverable goal.
        """
        if not isinstance(data, dict):
            raise ValueError("goal.json must be a JSON object")
        known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        payload = {k: v for k, v in data.items() if k in known}

        gid = validate_goal_id(payload.get("id", ""))
        outcome = str(payload.get("outcome") or "").strip()
        if not outcome:
            raise ValueError("goal outcome must be non-empty")
        status = str(payload.get("status") or DRAFT).strip()
        if status not in STATUSES:
            raise ValueError(
                f"unknown status {status!r}; expected one of {', '.join(sorted(STATUSES))}"
            )

        payload["id"] = gid
        payload["outcome"] = outcome
        payload["status"] = status
        for name in (
            "verification",
            "constraints",
            "non_goals",
            "human_gates",
            "repos",
            "source_refs",
        ):
            payload[name] = _coerce_str_list(payload.get(name))
        for name in (
            "iteration_policy",
            "blocked_condition",
            "owner",
            "created_at",
            "updated_at",
        ):
            payload[name] = str(payload.get(name) or "")

        return cls(**payload)


def _coerce_str_list(value: Any) -> list[str]:
    """Normalize ``None`` / a bare string / an iterable into a list[str]."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Iterable):
        return [str(v) for v in value]
    return [str(value)]


# ---------------------------------------------------------------------------
# Event log (append-only JSONL, best-effort).
# ---------------------------------------------------------------------------
def add_event(
    goal_id: str,
    event: str,
    *,
    root: Path | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Append one record to the goal's ``events.jsonl``.

    Every record carries ``ts`` (UTC ISO), ``goal_id``, and ``event``.
    ``event`` is validated against ``EVENT_NAMES`` so the audit vocabulary
    stays closed. Writing is best-effort: an OSError prints to stderr and
    the record is still returned, mirroring ``EventLog.emit`` so a broken
    log never kills a goal mutation.
    """
    gid = validate_goal_id(goal_id)
    if event not in EVENT_NAMES:
        raise ValueError(
            f"unknown goal event {event!r}; expected one of {', '.join(sorted(EVENT_NAMES))}"
        )
    record: dict[str, Any] = {
        "ts": _now_iso(),
        "goal_id": gid,
        "event": event,
        **fields,
    }
    path = _events_path(gid, root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except OSError as e:
        print(f"[goals] event-log write failed for {gid}: {e}", file=sys.stderr)
    return record


def read_events(goal_id: str, *, root: Path | None = None) -> list[dict[str, Any]]:
    """Return every event record for a goal in append order.

    Missing log -> empty list. Unparseable lines are skipped rather than
    raising, so a single corrupt append never hides the rest of the trail.
    """
    path = _events_path(goal_id, root)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            continue
    return out


# ---------------------------------------------------------------------------
# CRUD + lifecycle.
# ---------------------------------------------------------------------------
def create(
    outcome: str,
    *,
    verification: Any = None,
    constraints: Any = None,
    non_goals: Any = None,
    iteration_policy: str = "",
    human_gates: Any = None,
    blocked_condition: str = "",
    owner: str = "",
    repos: Any = None,
    source_refs: Any = None,
    goal_id: str | None = None,
    root: Path | None = None,
) -> Goal:
    """Create a new durable goal in ``draft`` status.

    The id is deterministic from ``outcome`` (see ``make_goal_id``). When a
    goal with that id already exists, a numeric suffix (``-2``, ``-3``, ...)
    is appended so re-filing the same outcome never clobbers the original.
    Pass ``goal_id`` to force a specific id; that path raises ``GoalExists``
    on collision rather than disambiguating.

    Emits a ``created`` event. Returns the persisted ``Goal``.
    """
    outcome = outcome.strip()
    if not outcome:
        raise ValueError("create: outcome must be non-empty")

    if goal_id is not None:
        gid = validate_goal_id(goal_id)
        if _goal_json_path(gid, root).exists():
            raise GoalExists(f"goal {gid!r} already exists")
    else:
        base = make_goal_id(outcome)
        gid = base
        n = 2
        while _goal_json_path(gid, root).exists():
            gid = f"{base}-{n}"
            n += 1

    ts = _now_iso()
    goal = Goal(
        id=gid,
        outcome=outcome,
        status=DRAFT,
        verification=_coerce_str_list(verification),
        constraints=_coerce_str_list(constraints),
        non_goals=_coerce_str_list(non_goals),
        iteration_policy=iteration_policy,
        human_gates=_coerce_str_list(human_gates),
        blocked_condition=blocked_condition,
        owner=owner,
        repos=_coerce_str_list(repos),
        source_refs=_coerce_str_list(source_refs),
        created_at=ts,
        updated_at=ts,
    )
    _atomic_write_json(_goal_json_path(gid, root), goal.to_dict())
    add_event(gid, EVENT_CREATED, root=root, outcome=outcome, owner=owner)
    return goal


def get(goal_id: str, *, root: Path | None = None) -> Goal:
    """Load a goal by id. Raises ``GoalNotFound`` if absent or corrupt."""
    path = _goal_json_path(goal_id, root)
    if not path.exists():
        raise GoalNotFound(f"goal {goal_id!r} not found")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError, OSError) as e:
        raise GoalNotFound(f"goal {goal_id!r} is unreadable: {e}") from e
    try:
        return Goal.from_dict(data)
    except (TypeError, ValueError) as e:
        raise GoalNotFound(f"goal {goal_id!r} is invalid: {e}") from e


def exists(goal_id: str, *, root: Path | None = None) -> bool:
    """True if a ``goal.json`` exists for ``goal_id``."""
    return _goal_json_path(goal_id, root).exists()


def list_goals(*, status: str | None = None, root: Path | None = None) -> list[Goal]:
    """Return every goal, optionally filtered by ``status``.

    Sorted by ``created_at`` then ``id`` for a stable, human-scannable
    order. A directory without a readable ``goal.json`` is skipped rather
    than raising, so one corrupt goal never hides the rest of the ledger.
    """
    base = goals_root(root)
    if not base.exists():
        return []
    out: list[Goal] = []
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        gj = child / "goal.json"
        if not gj.exists():
            continue
        try:
            data = json.loads(gj.read_text(encoding="utf-8"))
            goal = Goal.from_dict(data)
        except (json.JSONDecodeError, ValueError, OSError, TypeError):
            continue
        if status is not None and goal.status != status:
            continue
        out.append(goal)
    out.sort(key=lambda g: (g.created_at, g.id))
    return out


def _save(goal: Goal, *, root: Path | None = None) -> Goal:
    goal.updated_at = _now_iso()
    _atomic_write_json(_goal_json_path(goal.id, root), goal.to_dict())
    return goal


def set_status(
    goal_id: str,
    new_status: str,
    *,
    root: Path | None = None,
    emit_event: bool = True,
    event_override: str | None = None,
    **event_fields: Any,
) -> Goal:
    """Move a goal to ``new_status`` through the validated state machine.

    Raises ``InvalidTransition`` when ``current -> new_status`` is not a
    documented move (a no-op same-status set is rejected too; use the
    convenience wrappers for idempotent intent). On success the goal is
    persisted with a fresh ``updated_at`` and, unless ``emit_event`` is
    False, a lifecycle event is appended. The default event is the one
    ``_STATUS_EVENT`` maps the destination status to; ``event_override``
    forces a specific event name (e.g. ``resumed`` when moving
    paused -> active, where the default ``approved`` would mislead the
    audit trail).
    """
    if new_status not in STATUSES:
        raise ValueError(
            f"unknown status {new_status!r}; expected one of {', '.join(sorted(STATUSES))}"
        )
    goal = get(goal_id, root=root)
    if not is_legal_transition(goal.status, new_status):
        legal = ", ".join(sorted(t.dst for t in legal_transitions(goal.status))) or "(none)"
        raise InvalidTransition(
            f"illegal transition {goal.status!r} -> {new_status!r} "
            f"for goal {goal_id!r}; legal next states: {legal}"
        )
    goal.status = new_status
    _save(goal, root=root)
    if emit_event:
        event = event_override or _STATUS_EVENT.get(new_status)
        if event is not None:
            add_event(goal_id, event, root=root, **event_fields)
    return goal


def approve(goal_id: str, *, root: Path | None = None, **fields: Any) -> Goal:
    """Move a draft to ``active`` (the ``approved`` lifecycle event)."""
    return set_status(goal_id, ACTIVE, root=root, **fields)


def start(goal_id: str, *, root: Path | None = None, **fields: Any) -> Goal:
    """Record that work has started on an active goal.

    Unlike approve/pause/etc this is not a status change (the goal is
    already ``active``); it only appends a ``started`` event so the audit
    trail captures the first firing without a redundant transition.
    """
    goal = get(goal_id, root=root)
    if goal.status != ACTIVE:
        raise InvalidTransition(
            f"cannot start goal {goal_id!r} in status {goal.status!r}; must be {ACTIVE!r}"
        )
    add_event(goal_id, EVENT_STARTED, root=root, **fields)
    return goal


def add_attempt(goal_id: str, *, root: Path | None = None, **fields: Any) -> Goal:
    """Append an ``attempted`` event (one engine firing against the goal).

    ``fields`` typically carry ``firing_id``, ``engine``, ``result``, and
    refs. The goal must not be terminal.
    """
    goal = get(goal_id, root=root)
    if goal.status in TERMINAL_STATUSES:
        raise InvalidTransition(
            f"cannot record an attempt on terminal goal {goal_id!r} (status {goal.status!r})"
        )
    add_event(goal_id, EVENT_ATTEMPTED, root=root, **fields)
    return goal


def add_evidence(goal_id: str, *, root: Path | None = None, **fields: Any) -> Goal:
    """Append an ``evidence_added`` event.

    ``fields`` describe the evidence (e.g. ``kind="tests"``,
    ``ref="https://..."``, ``summary="..."``). Recording evidence does not
    change status; the evaluator decides achieved/blocked separately. The
    goal must not be terminal.
    """
    goal = get(goal_id, root=root)
    if goal.status in TERMINAL_STATUSES:
        raise InvalidTransition(
            f"cannot add evidence to terminal goal {goal_id!r} (status {goal.status!r})"
        )
    add_event(goal_id, EVENT_EVIDENCE_ADDED, root=root, **fields)
    return goal


def clarify(goal_id: str, *, root: Path | None = None, **fields: Any) -> Goal:
    """Append a ``clarified`` event (operator answered a planner question).

    Pure audit; status is unchanged. Allowed in any non-terminal status so
    a draft can be refined before approval.
    """
    goal = get(goal_id, root=root)
    if goal.status in TERMINAL_STATUSES:
        raise InvalidTransition(
            f"cannot clarify terminal goal {goal_id!r} (status {goal.status!r})"
        )
    add_event(goal_id, EVENT_CLARIFIED, root=root, **fields)
    return goal


def pause(goal_id: str, *, root: Path | None = None, **fields: Any) -> Goal:
    """Pause an active goal. No-op (returns the goal) if already paused."""
    goal = get(goal_id, root=root)
    if goal.status == PAUSED:
        return goal
    return set_status(goal_id, PAUSED, root=root, **fields)


def resume(goal_id: str, *, root: Path | None = None, **fields: Any) -> Goal:
    """Resume a paused or blocked goal back to active. No-op if already active.

    Emits ``resumed`` (not the default ``approved`` that ``active`` maps
    to) so the audit trail distinguishes a resume from the initial
    approval. A draft cannot be resumed; it must be explicitly approved.
    """
    goal = get(goal_id, root=root)
    if goal.status == ACTIVE:
        return goal
    if goal.status not in {PAUSED, BLOCKED}:
        hint = "use approve for a draft goal" if goal.status == DRAFT else "resume is unavailable"
        raise InvalidTransition(f"cannot resume goal {goal_id!r} in status {goal.status!r}; {hint}")
    return set_status(goal_id, ACTIVE, root=root, event_override=EVENT_RESUMED, **fields)


def block(goal_id: str, *, root: Path | None = None, **fields: Any) -> Goal:
    """Mark an active goal blocked. No-op if already blocked."""
    goal = get(goal_id, root=root)
    if goal.status == BLOCKED:
        return goal
    return set_status(goal_id, BLOCKED, root=root, **fields)


def achieve(goal_id: str, *, root: Path | None = None, **fields: Any) -> Goal:
    """Mark an active goal achieved (terminal)."""
    return set_status(goal_id, ACHIEVED, root=root, **fields)


def clear(goal_id: str, *, root: Path | None = None, **fields: Any) -> Goal:
    """Clear (abandon) a goal. Terminal.

    Legal from draft/active/blocked/paused. No-op if already cleared.
    """
    goal = get(goal_id, root=root)
    if goal.status == CLEARED:
        return goal
    return set_status(goal_id, CLEARED, root=root, **fields)


__all__ = [
    "ACHIEVED",
    "ACTIVE",
    "BLOCKED",
    "CLEARED",
    # statuses
    "DRAFT",
    "EVENT_ACHIEVED",
    "EVENT_APPROVED",
    "EVENT_ATTEMPTED",
    "EVENT_BLOCKED",
    "EVENT_CLARIFIED",
    "EVENT_CLEARED",
    # events
    "EVENT_CREATED",
    "EVENT_EVIDENCE_ADDED",
    "EVENT_NAMES",
    "EVENT_PAUSED",
    "EVENT_RESUMED",
    "EVENT_STARTED",
    "PAUSED",
    "STATUSES",
    "TERMINAL_STATUSES",
    # entity
    "Goal",
    # errors
    "GoalError",
    "GoalExists",
    "GoalNotFound",
    "InvalidTransition",
    # state machine
    "Transition",
    "achieve",
    "add_attempt",
    "add_event",
    "add_evidence",
    "all_transitions",
    "approve",
    "block",
    "clarify",
    "clear",
    # crud + lifecycle
    "create",
    "exists",
    "get",
    "goals_root",
    "is_legal_transition",
    "legal_transitions",
    "list_goals",
    "make_goal_id",
    "pause",
    "read_events",
    "resume",
    "set_status",
    # id + paths
    "slugify",
    "start",
    "validate_goal_id",
]
