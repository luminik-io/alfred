# Alfred docs index

Current map of the public docs. Trust code first, then this index.

## Start Here

- [`../README.md`](../README.md): overview, quick start, repository map, and status.
- [`DEMO.md`](DEMO.md): `alfred demo`, the one-run tour. Watch the team plan, build in an isolated worktree, catch a planted bug in review, fix it, and ship locally on a throwaway sample repo, with only an authenticated `claude` CLI.
- [`../INSTALL.md`](../INSTALL.md): from-zero local install.
- [`AI_ASSISTED_INSTALL.md`](AI_ASSISTED_INSTALL.md): copy-paste prompt and guardrails for Claude Code, Codex, or another local coding assistant to install Alfred.
- [`ONBOARDING.md`](ONBOARDING.md): the two setup paths (chat with Alfred, or step through the form), the onboarding action allowlist, the human-approval gate on side-effectful actions, and the conversational theme builder for naming your team.
- [`INSTALL_TIERS.md`](INSTALL_TIERS.md): the three install tiers (`core`, `client`, `slack`) and how the CLI and fleet run fully standalone.
- [`WORKSPACE_PATTERNS.md`](WORKSPACE_PATTERNS.md): one-repo, multi-repo, specs-led, and architect planning layouts.
- [`MONOREPO.md`](MONOREPO.md): running Alfred against a pnpm, Turborepo, or Cargo workspace.
- [`MULTI_REPO_WORKED_EXAMPLE.md`](MULTI_REPO_WORKED_EXAMPLE.md): one feature shipped across three repos using the full fleet, including Batman.
- [`SPECS_DRIVEN_DEVELOPMENT.md`](SPECS_DRIVEN_DEVELOPMENT.md): turning specs into issue queues, architect plans, and reviewable PRs.
- [`SPEC_DRIVEN_FOR_EVERYONE.md`](SPEC_DRIVEN_FOR_EVERYONE.md): the plain-language version of spec-driven work for a non-technical reader. Describe an outcome, answer a question or two, approve a preview.
- [`INSTALL_TIME.md`](INSTALL_TIME.md): honest read on existing-setup (30 min) and fresh-machine (60 to 120 min) install duration.
- [`../BOOTSTRAP.md`](../BOOTSTRAP.md): full operations setup for a first fleet.
- [`TUTORIAL.md`](TUTORIAL.md): build the Echo example agent end-to-end.
- [`DRY_RUN.md`](DRY_RUN.md): watch a side-effect-safe firing lifecycle before trusting scheduled work.

## Operating Model

