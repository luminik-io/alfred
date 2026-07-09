"""Live operational grounding for the conversational surfaces.

Covers ``lib/converse_grounding`` and the conversation-first defaults that make a
Slack mention or a desktop Ask answer like a colleague instead of dumping a
planning form:

* the fleet snapshot renders agent state and recent firings from a stubbed
  read-only reader, with the classified failure cause surfaced for a failed run;
* it stays bounded (agent and firing caps) and trims long summaries;
* every reader failure and the OFF switch degrade to an empty block (never raise);
* ``render_system_prompt`` injects the snapshot into the interrogator prompt, and
  omits it cleanly when no snapshot is supplied;
* Slack converse is ON by default and only stands down when explicitly disabled.

No network, no live model, and no runtime state is touched: the reader is a stub
and the prompt loader is a trivial template pass.
"""

from __future__ import annotations

import string
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB = REPO_ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import compose_converse as cc  # noqa: E402
import converse_grounding as cg  # noqa: E402
import slack_converse as sc  # noqa: E402
from compose_converse import INTENT_BUILD, INTENT_CONVERSATION  # noqa: E402

# ---------------------------------------------------------------------------
# Stub reader (mirrors lib/server/reader.py's AgentSummary / FiringRecord shape)
# ---------------------------------------------------------------------------


@dataclass
class StubAgent:
    codename: str
    status: str = "idle"
    last_summary: str = ""
    firings_today: int = 0
    paused: bool = False
    last_run_at: str | None = None
    display_name: str | None = None
    role_title: str | None = None


@dataclass
class StubTimeline:
    error: str = ""
    headline: str = ""
    severity: str = ""


@dataclass
class StubFiring:
    firing_id: str
    codename: str
    status: str = "ok"
    summary: str = ""
    started_at: str | None = None
    ended_at: str | None = None
    timeline: StubTimeline | None = None


@dataclass
class StubReader:
    agents: list[StubAgent] = field(default_factory=list)
    firings: list[StubFiring] = field(default_factory=list)
    agents_error: bool = False
    firings_error: bool = False

    def list_agents(self) -> list[StubAgent]:
        if self.agents_error:
            raise RuntimeError("state briefly inconsistent")
        return list(self.agents)

    def list_recent_firings(self, *, limit: int = 50, codename: str | None = None):
        if self.firings_error:
            raise RuntimeError("state briefly inconsistent")
        return list(self.firings)[:limit]


# ---------------------------------------------------------------------------
# build_operational_grounding
# ---------------------------------------------------------------------------


def test_grounding_renders_agents_and_firings() -> None:
    reader = StubReader(
        agents=[
            StubAgent(
                codename="lucius", status="error", firings_today=3, last_summary="type check failed"
            ),
            StubAgent(codename="bane", status="live", firings_today=1),
        ],
        firings=[
            StubFiring(
                firing_id="lucius-20260703-0900",
                codename="lucius",
                status="error",
                summary="worked on #1038",
                timeline=StubTimeline(error="mypy: incompatible return type", severity="error"),
            ),
        ],
    )
    text = cg.build_operational_grounding(reader)
    assert "Fleet status (live)" in text
    assert "lucius" in text and "error" in text
    assert "3 runs today" in text
    assert "Recent firings" in text
    # The classified failure cause is surfaced so "why did lucius fail" is answerable.
    assert "mypy: incompatible return type" in text
    assert "lucius-20260703-0900" in text


def test_grounding_empty_reader_is_empty() -> None:
    assert cg.build_operational_grounding(StubReader()) == ""


def test_grounding_none_reader_is_empty() -> None:
    assert cg.build_operational_grounding(None) == ""


def test_grounding_degrades_on_reader_error() -> None:
    # A reader that raises on both reads must never propagate; the turn keeps going.
    reader = StubReader(agents_error=True, firings_error=True)
    assert cg.build_operational_grounding(reader) == ""


