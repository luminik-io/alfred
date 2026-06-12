# Alfred Design Spec

Canonical product design for the Alfred native client. This is the source of truth other sessions implement against. It defines the soul, the personas, the object model, the information architecture, the visual system, every screen, the full first-run journey, and a phased implementation plan tied to the live API.

Status: canonical draft, ready for Phase 1.
Scope: `clients/desktop` (Tauri + React + shadcn/Tailwind 4). Data contract: `lib/server/views.py` and `clients/desktop/src/types.ts`.
Rule: no code is changed by this document. It is read, then implemented.

---

## 1. The soul

**Wake up to shipped work you can trust.**

Alfred is a fleet of autonomous engineering agents that runs your own Claude Code and Codex subscriptions overnight and ships real GitHub work. The product is not a control panel. The hero is the morning-after story: what shipped, what needs you, what failed honestly. Everything else is depth you pull toward, not noise pushed at you.

Three promises the design must keep on every surface:

1. **Plain words first.** A non-technical founder must understand what happened without knowing git, specs, labels, or readiness scores. Jargon is opt-in depth, never the default surface.
2. **Honest status.** Idle is never shown as running. An `llm-error` run is never counted as fine. If the data is missing, we say so. We never fabricate a number, a status, or an outcome.
3. **One lifecycle.** Every object in the product is the same object at a different stage. There is one mental model to learn, and the whole app is a set of views onto it.

What we learned from the quality bar:

- **wuphf** (`github.com/nex-crm/wuphf`): "If it feels like a hidden agent loop, something is wrong. If it feels like The Office, you're exactly where you need to be." Agents are visible team members with names and a shared wiki. Work and knowledge are surfaced as events, never buried in an API. We borrow: agents as named characters, a promotion pipeline from raw observation to durable lesson, and onboarding that seeds the workspace so the user lands oriented, not empty.
- **fabro** (`github.com/fabro-sh/fabro`): workflows as version-controlled graphs, **human approval gates** (the hexagon node that pauses execution), git checkpoints at every stage, and a durable SSE event stream you can query to understand exactly what happened. The middle path between micromanaging every step and blindly trusting a 50-file diff. We borrow: the approval gate as a first-class lifecycle stage, evidence at every step, and an honest event stream as the backbone of the run view.

Both products feel coherent for the same reason: **one object, one lifecycle, every screen a view of it.** That is the discipline we adopt.

---

## 2. Core object model

One object travels a five-stage lifecycle. Every screen is a view of this object at one or more stages. Any screen that is not a view of this lifecycle is deleted.

```
Request  ->  Plan  ->  Run  ->  Shipped  ->  Lesson
(intent)    (proposal)  (execution)  (outcome)   (learning)
 plain      needs        live         plain       what the
 words      approval     evidence     language    fleet learned
```

| Stage | What it is | Persona-facing name | Primary API source |
|---|---|---|---|
| **Request** | Operator intent, captured from plain words. The thing you asked for. | "A request" / "what you asked for" | `POST /api/compose/converse`, `POST /api/plans/draft` |
| **Plan** | The agent's proposal for how to do it. Needs a go/no-go before any code moves. This is fabro's approval gate. | "A plan to approve" | `GET /api/plans`, `POST /api/plans/{id}/decision` |
| **Run** | Execution in progress, with live evidence (transcript, events, checkpoints). | "Working now" | `GET /api/firings`, `GET /api/firings/{id}/tail`, `GET /api/status` |
| **Shipped** | A merged outcome, described in plain language. The proof. | "What shipped" | `GET /api/shipped` |
| **Lesson** | What the fleet learned from the work, promoted from a raw observation. This is wuphf's notebook-to-wiki promotion. | "What the fleet learned" | `GET /api/memory/candidates`, promote/reject routes |

**Correlation key.** The lifecycle is stitched by `repo#number` (the GitHub issue ref) and, before an issue exists, by the compose `draft_id`. `types.ts` already models this as `RequestThreadModel` with a `correlationApproximate` flag. We keep that honesty: when a stage cannot be confirmed, it renders as "not yet" or "missing", never as a fabricated state.

**Agents are the cast, not a stage.** Batman (Architect, plans), Lucius (Senior Developer, ships), and the rest are named workers who act on the lifecycle. They appear as attribution on cards ("Lucius shipped this") and have a depth surface (Fleet) for the Dev persona. They are never the primary IA, because Maya does not think in agents, she thinks in outcomes.

---

## 3. Personas and JTBD

Two first-class personas. The IA serves Maya by default and reveals Dev's depth progressively. Never the reverse.

