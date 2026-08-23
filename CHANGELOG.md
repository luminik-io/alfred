# Changelog

This file records shipped product changes. See [ROADMAP.md](ROADMAP.md) for
planned work. Alfred follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Added a permanent Tools section in Desktop Settings for the same curated battery choices used during onboarding.
- Added the first versioned `alfred serve` endpoints. `GET /api/v1/meta`
  declares the local contract, `GET /api/v1/status` returns fleet readiness,
  and `GET /api/v1/usage` returns subscription headroom. The provider view is
  available at `GET /api/v1/usage/providers`. Every v1 response carries an API
  version header. Unknown routes and unsupported methods return a fixed JSON
  error shape.

### Changed

- Aligned the Learnings lesson list and technical disclosure to one readable
  content width across every Desktop theme and supported window size.
- OpenCode firings now receive the same role-scoped skills as Claude Code and
  Codex. Alfred copies only the selected skill directories into the firing's
  temporary configuration and allows only those skill names.
- Gave Prism a spectral cloud mark, Graphite a text-only wordmark, and Ledger a
  gold target mark. Work now shows full repository names, clearer lifecycle
  cards, and structured evidence details.
- Alfred Desktop now loads reliability actions from `GET /api/v1/actions`.
  The unversioned actions route is no longer served.
- Alfred Desktop and the local runtime health check now use
  `GET /api/v1/status`. The unversioned status route is no longer served.
- Graphify binary and graph paths, Headroom overrides, setup status, and agent
  firings now expand `~` from `HOME`, then `ALFRED_HOME`, so all surfaces use
  the same configured file when a service starts without `HOME`.
- The optional gbrain memory provider now expands `~` from `HOME`, then
  `ALFRED_HOME`. Its configured binary works when `HOME` is unset.
- Graphify overrides now appear ready and attach to runs only when the file is
  executable and its read-only MCP server starts successfully.
- `alfred auth status` now reports OpenCode with Claude Code and Codex. OpenCode
  affects the exit status only when an enabled agent selects it. Dedicated
  `alfred opencode status` and `alfred opencode probe` commands check its CLI
  contract and provider login.

## [0.8.0] - 2026-08-17

### Highlights

- Added OpenCode as a full Alfred engine for scheduled roles, custom agents,
  Desktop conversations, and Slack conversations.
- Added measured memory recall, starter-skill, and compression benchmarks.
  Alfred reports why each lesson was recalled and defaults only to starter
  skills that improved the paired task.
- Saved runs now record the engine, model, timeout, write boundary, memory
  attachments, approvals, decisions, commit, pull request, and imported
  transcript without storing credentials or prompt text.
- Finished the Desktop screen pass across Prism, Graphite, and Ledger, then
  split each primary screen into its own bundle. The initial JavaScript fell
  from 1,489.16 kB to 495.17 kB in the v0.8.0 release build.
- Re-recorded the public Desktop tour and gallery from fixture data on a Mac.
  Public media uses light mode and includes wide and narrow layouts.

### Added

- Added explicit, repository-scoped transcript import for saved runs. Alfred
  copies Claude Code, Codex, or OpenCode output into managed state, verifies the
  repository against the run event log, and can remove its copy without
  changing the source file.
- Added a paired starter-skill benchmark. It runs baseline and skill-assisted
  tasks in fresh local repositories, grades them outside the agent workspace,
  and records task quality, regressions, findings, turns, tokens, prompt bytes,
  elapsed time, engine version, and fixture digest.
- Added Alfred's read-only lesson memory and ready code graph to explicitly
  selected OpenCode firings. OpenCode checks each server before the firing and
  records attached and unavailable servers in local firing evidence.
- Added a compression quality gate that compares raw output, the built-in
  compactor, and Headroom against required failures, paths, line numbers, test
  counts, and final command status before reporting token savings.
- Added a plain reason to every lesson placed in an agent prompt. The CLI and
  Desktop also show the recall provider, source, repository scope, age, expiry,
  and current state for each active lesson.
- Added a provider-only memory recall benchmark for exact terms, wording
  changes, repository scope, current guidance, expired guidance, and true
  misses. The report records precision, recall, false injections, provider
  latency, and prompt bytes without reading operator data or calling a model.
- Added OpenCode as an explicit engine for scheduled roles, custom agents,
  Desktop conversations, and Slack conversations.
- Added bounded OpenCode version, protocol, and authentication checks. Alfred
  requires OpenCode 1.18.18 or newer and a stored provider login.
- Added per-role OpenCode model selection in the CLI, local API, and Desktop.

### Security

- OpenCode runs with an isolated temporary config root, disabled user and
  project config, disabled external plugins and skills, disabled automatic
  sharing, an exact worktree, and automatic rejection of unexpected permission
  prompts. Only Alfred's named read-only MCP tools are allowed. Read-only roles
  deny edits and shell commands. Write roles deny direct push, merge, and
  main-branch checkout commands.

### Changed