def test_grounding_survives_partial_reader_error() -> None:
    reader = StubReader(
        agents=[StubAgent(codename="oracle", status="live", firings_today=2)],
        firings_error=True,
    )
    text = cg.build_operational_grounding(reader)
    assert "oracle" in text
    assert "Recent firings" not in text  # the failing read is simply omitted


def test_grounding_respects_bounds() -> None:
    reader = StubReader(
        agents=[StubAgent(codename=f"agent{i}") for i in range(50)],
        firings=[StubFiring(firing_id=f"f{i}", codename="x") for i in range(50)],
    )
    text = cg.build_operational_grounding(reader, agent_limit=5, firings_limit=4)
    assert text.count("\n- ") <= (5 + 4) + 2  # bounded rows plus the two headers' leads
    assert "agent0" in text and "agent4" in text
    assert "agent5" not in text


def test_grounding_trims_long_summary() -> None:
    long_summary = "x" * 500
    reader = StubReader(agents=[StubAgent(codename="a", last_summary=long_summary)])
    text = cg.build_operational_grounding(reader)
    assert "…" in text
    assert long_summary not in text


def test_grounding_marks_paused_agent() -> None:
    reader = StubReader(agents=[StubAgent(codename="ra", status="idle", paused=True)])
    text = cg.build_operational_grounding(reader)
    assert "paused" in text


def test_grounding_disabled_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(cg.ENV_ENABLED, "0")
    reader = StubReader(agents=[StubAgent(codename="lucius", status="live")])
    assert cg.build_operational_grounding(reader) == ""


def test_grounding_enabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(cg.ENV_ENABLED, raising=False)
    assert cg.operational_grounding_enabled() is True


# ---------------------------------------------------------------------------
# build_repo_grounding path containment (py/path-injection)
# ---------------------------------------------------------------------------


def test_build_repo_grounding_still_reads_contained_claude_md(tmp_path: Path) -> None:
    # A production-shaped slug resolving (via the bare name) to a real subdir of
    # workspace_root must still inline its CLAUDE.md exactly as before.
    workspace = tmp_path / "workspace"
    repo_dir = workspace / "acme-frontend"
    repo_dir.mkdir(parents=True)
    (repo_dir / "CLAUDE.md").write_text("# Acme frontend canon\nUse tokens.", encoding="utf-8")

    grounding = cc.build_repo_grounding(["acme-io/acme-frontend"], workspace_root=workspace)

    assert "Acme frontend canon" in grounding
    assert "Use tokens." in grounding


def test_build_repo_grounding_rejects_path_traversal_slug(tmp_path: Path) -> None:
    # A secret CLAUDE.md sits OUTSIDE the workspace; a traversal slug that would
    # resolve to it must never be read. Grounding degrades to the safe fallback.
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "CLAUDE.md"
    secret.write_text("TOP SECRET should never be grounded", encoding="utf-8")

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    # ``x/../outside`` -> bare ``../outside`` -> workspace/../outside, which
    # escapes the workspace and points straight at the secret's directory.
    # Without containment this reads ``outside/CLAUDE.md`` (the vuln).
    grounding = cc.build_repo_grounding(
        ["x/../outside"],
        workspace_root=workspace,
    )

    assert "TOP SECRET" not in grounding
    assert "No local checkout or CLAUDE.md available" in grounding


def test_build_repo_grounding_reads_trusted_absolute_mapping(tmp_path: Path) -> None:
    # A TRUSTED repo_to_local mapping (operator's GH_REPO_TO_LOCAL) may point at
    # an absolute checkout OUTSIDE workspace_root. That is a legitimate operator
    # config, so its CLAUDE.md must still be read - containment applies only to
    # the untrusted request-slug fallback, not to this trusted mapping.
    checkout = tmp_path / "elsewhere" / "acme-api"
    checkout.mkdir(parents=True)
    (checkout / "CLAUDE.md").write_text("# Acme API canon\nBackend rules.", encoding="utf-8")

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    grounding = cc.build_repo_grounding(
        ["acme-io/acme-api"],
        workspace_root=workspace,
        repo_to_local={"acme-api": str(checkout)},
    )

    assert "Acme API canon" in grounding
    assert "Backend rules." in grounding