- [`../ARCHITECTURE.md`](../ARCHITECTURE.md): design rationale for host scheduling, worktrees, IAM, spend guards, and plan review.
- [`ARCHITECTURE.md`](ARCHITECTURE.md): the diagram companion. Mermaid diagrams for the agent lifecycle, model dispatch and tiers, distributed locking, the Slack conversational flow, the desktop app, the disk guardian, and the layered install and distribution.
- [`IDENTITY_AND_THEMES.md`](IDENTITY_AND_THEMES.md): the canonical identity model. Role-slugs are the identity; themes (the default `batman` roster plus presets and custom names) supply display names; how names resolve across Slack, the desktop app, and the CLI; and how to pick or build a theme.
- [`AGENTS.md`](AGENTS.md): the default engineering roles, the stable runtime identity, and the display-name themes layered on top.
- [`ARCHITECT.md`](ARCHITECT.md): the `architect` role (Batman in the default theme) for features spanning more than one repo. It reads a parent issue, drafts the rollout for operator approval, and files scoped child issues across the named repos.
- [`STATE_MACHINE.md`](STATE_MACHINE.md): issue claim lifecycle and stale-claim recovery.
- [`MERGE_GATE.md`](MERGE_GATE.md): the GitHub-native merge gate. Alfred merges a PR only when GitHub reports it approved (required approval count from branch protection), all review threads resolved, mergeable and clean, and checks green, using a SHA-guarded squash. The two config knobs and the `alfred pr check` / `alfred pr merge` commands.
- [`RECOVERY.md`](RECOVERY.md): failure auto-recovery. When a firing's push step fails on a lint or format hook, a non-fast-forward or conflict, a failing CI check, or a transient network blip, the same engine gets one bounded turn to fix the cause and re-push before the firing holds. Approval-gate, scrub-check, and auth failures are never recovered. The `ALFRED_RECOVERY_MAX_ATTEMPTS` knob and the distinct self-healed telemetry.
- [`VERIFICATION.md`](VERIFICATION.md): the `## Verification evidence` block on every agent PR. Test-check summary, diff summary, engine self-assessment against the issue's acceptance criteria, and optional opt-in before/after screenshots, with an honest "not captured" for anything that could not run.
- [`RUBRIC_GATE.md`](RUBRIC_GATE.md): the optional grade-then-revise gate on the build step. A cheap separate grader reads the diff against a rubric derived from the issue, the implementer revises once on `needs_revision`, and the final verdict is shown honestly in the PR body. Off by default (`ALFRED_RUBRIC_GATE`).
- [`STATE_AND_MEMORY.md`](STATE_AND_MEMORY.md): what Alfred remembers between firings, where every state file lives, and the local fleet-brain memory layer.
- [`FLEET_BRAIN.md`](FLEET_BRAIN.md): local memory schema, reviewable lesson candidates, failure history, CLI, and read-only MCP bridge.
- [`CODE_MEMORY.md`](CODE_MEMORY.md): the code-structure memory layer. codebase-memory-mcp indexes in-scope repos into a code graph and answers read-only symbol, caller, and ownership queries the fleet can call on demand.
- [`BATTERIES.md`](BATTERIES.md): the full battery catalogue. The always-on built-ins and the opt-in enhancements (compression, code-structure memory, dense embeddings, Redis or Postgres memory), what each one is and gets you, the `alfred batteries` command, and the desktop picker. Rendered from the manifest in `lib/batteries.py`.
- [`SKELETON_READS.md`](SKELETON_READS.md): the skeleton and delta reads battery. Structure-only file skeletons and delta-on-re-read, both reusing the existing code map (no vector store), with the orientation-versus-edit-target correctness guarantee and config knobs.
- [`MEMORY_PROVIDERS.md`](MEMORY_PROVIDERS.md): Redis Agent Memory, FleetBrain's local ledger role, provider chaining, and optional read-only fallback stores.
- [`MCP.md`](MCP.md): the MCP servers Alfred attaches to Claude-engine firings only (Codex-routed firings get no MCP). The read-only `alfred_memory` server over the fleet brain, the consumed `code_memory` (codebase-memory-mcp) code graph, per-role tool scoping, safety model, and configuration.
- [`CONVERSATION.md`](CONVERSATION.md): Alfred's conversational surfaces after setup. How a Slack mention, DM, or desktop Ask message becomes a natural, streamed, context-grounded reply (repositories, live fleet status, lessons), when it offers a plan versus answers a question, the streaming transport, the safety rails, and configuration. For conversational setup, see [`ONBOARDING.md`](ONBOARDING.md).
- [`SLACK_UX.md`](SLACK_UX.md): Slack-native message shape, planning replies, approval flow, and anti-patterns.
- [`DESKTOP_CLIENT.md`](DESKTOP_CLIENT.md): Alfred Desktop design rationale and tab-by-tab tour, the Setup screen (guided install plus conversational onboarding and the theme builder), the Slack-native boundary, the `alfred serve` API and native allowlist, and building native installers.
- [`SCREENSHOTS.md`](SCREENSHOTS.md): the desktop / served UI in light and dark - the Ask surface and the two-minute setup, and the plan/build/review/fix/ship loop they drive.
- [`SERVE.md`](SERVE.md): `alfred serve`, the localhost-only control API and browser UI over state, saved architect plans, the fleet brain, and planning drafts. Reads are open on localhost; mutations are token-gated. This is the API Alfred Desktop runs on.
- [`DESIGN.md`](DESIGN.md): the visual language for the native app and the site. Color tokens, the Instrument Sans plus Quicksand plus Fragment Mono type stack, glass surfaces, motion and `prefers-reduced-motion`, and accessibility.
- [`THEME_SYSTEM.md`](THEME_SYSTEM.md): the desktop two-axis theme model. The `data-theme` palette and `.dark`/`.light` mode axes, the `:root` token contract, and glass versus flat surface tokens.
- [`GOALS.md`](GOALS.md): durable goal contract for Slack, CLI, client, planning readiness, evaluator, and memory integration.
- [`PLAIN_MODE.md`](PLAIN_MODE.md): the non-technical intake profile (`ALFRED_INTAKE_PROFILE=plain`).
- [`ENGINE_ROUTING.md`](ENGINE_ROUTING.md): per-codename Claude, Codex, or hybrid routing; precedence chain; default matrix; multi-engine roadmap.
- [`OPERATING_THE_FLEET.md`](OPERATING_THE_FLEET.md): week-two runbook. Daily Slack rhythm, CLI recipes, sentinels, logs, "fleet went quiet" troubleshooting.
- [`CLI.md`](CLI.md): the read-only `alfred metrics` and `alfred logs` inspectors over the state tree, plus `alfred slack-listener`, the optional Socket Mode planning-intake listener.
- [`CONNECTORS.md`](CONNECTORS.md): input connectors that feed the `agent:implement` issue queue from non-GitHub sources such as Linear tickets and Sentry alerts without changing the agents.
- [`CLAUDE_CODE.md`](CLAUDE_CODE.md): Claude Code and Codex install, account routing, engine routing, and quota behavior.
- [`CAPABILITIES.md`](CAPABILITIES.md): read-only local inventory for code graph memory, Alfred's context governor, and engineering skill packs.
- [`TOOL_COMPACTOR.md`](TOOL_COMPACTOR.md): the tool-output compactor. Shrinks noisy Bash output before it enters context, compacting only on a confirmed-success exit so an error is never hidden, plus config knobs.
- [`BENCHMARKS.md`](BENCHMARKS.md): reproducible self-benchmark harness. The fixed task suite, the four metric families read from existing telemetry, how to run before/after, and cost framed as a share of subscription quota.
- [`TELEMETRY.md`](TELEMETRY.md): the opt-out anonymous usage reporter that sends aggregate totals to the public Impact counter, its controls, and how to point it at a self-hosted collector.
- [`CODEX_PROVIDER.md`](CODEX_PROVIDER.md): Codex engine modes, diagnostics, runtime contract, and billing posture.
- [`SLACK_SETUP.md`](SLACK_SETUP.md): incoming webhook, optional bot-token setup, planning listener, trusted control commands, the issue bridge, and in-thread fleet-progress thread-sync.
- [`SLACK_APPROVAL.md`](SLACK_APPROVAL.md): reaction approval gate, trusted feedback users, and Socket Mode listener boundary.
- [`AWS_SETUP.md`](AWS_SETUP.md): per-agent IAM and Secrets Manager setup.
- [`SKILLS.md`](SKILLS.md): recommended Claude Code skills.
- [`INTEGRATIONS.md`](INTEGRATIONS.md): what Alfred does and does not bundle.
- [`LINUX.md`](LINUX.md): running the fleet on Debian/Ubuntu via `systemd --user` timers. Install, deploy, operate, `linger`.
- [`PUBLISHING.md`](PUBLISHING.md): GitHub Pages, release-site, and custom-domain operations.

