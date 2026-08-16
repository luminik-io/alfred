# Desktop app guide

Alfred Desktop (`clients/desktop`) is the recommended native Mac/Linux installer,
onboarding path, and control surface for a local Alfred fleet. The core fleet and
CLI also run without it. First-run onboarding can install or repair Alfred core,
detect an existing install, deploy the built-in fleet, and install starter
engineering skills. It can also check code memory, start the runtime, verify
authentication, select repositories, and configure appearance and roster names.
After onboarding, those controls remain available in Settings.

Slack remains Alfred's collaboration surface. The desktop app handles local
installation, onboarding, status, and repair. It shows decisions, waiting plans,
run failures, memory candidates, and safe local actions. It uses the same local
runtime. It is not a second scheduler or a hosted service.

For the JSON API, see [`SERVE.md`](SERVE.md). This guide explains the desktop
structure, each tab, and the installer build process.

## Decision

Use the native Mac/Linux app as the recommended local installer and control
surface. Do not make it a second scheduler or a hosted runtime.

Slack remains the primary collaboration UI because it already has threads, reactions, search, mobile push, and shared context. The native client makes Alfred easier to set up, trust, and repair: install detection, dependency checks, auth, repo selection, full-fleet roster seed, roster themes, custom display names, health, logs, approvals, memory review, safe pause/resume, dry-run launch, and recovery.

The client reads and writes through the same local APIs, state files, and CLI commands Alfred already uses. You can run Alfred with or without the client, and Slack remains the collaboration surface.

## Product principles

- One source of truth: `$ALFRED_HOME`, GitHub, Slack threads, and the local fleet brain.
- Direct host access: local HTTP on `127.0.0.1` for same-machine installs, SSH for remote Linux hosts. No public port, no relay, no sync service.
- Slack-native collaboration: every plan, approval, rejection, and follow-up should have a Slack thread link where discussion happened.
- Actionable, not chatty: the home screen answers "what needs me right now?" before showing logs or historical data.
- Explain before acting: write actions show the target, expected effect, and rollback path before running. The underlying command is audit detail, not the primary interface.
- Accessible to technical and non-technical users: product language first, shell commands second. The app can reveal details without making them the default.

## The control surface

The app is a Tauri shell around a React UI. It opens on Inbox and keeps primary navigation to six everyday work surfaces:

| Tab | What it shows | What it can do |
|---|---|---|
| **Inbox** | The decision queue: blocked plans, follow-ups, stale workers, repeated failures, memory candidates, recent runs, and the capacity rail for Claude and Codex subscription headroom (backed by the live `GET /api/usage` endpoint). | Draft work, refresh state, pause or resume scheduled firings through the native allowlist, and jump to the right surface. |
| **Ask** | Plain-language planning intake backed by the same readiness engine as Slack. | Draft or refine a plan before it is converted into an issue or spec. |
| **Work** | The Kanban board: Queued / Working now / Shipped, saved plans, Slack follow-ups, and local draft actions. | Queue an issue, hold work, mark work done, convert follow-ups, or inspect saved detail in-app. |
| **Code** | The local code-map catalog and a bounded impact view for one file: direct dependents, dependencies, symbols, contract surfaces, contract drift, nearby files, and recommended checks. | Select an indexed repository, analyze a file path without sending source away, and inspect the evidence Alfred should use before changing it. |
| **Agents** | The agent roster, activity feed, latest-run inspector, and memory learning queue. | Pause, resume, run once, dry-run a codename, promote or reject memory candidates, and inspect firing traces. |
| **Settings** | Runtime repair and configuration after first-run onboarding: existing install inventory, auth, repos, engine checks, capability checks, roster naming, Slack collaborators, and appearance. | Install or repair Alfred core, start or reconnect the local runtime, run curated checks in-app, change repository scope, choose an appearance and mode, choose a roster theme, set custom display names, and manage trusted Slack collaborators. |

Plans carry their origin so the Slack collaboration trail stays visible while the app keeps a clean local draft inbox.

### Inbox in detail

Inbox is the first screen: a decision queue and local command center.