def test_build_repo_grounding_rejects_absolute_untrusted_slug(tmp_path: Path) -> None:
    # With no mapping, an absolute bare name from the request slug must be
    # rejected before any read/list. ``x//abs`` splits (maxsplit=1) to a bare
    # name of ``/abs`` - an absolute, untrusted path - which must not be read.
    outside = tmp_path / "abs-secret"
    outside.mkdir()
    (outside / "CLAUDE.md").write_text("absolute-path secret", encoding="utf-8")

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    grounding = cc.build_repo_grounding(
        [f"x/{outside}"],  # bare name becomes the absolute str(outside)
        workspace_root=workspace,
    )

    assert "absolute-path secret" not in grounding
    assert "No local checkout or CLAUDE.md available" in grounding


def test_build_repo_grounding_traversal_does_not_list_outside_dir(tmp_path: Path) -> None:
    # The _file_tree_summary fallback (iterdir) must also stay contained: a
    # traversal slug pointing at a dir with no CLAUDE.md must not leak its
    # entries into the grounding.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leaked-file.txt").write_text("x", encoding="utf-8")

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    grounding = cc.build_repo_grounding(
        ["x/../outside"],
        workspace_root=workspace,
    )

    assert "leaked-file.txt" not in grounding
    assert "No local checkout or CLAUDE.md available" in grounding


# ---------------------------------------------------------------------------
# render_system_prompt injection
# ---------------------------------------------------------------------------


def _loader(path: Path, *, extra_vars: dict[str, str] | None = None) -> str:
    text = path.read_text(encoding="utf-8")
    return string.Template(text).safe_substitute(extra_vars or {})


def test_render_system_prompt_injects_operational_grounding(tmp_path: Path) -> None:
    prompt = tmp_path / "p.md"
    prompt.write_text(
        "grounding: ${REPO_GROUNDING}\nstatus:\n${OPERATIONAL_GROUNDING}\n",
        encoding="utf-8",
    )
    rendered = cc.render_system_prompt(
        prompt_path=prompt,
        repo_grounding="REPOS",
        code_map="MAP",
        intake_guidance="GUIDE",
        loader=_loader,
        operational_grounding="### Fleet status (live)\n- lucius: error",
    )
    assert "Fleet status (live)" in rendered
    assert "lucius: error" in rendered


def test_render_system_prompt_omits_grounding_cleanly(tmp_path: Path) -> None:
    prompt = tmp_path / "p.md"
    prompt.write_text("status:\n${OPERATIONAL_GROUNDING}\n", encoding="utf-8")
    rendered = cc.render_system_prompt(
        prompt_path=prompt,
        repo_grounding="REPOS",
        code_map="MAP",
        intake_guidance="GUIDE",
        loader=_loader,
    )
    # No leftover literal placeholder, and an honest "no status" note instead.
    assert "${OPERATIONAL_GROUNDING}" not in rendered
    assert "No live fleet status is available" in rendered


# ---------------------------------------------------------------------------
# Conversation-first defaults
# ---------------------------------------------------------------------------


def test_slack_converse_enabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    # The engine resolves (planning-assistant engine is set), and converse is not
    # explicitly disabled, so a mention engages converse rather than plan-drafting.
    monkeypatch.delenv(sc.ENV_ENABLED, raising=False)
    monkeypatch.delenv(sc.ENV_ENGINE, raising=False)
    monkeypatch.setenv(sc.ENV_FALLBACK_ENGINE, "hybrid")
    monkeypatch.delenv(sc.ENV_CHANNELS, raising=False)
    config = sc.SlackConverseConfig.from_env()
    assert config.enabled is True
    assert config.engages("C123") is True


