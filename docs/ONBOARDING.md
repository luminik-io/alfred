# Setting Alfred up

There are two ways to set up a local Alfred fleet, and they end in the same
place. Pick whichever feels natural.

- **Chat with Alfred.** Type what you want in plain English and Alfred walks you
  through setup one question at a time, proposing each step as you go.
- **Step through the form.** Work through the guided Setup screens: engine
  check, GitHub, repositories, team names, schedule.

Both paths run the same underlying setup handlers, so they can never drift.
Whatever you can do by chatting, you can do by stepping, and the config that
lands is identical.

## The conversational path

The desktop app's Setup screen can hold a conversation. You say something like
"I want Alfred watching my API and web repos", and Alfred replies with the next
setup question. Each reply is one turn: a short question plus, when it is ready,
a proposed next action.

Under the hood every turn calls `POST /api/onboarding/converse`. Alfred reads
the conversation so far, asks the next question, and names one action for the
client to run. The model never writes config, never writes a token, and never
deploys. It only proposes the next step; the client carries it out.

### The actions Alfred can propose

Alfred can only propose actions from a fixed, short list. Anything outside this
list is dropped, so a stray phrase in your message can never trigger something
unexpected.

| Action | What it does | Auto-proceeds? |
|---|---|---|
| `check_engine` | Shows whether your Claude and Codex CLIs are ready. | Yes, read-only |
| `connect_github` | Starts the GitHub sign-in flow. | No, click to run |
| `set_repos` | Scopes the fleet to the repositories you named. | No, click Approve |
| `pick_agents` | Records which roles you want surfaced. | No, click Approve |
| `propose_theme` | Suggests names for your team (the theme builder). | No, click to preview |
| `save_theme` | Saves your chosen team names. | No, click Approve |
| `set_schedule` | Sets how often the fleet runs (off, hourly, daily, weekly). | No, click Approve |
| `finish_setup` | Ends the guided flow and routes you to the board. | No, click Approve |

### The approval gate

The split is deliberate, and the desktop client is conservative about it. Only
`check_engine`, a pure read of your local CLI status, auto-proceeds, because it
changes nothing and reading it straight away keeps the flow smooth. Every other
proposed action, including starting the GitHub sign-in and previewing a theme,
parks behind a labeled button you click before it runs. Nothing that touches
config, such as saving repos, saving a theme, or setting a schedule, happens
without that click. Alfred proposes; you decide.

Because the conversational path reuses the same setup handlers as the stepped
form, every action passes through the same human gate a stepped write would.
There is no faster, quieter path to changing your config just because you typed
it in chat.

### When the chat is not available

The conversation needs a model engine configured. If none is, or the engine
cannot be reached, the desktop app falls back to the stepped Setup form so you
are never stuck. A one-off garbled reply from the model is treated as a hiccup:
Alfred keeps the chat open and asks you to say it again, rather than dropping you
to the form.

Set `ALFRED_ONBOARDING_ENGINE` to choose the engine for onboarding
specifically, or let it inherit the shared conversation engine.

## Building your team by chatting: the theme builder

Naming your team is its own small conversation, and you can reach it from
onboarding or on its own. Describe the vibe you want and Alfred proposes a full
set of names, one per role.

Each turn calls `POST /api/theme-builder/converse`. Alfred asks "what vibe?",
then proposes a complete role-to-name mapping as a `propose_theme` action. You
tweak the names in the theme editor and save. Nothing here saves on its own: the
model only proposes, and the save is the same `POST /api/roster-theme` write the
manual editor uses, behind the same approval gate.

A proposal is only offered once it names every core engineering role. The ops
and release jobs are optional and fall back to their default theme names, so a
small theme that only casts the core team is still complete. Names have to be
distinct: Alfred will not propose two roles sharing one name.

Set `ALFRED_THEME_BUILDER_ENGINE` to choose the engine for the theme builder
specifically, or let it inherit the shared conversation engine. If no engine is
configured, the desktop app falls back to the manual theme editor.

For the identity model behind all of this (roles are canonical, themes supply
display names), see [`IDENTITY_AND_THEMES.md`](IDENTITY_AND_THEMES.md).

## The stepped path

If you prefer the form, the Setup screen walks the same steps in order: find any
existing install, install or repair Alfred core, check the engine CLIs, sign in
to GitHub, scope repositories, name the team, set a schedule, and finish. Each
step calls the same handler the conversational path proposes. See
[`DESKTOP_CLIENT.md`](DESKTOP_CLIENT.md) for the full tab-by-tab tour.

## See also

- [`IDENTITY_AND_THEMES.md`](IDENTITY_AND_THEMES.md): roles, themes, and how
  names resolve.
- [`CONVERSATION.md`](CONVERSATION.md): talking to Alfred after setup, in Slack
  and the desktop Ask.
- [`DESKTOP_CLIENT.md`](DESKTOP_CLIENT.md): the desktop app and the Setup screen.
- [`PLAIN_MODE.md`](PLAIN_MODE.md): the non-technical intake profile for everyday
  work.
