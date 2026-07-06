"""Dry-run architect lifecycle path checks for ``doctor.sh --lifecycle``."""

from __future__ import annotations

import argparse
import contextlib
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TextIO, cast

import labels as label_constants
from architect_lifecycle import parse_parent_issue
from slack_approval import default_slack_client

from .paths import CLAUDE_BIN, decode_env_value

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "lifecycle_doctor_body.md"
DEFAULT_PARENT_BODY = """Bundle: doctor-hello

Repos:
- doctor-org/repo-a
- doctor-org/repo-b
- doctor-org/repo-c

Children:
- repo-a: add the hello endpoint
- repo-b: render the hello state
- repo-c: capture hello analytics

Done when:
- All children are filed with the shared bundle label
- Each repo has a narrow implementation issue
"""
PARENT_TITLE = "Bundle: doctor-hello"
PARENT_REPO = "doctor-org/doctor-parent"
PARENT_ISSUE_NUMBER = 1
GITHUB_LABEL_NAME_LIMIT = 50


class SlackProbeClient(Protocol):
    def chat_postMessage(self, **kwargs: Any) -> Any: ...
    def reactions_get(self, *, channel: str, timestamp: str, full: bool = True) -> Any: ...
    def chat_delete(self, *, channel: str, ts: str) -> Any: ...


class CommandRunner(Protocol):
    def __call__(
        self,
        cmd: Sequence[str],
        *,
        input_text: str,
        timeout_s: int,
        env: Mapping[str, str],
    ) -> subprocess.CompletedProcess[str]: ...


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    lines: tuple[str, ...]
    hint: str = ""


def _response_get(response: Any, key: str, default: Any = None) -> Any:
    if isinstance(response, Mapping):
        return response.get(key, default)
    getter = getattr(response, "get", None)
    if callable(getter):
        return getter(key, default)
    return getattr(response, key, default)


def _load_body(path: Path | None) -> str:
    if path and path.exists():
        return path.read_text(encoding="utf-8")
    if FIXTURE_PATH.exists():
        return FIXTURE_PATH.read_text(encoding="utf-8")
    return DEFAULT_PARENT_BODY


def check_parent_parser(
    body: str,
    *,
    bundle_slug_prefix: str = "",
) -> tuple[CheckResult, Any | None]:
    try:
        plan = parse_parent_issue(
            body=body,
            title=PARENT_TITLE,
            parent_repo=PARENT_REPO,
            parent_issue_number=PARENT_ISSUE_NUMBER,
            bundle_slug_prefix=bundle_slug_prefix,
        )
    except Exception as exc:
        return (
            CheckResult(
                "parent-issue parser",
                False,
                (f"parse_parent_issue raised {type(exc).__name__}: {exc}",),
                "See docs/ARCHITECT.md for the validated parent issue shape.",
            ),
            None,
        )
    lines = (
        f'parsed bundle slug: "{plan.bundle_slug}"',
        f"parsed {len(plan.affected_repos)} repos: {list(plan.affected_repos)!r}",
        f"parsed {len(plan.children)} children",
    )
    expected_slug = (
        f"{bundle_slug_prefix.strip('-')}-doctor-hello" if bundle_slug_prefix else "doctor-hello"
    )
    ok = (
        plan.bundle_slug == expected_slug
        and len(plan.affected_repos) == 3
        and len(plan.children) == 3
    )
    hint = "" if ok else "See docs/ARCHITECT.md for the validated parent issue shape."
    return CheckResult("parent-issue parser", ok, lines, hint), plan


def check_bundle_label(plan: Any | None) -> CheckResult:
    if plan is None:
        return CheckResult(
            "bundle label generation",
            False,
            ("skipped because parent parser failed",),
        )
    try:
        label = label_constants.bundle_label(str(plan.bundle_slug))
    except ValueError as exc:
        return CheckResult(
            "bundle label generation",
            False,
            (f"bundle slug rejected: {exc}",),
        )
    ok = len(label) <= GITHUB_LABEL_NAME_LIMIT
    return CheckResult(
        "bundle label generation",
        ok,
        (
            f'bundle slug: "{plan.bundle_slug}"',
            f'full label: "{label}"',
            f"length: {len(label)} chars",
        ),
        "" if ok else "Keep bundle slugs short enough for GitHub label names.",
    )