- Grouped setup batteries as included, optional local tools, or external
  services. The shared manifest records source, version, licence, integrity,
  install, check, disable, and removal data. `alfred batteries remove` removes
  disabled local dependencies without changing Alfred configuration.
- Re-recorded the public Desktop tour and gallery from the v0.8 fixture on a
  Mac. Public media now uses light mode and includes a checked narrow-window
  Settings capture alongside the wide-screen tour.
- Saved run evidence now records the selected engine route, actual provider,
  model source, binary, capability contract, timeout, write boundary, and
  memory attachment state without storing credentials or prompt text.
- Run evidence now links an approved Architect plan to its durable decision
  record and records the full commit SHA after senior-dev or fixer pushes.
- Limited the default first-party starter set to `spec-to-issues`,
  `review-security`, and `add-observability`. Each improved deterministic task
  results and passed every skill-assisted fixture. The other first-party skills
  remain available for explicit installation.
- Headroom setup now installs the pinned library into Alfred's Python
  interpreter. A CLI-only install no longer appears ready without a configured
  compression command.
- The memory recall benchmark now records searchable-text bytes, final body
  bytes, avoided full-scan bytes, and index/body query counts. SQLite fetches
  all final lesson bodies in one query after ranking IDs and searchable text.
- Renamed the three Desktop appearances to Prism, Graphite, and Ledger. The
  names now describe what each appearance looks like.
- Split Work, Ask, Code, Agents, Settings, Activity, and Learnings into separate
  Desktop bundles. The initial JavaScript in the production build decreased
  from 1,489.16 kB to 494.44 kB.
- Preload a Desktop screen when pointer or keyboard focus reaches its navigation
  control. A build check now rejects an initial bundle above 550 kB raw or 170
  kB gzip.

### Fixed

- Docked Work evidence beside the lifecycle board at standard Desktop widths.
  Public screenshots and video no longer blur the board behind a modal.
- Applied the phone-width media query on the first render, so a quick sidebar
  selection closes the menu and shows the selected screen.
- Updated the visual audit to use the current fixture and complete every
  theme, mode, viewport, workflow, empty-state, and onboarding check.

## [0.7.5] - 2026-08-17

### Highlights

- Added confirmation before removing a trusted collaborator or deleting a
  saved Ask chat.
- Kept keyboard focus on the destructive action and made Escape cancel without
  closing the parent screen.
- Expanded the permanent Desktop audit to 168 fixture-backed captures across
  every theme, mode, and supported width.

### Fixed

- Settings no longer removes a trusted collaborator on the first click.
- Ask no longer deletes a saved conversation on the first click.
- Confirmation dialogs name the affected item, state that the action cannot be
  undone, and preserve a clear Cancel path.

## [0.7.4] - 2026-08-17

### Highlights

- Replaced the brown Ledger dark field with warm graphite surfaces.
- Kept the approved gold accent and clipped-corner treatment.
- Checked the final palette in 144 fixture-backed Desktop captures across all
  themes, modes, and supported widths.

### Fixed

- Ledger dark mode now separates the background, sidebar, cards, and
  overlays without tinting the whole interface brown.
- A focused regression test locks the final Ledger dark cascade values.

## [0.7.3] - 2026-08-17

### Highlights

- Fixed Settings at phone widths. Every section label remains visible, and
  each tab is 38 pixels high.
- Added a GitHub Actions workflow that builds and checks Linux AppImage and
  Debian packages from a signed release tag.
- Checked every primary Desktop screen on this Mac across all three themes,
  light and dark modes, and desktop and phone widths.

### Added

- A release workflow that starts from `main`. It checks the signed tag, commit,
  version, file formats, package metadata, AppImage extraction, and SHA-256
  digests before it adds Linux packages to the matching draft release.

### Fixed

- Settings tabs use a two-column phone layout with complete labels and
  consistent heights.
- The upload job checks that the matching GitHub release is still a draft
  before it adds Linux packages.

## [0.7.2] - 2026-08-17

### Highlights

- Added three fixture-backed light-mode screenshots for the Work board, agent
  run, and approval decision.
- Fixed the short-window Inbox layout so the selected agent card stays visible.
- Added a visible sample-data notice and restored the Ledger approval-card
  edge.

### Added

- A repeatable gallery capture command that produces three 1270 by 760 images
  for launch pages and listings.
- Tests that require fixture data, light mode, fixed image dimensions, and
  identical copies in the documentation and site.

### Fixed

- The agent-role rail no longer clips in short desktop windows.
- Sample run data is now identified in the run view.
- Approval decisions use the approved Ledger clipped-corner treatment.

## [0.7.1] - 2026-08-17

### Highlights

- Added one capability registry for Claude Code and Codex across setup,
  diagnostics, scheduled roles, Slack, and Desktop.
- Added three desktop themes and a Work inspector that shows pull-request
  evidence without treating unavailable data as zero. Reworked every primary
  Desktop screen against the approved theme references at desktop and phone
  widths.
- Made memory query misses stay empty and aligned SQLite, pgvector, and
  FleetBrain on one bounded lexical policy.
- Required explicit trusted scope for code-memory indexing and isolated each
  resolved repository scope in its own graph cache.
