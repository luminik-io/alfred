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
currently has no pending changes.

## 0.7.0 (2026-08-16)

Version 0.7.0 unifies engine checks, tightens memory recall, and refreshes the
Desktop workflow.

- One capability registry now controls engine discovery, authentication,
  routing, setup, diagnostics, scheduled roles, Slack, and Desktop.
- Signal Edge, The Category Standard, and Linked Fold provide coordinated
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
[`0.7.0` entry](https://github.com/luminik-io/alfred/blob/main/CHANGELOG.md#070---2026-08-16).

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