def _slack_channel(env: Mapping[str, str]) -> str:
    return (env.get("ARCHITECT_SLACK_CHANNEL") or env.get("SLACK_HOME_CHANNEL") or "alfred").lstrip(
        "#"
    )


def check_slack_probe(
    env: Mapping[str, str],
    *,
    slack_client: SlackProbeClient | None = None,
) -> CheckResult:
    channel = _slack_channel(env)
    if slack_client is None:
        try:
            client = cast(SlackProbeClient, default_slack_client())
        except Exception as exc:
            return CheckResult(
                "Slack bot-token + reactions.get smoke",
                False,
                (f"could not create Slack client: {exc}",),
                "Set SLACK_BOT_TOKEN or configure the Alfred Slack bot token resolver.",
            )
    else:
        client = slack_client

    posted_channel = channel
    ts = ""
    try:
        post = client.chat_postMessage(
            channel=channel,
            text="alfred lifecycle doctor smoke test",
            unfurl_links=False,
            unfurl_media=False,
        )
        if not bool(_response_get(post, "ok", False)):
            error = str(_response_get(post, "error", "unknown_error"))
            return CheckResult(
                "Slack bot-token + reactions.get smoke",
                False,
                (f"chat.postMessage failed: {error}",),
                "Invite the bot to the approval channel and confirm chat:write scope.",
            )
        posted_channel = str(_response_get(post, "channel", channel))
        ts = str(_response_get(post, "ts", ""))
        if not ts:
            return CheckResult(
                "Slack bot-token + reactions.get smoke",
                False,
                ("chat.postMessage returned no timestamp",),
            )
        reactions = client.reactions_get(channel=posted_channel, timestamp=ts, full=True)
        if not bool(_response_get(reactions, "ok", False)):
            error = str(_response_get(reactions, "error", "unknown_error"))
            needed = str(_response_get(reactions, "needed", "reactions:read"))
            return CheckResult(
                "Slack bot-token + reactions.get smoke",
                False,
                (f"reactions.get failed: {error}",),
                f"Reinstall the Slack app with {needed} scope.",
            )
        return CheckResult(
            "Slack bot-token + reactions.get smoke",
            True,
            (
                f"chat.postMessage -> channel {posted_channel}, ts {ts}",
                "reactions.get -> ok",
                "chat.delete cleanup -> attempted",
            ),
        )
    except Exception as exc:
        return CheckResult(
            "Slack bot-token + reactions.get smoke",
            False,
            (f"Slack probe raised {type(exc).__name__}: {exc}",),
        )
    finally:
        if ts:
            with contextlib.suppress(Exception):
                client.chat_delete(channel=posted_channel, ts=ts)


def _default_command_runner(
    cmd: Sequence[str],
    *,
    input_text: str,
    timeout_s: int,
    env: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(cmd),
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
        env=dict(env),
    )


