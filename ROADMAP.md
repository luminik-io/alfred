# Roadmap

This document lists forward-looking work. It does not repeat release history.
See [CHANGELOG.md](CHANGELOG.md) for shipped changes and
[GitHub releases](https://github.com/luminik-io/alfred/releases) for tagged
artifacts.

The sections have different commitment levels:

- **Current priorities** have active design or implementation work.
- **Next** describes the intended sequence after current priorities.
- **Explore** contains research topics with no delivery commitment.
- **Non-goals** records product boundaries.

The order can change when testing finds a reliability or safety problem.

## Current priorities

### Launch quality

- Publish v0.7.5 with signed and notarized macOS packages, Linux packages,
  concise release notes, and a verified clean-install path.
- [Match every Desktop screen](https://github.com/luminik-io/alfred/issues/652)
  to the approved Prism, Graphite, and Ledger references. Verify light and dark
  mode at desktop and phone widths on a real Mac.
- [Reduce Desktop startup work](https://github.com/luminik-io/alfred/issues/657)
  with screen-level bundles, navigation preloading, and a checked size limit.
- Keep the current top-level information architecture unless task testing shows
  that a destination is duplicated or hard to find. Fix screen composition,
  spacing, type, empty states, and responsive behavior without hiding evidence.
- Publish only fixture-backed light-mode screenshots and video. Keep dark-mode
  captures in the internal visual audit.
- [Refresh all public screenshots and video](https://github.com/luminik-io/alfred/issues/659)
  after the v0.8.0 screens and features are stable.
- Require clean Markdown, direct copy, and current verification evidence in
  every pull request description, including documentation-only changes.
- Add a complete scratch-home test. It must install Alfred, configure a fleet,
  start the local API, exercise the desktop-ready setup path, verify optional
  battery status, and remove its temporary state.
- Record a current end-to-end demo from request through reviewed PR. Show real
  failures, approval points, and verification evidence.
- Keep the README, site, CLI help, and setup copy aligned with the shipped
  runtime.
- Launch with a reproducible demo and direct links to the source, install
  guide, threat model, and benchmark method. Submit only to directories whose
  published scope matches the shipped product.

### Engine capability contract

- Separate detection from support. A detected CLI must not become dispatchable
  until its full contract passes.
- Keep the OpenCode adapter pinned to a tested minimum version and recheck its
  CLI, permission, and event contracts when that minimum changes.

### Session and evidence continuity

- [Define one local event envelope](https://github.com/luminik-io/alfred/issues/656)
  for sessions, turns, tools, evidence,
  repositories, branches, pull requests, roles, and firing IDs.
- Link interactive coding sessions to scheduled Alfred work when the operator
  chooses to import them.
- Keep collection local by default. Require explicit repository scope and apply
  redaction before any export.
- Make recovery and review use the same evidence instead of separate harness
  transcripts and Alfred event logs.

### Memory quality and proof

- [Benchmark memory recall](https://github.com/luminik-io/alfred/issues/654)
  after retrieval-policy changes and publish
  the fixture, provider chain, engine, and limitations with each result.
- Show why a lesson was recalled, where it came from, and when it expires.
- Keep promotion, retirement, merge, and revert actions visible and reversible.

### Curated batteries

- Keep built-in context controls enabled without a daemon.
- [Benchmark skills and compression](https://github.com/luminik-io/alfred/issues/655)
  on task outcomes before expanding the default set.
- Pin external tools and record checksums, versions, licenses, and provenance.
- Package small, opt-in skill and MCP sets by use case. Do not bundle an
  unreviewed marketplace.
- Make setup and removal idempotent. Preserve user-owned CLI configuration.

## Next

- Add a versioned `alfred serve` API contract with contract tests and
  stable error payloads.
- Extend engine diagnostics to report permissions, MCPs, skills, and config
  ownership alongside the shipped version, authentication, and scheduler
  profile checks.
- Add role-specific harness configuration writers that derive from the same
  capability model and can undo only the settings Alfred owns.
- Track approved multi-repository work until each child PR is merged, dropped,
  or blocked. Produce one final evidence rollup.
- Give durable goals a first-class desktop and CLI view with constraints,
  verification, evidence, and blocked-state history.
- Expand lifecycle dry runs until every shipped role can execute a full
  side-effect-free simulation.

## Explore

These items need research and proof before they can enter the delivery plan:

- Gemini, Ollama, Cline, and other harness adapters under the same capability
  and containment contract.
- Local replay and evaluation of normalized harness sessions.
- Portable role packs for documentation, release, and repository maintenance.
- A local fleet workspace command that measures harness readiness, worktree
  capacity, repository scope, and queue pressure before it assigns work.
- Additional code-graph backends with measured retrieval and blast-radius
  quality.
- Optional remote workers that preserve Alfred's approval, evidence, and
  repository-scope rules.

## Non-goals

- A hosted, multi-tenant Alfred service.
- A model gateway that replaces local harness authentication.
- An always-running central orchestrator. The host OS remains the scheduler.
- Automatic merge authority by default.
- An uncurated skill or plugin marketplace.
- Silent repository discovery, credential import, or configuration takeover.

## Design boundaries

- **One installation.** Alfred supports one operator or small team on one
  trusted host with one local configuration.
- **Short-lived runs.** `launchd` or `systemd --user` starts each firing.
- **Local harnesses.** Alfred invokes authenticated local CLIs and does not
  proxy model traffic.
- **Explicit repository scope.** Operators choose which repositories Alfred can
  read or change.
- **Reviewable output.** Plans, diffs, tests, evidence, and PRs remain visible.
- **Reversible integration.** Alfred must not overwrite user-owned harness
  settings or make optional tools hard to remove.

## Contributing to the roadmap

A useful proposal includes a real use case, the affected boundary, a small
first increment, failure behavior, and a verification method. Open a discussion
before implementing a change that expands repository access, credentials,
network destinations, merge authority, or the supported-host model.