- fleet health
- Claude and Codex subscription headroom on the capacity rail (backed by the live `GET /api/usage` endpoint)
- pending approvals
- blocked plans
- stale workers
- repeated failures
- memory candidates ready for review
- Slack listener health and newly saved planning drafts
- safe actions: refresh, pause all, resume all, open Slack thread, open PR, and run the memory doctor

The top row should feel like an operations status strip, not a dashboard full of vanity metrics.

### Ask in detail

Ask owns plain-language intake and the planning inbox. Plan cards show:

- parent issue and Slack thread
- readiness verdict
- affected repos and rollout order
- open questions
- latest revision summary
- source: local form, Slack DM, app mention, or registered thread
- approve/reject status
- PR chain after execution starts

The app can help draft or refine a spec, but the final collaboration loop stays in Slack. Any "send to Alfred" action posts to or links back to the approval thread. A locally drafted single-repo issue lands behind an approval gate (`agent:plan-pending-approval`) and is held from autonomous pickup until you approve it, so nothing single-repo ships without a go-ahead.

### Agents in detail

Agents is the operational surface. The Workflow view is the default. It shows
roles in delivery lanes and labels the handoffs between them. The operator
approval handoff has a distinct dashed edge and text label.

A Workflow / List control stores the selected view in local storage. The List
view uses a responsive card grid for the full roster. Both views open agent
details in a drawer, so they do not reserve a permanent inspector column. The
view control uses labelled buttons with `aria-pressed`. Motion respects
`prefers-reduced-motion`.

Per-agent controls: enabled/paused state, schedule, last run, last failure, dry-run, pause/resume, run once, clear stale lock with proof, and open prompt/config files. Every destructive or state-changing action has a dry-run preview where the CLI supports one.

The Agents activity feed combines in-app notifications and firing timelines for forensics: timeline by firing, step-level run events (plan created, worktree created, pre-push checks passed, branch pushed, PR opened) emitted the moment the underlying action succeeds, the engine used, the worktree path, issue and PR links, the event log, the transcript link when present, and the final status with the next recommended action. The feed reads without horizontal scrolling on narrow screens.

### Memory review

Memory review is reviewable and appears where you are already working:

- Inbox surfaces memory candidates ready for review
- Ask recalls promoted planning hints beside drafts
- candidate promote/reject, the memory doctor, Redis status, a Redis sync preview, and the repeated-failure harvest are available in-app
- Setup keeps memory, code-memory, and Redis checks available as repair actions
- Slack exposes `memory`, `remember`, `memory remember`, `memory promote`, `memory reject`, `memory redis`, and `memory sync`

The app visibly separates promoted lessons from candidates and raw logs.

### Settings and first-run onboarding

First-run onboarding takes over the window without the normal sidebar. It discovers existing Alfred files, installs or repairs bundled Alfred core, seeds the full built-in runtime roster, deploys the CLI and agents into `~/.alfred`, installs starter engineering skills, checks code memory, starts or reconnects the runtime, verifies GitHub auth and engine CLIs, selects watched repos, and configures roster names. After onboarding, the same repair and configuration controls live under Settings. Repo-scoped agents stay idle until repositories are saved, and the `architect` role (Batman in the default theme) stays idle until `ARCHITECT_PARENT_REPO` is configured. Failures tell you what Alfred checked, why it matters, and the smallest next step.

Onboarding has a chat path and a form path. Both paths use the same setup
handlers and write the same configuration. In chat, Alfred asks one setup
question and proposes the next action. Only the engine check starts without a
button click. All other proposed actions wait for the operator to select their
button. A configuration change also waits for approval.

If no engine is ready, the chat path returns to the setup form. The roster theme
builder is a separate chat. It proposes role names, which the operator can edit
and save in the theme editor. See [`ONBOARDING.md`](ONBOARDING.md) for the full
sequence.

Settings separates visual appearance from roster naming. Appearance selects
Signal Edge, The Category Standard, or Linked Fold. A separate control selects
light or dark mode. Roster themes only change the names shown for roles.

## How it talks to the fleet

The client reads the fleet's own state over the `alfred serve` JSON API and runs a small set of safe local actions through a native command allowlist. It opens no public port, and `$ALFRED_HOME` remains the single source of truth.

