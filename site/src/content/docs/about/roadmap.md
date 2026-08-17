---
title: Roadmap
description: Current priorities, intended next work, research topics, and product boundaries.
---

This roadmap is forward-looking. The [changelog](/about/changelog/) records
shipped work. The canonical source is
[`ROADMAP.md`](https://github.com/luminik-io/alfred/blob/main/ROADMAP.md).

- **Current priorities** have active design or implementation work.
- **Next** describes the intended sequence after current priorities.
- **Explore** contains research topics with no delivery commitment.
- **Non-goals** records product boundaries.

## Current priorities

### Launch quality

- Publish v0.8.0 with signed and notarized macOS packages, Linux packages,
  concise release notes, and a verified clean-install path.
- Finish the [screen-by-screen Desktop quality pass](https://github.com/luminik-io/alfred/issues/652)
  across Prism, Graphite, and Ledger. Test light and dark mode at every supported
  size. Publish only fixture-backed light-mode media.
- [Reduce Desktop startup work](https://github.com/luminik-io/alfred/issues/657)
  with screen-level bundles, navigation preloading, and a checked size limit.
- [Refresh public screenshots and video](https://github.com/luminik-io/alfred/issues/659)
  after the v0.8.0 screens and features are stable.
- Add a complete scratch-home installation and setup test.
- Record a current request-to-reviewed-PR demo with real approval and evidence.
- Keep the README, site, CLI help, and setup copy aligned with the runtime.
- Launch with a reproducible demo and direct links to the source, install
  guide, threat model, and benchmark method. Submit only to directories whose
  published scope matches the shipped product.

### Engine capability contract

- Keep CLI detection separate from support status.
- Recheck OpenCode's CLI, permission, and event contracts before changing the
  tested minimum version.

### Session and evidence continuity

- [x] [Normalize local session, turn, tool, evidence, repository, branch, PR, role,
  and firing identifiers](https://github.com/luminik-io/alfred/issues/656) across coding CLIs.
- [x] Import an interactive transcript only when the operator names a saved
  run and its exact repository scope. Keep import removal local and reversible.
- Apply redaction before any export.
- Use the same evidence for recovery, review, and run history.

### Memory quality and proof

- [Benchmark memory recall](https://github.com/luminik-io/alfred/issues/654)
  after retrieval-policy changes. Publish the fixture, provider chain, engine,
  false-recall rate, latency, and limitations with each result.
- Show a lesson's source, recall reason, and validity period.
- Keep every memory change visible and reversible.

### Curated batteries

- Keep built-in context controls available without a daemon.
- Keep the default skill set limited to skills that pass the paired task gate.
  Re-run the skill and compression benchmarks when their fixtures, tools, or
  engine versions change.
- Pin external tools and record checksums, versions, licenses, and provenance.
- Package small, opt-in skill and MCP sets by use case.
- Preserve user-owned CLI configuration during setup and removal.

## Next

- Version the `alfred serve` API and add contract tests.
- Expand engine diagnostics to report permissions, MCPs, skills, and config
  ownership alongside the shipped authentication and profile checks.
- Generate reversible role-specific CLI configuration from one capability
  model.
- Track approved multi-repository work to a final merged, dropped, or blocked
  state with one evidence rollup.
- Give durable goals a first-class desktop and CLI view.
- Add a full side-effect-free lifecycle simulation for every shipped role.

## Explore

- Gemini, Ollama, Cline, and other engine adapters.
- Local replay and evaluation of normalized sessions.
- Portable role packs for documentation, release, and repository maintenance.
- A local fleet workspace command that measures engine readiness, worktree
  capacity, repository scope, and queue pressure before it assigns work.
- More code-graph backends with measured retrieval quality.
- Optional remote workers that keep Alfred's scope and approval controls.

## Non-goals

- A hosted, multi-tenant Alfred service.
- A model gateway that replaces local CLI authentication.
- An always-running central orchestrator.
- Automatic merge authority by default.
- An uncurated skill or plugin marketplace.
- Silent repository discovery or configuration takeover.

Read the full [design boundaries and contribution guidance](https://github.com/luminik-io/alfred/blob/main/ROADMAP.md#design-boundaries)
before proposing a scope expansion.
