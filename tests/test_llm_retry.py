"""Unit tests for :mod:`lib.llm_retry`.

Covers the three pieces of the direct-call retry utility:

* :meth:`RetryPolicy.compute_backoff_delay` - monotonic-ish growth, capped at
  ``backoff_max_s``, and honoring a server ``Retry-After`` as a floor. The
  jitter draw is injected (``rand=``) so every assertion is deterministic.
* :func:`classify_exception` - representative provider/HTTP exception shapes
  map to the stable semantic codes. Fake exceptions carry ``.status_code`` /
  ``.response.status_code`` so no real SDK is needed.
* :func:`retry_call` - transient-then-success retries and sleeps the computed
  delays (via an injected sleep recorder), a non-retryable error re-raises
  immediately, and exhausting ``max_retries`` re-raises the last error.

All non-determinism (``sleep``, ``rand``) is injected, mirroring
``tests/test_redis_memory_resilience.py``.
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path
from typing import Any

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "lib"))

from llm_retry import (  # noqa: E402 - sys.path shim must run first
    CLIENT_ERROR,
    CONNECTION_ERROR,
    RATE_LIMIT,
    SERVER_ERROR,
    TIMEOUT,
    UNKNOWN,
    RetryPolicy,
    classify_exception,
    is_retryable_code,
    retry_after_from_exception,
    retry_call,
)

# ---------------------------------------------------------------------------
# Fakes: exceptions with the status shapes real SDKs expose.
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {}


class StatusCodeError(Exception):
    """Exception carrying a top-level ``.status_code`` (httpx.HTTPStatusError-ish)."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


class ResponseStatusError(Exception):
    """Exception carrying a nested ``.response.status_code`` (requests-ish)."""

    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        super().__init__(f"response status {status_code}")
        self.response = _FakeResponse(status_code, headers)


class FakeTimeout(Exception):
    """No status; class name signals a timeout (unimported-SDK fallback path)."""


# ---------------------------------------------------------------------------
# RetryPolicy.compute_backoff_delay
# ---------------------------------------------------------------------------


def _no_jitter(low: float, high: float) -> float:
    # Deterministic: always draw the midpoint 1.0 for the default [0.5, 1.5].
    return (low + high) / 2.0


def test_backoff_grows_geometrically_with_fixed_jitter() -> None:
    policy = RetryPolicy(backoff_base_s=2.0, backoff_max_s=1000.0)
    delays = [policy.compute_backoff_delay(a, rand=_no_jitter) for a in range(4)]
    # base * 2**attempt * 1.0 jitter: 2, 4, 8, 16.
    assert delays == [2.0, 4.0, 8.0, 16.0]
    # Monotonic non-decreasing while below the cap.
    assert all(b >= a for a, b in itertools.pairwise(delays))


def test_backoff_is_capped_at_backoff_max_s() -> None:
    policy = RetryPolicy(backoff_base_s=2.0, backoff_max_s=10.0)
    # attempt 5 would be 64s uncapped; must clamp to 10.
    assert policy.compute_backoff_delay(5, rand=_no_jitter) == 10.0
    # Even the top of the jitter band cannot exceed the cap.
    high_jitter = policy.compute_backoff_delay(5, rand=lambda lo, hi: hi)
    assert high_jitter <= policy.backoff_max_s


def test_backoff_honors_retry_after_as_floor() -> None:
    policy = RetryPolicy(backoff_base_s=2.0, backoff_max_s=60.0)
    # Computed backoff for attempt 0 is ~2s; a 30s Retry-After wins.
    assert policy.compute_backoff_delay(0, retry_after_s=30.0, rand=_no_jitter) == 30.0
    # A Retry-After larger than the cap is still honored in full.
    assert policy.compute_backoff_delay(0, retry_after_s=120.0, rand=_no_jitter) == 120.0
    # A tiny Retry-After does not lower the computed backoff.
    assert policy.compute_backoff_delay(3, retry_after_s=1.0, rand=_no_jitter) == 16.0


def test_backoff_jitter_band_is_respected() -> None:
    policy = RetryPolicy(backoff_base_s=2.0, backoff_max_s=1000.0)
    low = policy.compute_backoff_delay(1, rand=lambda lo, hi: lo)  # 0.5x of 4 = 2
    high = policy.compute_backoff_delay(1, rand=lambda lo, hi: hi)  # 1.5x of 4 = 6
    assert low == 2.0
    assert high == 6.0


