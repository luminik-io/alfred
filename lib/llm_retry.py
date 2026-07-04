"""A reusable retry policy and provider-exception classifier for Alfred's
DIRECT LLM/HTTP calls.

Alfred's autonomous fleet shells out to the Claude Code / Codex CLIs, which
own their own retry, and the Redis agent-memory layer already has a hardened
retry-plus-circuit-breaker at its ``_request`` choke point. Neither of those
paths is touched here. This module is the shared, dependency-light helper for
the OTHER kind of failure: Alfred's own direct outbound API/HTTP calls (today,
the connector HTTP client; tomorrow, any direct provider SDK call), which need
to survive transient rate limits, 5xx responses, and timeouts with bounded
exponential backoff.

Three cooperating pieces, all pure and side-effect-free except for the
injected ``sleep``:

* :class:`RetryPolicy`: a frozen, env-overridable dataclass of tunables plus
  :meth:`RetryPolicy.compute_backoff_delay`, which produces a jittered
  exponential delay capped at ``backoff_max_s`` and HONORS a server
  ``Retry-After`` hint as a floor.

* :func:`classify_exception`: maps a provider SDK / HTTP exception to one of a
  stable set of semantic string codes (``rate_limit``, ``timeout``,
  ``connection_error``, ``server_error``, ``client_error``, ``unknown``) so a
  caller can retry only the retryable ones. Optional SDK types (``httpx``,
  ``anthropic``, ``openai``) are imported defensively: a missing SDK never
  breaks the classifier, it just falls back to duck-typed ``.status_code`` /
  ``.response.status_code`` inspection.

* :func:`retry_call`: a small synchronous loop that runs a callable, classifies
  any exception, retries the retryable ones with the policy's backoff (honoring
  a per-exception ``Retry-After``), and re-raises after ``max_retries``.

Design borrowed clean-room from omnigent-ai/omnigent's ``RetryPolicy`` (frozen
dataclass, ``compute_backoff_delay`` that honors ``Retry-After``, companion
classifier). No code was copied.

All tunables are read at construction time from the environment so a launchd
plist or deployment config can retune behaviour without a redeploy, per the
config-driven-tunables rule.
"""

from __future__ import annotations

import os
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

__all__ = [
    "CLIENT_ERROR",
    "CONNECTION_ERROR",
    "RATE_LIMIT",
    "RETRYABLE_CODES",
    "SERVER_ERROR",
    "TIMEOUT",
    "UNKNOWN",
    "RetryPolicy",
    "classify_exception",
    "is_retryable_code",
    "retry_after_from_exception",
    "retry_call",
]

# --------------------------------------------------------------------------
# Stable semantic classification codes.
# --------------------------------------------------------------------------

RATE_LIMIT = "rate_limit"
TIMEOUT = "timeout"
CONNECTION_ERROR = "connection_error"
SERVER_ERROR = "server_error"
CLIENT_ERROR = "client_error"
UNKNOWN = "unknown"

#: Codes worth retrying: transient provider or network failures. A 4xx other
#: than 429 (``client_error``) is the caller's fault and never retried.
RETRYABLE_CODES: frozenset[str] = frozenset({RATE_LIMIT, TIMEOUT, CONNECTION_ERROR, SERVER_ERROR})

# --------------------------------------------------------------------------
# Defaults (omnigent-spec) + env knob names.
# --------------------------------------------------------------------------

_DEFAULT_MAX_RETRIES = 6
_MAX_RETRIES_CEILING = 20
_DEFAULT_BACKOFF_BASE_S = 2.0
_DEFAULT_BACKOFF_MAX_S = 60.0
_DEFAULT_JITTER_LOW = 0.5
_DEFAULT_JITTER_HIGH = 1.5
_DEFAULT_TIMEOUT_PER_REQUEST_S = 120.0
_DEFAULT_RETRYABLE_STATUS_CODES: tuple[int, ...] = (429, 500, 502, 503, 504)

