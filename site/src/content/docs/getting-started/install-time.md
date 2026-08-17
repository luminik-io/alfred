---
title: What affects install time
description: Prerequisites and external setup steps that determine how long Alfred takes to install.
---

Install time depends mainly on external account and host setup. Alfred cannot
complete those steps for you.

This page mirrors
[`docs/INSTALL_TIME.md`](https://github.com/luminik-io/alfred/blob/main/docs/INSTALL_TIME.md).

## Fast path

Setup is short when the host already has:

- macOS with Homebrew, or a supported Debian or Ubuntu environment;
- Python 3.11 or newer;
- an authenticated `gh` CLI with access to the target repositories;
- an authenticated Claude Code, Codex, or OpenCode CLI;
- at least one target repository cloned locally.

Install Alfred, select the repositories, then run:

```sh
alfred auth status
alfred doctor
alfred dry-run senior-dev
```

## Steps that add time

- Creating or approving a GitHub account, organization membership, or token.
- Installing and authenticating a coding CLI.
- Obtaining Slack app approval when Slack is required.
- Installing desktop build dependencies for source development.
- Fixing repository permissions, scheduler permissions, or missing build tools.

Slack is optional. Skip it during setup and add it later.

## Before you start

- [ ] `gh auth status` succeeds for the repositories you will configure.
- [ ] `claude` or `codex` runs non-interactively after sign-in.
- [ ] The target repositories exist as local git checkouts.
- [ ] The local user has permission to create worktrees and scheduler entries.
- [ ] You have chosen whether to enable Slack and optional batteries.

Use `alfred run senior-dev --force` when you want to test one configured role
without waiting for its normal interval.

## See also

- [Install](/getting-started/install/)
- [AI-assisted install](/getting-started/ai-assisted-install/)
- [Dry-run mode](/getting-started/dry-run/)