## Reference

- [`OUTPUT_SAMPLES.md`](OUTPUT_SAMPLES.md): every shape of Slack post, doctor run, issue body, PR, and state JSON in one place.
- [`GLOSSARY.md`](GLOSSARY.md): one-sentence definitions for every role, themed name, label, sentinel, and runtime concept.
- [`ARCHITECT_PARENT_ISSUE_TEMPLATE.md`](ARCHITECT_PARENT_ISSUE_TEMPLATE.md): the exact parent-issue body shape the architect lifecycle parser expects, the gotchas it does not surface, and a copy-paste template.
- [`SHIPPED_EMITTER.md`](SHIPPED_EMITTER.md): the `alfred-shipped-public` emitter that scrubs local state through a field allowlist and redaction table to publish a weekly shipped-work feed for your own repos.
- [`SLOP_DETECTOR.md`](SLOP_DETECTOR.md): the read-only, stdlib-only `alfred slop-detect` scanner that flags LLM-cliche vocabulary and phrasing in prose and exits non-zero in CI.
- [`../lib/agent_runner/`](../lib/agent_runner/__init__.py): shared runtime library (package; public API in `__init__.py`).
- [`../lib/slack_format.py`](../lib/slack_format.py): Slack Block Kit formatting helpers.
- [`../lib/architect_lifecycle.py`](../lib/architect_lifecycle.py): multi-repo bundle primitives.
- [`../bin/`](../bin/): Alfred CLI, init wizard, doctor, deploy helpers, and reference agent runners.
- [`../launchd/`](../launchd/): plist template, renderer, and `agents.conf.example`.
- [`../examples/`](../examples/): minimal example agents, label-state CLI, and pre-push hook.

## Project

- [`../CONTRIBUTING.md`](../CONTRIBUTING.md)
- [`../ROADMAP.md`](../ROADMAP.md)
- [`../CHANGELOG.md`](../CHANGELOG.md)
- [`../SECURITY.md`](../SECURITY.md)
- [`THREAT_MODEL.md`](THREAT_MODEL.md): what one run can and cannot do, the containment boundaries, and how to verify the privacy claim yourself.
- [`MACOS_PERMISSIONS.md`](MACOS_PERMISSIONS.md): every macOS prompt explained, plus the permissions Alfred never requests.
- [`../SUPPORT.md`](../SUPPORT.md)
- [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md)
- [`RELEASING.md`](RELEASING.md): the tag-to-publish release process, including the draft-release gate for desktop assets.

## Tests

Run the whole suite with:

```sh
python3 -m pytest tests/
```

Use `bash bin/scrub-check.sh` before public releases.
