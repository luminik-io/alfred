---
title: Changelog
description: Recent Alfred releases and links to the complete version history.
---

The canonical version history is
[`CHANGELOG.md`](https://github.com/luminik-io/alfred/blob/main/CHANGELOG.md).
It follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Tagged artifacts are
on [GitHub Releases](https://github.com/luminik-io/alfred/releases).

Use the [roadmap](/about/roadmap/) for future work. The changelog records only
changes that are merged or released.

## Unreleased

The
[`Unreleased` section](https://github.com/luminik-io/alfred/blob/main/CHANGELOG.md#unreleased)
lists the permanent Desktop tool manager, the Prism, Graphite, and Ledger
branding and Work view updates, the versioned local API, path handling fixes,
stricter Graphify probes, and OpenCode status and probe commands.

## 0.8.0 (2026-08-17)

Version 0.8.0 adds OpenCode, benchmarks memory recall, starter skills, and
compression, records each run's configuration and evidence, and finishes the
Desktop screen pass.

- OpenCode can run scheduled roles, custom agents, Desktop conversations, and
  Slack conversations after version, protocol, login, and containment checks.
- Memory recall reports its reason, provider, source, repository scope, age,
  expiry, and current state. A provider-only benchmark measures false
  injections, latency, prompt bytes, and query work.
- Paired task tests limit the default starter set to three skills that improved
  their fixtures. A separate gate checks whether compression keeps required
  facts before it reports token savings.
- Saved runs record engine and model selection, capability checks, timeout,
  write boundary, memory attachments, approvals, decisions, commit, pull
  request, and an optional imported transcript.
- Prism, Graphite, and Ledger pass the screen matrix in light and dark modes at
  desktop, tablet, and phone widths. Public screenshots and video use fixture
  data in light mode.
- Screen-level bundles cut initial Desktop JavaScript from 1,489.16 kB to
  495.17 kB in the v0.8.0 release build.

Read the complete
[`0.8.0` entry](https://github.com/luminik-io/alfred/blob/main/CHANGELOG.md#080---2026-08-17).

## 0.7.5 (2026-08-17)

Version 0.7.5 prevents accidental collaborator removal and saved-chat deletion
in Alfred Desktop.

- Both actions now require a clear confirmation.
- The affected collaborator or chat is named before removal.
- Escape cancels the action without closing the parent screen.
- The permanent Desktop audit now checks 168 fixture-backed captures across all
  themes, modes, and supported widths.

Read the complete
[`0.7.5` entry](https://github.com/luminik-io/alfred/blob/main/CHANGELOG.md#075---2026-08-17).

## 0.7.4 (2026-08-17)

Version 0.7.4 replaces the brown Ledger dark field with warm graphite
surfaces while keeping its gold accent and clipped-corner treatment.

- The background, sidebar, cards, and overlays now have clear separation.
- The final palette passes the 144-state fixture-backed Desktop screen matrix.
- A focused test locks the final dark-mode cascade values.

Read the complete
[`0.7.4` entry](https://github.com/luminik-io/alfred/blob/main/CHANGELOG.md#074---2026-08-17).

## 0.7.3 (2026-08-17)

Version 0.7.3 fixes Settings at phone widths and adds a GitHub Actions workflow
that builds and checks Linux packages from signed release tags.

- Settings shows every section label in a two-column phone layout. Each tab is
  38 pixels high.
- The Linux workflow checks the tag, source version, AppImage, Debian
  package, and SHA-256 digests before it uploads files to a draft release.
- The Desktop screen matrix passes for all three themes in light and dark modes
  at desktop and phone widths.

Read the complete
[`0.7.3` entry](https://github.com/luminik-io/alfred/blob/main/CHANGELOG.md#073---2026-08-17).

## 0.7.2 (2026-08-17)

Version 0.7.2 adds the public launch gallery, keeps the selected agent visible
in short windows, and restores the approved approval-card treatment.

- Three fixture-backed screenshots show the Work board, agent run, and approval
  decision in light mode at 1270 by 760.
- The short-window Inbox keeps the selected agent card visible.
- The run view identifies sample data.
- Ledger approval cards use the approved clipped-corner treatment.
- The capture command and tests keep the documentation and site images
  identical.

Read the complete
[`0.7.2` entry](https://github.com/luminik-io/alfred/blob/main/CHANGELOG.md#072---2026-08-17).

## 0.7.1 (2026-08-17)

Version 0.7.1 unifies engine checks, tightens memory recall, and refreshes the
Desktop workflow.

- One capability registry now controls engine discovery, authentication,
  routing, setup, diagnostics, scheduled roles, Slack, and Desktop.
- Prism, Graphite, and Ledger provide coordinated
  light and dark desktop themes. The Work inspector shows checks, reviews,
  files, commits, and unavailable GitHub evidence. Every primary screen is
  checked at desktop and phone widths against the approved theme references.
- Query-bearing memory recall now keeps misses empty. SQLite, pgvector, and
  FleetBrain use one bounded lexical policy for technical symbols, Unicode
  text, tags, scope, and result limits.
- Code-memory indexing requires explicit repository scope and a trusted binary.
  Each resolved scope has a separate graph cache.
- Behavior-changing memory is held for review by default.
- The release removes Mac Mini CI, retired proof assets, and fixed subscription
  quota estimates from benchmark output.
- Public product media uses fixture data in light mode. Pull-request metadata
  rejects escaped newline markers and automated attribution.

Read the complete
[`0.7.1` entry](https://github.com/luminik-io/alfred/blob/main/CHANGELOG.md#071---2026-08-17).

## 0.6.0 (2026-07-10)

Version 0.6.0 made role identity stable, changed the default recalled-lesson
store to embedded SQLite, added optional context and scale batteries, and added
conversational setup.

- Stable role slugs now control runtime identity. Themes change display names
  without changing schedules, labels, or worktrees.
- Desktop setup and theme configuration can run through an allowlisted
  conversation with approval before each write.
- The embedded SQLite memory provider gives the default install lexical recall
  without Redis or another daemon. Dense embeddings remain optional.
- Memory gained typed links, validity data, consolidation, reuse counts, and a
  repository-profile injector.
- Built-in context controls compact successful tool output, read large files by
  structure and delta, rank recalled lessons, and provide blast-radius context.
- The battery manifest exposes optional Redis, Postgres with pgvector,
  Headroom, dense embeddings, and code-graph integrations.
- Curated skill packs record license and provenance data and require explicit
  installation where network access is needed.
- `alfred demo` exercises plan, approval, implementation, review, fix, and
  verification against a temporary repository.
- Runner self-halt logic stops repeated failures from consuming more work.
- A repository-grounding path traversal issue was contained.

Read the complete
[`0.6.0` entry](https://github.com/luminik-io/alfred/blob/main/CHANGELOG.md#060---2026-07-10).

## Earlier releases

- [0.5.3](https://github.com/luminik-io/alfred/blob/main/CHANGELOG.md#053---2026-06-24): signed desktop packages, conversational Ask, workflow and timeline changes, reliability, code memory, and auth fixes.
- [0.5.2](https://github.com/luminik-io/alfred/blob/main/CHANGELOG.md#052---2026-06-22) (changelog entry, no published tag): Slack planning, code-map graph support, visual checks, and site updates.
- [0.5.1](https://github.com/luminik-io/alfred/blob/main/CHANGELOG.md#051---2026-06-17): first-run trust work, desktop download path, dry-run coverage, and planning controls.
- [0.5.0](https://github.com/luminik-io/alfred/blob/main/CHANGELOG.md#050---2026-06-15): native desktop client, approval gate, disk guardian, Slack issue bridge, and review-first memory.
- [0.4.0](https://github.com/luminik-io/alfred/blob/main/CHANGELOG.md#040---2026-05-23): runner decomposition, observability, state machine, planning, FleetBrain, connectors, and `alfred serve`.
- [0.3.0 and earlier](https://github.com/luminik-io/alfred/blob/main/CHANGELOG.md#030---2026-05-21): Linux scheduling, dry-run mode, control commands, auth diagnostics, and the initial fleet setup.
