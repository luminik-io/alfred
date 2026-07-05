# Glossary

One-sentence definitions for the terms a first-reader meets in Alfred docs.
Entries are alphabetical, with cross-links to the page that covers each term
in depth.

Note on names: an agent's canonical identity is its **role** (a slug like
`senior-dev` or `reviewer`). The Batman-cast names below (Lucius, Ra's al Ghul,
and so on) are the display names of the default `batman` theme, not the identity
the machine uses. See [Identity and themes](IDENTITY_AND_THEMES.md).

- **`agent:authored` label**: Marks a PR opened by an Alfred agent, used by
  Ra's al Ghul, Nightwing, and automerge to know which PRs are theirs to act on.
  See also: [State machine](STATE_MACHINE.md).
- **`agent:bundle:<slug>` label**: Groups GitHub issues that belong to one
  cross-repo feature; Batman resolves the bundle from this label.
  See also: [Multi-repo worked example](MULTI_REPO_WORKED_EXAMPLE.md).
- **`agent-cleanup`**: Housekeeping agent that sweeps stale worktrees,
  expired spend files, stuck locks, and stale `agent:in-flight` claims.
  See also: [Agents](AGENTS.md).
- **`agent:done` label**: Terminal state for an issue whose PR has merged;
  set by `release_issue(transition_to="agent:done")`.
  See also: [State machine](STATE_MACHINE.md).
- **`agent:implement` label**: Marks an issue ready for Lucius (or another
  feature-dev agent) to claim and implement.
  See also: [State machine](STATE_MACHINE.md).
- **`agent:in-flight` label**: Marks an issue currently claimed by an agent;
  another agent will not pick it up until the claim is released.
  See also: [State machine](STATE_MACHINE.md).
- **`agent:large-feature` label**: Marks an issue Batman should plan as a
  multi-repo rollout rather than a single-repo implementation.
  See also: [Multi-repo worked example](MULTI_REPO_WORKED_EXAMPLE.md).
- **`agent:pr-open` label**: Set on the issue when the implementing agent
  opens its PR; cleared when the PR merges or closes.
  See also: [State machine](STATE_MACHINE.md).
- **AGENT_RUNNER**: The shared Python library at `lib/agent_runner/` that
  every agent script imports for preflight, locking, spend, gh, and Slack.
  See also: [agent_runner reference](../site/src/content/docs/reference/agent-runner.md).
- **AgentResult**: The dataclass an engine returns to the runner, containing
  success flag, subtype, turn count, cost, session id, and result text.
  See also: [How it works](../site/src/content/docs/concepts/how-it-works.md).
- **ALFRED_HOME**: Operator-overridable directory holding runtime state,
  prompts, worktrees, and logs; defaults to `~/.alfred`.
  See also: [Install](../INSTALL.md).
- **Bane**: The default `batman` theme's name for the `test-engineer` role,
  which picks the lowest-coverage actively changed file and opens a tests-only
  PR for it. See also: [Agents](AGENTS.md).
- **Bat-signal**: The Slack alert raised when an agent prints `[BLOCKED]`
  or when a fleet-wide spend or rate-limit cap trips.
  See also: [Output samples](OUTPUT_SAMPLES.md).
- **Batman**: The default `batman` theme's name for the `architect` role, the
  cross-repo architect that turns `agent:large-feature` issues into rollout
  plans and, on the parent-issue path, approved child `agent:implement` issues
  for the normal fleet queue. Also the id of the default theme itself. See also:
  [Identity and themes](IDENTITY_AND_THEMES.md),
  [Multi-repo worked example](MULTI_REPO_WORKED_EXAMPLE.md).
- **`claude -p`**: Claude Code's non-interactive subprocess mode, the surface
  Alfred uses to invoke Claude with a prompt and capture an AgentResult.
  See also: [Claude Code guide](CLAUDE_CODE.md).
- **conversational onboarding**: The chat-driven setup path (`POST
  /api/onboarding/converse`) where Alfred asks setup questions and proposes
  actions the client runs under a human gate; read-only actions auto-proceed,
  side-effectful ones need an explicit Approve. Reuses the same handlers as the
  stepped Setup form. See also: [Setting Alfred up](ONBOARDING.md).
- **code-map**: JSON snapshot of every watched repo's source files, symbols,
  imports, API calls, server routes, and contract drift, written to
  `$ALFRED_HOME/state/code-map.json` by `code-map-refresh`.
  See also: [Monorepo](MONOREPO.md).
- **Codex**: OpenAI's local coding agent, supported as an engine alongside
  Claude Code and selectable per agent via `ALFRED_<AGENT>_ENGINE`.
  See also: [Codex provider](CODEX_PROVIDER.md).
- **doctor.sh**: Script at `bin/doctor.sh` that runs every enabled agent in
  doctor mode (no LLM spend) and reports preflight status.
  See also: [Output samples](OUTPUT_SAMPLES.md).
- **Drake**: The default `batman` theme's name for the `planner` role, which
  reads specs, roadmap, and code-reality and files the next well-scoped
  `agent:implement` issue for the fleet to work.
  See also: [Specs-driven development](SPECS_DRIVEN_DEVELOPMENT.md).
- **dry-run**: Mode toggled by `--dry-run` or `ALFRED_DRY_RUN=1` that
  narrates a full firing lifecycle without LLM calls or side effects.
  See also: [Dry-run](DRY_RUN.md).
- **engine**: The coding backend an agent invokes; Claude Code, Codex, or a
  hybrid that tries Claude first and falls back to Codex on rate limits.
  See also: [Claude Code guide](CLAUDE_CODE.md).
