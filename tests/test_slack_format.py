"""Tests for ``lib/slack_format.py``, Block Kit threading helpers.

We don't hit the real Slack API; we monkeypatch ``_api_post`` and
``_resolve_bot_token`` so the tests stay deterministic and offline.

The contract being verified:

- Without a bot token, the helpers return None / False (silent skip).
- With a token + canned API responses, ``firing_thread_root`` returns a
  ``ThreadHandle`` carrying channel + ts.
- The header text is built from ``codename_with_role`` so role wiring
  flows through to the Slack post.
- Severity drives the attachment colour stripe (green / yellow / red).
- The duplicate-render guard from PR #141: top-level ``text`` is a
  generic notification preview and the attachment must NOT also carry
  ``text`` (only ``fallback`` + ``blocks``).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_alfred_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ALFRED_HOME", str(tmp_path / "alfred"))
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))
    for mod in list(sys.modules):
        if mod.startswith("agent_runner") or mod == "slack.posting":
            del sys.modules[mod]
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
    yield


def test_firing_thread_root_returns_none_without_bot_token(monkeypatch):
    import slack.posting as sf

    monkeypatch.setattr(sf, "_resolve_bot_token", lambda: None)
    handle = sf.firing_thread_root(
        codename="senior-dev",
        firing_id="2026-05-09-1432-aa",
        summary_one_liner="firing started",
    )
    assert handle is None


def test_post_flat_returns_false_without_bot_token(monkeypatch):
    import slack.posting as sf

    monkeypatch.setattr(sf, "_resolve_bot_token", lambda: None)
    monkeypatch.setattr(
        sf, "_api_post", lambda *a, **kw: pytest.fail("post_flat hit the API with no token")
    )
    assert sf.post_flat("anything", severity="warn") is False


def test_post_flat_posts_via_chat_postmessage_with_colour_stripe(monkeypatch):
    import slack.posting as sf

    monkeypatch.setenv("SLACK_HOME_CHANNEL", "eng-fleet")
    monkeypatch.setattr(sf, "_resolve_bot_token", lambda: "xoxb-fake")

    captured: dict = {}

    def fake_api_post(method, payload, *, token):
        captured["method"] = method
        captured["payload"] = payload
        captured["token"] = token
        return {"ok": True, "ts": "1700000000.000200", "channel": "C9"}

    monkeypatch.setattr(sf, "_api_post", fake_api_post)

    assert sf.post_flat("staging is down", severity="alert") is True
    assert captured["method"] == "chat.postMessage"
    assert captured["token"] == "xoxb-fake"
    payload = captured["payload"]
    assert payload["channel"] == "eng-fleet"
    # Flat post: body in top-level text, colour stripe on the attachment.
    assert payload["text"] == "staging is down"
    assert payload["attachments"][0]["color"] == sf.SEVERITY_COLOUR["alert"]


def test_post_flat_does_not_truncate_at_section_limit(monkeypatch):
    """The flat body rides in top-level ``text`` (40k ceiling), so a message
    longer than a Block Kit section (3000) must survive - the webhook path
    delivered up to ~3500, and the app path must not truncate more."""
    import slack.posting as sf

    monkeypatch.setattr(sf, "_resolve_bot_token", lambda: "xoxb-fake")
    captured: dict = {}

    def fake_api_post(method, payload, *, token):
        captured["payload"] = payload
        return {"ok": True}

    monkeypatch.setattr(sf, "_api_post", fake_api_post)

    body = "x" * 3400
    assert sf.post_flat(body, severity="info") is True
    assert captured["payload"]["text"] == body  # untouched, no "...[truncated]"


def test_post_flat_returns_false_when_api_refuses(monkeypatch):
    import slack.posting as sf

    monkeypatch.setattr(sf, "_resolve_bot_token", lambda: "xoxb-fake")
    monkeypatch.setattr(
        sf, "_api_post", lambda *a, **kw: {"ok": False, "error": "channel_not_found"}
    )
    assert sf.post_flat("hello", severity="info") is False


def test_firing_thread_root_posts_block_kit_with_role_when_set(monkeypatch):
    import slack.posting as sf

    monkeypatch.setenv("ALFRED_SENIOR_DEV_ROLE", "Single-repo feature engineer")
    monkeypatch.setattr(sf, "_resolve_bot_token", lambda: "xoxb-fake")
    monkeypatch.setattr(sf, "_get_permalink", lambda *a, **kw: None)

    captured: dict = {}

    def fake_api_post(method, payload, *, token):
        captured["method"] = method
        captured["payload"] = payload
        return {"ok": True, "ts": "1700000000.000100", "channel": "C0123"}

    monkeypatch.setattr(sf, "_api_post", fake_api_post)
    handle = sf.firing_thread_root(
        codename="senior-dev",
        firing_id="2026-05-09-1432-aa",
        summary_one_liner="firing started",
    )
    assert handle is not None
    assert handle.channel == "C0123"
    assert handle.ts == "1700000000.000100"

    # Header carries the default themed name and role for the runtime codename.
    blocks = captured["payload"]["attachments"][0]["blocks"]
    header_text = blocks[0]["text"]["text"]
    assert "Lucius (Senior developer)" in header_text
    assert "firing started" in header_text


def test_firing_thread_root_severity_drives_colour(monkeypatch):
    import slack.posting as sf

    monkeypatch.setattr(sf, "_resolve_bot_token", lambda: "xoxb-fake")
    monkeypatch.setattr(sf, "_get_permalink", lambda *a, **kw: None)

    captured: dict = {}

    def fake_api_post(method, payload, *, token):
        captured["payload"] = payload
        return {"ok": True, "ts": "1.0", "channel": "C0"}

    monkeypatch.setattr(sf, "_api_post", fake_api_post)
    sf.firing_thread_root(
        codename="senior-dev",
        firing_id="x",
        summary_one_liner="oops",
        severity="alert",
    )
    assert captured["payload"]["attachments"][0]["color"] == sf.SEVERITY_COLOUR["alert"]


def test_firing_thread_root_no_duplicate_text_in_attachment(monkeypatch):
    """PR #141-equivalent guard: the attachment must NOT carry a ``text``
    field that mirrors the top-level ``text``, otherwise Slack renders
    the body twice in the channel."""
    import slack.posting as sf

    monkeypatch.setattr(sf, "_resolve_bot_token", lambda: "xoxb-fake")
    monkeypatch.setattr(sf, "_get_permalink", lambda *a, **kw: None)

    captured: dict = {}

    def fake_api_post(method, payload, *, token):
        captured["payload"] = payload
        return {"ok": True, "ts": "1.0", "channel": "C0"}

    monkeypatch.setattr(sf, "_api_post", fake_api_post)
    sf.firing_thread_root(
        codename="senior-dev",
        firing_id="x",
        summary_one_liner="post body",
    )
    payload = captured["payload"]
    # Top-level text is the generic preview, not the post body.
    assert payload["text"] == "Alfred · senior-dev firing"
    # Attachment carries the body inside blocks, NOT in a top-level
    # attachment[].text field.
    assert "text" not in payload["attachments"][0]


def test_firing_thread_reply_returns_false_without_handle():
    import slack.posting as sf

    assert sf.firing_thread_reply(None, text="anything") is False


def test_firing_thread_reply_posts_to_thread_ts(monkeypatch):
    import slack.posting as sf

    monkeypatch.setattr(sf, "_resolve_bot_token", lambda: "xoxb-fake")

    captured: dict = {}

    def fake_api_post(method, payload, *, token):
        captured["payload"] = payload
        return {"ok": True}

    monkeypatch.setattr(sf, "_api_post", fake_api_post)
    handle = sf.ThreadHandle(channel="C0", ts="1700.0001")
    ok = sf.firing_thread_reply(handle, text="worktree created", severity="info")
    assert ok is True
    assert captured["payload"]["channel"] == "C0"
    assert captured["payload"]["thread_ts"] == "1700.0001"
    # Reply attachment: also no top-level text duplicate.
    assert "text" not in captured["payload"]["attachments"][0]


def test_firing_thread_close_summarises_outcome_duration_firing_id(monkeypatch):
    import slack.posting as sf

    monkeypatch.setattr(sf, "_resolve_bot_token", lambda: "xoxb-fake")

    captured: dict = {}

    def fake_api_post(method, payload, *, token):
        captured["payload"] = payload
        return {"ok": True}

    monkeypatch.setattr(sf, "_api_post", fake_api_post)
    handle = sf.ThreadHandle(channel="C0", ts="1700.0001")
    sf.firing_thread_close(
        handle,
        codename="senior-dev",
        firing_id="2026-05-09-aa",
        outcome="pr-opened",
        duration_seconds=125.4,
    )
    body = captured["payload"]["attachments"][0]["blocks"][0]["text"]["text"]
    assert "Lucius (Senior developer)" in body
    assert "pr-opened" in body
    assert "2m 5s" in body
    assert "2026-05-09-aa" in body


def test_home_channel_resolution(monkeypatch):
    import slack.posting as sf

    monkeypatch.delenv("SLACK_HOME_CHANNEL", raising=False)
    assert sf._home_channel() == "alfred"
    monkeypatch.setenv("SLACK_HOME_CHANNEL", "#fleet-ops")
    assert sf._home_channel() == "fleet-ops"
    # Caller-supplied wins.
    assert sf._home_channel("custom") == "custom"


def test_truncate_aggressive_with_marker():
    import slack.posting as sf

    short = sf._truncate("abc", 10)
    assert short == "abc"
    long = sf._truncate("a" * 200, 50)
    assert long.endswith("...[truncated]")
    assert len(long) == 50


def test_github_links_render_as_slack_mrkdwn():
    import slack.posting as sf

    assert (
        sf.github_issue_link("luminik-io/alfred", 113)
        == "<https://github.com/luminik-io/alfred/issues/113|luminik-io/alfred#113>"
    )
    assert (
        sf.github_url_link("https://github.com/luminik-io/alfred/pull/139")
        == "<https://github.com/luminik-io/alfred/pull/139|luminik-io/alfred#139>"
    )


# --------------------------------------------------------------------------
# Persisted roster theme honored in the Slack header label
# --------------------------------------------------------------------------


def _persist_theme(tmp_path, **payload):
    """Write a roster-theme state file under the isolated ALFRED_HOME."""
    from agent_runner.paths import STATE_ROOT
    from roster_theme_store import RosterThemeStore

    RosterThemeStore.from_state_root(STATE_ROOT).save(**payload)


def test_themed_label_default_uses_batman_theme(monkeypatch):
    import slack.posting as sf

    monkeypatch.setenv("ALFRED_SENIOR_DEV_ROLE", "Single-repo feature engineer")
    # No theme persisted: Slack renders the default theme rather than raw
    # runtime slugs or launchd role strings.
    assert sf._themed_codename_label("senior-dev") == "Lucius (Senior developer)"


def test_themed_label_preset_renders_preset_identity(tmp_path, monkeypatch):
    import slack.posting as sf

    monkeypatch.setenv("ALFRED_SENIOR_DEV_ROLE", "Fleet lead")
    # A saved preset must render the preset's themed name on Slack the same way
    # the desktop does, not fall back to the bare codename or the env role. The
    # role label is the Batman-base label the preset shares.
    _persist_theme(tmp_path, theme="justice-league")
    assert sf._themed_codename_label("senior-dev") == "Superman (Senior developer)"
    assert sf._themed_codename_label("architect") == "Batman (Architect)"


def test_themed_label_preset_transformers_differs_from_justice_league(tmp_path, monkeypatch):
    import slack.posting as sf

    _persist_theme(tmp_path, theme="transformers")
    assert sf._themed_codename_label("senior-dev") == "Ironhide (Senior developer)"
    assert sf._themed_codename_label("architect") == "Optimus Prime (Architect)"


def test_themed_label_preset_unknown_codename_falls_back(tmp_path, monkeypatch):
    import slack.posting as sf
    from agent_runner.metadata import codename_with_role

    _persist_theme(tmp_path, theme="transformers")
    # A codename the preset does not name keeps the shipped rendering.
    assert sf._themed_codename_label("nobody") == codename_with_role("nobody")


def test_themed_label_custom_name_and_role_applied(tmp_path, monkeypatch):
    import slack.posting as sf

    monkeypatch.setenv("ALFRED_ARCHITECT_ROLE", "Fleet lead")
    _persist_theme(
        tmp_path,
        theme="custom",
        custom_names={"batman": "Sherlock"},
        custom_roles={"batman": "Lead detective"},
    )
    assert sf._themed_codename_label("batman") == "Sherlock (Lead detective)"


def test_themed_label_custom_name_falls_back_to_batman_base_role(tmp_path, monkeypatch):
    import slack.posting as sf

    monkeypatch.setenv("ALFRED_ARCHITECT_ROLE", "Fleet lead")
    # Custom name set, but no custom role: the desktop shows the base-theme role
    # label (``Architect``), NOT the ``ALFRED_ARCHITECT_ROLE`` env label, so the
    # Slack path must match it rather than diverging to ``Fleet lead``.
    _persist_theme(tmp_path, theme="custom", custom_names={"architect": "Sherlock"})
    assert sf._themed_codename_label("architect") == "Sherlock (Architect)"


def test_themed_label_custom_without_name_uses_batman_base_name_and_role(tmp_path, monkeypatch):
    import slack.posting as sf

    monkeypatch.setenv("ALFRED_SENIOR_DEV_ROLE", "Engineer")
    # A custom theme that did not name THIS agent must still match the desktop,
    # which shows the Batman-base name (``Lucius``) and the Batman-base role
    # label (``Senior developer``), not the bare codename or the env role.
    _persist_theme(tmp_path, theme="custom", custom_names={"architect": "Sherlock"})
    assert sf._themed_codename_label("senior-dev") == "Lucius (Senior developer)"


def test_themed_label_custom_unknown_codename_keeps_shipped_behavior(tmp_path, monkeypatch):
    import slack.posting as sf
    from agent_runner.metadata import codename_with_role

    monkeypatch.setenv("ALFRED_MYSTERY_BOT_ROLE", "Wildcard")
    # A codename outside the Batman base (no desktop persona) is left as shipped:
    # the env role still applies, since the desktop has no base label for it.
    _persist_theme(tmp_path, theme="custom", custom_names={"batman": "Sherlock"})
    assert sf._themed_codename_label("mystery-bot") == codename_with_role("mystery-bot")


def test_themed_label_escapes_slack_markup(tmp_path, monkeypatch):
    import slack.posting as sf

    # The label lands in a mrkdwn message body, so an operator-authored name like
    # ``<!channel>`` or ``<@U123>`` must not render as a broadcast or a mention.
    # The visible text is preserved through HTML entities Slack decodes on display.
    _persist_theme(
        tmp_path,
        theme="custom",
        custom_names={"batman": "<!channel> & <@U123>"},
        custom_roles={"batman": "<lead>"},
    )
    label = sf._themed_codename_label("batman")
    assert "<!channel>" not in label
    assert "<@U123>" not in label
    assert label == "&lt;!channel&gt; &amp; &lt;@U123&gt; (&lt;lead&gt;)"


def test_themed_label_default_theme_resolves_slug_to_batman_cast_name(monkeypatch):
    import slack.posting as sf

    # After the role-slug rename the codename is a slug (``senior-dev``). With no
    # theme persisted the default Batman theme must still render the Batman-cast
    # name and its base role, NOT the bare slug, so the Slack post reads the same
    # as it did before the rename.
    assert sf._themed_codename_label("senior-dev") == "Lucius (Senior developer)"
    assert sf._themed_codename_label("architect") == "Batman (Architect)"


def test_themed_agent_name_default_theme_is_batman_cast_bare_name(monkeypatch):
    import slack.posting as sf

    # ``themed_agent_name`` is the bare-name resolver (no role suffix) the Slack
    # assignment lane and CLI status table use. Default theme -> Batman-cast name.
    assert sf.themed_agent_name("senior-dev") == "Lucius"
    assert sf.themed_agent_name("architect") == "Batman"


def test_themed_agent_name_preset_and_custom(tmp_path, monkeypatch):
    import slack.posting as sf

    _persist_theme(tmp_path, theme="transformers")
    assert sf.themed_agent_name("senior-dev") == "Ironhide"
    assert sf.themed_agent_name("architect") == "Optimus Prime"

    _persist_theme(tmp_path, theme="custom", custom_names={"architect": "Sherlock"})
    assert sf.themed_agent_name("architect") == "Sherlock"
    # A custom theme that did not name this agent keeps its Batman-base name.
    assert sf.themed_agent_name("senior-dev") == "Lucius"


def test_themed_agent_name_unknown_codename_returns_raw(monkeypatch):
    import slack.posting as sf

    # A codename outside the known fleet has no theme name; keep the bare slug so
    # the caller still prints something (a custom agent falls here).
    assert sf.themed_agent_name("release-captain") == "release-captain"


def test_escape_mrkdwn_neutralizes_markup_and_entities():
    import slack.posting as sf

    ZWSP = "​"
    # Slack entity chars become HTML entities so a mention/broadcast/link can't fire.
    assert sf.escape_mrkdwn("<@U123> & <!channel>") == "&lt;@U123&gt; &amp; &lt;!channel&gt;"
    # Formatting markup chars keep their glyph but gain a zero-width space so they
    # cannot pair into bold/italic/strike/code.
    assert sf.escape_mrkdwn("*Boss*") == f"*{ZWSP}Boss*{ZWSP}"
    assert sf.escape_mrkdwn("_x_ ~y~ `z`") == f"_{ZWSP}x_{ZWSP} ~{ZWSP}y~{ZWSP} `{ZWSP}z`{ZWSP}"
    # A plain label is returned unchanged (no markup, no entities).
    assert sf.escape_mrkdwn("Optimus Prime") == "Optimus Prime"


def test_themed_agent_name_stays_raw_for_non_slack_surfaces(tmp_path, monkeypatch):
    import slack.posting as sf

    # ``themed_agent_name`` feeds the plain-text CLI too, so it must NOT escape:
    # the raw operator name comes back verbatim. Slack callers escape themselves.
    _persist_theme(tmp_path, theme="custom", custom_names={"architect": "*Boss* <@U123>"})
    assert sf.themed_agent_name("architect") == "*Boss* <@U123>"
    assert "​" not in sf.themed_agent_name("architect")


def test_themed_agent_role_uses_custom_role_overlay(tmp_path, monkeypatch):
    import slack.posting as sf

    # A custom theme's per-agent role label wins over the manifest default, so the
    # role a surface renders matches what the operator set on the desktop.
    _persist_theme(
        tmp_path,
        theme="custom",
        custom_names={"architect": "Sherlock"},
        custom_roles={"architect": "Lead detective"},
    )
    assert sf.themed_agent_role("architect") == "Lead detective"
    # An agent the custom theme did not re-role keeps its Batman-base role label.
    assert sf.themed_agent_role("senior-dev") == "Senior developer"


def test_themed_agent_role_unknown_codename_is_none(monkeypatch):
    import slack.posting as sf

    # No theme persisted (default Batman) and a codename outside the fleet: no
    # themed role, so the caller keeps its own fallback.
    assert sf.themed_agent_role("release-captain") is None