_ENV_MAX_RETRIES = "ALFRED_LLM_MAX_RETRIES"
_ENV_BACKOFF_BASE_S = "ALFRED_LLM_BACKOFF_BASE_S"
_ENV_BACKOFF_MAX_S = "ALFRED_LLM_BACKOFF_MAX_S"
_ENV_TIMEOUT_PER_REQUEST_S = "ALFRED_LLM_TIMEOUT_PER_REQUEST_S"


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    """Read a clamped integer knob from ``os.environ``; fall back on any error."""
    raw = os.environ.get(name, "").strip()
    value = default
    if raw:
        try:
            value = int(raw)
        except ValueError:
            value = default
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, *, minimum: float) -> float:
    """Read a floored float knob from ``os.environ``; fall back on any error."""
    raw = os.environ.get(name, "").strip()
    value = default
    if raw:
        try:
            value = float(raw)
        except ValueError:
            value = default
    return max(minimum, value)


# --------------------------------------------------------------------------
# RetryPolicy.
# --------------------------------------------------------------------------

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    """Immutable tunables for retrying a direct API/HTTP call.

    The defaults mirror omnigent-ai/omnigent's ``RetryPolicy`` (clean-room):
    six retries with a 2s exponential base capped at 60s, full jitter in
    ``[0.5x, 1.5x]``, a 120s per-request timeout budget, and the canonical
    retryable HTTP status set ``(429, 500, 502, 503, 504)``.

    Being frozen, a policy is safe to share across threads and to treat as a
    value. Use :meth:`from_env` to build one from ``ALFRED_LLM_*`` env knobs.
    """

    max_retries: int = _DEFAULT_MAX_RETRIES
    backoff_base_s: float = _DEFAULT_BACKOFF_BASE_S
    backoff_max_s: float = _DEFAULT_BACKOFF_MAX_S
    jitter: tuple[float, float] = (_DEFAULT_JITTER_LOW, _DEFAULT_JITTER_HIGH)
    timeout_per_request_s: float = _DEFAULT_TIMEOUT_PER_REQUEST_S
    retryable_status_codes: tuple[int, ...] = field(default=_DEFAULT_RETRYABLE_STATUS_CODES)

    def __post_init__(self) -> None:
        # Clamp into sane ranges without mutating a frozen instance in place:
        # object.__setattr__ is the sanctioned way for a frozen dataclass.
        clamped_retries = max(0, min(_MAX_RETRIES_CEILING, int(self.max_retries)))
        object.__setattr__(self, "max_retries", clamped_retries)
        object.__setattr__(self, "backoff_base_s", max(0.0, float(self.backoff_base_s)))
        object.__setattr__(self, "backoff_max_s", max(0.0, float(self.backoff_max_s)))
        object.__setattr__(
            self, "timeout_per_request_s", max(0.0, float(self.timeout_per_request_s))
        )
        low, high = self.jitter
        low_f, high_f = float(low), float(high)
        if low_f > high_f:
            low_f, high_f = high_f, low_f
        object.__setattr__(self, "jitter", (max(0.0, low_f), max(0.0, high_f)))

    @classmethod
    def from_env(cls) -> RetryPolicy:
        """Build a policy from ``ALFRED_LLM_*`` env knobs, else the defaults."""
        return cls(
            max_retries=_env_int(
                _ENV_MAX_RETRIES,
                _DEFAULT_MAX_RETRIES,
                minimum=0,
                maximum=_MAX_RETRIES_CEILING,
            ),
            backoff_base_s=_env_float(_ENV_BACKOFF_BASE_S, _DEFAULT_BACKOFF_BASE_S, minimum=0.0),
            backoff_max_s=_env_float(_ENV_BACKOFF_MAX_S, _DEFAULT_BACKOFF_MAX_S, minimum=0.0),
            timeout_per_request_s=_env_float(
                _ENV_TIMEOUT_PER_REQUEST_S,
                _DEFAULT_TIMEOUT_PER_REQUEST_S,
                minimum=0.0,
            ),
        )

    def compute_backoff_delay(
        self,
        attempt: int,
        retry_after_s: float | None = None,
        *,
        rand: Callable[[float, float], float] = random.uniform,
    ) -> float:
        """Delay in seconds to wait before retry ``attempt`` (0-indexed).

        The base curve is ``backoff_base_s * 2 ** attempt`` capped at
        ``backoff_max_s``, then multiplied by a jitter factor drawn uniformly
        from ``jitter`` (default full jitter ``[0.5, 1.5]``). Jitter is applied
        BEFORE the cap so the returned delay never exceeds ``backoff_max_s``.

        When ``retry_after_s`` is a positive server hint (e.g. from a
        ``Retry-After`` header) it is honored as a FLOOR: the result is
        ``max(retry_after_s, jittered_backoff)``, so we never hammer a server
        sooner than it asked. A ``Retry-After`` larger than ``backoff_max_s`` is
        respected in full, because the server's explicit instruction outranks
        our local cap.

        ``rand`` is injectable so tests are deterministic.

        Args:
            attempt: zero-based retry index (0 is the first retry).
            retry_after_s: optional server hint honored as a floor.
            rand: uniform draw ``(low, high) -> float``; injected in tests.

        Returns:
            A non-negative delay in seconds.
        """
        exp = self.backoff_base_s * (2.0 ** max(0, attempt))
        low, high = self.jitter
        jittered = exp * rand(low, high)
        delay = min(jittered, self.backoff_max_s)
        if retry_after_s is not None and retry_after_s > 0:
            return max(float(retry_after_s), delay)
        return max(0.0, delay)