- Held behavior-changing memory for review by default.
- Removed the Mac Mini CI path, retired proof assets, and fixed benchmark copy
  that inferred subscription quotas from fixed estimates.

### Added

- A shared engine registry for Claude Code and Codex. Setup, diagnostics,
  scheduled runs, Slack, and Desktop now use the same executable, version,
  authentication, and routing checks.
- Three desktop themes: Prism, Graphite, and Ledger.
  Each theme has coordinated light and dark modes, translucent surfaces, and
  accessible operational states.
- A Work inspector that shows pull-request evidence, checks, reviews, files,
  commits, and unavailable GitHub data without presenting missing evidence as
  zero.
- Per-agent Claude and Codex model controls in Alfred Desktop.
- An optional Graphify code-graph battery.
- A benchmark that measures the memory provider chain Alfred ships by default.

### Changed

- Code-memory indexing and serving require explicit repository scope. Each
  resolved scope has a separate graph cache.
- Code-memory binaries come from an explicit trusted path or Alfred's pinned,
  checksum-verified cache. Ambient `PATH` executables are ignored.
- Memory auto-promotion holds behavior-changing lessons for review by default.
  Factual lessons can still pass the normal evidence and confidence gates.
- Memory recall treats a query miss as a miss. It does not fill the result with
  unrelated recent lessons.
- SQLite, pgvector, and FleetBrain use the same bounded lexical policy for
  technical symbols, Unicode text, tags, scope, and result limits.
- The documentation site now uses Astro 7.2.1 and Starlight 0.41.7.

### Fixed

- Engine probes now have one bounded deadline and use the same environment and
  profile as the later dispatch.
- Hybrid routing fails closed for authentication errors and uses Codex only for
  a supported fallback condition.
- Code-memory status, setup, the launcher, and battery detection now agree on
  scope, cache location, home-directory expansion, and trusted binaries.
- Work evidence collection now has a board-wide request budget, fair selection,
  bounded concurrency, and bounded semaphore waits.
- Desktop Settings tabs keep full labels at narrow window widths.
- Desktop navigation, Work lanes, the evidence inspector, the one-agent roster,
  and Runtime settings keep readable proportions across supported window sizes.
- Public screenshots and video use fixture data in light mode. The internal
  visual audit still checks all three themes in light and dark mode.
- Public PR metadata rejects escaped newline markers and automated attribution.

### Removed

- The Mac Mini self-hosted CI workflow, runner, CLI, tests, and configuration.
- The retired proof workflow and its generated assets.
- Older desktop themes replaced by the current theme system.

## [0.6.0] - 2026-07-10

### Highlights

- Added stable role identities with optional roster themes.
- Added a real conversational path for Desktop Ask and Slack.
- Added the local `alfred demo` workflow and bundled sample repository.
- Added ranked, budgeted memory injection and broader reliability controls.
- Added security fixes for repository grounding and path validation.

## [0.5.3] - 2026-06-24

### Highlights

- Published signed macOS packages and Linux desktop packages.
- Added direct Ask conversations and the primary workflow canvas.
- Added failure classification, bounded retry, and capability-based fallback.
- Added review-first memory capture and disk-pressure recovery.

## [0.5.2] - 2026-06-22

This changelog version was not published as a git tag.

### Highlights

- Added repository graph support to the code-map workflow.
- Improved Slack planning threads and desktop visual checks.
- Refined the public site, telemetry handling, and setup documentation.

## [0.5.1] - 2026-06-17

### Highlights

- Added stable desktop download links and signed-package documentation.
- Standardized the local API on port 7010.
- Added the first public aggregate telemetry controls.
- Updated desktop setup and navigation for the packaged application.

## [0.5.0] - 2026-06-15

### Highlights

- Added the Tauri desktop application for macOS and Linux.
- Added live Claude and Codex subscription-usage views from local CLI state.
- Added approval gates for planned single-repository work.
- Added step-level run events, disk safeguards, and review-first lessons.

## [0.4.0] - 2026-05-23

### Highlights

- Split the agent runner into focused modules with shared preflight controls.
- Added the local server API, planning assistant, FleetBrain, connectors, and
  the first memory-provider interface.
- Added multi-repository planning and a documented agent state machine.

## [0.3.0] - 2026-05-21

### Highlights

- Added Linux scheduling through `systemd --user`.
- Added dry-run mode, operator control commands, and authentication diagnostics.
- Added role-based model routing and the first public documentation site.

## [0.2.1] - 2026-05-12

### Highlights

- Added release automation and source checksum output for Homebrew packaging.
- Added stricter CI, secret scanning, and public metadata checks.

## [0.2.0] - 2026-05-12

### Highlights

- Added shared preflight checks, locks, worktree cleanup, and daily limits.
- Added the first GitHub and Slack coordination workflows.
- Added install, doctor, and scheduler rendering commands.

## [0.1.0] - 2026-05-02

### Highlights

- Published the first Alfred fleet with scheduled planner, implementer,
  reviewer, test, fixer, cleanup, and health roles.