def test_policy_clamps_out_of_range_construction() -> None:
    assert RetryPolicy(max_retries=99).max_retries == 20
    assert RetryPolicy(max_retries=-5).max_retries == 0
    # Inverted jitter band is normalized to (low, high).
    assert RetryPolicy(jitter=(1.5, 0.5)).jitter == (0.5, 1.5)


def test_policy_from_env_reads_knobs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_LLM_MAX_RETRIES", "3")
    monkeypatch.setenv("ALFRED_LLM_BACKOFF_BASE_S", "1.5")
    monkeypatch.setenv("ALFRED_LLM_BACKOFF_MAX_S", "45")
    policy = RetryPolicy.from_env()
    assert policy.max_retries == 3
    assert policy.backoff_base_s == 1.5
    assert policy.backoff_max_s == 45.0


def test_policy_from_env_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "ALFRED_LLM_MAX_RETRIES",
        "ALFRED_LLM_BACKOFF_BASE_S",
        "ALFRED_LLM_BACKOFF_MAX_S",
        "ALFRED_LLM_TIMEOUT_PER_REQUEST_S",
    ):
        monkeypatch.delenv(name, raising=False)
    policy = RetryPolicy.from_env()
    assert policy.max_retries == 6
    assert policy.backoff_base_s == 2.0
    assert policy.backoff_max_s == 60.0
    assert policy.timeout_per_request_s == 120.0
    assert policy.retryable_status_codes == (429, 500, 502, 503, 504)


# ---------------------------------------------------------------------------
# classify_exception
# ---------------------------------------------------------------------------


def test_classify_429_is_rate_limit() -> None:
    assert classify_exception(StatusCodeError(429)) == RATE_LIMIT
    assert classify_exception(ResponseStatusError(429)) == RATE_LIMIT


def test_classify_503_is_server_error() -> None:
    assert classify_exception(StatusCodeError(503)) == SERVER_ERROR
    assert classify_exception(ResponseStatusError(500)) == SERVER_ERROR
    assert classify_exception(StatusCodeError(502)) == SERVER_ERROR


def test_classify_400_is_client_error_non_retryable() -> None:
    assert classify_exception(StatusCodeError(400)) == CLIENT_ERROR
    assert classify_exception(ResponseStatusError(404)) == CLIENT_ERROR
    assert not is_retryable_code(CLIENT_ERROR)


def test_classify_timeout() -> None:
    assert classify_exception(TimeoutError("slow")) == TIMEOUT
    # Unimported-SDK fallback: class name contains "timeout".
    assert classify_exception(FakeTimeout("deadline")) == TIMEOUT


def test_classify_connection_error() -> None:
    assert classify_exception(ConnectionError("refused")) == CONNECTION_ERROR


def test_classify_unknown_when_no_signal() -> None:
    assert classify_exception(ValueError("nope")) == UNKNOWN
    assert not is_retryable_code(UNKNOWN)


def test_retryable_code_set() -> None:
    assert is_retryable_code(RATE_LIMIT)
    assert is_retryable_code(SERVER_ERROR)
    assert is_retryable_code(TIMEOUT)
    assert is_retryable_code(CONNECTION_ERROR)
    assert not is_retryable_code(CLIENT_ERROR)
    assert not is_retryable_code(UNKNOWN)


def test_retry_after_from_exception_reads_header() -> None:
    exc = ResponseStatusError(429, headers={"Retry-After": "12"})
    assert retry_after_from_exception(exc) == 12.0
    assert retry_after_from_exception(StatusCodeError(429)) is None


# ---------------------------------------------------------------------------
# retry_call
# ---------------------------------------------------------------------------


class _SleepRecorder:
    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def test_retry_call_transient_then_success_records_delays() -> None:
    sleeper = _SleepRecorder()
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise StatusCodeError(503)
        return "ok"

    policy = RetryPolicy(max_retries=5, backoff_base_s=2.0, backoff_max_s=1000.0)
    result = retry_call(flaky, policy, sleep=sleeper, rand=_no_jitter)

    assert result == "ok"
    assert calls["n"] == 3  # two failures, one success.
    # Slept the computed backoff for attempts 0 and 1: 2s then 4s.
    assert sleeper.delays == [2.0, 4.0]