def _resolve_oauth_token(env: Mapping[str, str]) -> str:
    """Resolve the Claude OAuth token the way the runtime does.

    Process/passed env first, then ``$ALFRED_HOME/.env`` -- the canonical
    runtime store that ``alfred setup-token`` writes and ``agent-launch``
    loads. Reading only the passed env (the old behaviour) reported "no
    token" for a perfectly good token that lived in ``.env``, sending the
    operator to re-run setup-token when auth was actually fine.
    """
    token = (env.get("CLAUDE_CODE_OAUTH_TOKEN") or "").strip()
    if token:
        return token
    home = (env.get("ALFRED_HOME") or os.path.expanduser("~/.alfred")).strip()
    if not home:
        return ""
    try:
        with open(Path(home) / ".env", encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, _, value = line.partition("=")
                if name.removeprefix("export").strip() == "CLAUDE_CODE_OAUTH_TOKEN":
                    # Shared decoder keeps this in lockstep with the bash
                    # `decode_env_value` loaders; a naive strip() of quote
                    # chars would diverge for shlex-quoted edge cases.
                    return decode_env_value(value.strip())
    except OSError:
        pass
    return ""


def check_claude_oauth(
    env: Mapping[str, str],
    *,
    command_runner: CommandRunner | None = None,
) -> CheckResult:
    token = _resolve_oauth_token(env)
    if not token:
        return CheckResult(
            "OAuth token",
            False,
            ("CLAUDE_CODE_OAUTH_TOKEN reachable (env or $ALFRED_HOME/.env): no",),
            "Run `alfred setup-token` from an interactive terminal, then rerun doctor.",
        )
    runner = command_runner or _default_command_runner
    # The live `claude -p` probe must see the token even when it only lived
    # in .env, so it validates the same credential the runtime would use.
    if not (env.get("CLAUDE_CODE_OAUTH_TOKEN") or "").strip():
        env = {**env, "CLAUDE_CODE_OAUTH_TOKEN": token}
    claude_bin = env.get("CLAUDE_BIN") or CLAUDE_BIN
    try:
        result = runner(
            [claude_bin, "-p", "--max-turns", "1"],
            input_text="say hi\n",
            timeout_s=30,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            "OAuth token",
            False,
            ("CLAUDE_CODE_OAUTH_TOKEN reachable: yes", "`claude -p` probe timed out"),
            "Run `claude` interactively to verify auth.",
        )
    except OSError as exc:
        return CheckResult(
            "OAuth token",
            False,
            ("CLAUDE_CODE_OAUTH_TOKEN reachable: yes", f"`{claude_bin}` failed: {exc}"),
            "Set CLAUDE_BIN to the scheduler-visible Claude CLI path.",
        )
    if result.returncode != 0:
        blob = (result.stdout + result.stderr).strip()
        return CheckResult(
            "OAuth token",
            False,
            (
                "CLAUDE_CODE_OAUTH_TOKEN reachable: yes",
                f"`claude -p` exit {result.returncode}",
            ),
            blob[:500] or "Run `claude` interactively to verify auth.",
        )
    return CheckResult(
        "OAuth token",
        True,
        ("CLAUDE_CODE_OAUTH_TOKEN reachable: yes", "`claude -p --max-turns 1` -> ok"),
    )


def render_results(results: Sequence[CheckResult], stream: TextIO) -> None:
    print("=> lifecycle path validation (dry-run, no GitHub issue side effects)", file=stream)
    for result in results:
        print(file=stream)
        print(f"  {result.name}:", file=stream)
        for line in result.lines:
            print(f"    {line}", file=stream)
        print(f"    {'ok' if result.ok else 'FAIL'}", file=stream)
        if result.hint:
            print(f"    HINT: {result.hint}", file=stream)
    passed = sum(1 for result in results if result.ok)
    failed = len(results) - passed
    print(file=stream)
    print(f"  lifecycle preflight: {passed} passed, {failed} failed", file=stream)


def run_lifecycle_doctor(
    *,
    fixture: Path | None = None,
    env: Mapping[str, str] | None = None,
    slack_client: SlackProbeClient | None = None,
    command_runner: CommandRunner | None = None,
    stream: TextIO | None = None,
) -> int:
    effective_env = dict(os.environ if env is None else env)
    out = stream or sys.stdout
    body = _load_body(fixture)
    bundle_slug_prefix = (effective_env.get("ARCHITECT_BUNDLE_SLUG_PREFIX") or "").strip()
    parser_result, plan = check_parent_parser(body, bundle_slug_prefix=bundle_slug_prefix)
    results = (
        parser_result,
        check_bundle_label(plan),
        check_slack_probe(effective_env, slack_client=slack_client),
        check_claude_oauth(effective_env, command_runner=command_runner),
    )
    render_results(results, out)
    return 0 if all(result.ok for result in results) else 1


# --------------------------------------------------------------------------
# Deep headless-auth probe (scrubbed-env, mimics launchd)
# --------------------------------------------------------------------------

# Env keys that make an interactive shell "look" authenticated but that a
# launchd-spawned firing never inherits. Scrubbing them reproduces the exact
# outage class where `claude` works in the terminal yet 401s under the
# scheduler: the credential lived only in the interactive session's env, not in
# $ALFRED_HOME/.env that agent-launch actually loads.
_INTERACTIVE_ONLY_ENV_KEYS: tuple[str, ...] = (
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_SECURITY_TOKEN",
    "OPENAI_API_KEY",
)


def _scrubbed_launchd_env(base_env: Mapping[str, str]) -> dict[str, str]:
    """Build the minimal env a launchd firing actually sees.

    Starts from a bare env (PATH/HOME/ALFRED_HOME/WORKSPACE_ROOT + the CLI
    path knobs), then overlays ``$ALFRED_HOME/.env`` the way ``agent-launch``
    does. Any interactive-only credential that was NOT persisted to ``.env`` is
    dropped, so the probe fails exactly where a scheduled firing would. This is
    the ``env -i`` mimic the deep check needs: a token in Keychain or a shell
    rc file is invisible here.
    """
    keep = ("PATH", "HOME", "ALFRED_HOME", "WORKSPACE_ROOT", "CLAUDE_BIN", "CLAUDE_CONFIG_DIR")
    scrubbed: dict[str, str] = {k: base_env[k] for k in keep if k in base_env}
    # Overlay .env exactly like the runtime loader: only these persisted values
    # are legitimately visible to a scheduled firing.
    home = (scrubbed.get("ALFRED_HOME") or os.path.expanduser("~/.alfred")).strip()
    env_path = Path(home) / ".env"
    try:
        with open(env_path, encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, _, value = line.partition("=")
                name = name.removeprefix("export").strip()
                if name and name.isidentifier():
                    scrubbed[name] = decode_env_value(value.strip())
    except OSError:
        pass
    return scrubbed


def run_deep_auth_probe(
    *,
    env: Mapping[str, str] | None = None,
    command_runner: CommandRunner | None = None,
    stream: TextIO | None = None,
) -> int:
    """Run the headless Claude auth probe from a scrubbed launchd-like env.

    This is the opt-in deep check behind ``alfred doctor --deep``. It proves a
    minimal ``claude -p`` invocation works under the exact env a launchd firing
    sees (bare env + ``$ALFRED_HOME/.env`` overlay), catching the silent-401
    class that the cheap presence check cannot: a token that resolves
    interactively but was never persisted where the scheduler can read it.
    """
    out = stream or sys.stdout
    base_env = dict(os.environ if env is None else env)
    scrubbed = _scrubbed_launchd_env(base_env)
    print("=> deep headless-auth probe (scrubbed env, mimics launchd `env -i`)", file=out)
    print(f"    ALFRED_HOME={scrubbed.get('ALFRED_HOME', '(unset)')}", file=out)
    result = check_claude_oauth(scrubbed, command_runner=command_runner)
    print(file=out)
    print(f"  {result.name}:", file=out)
    for line in result.lines:
        print(f"    {line}", file=out)
    print(f"    {'ok' if result.ok else 'FAIL'}", file=out)
    if result.hint:
        print(f"    HINT: {result.hint}", file=out)
    if not result.ok:
        print(
            "\n  A scheduled firing would 401 with this env. Persist the token with "
            "`alfred setup-token` so it lands in $ALFRED_HOME/.env.",
            file=out,
        )
    return 0 if result.ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the architect lifecycle path.")
    parser.add_argument("--fixture", type=Path, default=None)
    parser.add_argument(
        "--deep",
        action="store_true",
        help="run only the scrubbed-env headless Claude auth probe",
    )
    args = parser.parse_args(argv)
    if args.deep:
        return run_deep_auth_probe()
    return run_lifecycle_doctor(fixture=args.fixture)


if __name__ == "__main__":
    raise SystemExit(main())
