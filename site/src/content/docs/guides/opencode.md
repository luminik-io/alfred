---
title: OpenCode
description: Install, authenticate, select, and diagnose the OpenCode engine.
---

Alfred can run a scheduled role through OpenCode 1.18.18 or newer. OpenCode is
an explicit engine choice. It is not part of the default Claude-first hybrid
route.

## Install and connect a provider

```sh
brew install anomalyco/tap/opencode
opencode auth login
opencode auth list
alfred engine doctor
```

Alfred requires a provider stored by `opencode auth login`. If the scheduler
cannot find the binary, set `OPENCODE_BIN` in `$ALFRED_HOME/.env`.

## Select the engine

```sh
alfred engine set senior-dev opencode
alfred model set senior-dev opencode anthropic/claude-sonnet-4-5
alfred engine doctor senior-dev
```

Model values use OpenCode's `provider/model` form. The same controls are in the
Desktop agent drawer.

## Firing boundary

Each firing uses a new OpenCode session, an exact worktree, an isolated
temporary config, disabled external plugins, disabled automatic sharing, and
newline-delimited JSON events. Alfred does not pass `--auto`, so an unexpected
permission request is rejected.

Project subagents, skills, and MCP tools are denied for the firing.

Read-only roles deny edits and shell commands. Approved write roles can edit
and run shell commands in the worktree, while direct push, merge, and
main-branch checkout commands are denied. This is not an operating-system or
network sandbox. Use a dedicated user, virtual machine, or container when you
need host isolation.

The full contract, failure states, and scope limits are in
[`docs/OPENCODE.md`](https://github.com/luminik-io/alfred/blob/main/docs/OPENCODE.md).