def test_slack_converse_kill_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(sc.ENV_ENABLED, "0")
    monkeypatch.setenv(sc.ENV_FALLBACK_ENGINE, "hybrid")
    config = sc.SlackConverseConfig.from_env()
    assert config.enabled is False
    assert config.engages("C123") is False


def test_slack_converse_default_still_needs_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    # ON by default, but with no engine resolvable it does NOT engage, so an
    # unconfigured runtime degrades to planning intake instead of erroring.
    monkeypatch.delenv(sc.ENV_ENABLED, raising=False)
    monkeypatch.delenv(sc.ENV_ENGINE, raising=False)
    monkeypatch.delenv(sc.ENV_FALLBACK_ENGINE, raising=False)
    config = sc.SlackConverseConfig.from_env()
    assert config.enabled is True
    assert config.engages("C123") is False


# ---------------------------------------------------------------------------
# Full listener path: a real mention is conversational, not a planning form
# ---------------------------------------------------------------------------


class _Poster:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    def chat_postMessage(self, **kwargs: object) -> dict:
        self.messages.append(dict(kwargs))
        return {"ok": True, "ts": "1.1"}


def _mention(text: str, *, event_id: str = "EvX") -> dict:
    return {
        "event_id": event_id,
        "event": {
            "type": "app_mention",
            "channel": "C1",
            "user": "U1",
            "text": text,
            "ts": "1716480099.000001",
        },
    }


def _make_listener(tmp_path: Path, converse_runner):
    from slack_listener import SlackPlanningListener

    config = sc.SlackConverseConfig(enabled=True, engine="claude", channels=frozenset())
    return SlackPlanningListener(
        state_root=tmp_path,
        poster=_Poster(),
        trusted_user_ids=("U1",),
        converse_config=config,
        converse_runner=converse_runner,
    )


def test_question_mention_gets_conversational_answer_not_draft(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def runner(**kwargs):
        captured.update(kwargs)
        client = kwargs["client"]
        client.chat_postMessage(
            channel=kwargs["channel"],
            thread_ts=kwargs["thread_ts"],
            text="Right now Lucius is retrying a failed run and Bane is idle.",
        )
        return sc.SlackConverseOutcome(handled=True, intent=INTENT_CONVERSATION, streamed=True)

    listener = _make_listener(tmp_path, runner)
    result = listener.handle_payload(_mention("<@UALFRED> what's the fleet doing?"))

    assert result.handled is True
    assert result.action == "converse"
    # It answered conversationally; it did NOT open a planning draft.
    assert not getattr(result, "draft_path", None)
    assert not list((tmp_path / "planning-drafts").glob("*.json"))
    assert "Lucius" in listener.poster.messages[-1]["text"]


def test_build_request_mention_offers_plan_not_autodraft(tmp_path: Path) -> None:
    def runner(**kwargs):
        client = kwargs["client"]
        client.chat_postMessage(
            channel=kwargs["channel"],
            thread_ts=kwargs["thread_ts"],
            text="Happy to. I can turn this into a tracked issue when you are ready.",
        )
        return sc.SlackConverseOutcome(handled=True, intent=INTENT_BUILD, offered_issue=True)

    listener = _make_listener(tmp_path, runner)
    result = listener.handle_payload(
        _mention("<@UALFRED> add a dark mode toggle to the settings screen")
    )

    assert result.handled is True
    assert result.action == "converse_build"
    # The build request was OFFERED as a plan, not silently auto-drafted.
    assert not list((tmp_path / "planning-drafts").glob("*.json"))


def test_converse_failure_falls_through_to_planning_draft(tmp_path: Path) -> None:
    # When converse cannot answer (unhandled), the listener keeps its prior
    # planning-intake fallback so a mention is never dropped.
    def runner(**kwargs):
        return sc.SlackConverseOutcome(handled=False, detail="live_session_unavailable")

    listener = _make_listener(tmp_path, runner)
    result = listener.handle_payload(
        _mention("<@UALFRED> title: fix the login bug\nrepo: acme/api")
    )

    assert result.handled is True
    assert result.action == "draft_created"