- **engine routing**: Per-agent assignment of which engine to use, set via
  `ALFRED_<AGENT>_ENGINE` (`claude`, `codex`, or `hybrid`).
  See also: [Claude Code guide](CLAUDE_CODE.md).
- **fast-cleanup**: `agent-cleanup`'s sub-pass that runs after every Lucius
  firing to delete just-closed worktrees without waiting for the nightly sweep.
  See also: [Agents](AGENTS.md).
- **firing**: One run of one agent triggered by the host scheduler; bounded
  by lock, preflight, spend caps, and a hard timeout.
  See also: [How it works](../site/src/content/docs/concepts/how-it-works.md).
- **GH_ORG**: The GitHub org or user that owns the repos Alfred operates
  against; agents refuse to act on repos outside it.
  See also: [Install](../INSTALL.md).
- **hybrid fallback**: Engine routing mode where the runner tries Claude
  first and falls back to Codex only when Claude ran but produced no useful
  result.
  See also: [Claude Code guide](CLAUDE_CODE.md).
- **IAM-per-agent**: AWS pattern where each agent gets its own IAM identity
  and Secrets Manager scope, so a compromised agent can only reach its own keys.
  See also: [AWS setup](AWS_SETUP.md).
- **launchd** macOS host scheduler that owns the firing cadence on Mac;
  agents ship as `.plist` files in `~/Library/LaunchAgents/`.
  See also: [Architecture](../ARCHITECTURE.md).
- **Lucius**: The default `batman` theme's name for the `senior-dev` role, which
  claims an `agent:implement` issue, opens a worktree, invokes the engine, and
  pushes a PR labelled `agent:authored`.
  See also: [How it works](../site/src/content/docs/concepts/how-it-works.md).
- **Nightwing**: The default `batman` theme's name for the `fixer` role, which
  lands P0/P1 reviewer comments on open `agent:authored` PRs without
  re-litigating design. See also: [Agents](AGENTS.md).
- **plist** macOS launchd unit file; one per agent, generated by the
  renderer from `launchd/agents.conf` and `launchd/template.plist`.
  See also: [launchd reference](../site/src/content/docs/reference/launchd.md).
- **preflight**: Cheap pre-firing check that the required CLIs, gh auth,
  and workspace checkouts exist before any LLM turn is spent.
  See also: [How it works](../site/src/content/docs/concepts/how-it-works.md).
- **Ra's al Ghul**: The default `batman` theme's name for the `reviewer` role,
  which posts a multi-axis review (correctness, security, performance,
  maintainability) on every fresh `agent:authored` PR. See also:
  [Agents](AGENTS.md).
- **role**: An agent's canonical identity, a short slug like `architect`,
  `senior-dev`, or `reviewer`. Scheduler labels, GitHub labels, worktree paths,
  and merge gates all key off the role; the display name is a theme layer on top.
  See also: [Identity and themes](IDENTITY_AND_THEMES.md).
- **role runner**: A Python script under `bin/` that implements one agent
  role; the runtime codename maps to a role runner via `AGENT_CODENAME`.
  See also: [Agents](AGENTS.md).
- **roster theme**: The named set of display names applied to the roles, one
  name per role. Ships as `batman` (default), `transformers`, and
  `justice-league`, plus an operator-authored `custom` theme. Stored under
  `$ALFRED_HOME/state/roster-theme/` and honored by Slack, the desktop app, and
  the CLI alike. See also: [Identity and themes](IDENTITY_AND_THEMES.md).
- **sentinel string**: Bracketed marker like `[OK]`, `[BLOCKED]`, or
  `[SILENT]` that an agent prints to stdout to signal its exit path.
  See also: [Output samples](OUTPUT_SAMPLES.md).
- **Slack post**: One outbound message Alfred sends to a configured Slack
  channel via webhook or bot token, at info, warn, or alert severity.
  See also: [Slack setup](SLACK_SETUP.md).
- **full fleet**: The default local engineering team Alfred installs: planning,
  implementation, review, test, review-fix, reporting, memory, code-map, cleanup,
  and reliability jobs. `--agents starter` remains an explicit lab shortcut.
  See also: [Install](../INSTALL.md).
- **state machine**: The label transitions on an issue
  (`agent:implement` → `agent:in-flight` → `agent:pr-open` → `agent:done`)
  that coordinate agent handoffs. See also: [State machine](STATE_MACHINE.md).
- **`systemd --user`**: Linux host scheduler that owns the firing cadence
  on Debian/Ubuntu; the equivalent of launchd on macOS.
  See also: [Linux](LINUX.md).
- **theme builder**: The conversational flow (`POST
  /api/theme-builder/converse`) that proposes a full role-to-name mapping from a
  described vibe; you tweak and save it via the normal theme editor, behind the
  usual approval gate. See also: [Setting Alfred up](ONBOARDING.md).
- **turn budget**: Per-firing cap on LLM turns (`max_turns`) plus daily
  rolling caps on turns and cost, enforced before and during a firing.
  See also: [Architecture](../ARCHITECTURE.md).
- **worktree**: Throwaway git worktree under `$ALFRED_HOME/worktrees/`
  branched from a fresh `origin/main`, where the engine writes code for one
  firing. See also: [Monorepo](MONOREPO.md).
- **WORKSPACE_ROOT**: Operator-set directory whose `product/` subdirectory
  contains the local checkouts of every repo Alfred operates against.
  See also: [Workspace patterns](WORKSPACE_PATTERNS.md).
