"""Bounded auto-recovery for a firing's push / CI / merge-gate step.

Borrowed from orca's push-failure AI recovery, implemented natively. When an
agent firing's push, pre-push (lint/format/compile/test), or merge-gate step
fails, the runner classifies the failure and, when it is a recoverable class,
spawns ONE bounded recovery engine turn in the firing worktree that fixes the
cause and re-pushes, BEFORE falling back to the existing preserve/HOLD path.

The split of responsibility is deliberate:

* Classification (:func:`classify_failure`) is a pure function over the failure
  text (captured stderr / log excerpt). It has one job: decide which of the
  recoverable classes a failure is, or that it is one of the never-recover
  classes (approval-gate denial, scrub-check rejection, auth error) that must
  fall straight to HOLD.
* Dispatch (:func:`run_recovery`) owns the attempt cap, the never-recover
  guard, and the distinct telemetry markers. The engine turn itself is injected
  as ``attempt_fn`` so the whole control flow is unit-testable without running a
  real engine or touching a worktree.

Config: ``ALFRED_RECOVERY_MAX_ATTEMPTS`` (int, default 1; 0 disables). It is
declared once in :mod:`alfred_config` and clamped to a small ceiling here so a
plist typo cannot spawn an unbounded recovery loop.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from .config import env_int

# Recovery attempts are intentionally capped low: recovery is a last resort
# before HOLD, not a retry engine. The env var floor is 0 (disabled) and the
# ceiling keeps a misconfigured plist from spawning a long chain of paid turns.
_MAX_ATTEMPTS_DEFAULT = 1
_MAX_ATTEMPTS_CEILING = 3


class RecoveryCategory(StrEnum):
    """What kind of push/CI/merge-gate failure this is, and thus whether a
    bounded recovery turn may attempt it.

    Recoverable classes (a recovery turn is worth one bounded attempt):

    * ``LINT_FORMAT_HOOK``: a pre-commit / pre-push lint or format hook rejected
      the change (ruff, eslint, prettier, black, gofmt, a husky hook). A turn
      can run the formatter / fix the lint and re-push.
    * ``NON_FAST_FORWARD``: the remote moved, so the push was rejected as
      non-fast-forward, or a rebase/merge hit a conflict. A turn can rebase onto
      the updated base, resolve the conflict, and re-push.
    * ``FAILING_CI``: a CI check (compile / type-check / test) failed. A turn can
      reproduce it locally, fix it, and re-push.
    * ``TRANSIENT_NETWORK``: a transport-layer blip (timeout, connection reset,
      5xx from the remote). A turn can simply re-push.

    Never-recover classes (fall straight to HOLD; a turn cannot or must not fix
    these):

    * ``APPROVAL_GATE``: the merge gate withheld the merge for a human decision
      (not approved, unresolved review threads, changes requested). Recovering
      would bypass a person; forbidden.
    * ``SCRUB_CHECK``: the public/private scrub gate rejected the change (a home
      path, an internal name, a secret leaked in). A turn must not paper over a
      boundary violation; a human decides.
    * ``AUTH``: bad or missing credentials (401 / 403, permission denied, auth
      failed). A turn cannot mint credentials; HOLD and surface honestly.

    * ``UNKNOWN``: unclassified. Treated as never-recover so an unrecognised
      failure fails closed to HOLD rather than burning a turn on a guess.
    """

    LINT_FORMAT_HOOK = "lint_format_hook"
    NON_FAST_FORWARD = "non_fast_forward"
    FAILING_CI = "failing_ci"
    TRANSIENT_NETWORK = "transient_network"
    APPROVAL_GATE = "approval_gate"
    SCRUB_CHECK = "scrub_check"
    AUTH = "auth"
    UNKNOWN = "unknown"


# The classes a bounded recovery turn is allowed to attempt. Everything else
# falls straight to HOLD.
RECOVERABLE: frozenset[RecoveryCategory] = frozenset(
    {
        RecoveryCategory.LINT_FORMAT_HOOK,
        RecoveryCategory.NON_FAST_FORWARD,
        RecoveryCategory.FAILING_CI,
        RecoveryCategory.TRANSIENT_NETWORK,
    }
)


# Distinct telemetry markers. Emitted through the injected ``on_event`` so proof
# / telemetry can count self-healed runs (RECOVERY_SUCCEEDED) against attempts
# (RECOVERY_ATTEMPTED) and never-recover / disabled skips (RECOVERY_SKIPPED).
EVENT_ATTEMPTED = "recovery_attempted"
EVENT_SUCCEEDED = "recovery_succeeded"
EVENT_EXHAUSTED = "recovery_exhausted"
EVENT_SKIPPED = "recovery_skipped"


# Marker tables. Order of the checks in ``classify_failure`` encodes precedence:
# the never-recover classes are tested FIRST so an auth or scrub-check failure
# that also happens to mention "push" or "hook" is never mistaken for a
# recoverable one. Each tuple is lower-cased substrings; matching is
# case-insensitive.
_AUTH_MARKERS: tuple[str, ...] = (
    "authentication failed",
    "auth failed",
    "bad credentials",
    "invalid credentials",
    "could not read username",
    "could not read password",
    "permission denied",
    "403 forbidden",
    "http 403",
    "401 unauthorized",
    "http 401",
    "remote: invalid username or password",
    "support for password authentication was removed",
    "fatal: authentication",
)

_SCRUB_MARKERS: tuple[str, ...] = (
    "scrub-check",
    "scrub_check",
    "scrub check",
    "bin/scrub-check",
    "secret detected",
    "secret found",
    "leaked secret",
    "private-to-public",
    "private->public",
    "home-directory path",
    "home directory path",
    "banned token",
)

_APPROVAL_MARKERS: tuple[str, ...] = (
    "changes requested",
    "review required",
    "not yet approved",
    "unresolved review thread",
    "unresolved review threads",
    "reviewdecision",
    "approval required",
    "requires approval",
    "needs:human",
    "awaiting approval",
    "not approved",
)

# Transient transport markers are checked before the git-rejection markers so a
# push that failed on a network blip is not misread as a non-fast-forward.
_TRANSIENT_MARKERS: tuple[str, ...] = (
    "connection reset",
    "connection refused",
    "connection timed out",
    "connection aborted",
    "could not resolve host",
    "operation timed out",
    "timed out",
    "temporary failure",
    "temporarily unavailable",
    "service unavailable",
    "gateway timeout",
    "bad gateway",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
    "the remote end hung up unexpectedly",
    "rpc failed",
    "early eof",
    "ssl_error",
    "tls connection",
    "network is unreachable",
)

_NON_FAST_FORWARD_MARKERS: tuple[str, ...] = (
    "non-fast-forward",
    "failed to push some refs",
    "tip of your current branch is behind",
    "fetch first",
    "updates were rejected",
    "! [rejected]",
    "merge conflict",
    "conflict (content)",
    "automatic merge failed",
    "needs merge",
    "you have divergent branches",
    "cannot fast-forward",
)

_LINT_FORMAT_MARKERS: tuple[str, ...] = (
    "pre-commit hook",
    "pre-push hook",
    "hook declined",
    "hook failed",
    "husky",
    "lint-staged",
    "eslint",
    "prettier",
    "ruff",
    "ruff format",
    "black would reformat",
    "would reformat",
    "gofmt",
    "rustfmt",
    "clippy",
    "flake8",
    "lint error",
    "lint failed",
    "linting failed",
    "formatting check failed",
    "reformatted",
)

_FAILING_CI_MARKERS: tuple[str, ...] = (
    "test failed",
    "tests failed",
    "failing test",
    "failing check",
    "check run failed",
    "pytest",
    "jest",
    "vitest",
    "type error",
    "type-check failed",
    "typecheck failed",
    "mypy",
    "tsc",
    "compilation failed",
    "compile error",
    "build failed",
    "assertionerror",
    "exit code 1",
    "exited with 1",
    "pre-push command failed",
)


def _matches(haystack: str, markers: tuple[str, ...]) -> bool:
    return any(marker in haystack for marker in markers)


def classify_failure(text: str | None) -> RecoveryCategory:
    """Classify a push / CI / merge-gate failure from its captured text.

    Precedence is deliberate and never-recover-first: auth, scrub-check, and
    approval-gate failures are matched before any recoverable class so a
    failure that mentions both (e.g. an auth error printed alongside a push
    rejection) fails closed to HOLD. Among the recoverable classes, a transient
    transport blip is matched before a non-fast-forward rejection, and a
    non-fast-forward before lint/format and CI, because a network failure and a
    stale-base rejection both surface generic "failed to push" text.

    An empty or unrecognised failure returns :attr:`RecoveryCategory.UNKNOWN`,
    which is not recoverable.
    """
    haystack = (text or "").lower()
    if not haystack.strip():
        return RecoveryCategory.UNKNOWN

    # Never-recover classes first (fail closed).
    if _matches(haystack, _AUTH_MARKERS):
        return RecoveryCategory.AUTH
    if _matches(haystack, _SCRUB_MARKERS):
        return RecoveryCategory.SCRUB_CHECK
    if _matches(haystack, _APPROVAL_MARKERS):
        return RecoveryCategory.APPROVAL_GATE

    # Recoverable classes, most specific transport/vcs signal first.
    if _matches(haystack, _TRANSIENT_MARKERS):
        return RecoveryCategory.TRANSIENT_NETWORK
    if _matches(haystack, _NON_FAST_FORWARD_MARKERS):
        return RecoveryCategory.NON_FAST_FORWARD
    if _matches(haystack, _LINT_FORMAT_MARKERS):
        return RecoveryCategory.LINT_FORMAT_HOOK
    if _matches(haystack, _FAILING_CI_MARKERS):
        return RecoveryCategory.FAILING_CI

    return RecoveryCategory.UNKNOWN


def is_recoverable(category: RecoveryCategory) -> bool:
    """True when a bounded recovery turn may attempt this failure class."""
    return category in RECOVERABLE


def recovery_max_attempts(environ: Mapping[str, str] | None = None) -> int:
    """Resolve ``ALFRED_RECOVERY_MAX_ATTEMPTS`` clamped to ``[0, ceiling]``.

    Default is 1. A value of 0 disables recovery entirely. Non-integer or
    out-of-range values clamp rather than raise so a plist typo degrades to a
    safe bound instead of crashing the firing.

    ``environ`` is accepted for tests; when ``None`` the process environment is
    read (via :func:`agent_runner.config.env_int`).
    """
    if environ is not None:
        raw = str(environ.get("ALFRED_RECOVERY_MAX_ATTEMPTS", "")).strip()
        if not raw:
            return _MAX_ATTEMPTS_DEFAULT
        try:
            value = int(raw)
        except ValueError:
            return _MAX_ATTEMPTS_DEFAULT
        return max(0, min(_MAX_ATTEMPTS_CEILING, value))
    return env_int(
        "ALFRED_RECOVERY_MAX_ATTEMPTS",
        _MAX_ATTEMPTS_DEFAULT,
        minimum=0,
        maximum=_MAX_ATTEMPTS_CEILING,
    )


def recovery_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """True when recovery is armed (``ALFRED_RECOVERY_MAX_ATTEMPTS`` > 0)."""
    return recovery_max_attempts(environ) > 0


@dataclass(frozen=True)
class RecoveryOutcome:
    """Result of a recovery dispatch.

    * ``recovered``: a recovery turn fixed the failure and the re-push succeeded.
    * ``category``: the classified failure class (always set).
    * ``attempts_made``: how many bounded engine turns actually ran (0 when the
      class was never-recover or recovery was disabled).
    * ``reason``: a short, human-readable why (for the skip/exhausted paths).
    """

    recovered: bool
    category: RecoveryCategory
    attempts_made: int
    reason: str


# One recovery attempt: given the attempt number (1-based) and the classified
# category, run the bounded engine turn plus the re-push and return whether the
# push now succeeds. Injected so the dispatch loop is testable without an engine.
AttemptFn = Callable[[int, RecoveryCategory], bool]
EventFn = Callable[..., None]


def run_recovery(
    failure_text: str | None,
    *,
    attempt_fn: AttemptFn,
    on_event: EventFn | None = None,
    environ: Mapping[str, str] | None = None,
) -> RecoveryOutcome:
    """Classify ``failure_text`` and dispatch bounded recovery attempts.

    Behaviour:

    1. Classify the failure. If it is not a recoverable class (auth,
       scrub-check, approval-gate, or unknown), emit ``recovery_skipped`` and
       return without running any turn.
    2. Read the attempt cap. When it is 0 (disabled), emit ``recovery_skipped``
       and return without running any turn.
    3. Otherwise call ``attempt_fn`` up to the cap. Emit ``recovery_attempted``
       before each turn and stop at the first success (``recovery_succeeded``).
       If every attempt fails, emit ``recovery_exhausted``.

    ``attempt_fn`` performs the actual engine turn and re-push and returns
    ``True`` only when the push now succeeds. ``on_event(event_type, **payload)``
    receives the distinct telemetry markers; it is optional.
    """

    def _emit(event_type: str, **payload: object) -> None:
        if on_event is not None:
            on_event(event_type, **payload)

    category = classify_failure(failure_text)

    if not is_recoverable(category):
        reason = "non-recoverable failure class"
        _emit(EVENT_SKIPPED, category=str(category), reason=reason)
        return RecoveryOutcome(False, category, 0, reason)

    max_attempts = recovery_max_attempts(environ)
    if max_attempts <= 0:
        reason = "recovery disabled (ALFRED_RECOVERY_MAX_ATTEMPTS=0)"
        _emit(EVENT_SKIPPED, category=str(category), reason=reason)
        return RecoveryOutcome(False, category, 0, reason)

    for attempt in range(1, max_attempts + 1):
        _emit(
            EVENT_ATTEMPTED,
            category=str(category),
            attempt=attempt,
            max_attempts=max_attempts,
        )
        if attempt_fn(attempt, category):
            _emit(EVENT_SUCCEEDED, category=str(category), attempt=attempt)
            return RecoveryOutcome(True, category, attempt, "recovered")

    reason = f"recovery exhausted after {max_attempts} attempt(s)"
    _emit(EVENT_EXHAUSTED, category=str(category), attempts=max_attempts)
    return RecoveryOutcome(False, category, max_attempts, reason)


def build_recovery_prompt(
    category: RecoveryCategory,
    failure_text: str | None,
    *,
    branch: str,
    base_ref: str = "origin/main",
    log_excerpt_chars: int = 2000,
) -> str:
    """Compose the bounded recovery-turn prompt.

    The prompt states the classified failure, includes the captured stderr / log
    excerpt (trimmed to ``log_excerpt_chars``), and gives a class-specific,
    minimal instruction to fix the cause and re-push the SAME branch. It is
    deliberately terse: the turn is a targeted repair, not a fresh
    implementation.
    """
    excerpt = (failure_text or "").strip()
    if len(excerpt) > log_excerpt_chars:
        excerpt = excerpt[:log_excerpt_chars] + "\n...[truncated]"

    guidance = _CATEGORY_GUIDANCE.get(
        category,
        "Diagnose the failure from the log, fix the root cause, then re-push.",
    )

    return (
        f"A code change on branch `{branch}` failed its push / CI / merge-gate "
        f"step. Classified failure: {category}.\n\n"
        f"Captured failure output:\n```\n{excerpt or '(no output captured)'}\n```\n\n"
        f"Your task: {guidance}\n\n"
        "Rules:\n"
        f"- Work only on the current branch `{branch}`; do not open a new branch.\n"
        "- Make the smallest change that fixes the failure. Do not refactor "
        "unrelated code.\n"
        "- Commit your fix with a clear imperative message and push it to the "
        f"same branch (`git push origin HEAD:{branch}`).\n"
        f"- If the remote moved, rebase onto `{base_ref}` before pushing.\n"
        "- If you cannot fix the failure, stop and leave the tree unpushed "
        "rather than forcing a bad change."
    )


_CATEGORY_GUIDANCE: dict[RecoveryCategory, str] = {
    RecoveryCategory.LINT_FORMAT_HOOK: (
        "A lint or format hook rejected the change. Run the repo's formatter / "
        "linter, fix the reported issues, then commit and re-push."
    ),
    RecoveryCategory.NON_FAST_FORWARD: (
        "The push was rejected because the remote branch moved. Rebase your "
        "commits onto the updated base, resolve any conflicts, then re-push."
    ),
    RecoveryCategory.FAILING_CI: (
        "A CI check (compile / type-check / test) failed. Reproduce it locally, "
        "fix the root cause, confirm it passes, then commit and re-push."
    ),
    RecoveryCategory.TRANSIENT_NETWORK: (
        "The push failed on a transient network error. Confirm the working tree "
        "is intact and re-push the same commits."
    ),
}