- **Read path.** The UI loads `/api/status`, `/api/actions`, `/api/usage`, `/api/memory/candidates`, `/api/firings`, `/api/plans`, and `/api/slack/trusted-users` from `alfred serve`. In the desktop shell these go through a Tauri command (`fetch_alfred_json`) that only allows Alfred JSON API paths on `http://localhost`, `http://127.0.0.1`, or `http://[::1]`.
- **Local actions.** State-changing controls use a narrow native allowlist: install or repair Alfred core, start the local runtime, fleet status, list agents, auth status, brain doctor, code-memory doctor and index, starter skills install, Redis status, Redis sync preview, memory harvest, safe agent dry-runs, pause, resume, run once, local memory review endpoints (`promote`, `reject`), local follow-up planning endpoints (`convert-followup`, `mark-handled`), and local Slack collaborator edits. There is no arbitrary shell execution. Each action surfaces the result and command audit detail.
- **Outside links.** Slack and GitHub links open outside the app through Tauri's opener plugin. Local Alfred plans and firings stay in the native inspector panes.

When run in a plain browser (development preview), the app stays read-only: native actions are unavailable and only the JSON read path works.

## Usage on the capacity rail

The Inbox capacity rail shows real Claude and Codex subscription headroom for the rolling 5-hour and weekly windows. The figures come from `GET /api/usage`, which reads the engines' own local CLI state files on the host. Alfred drives Claude Code and Codex through their local subscription CLIs rather than API keys, so there is no billing API to query and no per-token dollar figure (it is meaningless under a Max or Pro subscription). A window the local state cannot confirm reads as not synced rather than a fabricated number. The same numbers are available from the command line with `alfred usage`. See [`SERVE.md`](SERVE.md) for the endpoint and [`CLI.md`](CLI.md) for the CLI.

## Run it locally

Install or repair core during onboarding or from Settings, then start the runtime there. For source development, you can run the same port manually:

```sh
alfred serve --port 7010 --no-browser
```

The app uses `7010` because macOS can reserve `7000` for Control Center. Settings lets you point the client at a custom localhost URL when needed, and the app uses that configured URL exactly.

Then run the desktop shell:

```sh
cd clients/desktop
npm install
npm run tauri dev
```

The client defaults to `http://127.0.0.1:7010`.

## Build native installers

`clients/desktop/src-tauri/tauri.conf.json` builds the native installer for the host platform:

```sh
cd clients/desktop
npm install
npm run tauri -- build
```

| Host | Artifacts |
|---|---|
| macOS 11+ on Apple silicon | `.app` and `.dmg` |
| Linux | `.AppImage` and `.deb` |

Continuous integration builds the client with `--no-bundle` to prove the native binary compiles without requiring code signing, DMG packaging, or Linux package artifacts:

```sh
npm run tauri -- build --no-bundle --ci
```

The public release workflow creates the draft release. Signed and notarized macOS assets, plus Linux AppImage and Debian packages, are attached before that release is published. Local `tauri build` still works when you need to inspect or test the installer output yourself.

## Checks

```sh
cd clients/desktop
npm run typecheck
npm run build
source "$HOME/.cargo/env"
cargo fmt --manifest-path src-tauri/Cargo.toml --check
cargo test --manifest-path src-tauri/Cargo.toml
npm run tauri -- build --no-bundle --ci
```

## Plain mode

The desktop Ask box can use plain-language intake when the runtime has
`ALFRED_INTAKE_PROFILE=plain`. A non-technical user enters a request, answers
one or two questions, and approves a plan. The structured draft and all later
gates stay the same. See [`PLAIN_MODE.md`](PLAIN_MODE.md).

## API shape to stabilize next

The client uses these local API contracts today:

```text
GET  /api/status
GET  /api/schedule
GET  /api/actions
GET  /api/shipped
GET  /api/usage             # served; backs the capacity rail
GET  /api/usage/providers   # served; flat per-engine re-projection of /api/usage
GET  /api/firings
GET  /api/firings/{firing_id}
GET  /api/firings/{firing_id}/tail
GET  /api/plans
GET  /api/plans/drafts
GET  /api/plans/{plan_id}
POST /api/plans/{plan_id}/convert-followup
POST /api/plans/{plan_id}/mark-handled
POST /api/plans/{plan_id}/discard
POST /api/plans/{plan_id}/decision
POST /api/plans/{plan_id}/file-issue
POST /api/plans/draft
POST /api/compose/converse
POST /api/compose/converse/stream
POST /api/onboarding/converse
POST /api/theme-builder/converse
POST /api/roster-theme
GET  /api/memory/candidates
POST /api/memory/candidates/{id}/promote
POST /api/memory/candidates/{id}/reject
POST /api/queue
GET  /api/setup/status
GET  /api/setup/repos
POST /api/setup/repos
GET  /api/setup/playbooks
POST /api/setup/playbook
POST /api/setup/demo
POST /api/setup/demo/clear
GET  /api/slack/trusted-users
POST /api/slack/trusted-users
POST /api/slack/trusted-users/{user_id}/remove
POST /api/conversation/control
```