# --------------------------------------------------------------------------
# Defensive optional-SDK imports for the classifier.
# --------------------------------------------------------------------------


def _optional_type(module_name: str, attr: str) -> type | None:
    """Return ``module.attr`` if importable, else ``None`` (never raises)."""
    try:
        module = __import__(module_name, fromlist=[attr])
    except Exception:
        return None
    candidate = getattr(module, attr, None)
    return candidate if isinstance(candidate, type) else None


# Resolve once at import time. Each is ``None`` when the SDK is absent, which is
# fine: the duck-typed status-code path below still classifies those exceptions.
_HTTPX_TIMEOUT = _optional_type("httpx", "TimeoutException")
_HTTPX_CONNECT_ERROR = _optional_type("httpx", "ConnectError")
_HTTPX_TRANSPORT_ERROR = _optional_type("httpx", "TransportError")


def _status_of(exc: BaseException) -> int | None:
    """Extract an HTTP status code from common exception shapes, or ``None``.

    Handles ``exc.status_code`` (httpx.HTTPStatusError, urllib-style wrappers,
    Alfred's connector ``HttpError``), ``exc.status`` (urllib ``HTTPError``),
    and the nested ``exc.response.status_code`` used by requests / httpx /
    provider SDKs. Any attribute access that misbehaves is swallowed.
    """
    for attr in ("status_code", "status", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and 100 <= value < 600:
            return value
    response = getattr(exc, "response", None)
    if response is not None:
        for attr in ("status_code", "status"):
            value = getattr(response, attr, None)
            if isinstance(value, int) and 100 <= value < 600:
                return value
    return None


def _code_for_status(status: int) -> str:
    """Map an HTTP status code to a semantic classification code."""
    if status == 429:
        return RATE_LIMIT
    if 500 <= status < 600:
        return SERVER_ERROR
    if 400 <= status < 500:
        return CLIENT_ERROR
    return UNKNOWN


def classify_exception(exc: BaseException) -> str:
    """Map a provider SDK / HTTP exception to a stable semantic code.

    Returns one of :data:`RATE_LIMIT`, :data:`TIMEOUT`,
    :data:`CONNECTION_ERROR`, :data:`SERVER_ERROR`, :data:`CLIENT_ERROR`, or
    :data:`UNKNOWN`. Callers pair this with :data:`RETRYABLE_CODES` (or
    :func:`is_retryable_code`) to decide whether to retry.

    Classification order:

    1. An HTTP status code (from ``status_code`` / ``status`` / nested
       ``response``) wins when present, since it is the most authoritative
       signal: 429 -> rate_limit, 5xx -> server_error, other 4xx ->
       client_error.
    2. Otherwise, timeout / connection shapes are recognised by ``httpx`` types
       (when installed) and by the stdlib ``TimeoutError`` /
       ``ConnectionError`` / ``socket.timeout`` hierarchy, plus a class-name
       fallback so an unimported SDK's ``*Timeout`` / ``*Connect*`` exception is
       still caught.
    3. Anything else is ``unknown`` (not retried by default).
    """
    status = _status_of(exc)
    if status is not None:
        return _code_for_status(status)

    # Timeout shapes.
    if _HTTPX_TIMEOUT is not None and isinstance(exc, _HTTPX_TIMEOUT):
        return TIMEOUT
    if isinstance(exc, TimeoutError):
        return TIMEOUT

    # Connection shapes.
    if _HTTPX_CONNECT_ERROR is not None and isinstance(exc, _HTTPX_CONNECT_ERROR):
        return CONNECTION_ERROR
    if _HTTPX_TRANSPORT_ERROR is not None and isinstance(exc, _HTTPX_TRANSPORT_ERROR):
        return CONNECTION_ERROR
    if isinstance(exc, ConnectionError):
        return CONNECTION_ERROR

    # Class-name fallback for SDK exceptions we could not import.
    name = type(exc).__name__.lower()
    if "timeout" in name or "timedout" in name:
        return TIMEOUT
    if "connect" in name:
        return CONNECTION_ERROR

    return UNKNOWN


def is_retryable_code(code: str) -> bool:
    """True when ``code`` is one of the transient, retryable classifications."""
    return code in RETRYABLE_CODES


def retry_after_from_exception(exc: BaseException) -> float | None:
    """Extract a ``Retry-After`` hint (seconds) from an exception, or ``None``.

    Looks for a ``retry_after`` attribute (provider SDKs sometimes surface one)
    and for a ``Retry-After`` header on a nested ``response.headers`` mapping.
    A header value may be an integer number of seconds; HTTP-date form is not
    parsed here (callers rarely need it and the local backoff still applies).
    """
    direct = getattr(exc, "retry_after", None)
    if isinstance(direct, (int, float)) and direct > 0:
        return float(direct)
    if isinstance(direct, str):
        try:
            parsed = float(direct.strip())
        except ValueError:
            parsed = -1.0
        if parsed > 0:
            return parsed
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        getter = getattr(headers, "get", None)
        if callable(getter):
            raw = getter("Retry-After") or getter("retry-after")
            if raw is not None:
                try:
                    seconds = float(str(raw).strip())
                except ValueError:
                    seconds = -1.0
                if seconds > 0:
                    return seconds
    return None


# --------------------------------------------------------------------------
# retry_call.
# --------------------------------------------------------------------------


def retry_call(
    fn: Callable[[], T],
    policy: RetryPolicy | None = None,
    *,
    is_retryable: Callable[[BaseException], bool] | None = None,
    retry_after_of: Callable[[BaseException], float | None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    rand: Callable[[float, float], float] = random.uniform,
) -> T:
    """Call ``fn`` with bounded retry on transient failures.

    ``fn`` is a zero-argument callable (wrap arguments in a lambda / partial).
    On success its result is returned. On exception, ``is_retryable`` decides
    whether to retry; when it returns ``True`` and retries remain, the loop
    sleeps for :meth:`RetryPolicy.compute_backoff_delay` (honoring any
    ``Retry-After`` hint from ``retry_after_of``) and calls ``fn`` again.
    A non-retryable exception, or exhausting ``policy.max_retries``, re-raises
    the LAST exception unchanged so the caller sees the real error.

    All non-determinism is injectable (``sleep``, ``rand``) so tests run
    instantly and deterministically, mirroring the redis-memory resilience
    tests.

    Args:
        fn: the zero-arg operation to attempt.
        policy: retry tunables; defaults to :meth:`RetryPolicy.from_env`.
        is_retryable: predicate on the raised exception; defaults to
            ``classify_exception(exc)`` in :data:`RETRYABLE_CODES`.
        retry_after_of: extracts a server ``Retry-After`` hint from the
            exception; defaults to :func:`retry_after_from_exception`.
        sleep: sleep function; injected in tests to record delays.
        rand: uniform draw for jitter; injected in tests.

    Returns:
        Whatever ``fn`` returns on its first successful call.

    Raises:
        BaseException: the last exception raised by ``fn`` once retries are
            exhausted or a non-retryable error occurs.
    """
    effective = policy or RetryPolicy.from_env()
    retryable = is_retryable or (lambda exc: is_retryable_code(classify_exception(exc)))
    hint_of = retry_after_of or retry_after_from_exception

    attempt = 0
    while True:
        try:
            return fn()
        except BaseException as exc:
            if attempt >= effective.max_retries or not retryable(exc):
                raise
            delay = effective.compute_backoff_delay(attempt, hint_of(exc), rand=rand)
            sleep(delay)
            attempt += 1