### Maya, non-technical founder / designer
Does not know git, specs, branches, or labels. Has a real instance (the operator's wife). She thinks in outcomes: "I want the signup page to stop losing people." She needs to describe an outcome in plain words, see what the fleet shipped in plain words, and approve or decline with confidence. She must complete onboarding without ever opening a terminal.

### Dev, senior engineer
Wants depth on demand: live transcripts, the event stream, schedules, quota headroom, engine routing, failure patterns, logs. Dev is happy with shortcuts (paste a server URL, run a CLI check) and wants nothing hidden, but does not want the depth shoved in Maya's face.

### Jobs to be done

Written as: When [situation], I want [motivation], so I can [outcome].

**Install and onboard**
- (Maya) When I first open Alfred, I want to be walked through connecting it step by step without a terminal, so I can get to a working setup without asking my engineer for help.
- (Dev) When I first open Alfred, I want to paste my local server URL or run a CLI check and skip the hand-holding, so I can be productive in under a minute.

**Connect repositories**
- (Maya) When I am asked which projects Alfred can touch, I want to pick from a plain list of my repos with descriptions, so I can choose confidently without reading slugs I do not understand.
- (Dev) When I connect repos, I want to reuse my existing `gh` session and multi-select fast, so I do not re-authenticate or retype scopes Alfred already knows.

**Express intent (outcome to spec to issue, without knowing those words)**
- (Maya) When I have a problem to solve, I want to describe the outcome in my own words and have Alfred ask me a couple of plain questions, so I can hand off a clear request without writing a spec.
- (Dev) When I know exactly what I want, I want to write a tight brief and have it become a filed issue with acceptance criteria, so I skip the conversational coaching.

**Approve plans**
- (Maya) When Alfred proposes a plan, I want a plain summary of what it will do and a clear Approve or Decline, so I can give the go-ahead and know nothing moves until I do.
- (Dev) When I review a plan, I want to read the full scope, affected repos, and the readiness signal, so I can approve with confidence or send it back.

**Follow progress**
- (Maya) When work is underway, I want to see in plain words that something is happening and roughly how far along it is, so I am not anxious about whether it stalled.
- (Dev) When a run is live, I want the transcript and event stream tailing in real time, so I can catch a wrong turn early and steer.

**See what shipped overnight**
- (Maya) When I start my day, I want a short list of what shipped described as outcomes, so I can see the value without reading PR titles or diffs.
- (Dev) When I review overnight work, I want each shipped item linked to its merged PR and evidence, so I can verify and merge follow-ups.

**Teach the fleet (lessons)**
- (Maya) When the fleet learns something worth keeping, I want to approve it in one tap with a plain explanation, so the team gets smarter without me managing a knowledge base.
- (Dev) When a lesson candidate appears, I want to see its evidence, severity, and source run, so I can promote, edit, or reject it accurately.

**Recover from failures**
- (Maya) When something failed, I want an honest, calm explanation and one clear next step, so I am not scared and I know what to do.
- (Dev) When a run errors, I want the failure pattern, the exact error, and a one-click retry or escalation, so I can fix the root cause fast.

**Tune schedules and engines**
- (Maya) Maya does not do this. The defaults must be safe and self-explaining so she never needs to.
- (Dev) When I want to change cadence or which engine an agent uses, I want clear controls with safe confirms on disruptive actions, so I can tune the fleet without breaking it.

---

## 4. Information architecture

### Principle
The current nav is Inbox / Ask / Work / Agents / Setup, with four "operator-depth" surfaces (Plans, Lessons, Roster, Activity) crammed as subtabs inside Agents. The problem is not the count, it is that the nav is organized by **screen type** instead of **lifecycle stage**, and the most important surfaces (Plans, Lessons) are buried two levels deep where Maya will never find them.

The new IA is organized by the lifecycle. Five primary destinations, each a view of the object:

| New nav | Lifecycle view | Replaces / absorbs | Primary persona |
|---|---|---|---|
| **Home** | The morning-after story: what shipped, what needs you, what failed. A digest across all stages. | current Inbox (`review`) | Maya |
| **Ask** | Create a Request. Conversational, plain-mode by default. | current Ask (`compose`) | both |
| **Pipeline** | The lifecycle board: Requests, Plans awaiting you, Runs in flight, Shipped. One object across columns. | current Work (`board`) + Plans subtab, merged | both |
| **Fleet** | Agent depth: roster, schedules, live activity stream, quotas, engine routing, failure patterns. | current Agents page (Roster + Activity subtabs) | Dev |
| **Lessons** | What the fleet learned. Promote, reject, browse. | current Lessons subtab | both |

Settings moves out of the primary rail into a top-bar affordance (gear), because it is a destination you visit rarely, not a stage you live in. Onboarding is a full-screen takeover before the app is usable, not a nav item.

**Why Plans is promoted, not a subtab.** Approving a plan is the single most important action Maya takes. It is the human-in-the-loop gate (fabro's hexagon). Burying it inside Agents > Plans is the reason the current Plans page is unloved and full of junk. In the new IA, plans awaiting approval surface in three places: a count on Home ("2 need you"), a dedicated column in Pipeline, and the command palette. They are never more than one click away.

**Why Work and Plans merge into Pipeline.** They are the same object at adjacent stages. A Plan that gets approved becomes a queued issue becomes a Run becomes a Shipped card. Showing them on separate screens forces the user to mentally stitch a lifecycle the product should stitch for them. Pipeline is the stitched view. This is the core structural fix.

### Capability map (nothing implemented is orphaned)

Every live API route maps to a destination, or is explicitly marked for deletion.

| API route | New home | Notes |
|---|---|---|
| `GET /api/status` | Home + Fleet | The spine. Agents, today's counts, reliability, metrics, intake_profile, setup_repos. |
| `GET /api/schedule` | Fleet | Upcoming runs. Also feeds Home "what's next" line. |
| `GET /api/actions` | Home (failures) + Fleet (patterns) | Reliability signals. Surfaced as honest failure cards. |
| `GET /api/shipped` | Home + Pipeline (Shipped column) | The proof. Needs outcome sentences (see Phase 2). |
| `GET /api/usage` | Fleet (quota panel) + Home (one-line capacity) | Real subscription headroom. Never invent a quota. |
| `POST /api/queue` | Pipeline (card actions) | assign / queue / hold / done on an issue. |
| `GET /api/setup/status` | Onboarding + Settings | Engine, GitHub, repos, demo readiness. |
| `GET,POST /api/setup/repos` | Onboarding + Settings | Pick repos. |
| `GET,POST /api/setup/playbooks`, `/playbook` | Onboarding (first request) + Ask (starter specs) | Starter specs become first Request. |
| `POST /api/setup/demo`, `/demo/clear` | Onboarding (demo mode) | Sample lifecycle for empty installs. |
| `GET,POST /api/slack/trusted-users` (+ remove) | Settings | Optional Slack approver list. |
| `POST /api/conversation/control` | Ask (steer) | Mid-conversation control verbs. |
| `GET /api/memory/candidates` + promote/reject | Lessons | The promotion pipeline. |
| `GET /api/firings`, `/{id}`, `/{id}/tail` | Run view (inside Pipeline) + Fleet activity | Live transcript and event tail. SSE-shaped. |
| `GET /api/plans`, `/drafts`, `/{id}` | Pipeline (Plans column) + Ask | Plan list and detail. |
| `POST /api/plans/{id}/decision` | Pipeline + Home (inline approve) | The go/no-go gate. |
| `POST /api/plans/{id}/file-issue` | Pipeline (plan detail) | Plan to filed issue. |
| `POST /api/plans/{id}/convert-followup`, `/mark-handled` | Pipeline (follow-up actions) | Follow-up lifecycle. |
| `GET /healthz` | (internal) | Connection probe, no UI surface. |

**Capabilities to delete or hide:**
- The raw "Inspect" button on every plan card (issue 314) is deleted. Selection opens a detail panel; there is no separate inspect verb.
- The `plan.source` chip ("compose", "planning", "batman", "followup") is removed from the card face. Source is an internal routing detail, not user-facing. It can stay in the detail panel as a small "origin" line for Dev.
- The raw `readiness_score`/100 chip on the card face is removed (see chip vocabulary). The number survives only in the Dev-facing detail panel.

---

## 5. Visual system

We keep the committed direction and tighten it. Near-black neutral base, glass floating layers, Instrument Sans + Fragment Mono, a single accent, fast motion, Cursor card grammar. The justification for keeping it: it already reads as a premium dark developer surface (the Cursor, Fey, Clay, Profound references below all live in this register), and the failures of the current UI are about information value and density, not the palette. We do not relitigate the aesthetic; we fix what it shows.

### Color

Near-black neutral base with layered glass. Light mode is a required first-class twin (the current build has known WCAG failures in light mode per the chief-designer audit; the spec demands both modes pass AA).

- `--bg`: near-black base (dark) / near-white (light).
- `--surface`: glass layer 1, raised cards (subtle translucency over bg).
- `--surface-2`: glass layer 2, popovers, sheets, command palette.
- `--border`: low-contrast hairline (the current `border/70` pattern is right).
- `--fg`: primary text, AA against bg and surface in both modes.
- `--fg-muted`: secondary text, AA against surface.
- `--accent`: single accent, used for the primary action, active nav, and "needs you". One accent only. No second hue competing for attention.
- Status colors are semantic and used sparingly, only on the status dot and the failure card:
  - `--ok` (shipped, healthy, done)
  - `--working` (run in flight)
  - `--attention` (needs you, uses accent)
  - `--error` (failed, honest)
  - `--idle` (paused, neutral gray, never green)

The status palette is the contract behind the "status vocabulary lies" fix: idle is gray, not green; error is `--error`, never folded into ok.

### Typography
- **Instrument Sans** for all UI text and headings. Headings medium weight, tracking normal (the current `font-heading` usage).
- **Fragment Mono** for code, repo slugs, event tokens, IDs, and the command palette input echo. Mono signals "this is a literal machine value", which is exactly the cue Maya needs to know she can ignore it.
- Type scale (rem): 0.75 (caption/chip), 0.8125 (meta), 0.875 (body), 1 (card title), 1.25 (section), 1.5 (page H1), 2 (Home hero number). No size outside this scale.

### Spacing scale
A single 4px-based scale. Every gap, pad, and margin is one of these. No arbitrary values.

`2, 4, 8, 12, 16, 20, 24, 32, 40, 48, 64` (px).

- Card inner padding: 16.
- Gap between cards in a list: 12.
- Gap between sections: 24.
- Page gutter: 24 (desktop), 16 (narrow).
- Chip height: 20, chip inner pad: 8 horizontal.

### Card anatomy (the canonical lifecycle card)

This is the single most reused component. Every Request, Plan, Run, and Shipped item renders as this card. It is the fix for "information density high, information value low."

```
+---------------------------------------------------------------+
|  [status chip]  [repo chip]  [+N]            [age]            |  <- meta row
|                                                               |
|  Outcome sentence in plain words, one line, the real value.   |  <- title (the outcome)
|                                                               |
|  [agent avatar] Lucius                       [primary action] |  <- attribution + 1 action
+---------------------------------------------------------------+
```

Rules:
- **Status chip**: one of the human-word chips below. Carries the only status color on the card.
- **Repo chip**: the repo short name (last path segment, `repo` not `acme-org/repo`), in mono. Maximum **2 repo chips shown**, then `+N` overflow that reveals the full list on hover or in the detail panel. This kills the "giant unreadable repo-list pill blobs listing 10 full slugs."
- **Age**: relative, friendly ("2h", "yesterday"), with the exact timestamp on hover. Right-aligned, muted.
- **Outcome sentence**: the headline is the outcome, not a truncated PR title or raw token. "Stopped the signup page from dropping users on slow networks" not "fix: debounce form submit (#412)". Where the API cannot yet supply an outcome sentence, we fall back to the cleaned title and flag the gap for Phase 2.
- **Attribution**: agent avatar + name, small, muted. Maya reads it as "who on the team did this."
- **One primary action** per card. Approve, or Open, or View run. Secondary actions live in the detail panel, never as a row of competing buttons. This kills "Inspect buttons everywhere."
- No empty card ever renders. A card with no outcome and no action does not exist; that state becomes an empty-state pattern instead. This kills "a tall empty card."

### Chip vocabulary (replace jargon with human words)

The current chips ("compose", "needs scope", "100/100") are removed. The replacement vocabulary, used everywhere a status is shown:

| Old / raw | New chip | Color | Meaning |
|---|---|---|---|
| `compose`, `planning`, `batman`, `followup` (source) | removed from card | n/a | source is internal; not shown |
| `readiness 100/100`, `readiness_ok: true` | **Ready to start** | ok | the plan is complete enough to run |
| `readiness 34/100`, `readiness_ok: false` | **Needs detail** | attention | the request is too thin to act on |
| `status: waiting` / awaiting decision | **Needs your go-ahead** | attention | the approval gate |
| `status: approved` | **Approved** | ok | you said yes; queued or running |
| `status: declined` | **Declined** | idle | you said no; parked |
| firing `status: running` | **Working now** | working | a run is live |
| firing `status: ok` | **Done** | ok | run finished clean |
| firing `status: error` / `llm-error` | **Hit a snag** | error | honest failure, never "fine" |
| agent `status: idle` + `paused` | **Paused** | idle | gray, never green |
| agent `status: idle` (not paused) | **Resting** | idle | scheduled, not currently running |
| agent `status: live` | **Working now** | working | actually executing |
| shipped PR merged | **Shipped** | ok | merged outcome |
| issue armed `agent:implement` | **Queued** | working | armed for pickup |
| issue `do-not-pickup` | **On hold** | idle | parked out of reach |
| `demo: true` | **Sample** | idle | seeded demo, clearly labelled |

Plain rule for the chip set: every chip is something a non-engineer can read out loud and understand. If it needs a glossary, it does not ship.

### Motion
- 120 to 200ms for all state transitions, ease-out. Card hover lift, sheet slide-in, chip color change, tab underline.
- The one place motion earns more: the live run view's event stream, where new events fade in at the bottom (mirrors fabro's SSE feed and Cursor's "Started 3 background agents" toast).
- No decorative motion. No spinners where a skeleton works. Respect `prefers-reduced-motion`.

### States (every surface defines all four)

1. **Empty** teaches the mental model. Not "No data." Instead: an illustration-light card that explains what will appear here and the one action to make it happen. Example, empty Pipeline: "Nothing in the pipeline yet. Ask Alfred for something and it shows up here as a plan to approve." with an Ask button.
2. **Loading** uses skeletons shaped like the real content (card silhouettes), not a centered spinner. Under 200ms, show nothing (avoid flash).
3. **Error** is honest and specific. It names what failed and the next step. "Couldn't reach the local runtime. Start Alfred, then reconnect." with a Reconnect button. Never a stack trace on the primary surface; the raw error is behind a "details" disclosure for Dev.
4. **Degraded** (partial failure) is first-class because the snapshot already models it (`Snapshot.degraded`). One section can fail without blanking the view. The failed section shows its own inline error; the rest renders normally.

---

## 6. Mobbin references

Every reference below was reviewed for this spec. Cited inline so the implementer can open the exact screen.

**Cursor background agents (the closest analog: AI coding agents you dispatch and review).**
- Agents dashboard with running tasks, per-task model, and a "Started 3 background agents" toast: [mobbin.com/screens/f3a0c140-cd83-498f-84f1-3ea991079865](https://mobbin.com/screens/f3a0c140-cd83-498f-84f1-3ea991079865). We borrow the calm list of in-progress agents with status dot, relative time, and model label. This is the Run/Pipeline grammar.
- Agent list with status grouping (Running, Finished, Error, Merged) and per-card file-count + state chip (Draft, Branch, Merged): [mobbin.com/screens/58450174-4034-4e5d-a126-9def2378b0a0](https://mobbin.com/screens/58450174-4034-4e5d-a126-9def2378b0a0). This validates our status-chip-on-card grammar and the Shipped/Queued/Working states.
- Usage dashboard, spend by model over a billing window: [mobbin.com/screens/aab19640-0c58-40f8-a349-f7f81943900b](https://mobbin.com/screens/aab19640-0c58-40f8-a349-f7f81943900b) and the included-usage summary table: [mobbin.com/screens/1032f580-3092-40c7-99d5-07eff4d430ce](https://mobbin.com/screens/1032f580-3092-40c7-99d5-07eff4d430ce). Model for the Fleet quota panel (maps to `GET /api/usage`).

**Linear (issue views, calm density, the depth bar).**
- Issue list grouped by status with a properties side panel and an Add Filter menu: [mobbin.com/screens/d1d26f7d-e1e5-490f-ab4f-c96dd12854c1](https://mobbin.com/screens/d1d26f7d-e1e5-490f-ab4f-c96dd12854c1). Model for Pipeline: status-grouped rows, a right-hand detail panel that opens on select (this is exactly the PlansView Sheet pattern, kept and elevated). The "Ask Linear" affordance bottom-right informs our command palette entry point.

**Premium dark surfaces (the register we are already in).**
- Clay command palette over a dark gradient with a coachmark teaching Cmd+K: [mobbin.com/screens/9112b54f-92d1-4a46-a48f-6f1150b4c03f](https://mobbin.com/screens/9112b54f-92d1-4a46-a48f-6f1150b4c03f). Model for our command palette and its first-run coachmark.
- Fey near-black onboarding teaching the command palette as the navigation primitive ("It's in your hands"): [mobbin.com/screens/ff52ac90-4d18-4765-98da-df1e362a5ee1](https://mobbin.com/screens/ff52ac90-4d18-4765-98da-df1e362a5ee1) and the command step [mobbin.com/screens/2b85c479-c2a5-4f7c-8240-25a7b3fcdac5](https://mobbin.com/screens/2b85c479-c2a5-4f7c-8240-25a7b3fcdac5). Confirms a dark, typographic, low-chrome onboarding can teach a mental model fast. Our accent-on-near-black hero borrows this restraint.
- Profound dark analytics shell with a left rail split into Analytics and Action groups, and a "Welcome to Profound" centered modal: [mobbin.com/screens/6a388e86-e819-4793-b7ab-bc5e8e6de06f](https://mobbin.com/flows/b848b477-84f1-452e-b528-ec00a14619e6). Model for the Fleet depth surface grouping (status vs control).

**Onboarding that teaches a mental model fast (under 2 minutes, web).**
- GitHub connect-and-select-repositories flow (account to repo list): [mobbin.com/flows/8a2a917a-647a-4c48-b4ac-7915bdb30b16](https://mobbin.com/flows/8a2a917a-647a-4c48-b4ac-7915bdb30b16). Direct model for our "pick repos" step (maps to `GET /api/setup/repos`).
- mymind onboarding, three plain cards that teach the core loop with one must-do highlighted ("Welcome to your new digital garden"): [mobbin.com/flows/edaa8951-53e9-4c13-98e3-7e48c40ee388](https://mobbin.com/flows/edaa8951-53e9-4c13-98e3-7e48c40ee388). Model for our first-request walkthrough: a tiny number of plain cards, one clear primary, skippable.
- Coda guided first-doc walkthrough with sample data and a "Clear sample data" affordance: [mobbin.com/flows/b584ec58-b480-4c98-9f54-fe498e2bb5be](https://mobbin.com/flows/b584ec58-b480-4c98-9f54-fe498e2bb5be). Direct model for demo mode: seed a sample lifecycle, label it clearly, one click to clear (maps to `/api/setup/demo`).
- Intercom onboarding with a progress checklist and a "6 of 6 steps" completion state: [mobbin.com/flows/92a46138-ae24-4c57-a4e1-f257b126cac7](https://mobbin.com/flows/92a46138-ae24-4c57-a4e1-f257b126cac7). Model for the onboarding progress rail (our setup already has this shape in OnboardingView; we keep the checklist, simplify the copy).

---

## 7. Onboarding: the full first-run journey

Onboarding is a full-screen takeover that runs before any backend may exist. It must complete without a terminal for Maya, and offer shortcuts for Dev. It ends with either a real filed issue or demo mode, so the user always lands on a populated Home, never an empty one (the wuphf "land oriented" principle).

The journey is six steps with a persistent progress rail (Intercom checklist model). Each step has a Maya path (guided, no terminal) and a Dev shortcut.

**Step 0: Welcome.** One screen. Near-black, accent headline: "Wake up to shipped work you can trust." One line: "Alfred runs your own Claude Code and Codex to ship GitHub work overnight. Let's connect it. About two minutes." Primary: Get started. Secondary (Dev): "I have a server running" jumps to paste-URL.

**Step 1: Connect Claude / Codex (tools).** Detect installed CLIs.
- Maya path: "Check my tools" button runs the native `auth_status` action and shows a plain result: green check "Claude Code is ready", or a calm "Claude Code isn't installed yet" with a link to install. No API keys, said explicitly.
- Dev shortcut: the engine probe result table (`SetupStatus.engines`) is one disclosure away.
- Data: `GET /api/setup/status` (engines, engine_ready), native `auth_status`.

**Step 2: Connect GitHub.** Detect `gh auth status`.
- Maya path: if `gh` is already signed in, show "Signed in as @account" green and auto-advance. If not, a guided card: "Alfred uses your GitHub sign-in. Click to sign in." with the native flow; the raw `gh auth login` command is behind an "Advanced: sign in from a terminal" disclosure (already built this way in OnboardingView, keep it).
- Dev shortcut: paste server URL + start runtime, already present.
- Data: `GET /api/setup/status` (github), `SetupGithub`.

**Step 3: Pick repositories.** The plain repo list.
- Both paths: load repos (`GET /api/setup/repos`), multi-select from a list that shows the **repo name and its description**, not just the slug. Private badge where relevant. Maya picks by recognizing the project, not parsing the org slug. Save (`POST /api/setup/repos`).
- This is the GitHub-flow Mobbin model. The current ReposStep is close; the fix is leading with description and showing the short name prominently with the full slug muted/secondary.

**Step 4: Connect Slack (optional, clearly skippable).** "Want approvals and questions in Slack too? Optional." Skip is a first-class button, not a tiny link. Maya skips. Dev who wants it adds a trusted approver (`POST /api/slack/trusted-users`).

**Step 5: First request (the payoff).** End on a real lifecycle object.
- Maya path: starter specs as plain cards (`GET /api/setup/playbooks`). "Pick something for Alfred to do first" with two or three human-titled playbooks. Selecting one drafts a real first Request (`POST /api/setup/playbook`) and lands her on Ask to refine it in plain words, or straight to Pipeline to see her first Plan-to-approve. This teaches the whole loop with her real repo.
- Demo path (no repo ready, or wants to look first): "Show me a sample first" seeds a demo lifecycle (`POST /api/setup/demo`) so Home, Pipeline, and Shipped all render populated and clearly labelled "Sample". One click to clear later (`/demo/clear`). Coda model.
- Dev shortcut: skip to writing a brief in Ask directly.

**Onboarding completion** drops the user on Home, populated either with their real first request or the labelled sample, with a one-time coachmark teaching Cmd+K (Clay/Fey model).

### Empty states per surface (each teaches the model)
- **Home, fresh:** "Your first results will land here. Right now there's nothing to review, nothing running, nothing shipped. Ask Alfred for something to start the loop." + Ask button. If demo seeded, Home is populated instead.
- **Ask, fresh:** the conversational opener itself is the empty state: a prompt and a plain placeholder ("Describe the outcome, who needs it, and any limits Alfred should respect.").
- **Pipeline, fresh:** "Nothing in the pipeline yet. When you ask Alfred for something, it appears here first as a plan for you to approve, then as work in progress, then as shipped." (teaches the four columns in one sentence).
- **Fleet, fresh:** "Your agents rest until there's work or a schedule fires. Batman plans, Lucius ships. They'll show activity here once a run starts."
- **Lessons, fresh:** "As the fleet works, it writes down what it learns. You'll approve the keepers here so the team gets smarter over time." (teaches the promotion pipeline).

---

## 8. Per-screen specs

Each screen: purpose, primary persona, layout, data contract, interactions, empty state, single primary action.

### 8.1 Home (the morning-after story)

- **Purpose:** answer "what happened while I was away, and what needs me?" in five seconds, in plain words. This is the soul surface.
- **Primary persona:** Maya. Dev gets a "depth" path to Fleet/Pipeline from here.
- **Layout:** single calm column, three honest sections stacked by priority, plus a slim hero line. No two-pane control-center grid (the current ReviewView is too busy; we simplify).
  - **Hero line:** "Good morning. Here's what your fleet did." + one-sentence rollup ("Shipped 3 things overnight. 2 need your go-ahead. 1 hit a snag.") built from real counts. If counts are zero, honest: "Quiet night. Nothing needed you."
  - **Needs you** (first, only if non-empty): lifecycle cards for plans awaiting a go-ahead and lessons awaiting approval. Inline Approve / Decline on genuine plan cards (already supported via `onPlanDecision`). This is the highest-value real estate.
  - **What shipped** (the proof): shipped cards with outcome sentences, agent attribution, Open PR. Time-window control (24h / 7d / 14d), default 24h.
  - **Hit a snag** (only if non-empty): honest failure cards from reliability signals, each with one next step (retry, or open the run). Never hidden, never alarmist.
  - **Slim footer strip:** one capacity line ("Claude quota: plenty for tonight" or "running low, resets in 3h") from `GET /api/usage`, and a "what's next" line from `GET /api/schedule` ("Next run: Lucius, 9pm"). Two facts, not a dashboard.
- **Data contract:** `GET /api/status` (counts, reliability), `GET /api/shipped` (shipped cards), `GET /api/usage` (one capacity line), `GET /api/schedule` (next run), `GET /api/plans` filtered to awaiting (needs-you), `GET /api/memory/candidates` (lesson count). Degraded sections render their own inline error.
- **Interactions:** approve/decline inline; click a shipped card opens its lifecycle thread (read-only modal, deep-links to GitHub); click a snag opens the run view; window toggle filters shipped.
- **Empty state:** see section 7.
- **Single primary action:** the persistent **Ask** button (start a new request). Everything else is reactive; Ask is the one proactive verb.

### 8.2 Ask (the request composer)

- **Purpose:** turn plain words into a real Request (and ultimately a filed issue) without the user knowing the words spec, acceptance criteria, or issue.
- **Primary persona:** both. Plain mode for Maya by default; technical mode for Dev.
- **Layout:** conversational. A transcript column (user + Alfred turns) and a sticky composer at the bottom. A quiet right rail shows the structured draft taking shape (title, repos, what changes) so Dev sees the spec forming, but Maya can ignore it. A readiness affordance reads in human words ("Ready to start" / "Needs a little more detail"), never "34/100" on the primary surface.
- **Data contract:** `POST /api/compose/converse` (and `/converse/stream` for token streaming), `POST /api/plans/draft`, `POST /api/conversation/control` (steer verbs), `intake_profile` from status drives plain vs technical copy, `context_repos` seeded from `setup_repos` so the user never retypes scope. `ConverseResponse.done` gates the hand-off.
- **Interactions:** type in plain words; Alfred asks one or two clarifying questions; the draft fills in; when ready, a single "Looks right, save this plan" action persists it as a Plan and routes to Pipeline. A plain/technical toggle (the `plain` flag) is available but defaults from server profile.
- **Empty state:** the opener + placeholder is the empty state. Optional starter-spec chips for the blank-page problem.
- **Single primary action:** **Save this plan** (becomes available only when `readiness.ready`), which creates the Plan object.

### 8.3 Pipeline (the lifecycle board)

- **Purpose:** the stitched lifecycle. See every object across Plans-awaiting-you, Queued, Working, Shipped. This is the merger of Work + Plans and the structural centerpiece.
- **Primary persona:** both. Maya lives in the first two columns; Dev uses all four plus the run view.
- **Layout:** Linear-style status-grouped board, four columns left to right matching the lifecycle:
  1. **Needs your go-ahead** (Plans awaiting decision), the gate.
  2. **Queued** (approved/armed issues waiting for pickup).
  3. **Working now** (live runs).
  4. **Shipped** (merged, with outcome sentences).
  Cards use the canonical card anatomy. Selecting any card opens a right-hand **detail panel** (the Linear/PlansView Sheet pattern), which is the only place secondary actions and Dev depth live.
- **Data contract:** `GET /api/plans` (col 1), `GET /api/shipped` columns queued/in_progress/shipped (cols 2-4), `GET /api/firings` for live run cross-reference, `POST /api/plans/{id}/decision` (approve gate), `POST /api/plans/{id}/file-issue`, `POST /api/queue` (assign/queue/hold/done), `POST /api/plans/{id}/convert-followup` and `/mark-handled`.
- **Interactions:** approve/decline in col 1 (inline + in panel); the detail panel exposes full scope, affected repos, readiness number (Dev), file-issue, queue actions, GitHub links, and the live run tail when a run is active. Dedupe and delete (issue 314) handled here: identical drafts collapse to one card with a revision count; each card has a delete/dismiss in its panel.
- **Empty state:** see section 7 (teaches the four columns).
- **Single primary action:** per card, the stage-appropriate verb (Approve in col 1, View run in col 3, Open PR in col 4).

#### 8.3a Run view (inside Pipeline, opened from a Working card)
- **Purpose:** live evidence of an in-flight run. fabro's event stream + Cursor's running-agent view.
- **Primary persona:** Dev (depth), with a Maya-readable summary at the top.
- **Layout:** a header with the outcome sentence, agent, repo chip, elapsed time, and a plain status line ("Working now, about halfway"). Below, the live transcript / event stream tailing in real time (new events fade in at the bottom). Checkpoints and stage outputs where available.
- **Data contract:** `GET /api/firings/{id}` (metadata, summary), `GET /api/firings/{id}/tail` (live tail, SSE-shaped), `GET /api/status` (agent live/idle truth). Status is honest: a run that errored shows "Hit a snag" with the real error, never "running" on a dead process.
- **Interactions:** auto-scroll tail with a pause-on-scroll-up; copy event; open transcript; for Dev, a raw-event toggle. Maya sees the summarized stream; raw tokens are behind the toggle, never the default (fixes "raw event tokens").
- **Empty / not-live state:** if the run finished, this becomes the read-only record with its outcome and a link to the shipped PR.
- **Single primary action:** **Open PR** when shipped, or **Steer** (a control verb via `conversation/control`) while live, if supported.

### 8.4 Fleet (agent depth)

- **Purpose:** the Dev depth surface. Roster, schedules, live activity stream, quotas, engine routing, failure patterns. Everything Maya never needs.
- **Primary persona:** Dev.
- **Layout:** Profound-style grouped left structure inside the page: a **Roster** (agent cards with honest status: Working now / Resting / Paused, last run, runs today, role), a **Schedule** list (upcoming runs), an **Activity** stream (the firing feed, live), and a **Capacity** panel (Claude 5h + 7d windows, Codex quota, from usage). A **Reliability** section surfaces failure patterns and stale workers honestly.
- **Data contract:** `GET /api/status` (agents, paused/loaded truth, metrics), `GET /api/schedule`, `GET /api/firings` (activity), `GET /api/usage` (quota panels), `GET /api/actions` (failure_patterns, stale_workers, promotion_suggestions). Native actions for pause/resume/run/schedule (FleetControlView already wires these with confirm verbs).
- **Interactions:** pause/resume/run an agent (confirmed for disruptive verbs), change cadence, open an agent's activity tail, view quota windows. The status dot uses the semantic palette: idle is gray, never green.
- **Empty state:** see section 7.
- **Single primary action:** none global; this is a monitoring/control surface. Per agent, Pause/Resume is the primary toggle.

### 8.5 Lessons (what the fleet learned)

- **Purpose:** the wuphf promotion pipeline. Review what the fleet learned and promote the keepers so the team gets smarter.
- **Primary persona:** both. Maya approves with a plain explanation; Dev sees evidence and source.
- **Layout:** a list of lesson candidates, each a card with a plain one-line statement of the lesson, the repo chip, a severity chip (info / worth noting / important, mapped from info/warning/blocker), and Approve / Not useful. The detail panel shows evidence, the source run, confidence, and tags for Dev.
- **Data contract:** `GET /api/memory/candidates` (candidates), promote/reject routes, `actions.promotion_suggestions` from `GET /api/actions`.
- **Interactions:** promote (one tap, plain confirm), reject, open source run. A promoted lesson shows where it landed.
- **Empty state:** see section 7 (teaches that the fleet writes down what it learns).
- **Single primary action:** **Approve** (promote) on a candidate card.

### 8.6 Settings / Setup

- **Purpose:** connection, repos, optional Slack, engine/diagnostics, demo controls. Visited rarely; lives behind the top-bar gear, not the primary rail.
- **Primary persona:** Dev mostly; Maya only returns here to add a repo.
- **Layout:** a sectioned settings page reusing the onboarding step content (engines, GitHub, repos, Slack, demo) plus a Diagnostics section (the current SetupView advanced content: server URL, native command results, redis/brain doctor). Onboarding and Settings share components; Settings is the non-takeover, any-time version.
- **Data contract:** all `/api/setup/*`, `/api/slack/trusted-users*`, native diagnostic actions.
- **Interactions:** re-pick repos, manage Slack approvers, run diagnostics, re-run onboarding, seed/clear demo.
- **Empty state:** n/a (always has connection content).
- **Single primary action:** context-dependent (Save repos, Add approver). No global primary.

### 8.7 Command palette (Cmd+K)

- **Purpose:** the Dev navigation primitive and the universal jump-to. Clay/Fey model.
- **Primary persona:** Dev; introduced to Maya via a gentle first-run coachmark she can ignore.
- **Layout:** glass `--surface-2` overlay, mono input echo, grouped results: Go to (Home, Ask, Pipeline, Fleet, Lessons), Actions (Refresh, toggle theme, seed/clear demo), and live objects (jump to a specific plan, run, or shipped item by title).
- **Data contract:** navigation + the same snapshot objects already loaded; no new route required for nav.
- **Interactions:** Cmd/Ctrl+K toggles; arrow + enter; type to filter.
- **Single primary action:** run the highlighted command.

---

## 9. Phased implementation plan

### Phase 1 (1 to 2 days): highest-leverage restructures, existing API only
No new backend. Pure client restructure and copy fixes that kill the screenshot failures.

1. **Rename + restructure nav** to Home / Ask / Pipeline / Fleet / Lessons; move Settings to the top-bar gear; keep the command palette. (App.tsx, uiTypes `TabKey`.)
2. **Merge Work + Plans into Pipeline** as a four-column lifecycle board with a right-hand detail panel. Reuse KanbanBoard columns and the PlansView Sheet. (KanbanBoard, PlansView -> Pipeline.)
3. **Ship the canonical card** with the new chip vocabulary. Replace the source/readiness/status raw chips on every card face. Cap repo chips at 2 + `+N`. Remove the per-card Inspect button; selection opens the panel. (atoms `PlanCard`, ReviewView cards, KanbanBoard cards.)
4. **Fix the status vocabulary lie.** Map `idle` to gray "Resting/Paused" (never green), `error`/`llm-error` to "Hit a snag" (never counted as ok). Correct the Home rollup counts to exclude errored runs from "ok". (derive.ts, the chip map, FleetControlView status dot.)
5. **Simplify Home** to the calm single-column morning-after story (Needs you / What shipped / Hit a snag + capacity + next-run strip). Outcome sentences fall back to cleaned titles until Phase 2. (ReviewView -> Home.)
6. **Dedupe plan cards client-side** (issue 314): collapse identical drafts (same title + repos) into one card with a revision count, so the "same draft 4x" never renders even before the server fix. Add a dismiss/delete affordance in the detail panel wired to `mark-handled` where applicable.
7. **Demo labelling:** ensure every `demo: true` card carries the "Sample" chip across Home and Pipeline.

### Phase 2: new API needs
Flagged here as the places the backend must change.

1. **Outcome sentences for shipped cards.** `GET /api/shipped` must return a plain-language `outcome` (what changed, in human words) and optionally `why`, per card, so Home and the Shipped column stop showing truncated PR titles. Until then we clean titles client-side, but the real fix is server-side summarization at merge time. (views.py `/api/shipped`, `lib/shipped_board.py`, `ShippedCard` type.)
2. **Draft dedupe + delete at the source (issue 314).** The server should not emit four identical drafts and a junk "Hi" draft with readiness 34/100. Add: dedupe by content hash per issue, a readiness floor that hides or clearly flags sub-threshold junk, and a real delete route (`DELETE /api/plans/{id}` or a dismiss marker) so the client can remove a draft, not just hide it. (views.py `/api/plans*`, plan store.)
3. **Run status truth.** `GET /api/firings` / status must reliably distinguish a live process from a dead/errored one so the Run view and counts never lie. Surface `llm-error` as a distinct honest state.
4. **Lesson plain statements.** `GET /api/memory/candidates` should carry a plain one-line `statement` for Maya, alongside the existing technical `body`/`evidence` for Dev.
5. **Schedule next-run for interval agents.** `GET /api/schedule` interval rows lack a `next_fire_at`; a best-effort anchor would let Home show "next run" for all agents, not just cron ones.

### Phase 3: polish and motion
1. Live event-stream fade-in on the Run view tail (SSE-shaped, fabro model); pause-on-scroll.
2. Command palette live-object jump (plans, runs, shipped by title) + first-run Cmd+K coachmark.
3. Light-mode AA pass (fix the known WCAG failures), full `prefers-reduced-motion` audit.
4. Skeleton loading states shaped like real cards across every surface.
5. Onboarding micro-polish: auto-advance on detected `gh`/engine, the populated-Home landing with sample data, the optional Slack skip styling.

---

## 10. Decisions a reviewer should be able to nod at

- The IA is reorganized by lifecycle stage, not screen type. Plans is promoted out of a buried subtab to a first-class Pipeline column and a Home count, because approving a plan is Maya's most important action.
- Work and Plans merge into Pipeline because they are the same object at adjacent stages; the product stitches the lifecycle so the user does not have to.
- The card is one component with a strict anatomy: status chip + repo chip (max 2 + N) + age + outcome sentence + one action. This is the direct fix for high-density / low-value, repo-slug blobs, Inspect-everywhere, and empty cards.
- Chip vocabulary is fully human. "Ready to start", "Needs detail", "Needs your go-ahead", "Working now", "Hit a snag", "Shipped". No "compose", no "100/100", no glossary required.
- Status never lies: idle is gray, errors are honest, missing data says missing. This is a correctness requirement, not a style choice.
- The aesthetic direction (near-black glass, Instrument Sans + Fragment Mono, single accent, fast motion, Cursor cards) is kept and justified against the Cursor/Fey/Clay/Profound references; the failures were information value, not palette.
- Onboarding always ends on a populated Home (real first request or labelled demo), so no one ever meets an empty product.
```