`GET /api/usage` is served by `alfred serve` today and backs the capacity rail. It reports your real Claude and Codex subscription headroom for the rolling 5-hour and weekly windows, read from the engines' own local CLI state files on the host.

`GET /api/setup/status` requires the local action token because it runs GitHub and engine probes. The native bridge attaches the token. The response includes `first_run`, the onboarding go/no-go contract for the first real local workflow. Required rows cover GitHub auth, a working default engine route, repo scope, queue coverage, local checkout mapping, scheduled fleet deployment, and the Desktop action token. Recommended rows cover code graph memory, Alfred's built-in context governor, and engineering skill packs. Optional rows cover the architect parent repo and Slack collaboration. Settings keeps recommended upgrades visible after onboarding without blocking a basic run.

`GET /api/usage/providers` is also served by `alfred serve` (a flat per-engine re-projection of `/api/usage`), and the same usage numbers are available from the command line with `alfred usage`.

The native client also has a narrow local command allowlist:

```text
alfred serve --port <port> --no-browser
alfred status --json
alfred agents
alfred enabled-agents
alfred auth status
alfred dry-run <codename>
alfred brain doctor --json
alfred brain redis-status --json
alfred brain redis-sync --dry-run --json
alfred brain harvest --apply --json
```

Broader write endpoints should come next, behind command previews:

```text
GET  /api/agents
GET  /api/agents/{codename}
POST /api/agents/{codename}/dry-run
POST /api/agents/{codename}/pause
POST /api/agents/{codename}/resume
GET  /api/doctor
POST /api/doctor/run
```

Write endpoints should return a command preview, a result, and the path or state file they changed.

## Native shell recommendation

Stay on Tauri for Mac and Linux. It keeps the app small, lets the UI reuse the existing site design tokens, and does not force Alfred into a bundled Node/Electron runtime. Electron remains a fallback only if terminal embedding or OS integration becomes the deciding constraint.

Distribution sequence:

1. `alfred serve` read APIs plus local follow-up action contracts with tests. Done.
2. Tauri shell with Inbox, Ask, Work, Code, Agents, Settings, first-run onboarding, safe local follow-up actions, runtime launch, status, pause/resume/run controls, memory checks, candidate promote/reject, Redis status, Redis sync preview, failure-pattern harvest, and dry-run launch. Done.
3. Guided install, signed update flow, and broader safe write actions with dry-run previews.
4. Signed Mac builds and Linux AppImage/deb artifacts. Done.

## UI direction

The desktop uses the two-axis system in
[`THEME_SYSTEM.md`](THEME_SYSTEM.md):

- Signal Edge is the default appearance.
- The Category Standard and Linked Fold are the other shipped appearances.
- Light and dark are separate modes within each appearance.
- Components use semantic tokens from `clients/desktop/src/styles/tokens.css`.
- Liquid-glass material is for chrome, dialogs, popovers, inspectors, and other
  elevated surfaces.
- Dense lists, lifecycle columns, logs, and tables use flat surfaces.
- Health states use the shared ok, warning, and error meanings.
- Workflow edges distinguish normal handoffs from operator approval. They use
  line style and text as well as color.
- Repeated cards must not become nested card stacks.
- Narrow layouts become one column. The roster becomes a responsive card grid,
  and Settings keeps full tab labels at phone widths.
- Phone-width controls have a 36-pixel minimum height.
- Every animation has a reduced-motion path.
- GitHub and Slack links open outside the app.

Read [`DESIGN.md`](DESIGN.md) for typography, glass, responsive behavior, and
accessibility rules.
