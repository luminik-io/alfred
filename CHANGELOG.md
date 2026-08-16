# Changelog

This file records shipped product changes. See [ROADMAP.md](ROADMAP.md) for
planned work. Alfred follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

No changes are pending.

## [0.7.0] - 2026-08-16

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
- Three desktop themes: Signal Edge, The Category Standard, and Linked Fold.
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