def test_retry_call_honors_retry_after_hint() -> None:
    sleeper = _SleepRecorder()
    calls = {"n": 0}

    def rate_limited() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ResponseStatusError(429, headers={"Retry-After": "30"})
        return "done"

    policy = RetryPolicy(max_retries=3, backoff_base_s=2.0, backoff_max_s=60.0)
    assert retry_call(rate_limited, policy, sleep=sleeper, rand=_no_jitter) == "done"
    # Retry-After (30s) overrides the ~2s computed backoff.
    assert sleeper.delays == [30.0]


def test_retry_call_non_retryable_raises_immediately() -> None:
    sleeper = _SleepRecorder()
    calls = {"n": 0}

    def bad_request() -> Any:
        calls["n"] += 1
        raise StatusCodeError(400)

    policy = RetryPolicy(max_retries=5)
    with pytest.raises(StatusCodeError):
        retry_call(bad_request, policy, sleep=sleeper, rand=_no_jitter)

    assert calls["n"] == 1  # never retried.
    assert sleeper.delays == []


def test_retry_call_exhausts_and_reraises_last() -> None:
    sleeper = _SleepRecorder()
    calls = {"n": 0}

    def always_503() -> Any:
        calls["n"] += 1
        raise StatusCodeError(503)

    policy = RetryPolicy(max_retries=2, backoff_base_s=2.0, backoff_max_s=1000.0)
    with pytest.raises(StatusCodeError):
        retry_call(always_503, policy, sleep=sleeper, rand=_no_jitter)

    # Initial call + 2 retries = 3 attempts; 2 sleeps between them.
    assert calls["n"] == 3
    assert sleeper.delays == [2.0, 4.0]


def test_retry_call_custom_is_retryable_predicate() -> None:
    sleeper = _SleepRecorder()
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 2:
            raise ValueError("normally-unknown-but-forced-retryable")
        return "ok"

    policy = RetryPolicy(max_retries=3, backoff_base_s=1.0, backoff_max_s=100.0)
    result = retry_call(
        flaky,
        policy,
        is_retryable=lambda exc: isinstance(exc, ValueError),
        sleep=sleeper,
        rand=_no_jitter,
    )
    assert result == "ok"
    assert calls["n"] == 2
    assert sleeper.delays == [1.0]


# ---------------------------------------------------------------------------
# Wiring: the connector HTTP client retries transient failures.
# ---------------------------------------------------------------------------


def test_urllib_http_client_retries_transient_5xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.error

    from connectors import UrllibHttpClient

    calls = {"n": 0}

    class _Resp:
        status = 200

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *_: Any) -> None:
            return None

        def read(self) -> bytes:
            return b'{"ok": true}'

    def fake_urlopen(req: Any, timeout: float = 0.0) -> Any:
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.HTTPError(
                url="https://api.example.com",
                code=503,
                msg="unavailable",
                hdrs=None,  # type: ignore[arg-type]
                fp=None,
            )
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    # Zero-delay policy so the test does not actually sleep.
    client = UrllibHttpClient(
        retry_policy=RetryPolicy(max_retries=5, backoff_base_s=0.0, backoff_max_s=0.0)
    )
    result = client.get_json("https://api.example.com")
    assert result == {"ok": True}
    assert calls["n"] == 3  # two 503s retried, third succeeded.


def test_urllib_http_client_does_not_retry_4xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.error

    from connectors import HttpError, UrllibHttpClient

    calls = {"n": 0}

    def fake_urlopen(req: Any, timeout: float = 0.0) -> Any:
        calls["n"] += 1
        raise urllib.error.HTTPError(
            url="https://api.example.com",
            code=404,
            msg="not found",
            hdrs=None,  # type: ignore[arg-type]
            fp=None,
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = UrllibHttpClient(
        retry_policy=RetryPolicy(max_retries=5, backoff_base_s=0.0, backoff_max_s=0.0)
    )
    with pytest.raises(HttpError):
        client.get_json("https://api.example.com")
    assert calls["n"] == 1  # 404 is client_error, never retried.
