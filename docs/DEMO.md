# The demo

`alfred demo` is the fastest way to see what Alfred does. It runs the whole
loop once, on a throwaway repo, and asks nothing of you except a working
`claude` CLI.

```sh
alfred demo
```

One run plans, builds, reviews, verifies, and creates a local pull-request
summary in a throwaway repository.

From a source checkout:

```sh
./bin/alfred demo
```

## What you need

One thing: the Claude Code CLI, signed in with the Claude subscription you
already pay for.

```sh
claude   # run once and sign in
```

No GitHub, no Slack, no API key, no token, no repo of your own. If the
`claude` CLI is missing, the demo prints an install pointer and stops.

## What it does

The demo copies the bundled sample project at
[`examples/demo-repo`](../examples/demo-repo) (a tiny Python string library
called `textkit`) into a temporary directory, makes it a real git repo, and
runs a compressed version of the real fleet loop against it with real
`claude` calls:

1. **Plan.** Drake reads the sample project and drafts a short plan to add
   the missing `slugify` helper.
2. **Approve.** The run pauses at an operator approval gate. You press Enter
   to approve, or type `n` to decline. The demo pauses once so you can inspect
   the plan before it enters the queue. After approval, build, review, and
   verification continue without another prompt.
3. **Build.** Lucius implements the plan directly in the worktree.
4. **Review.** Ra's al Ghul reviews the change. The sample
   project ships with a planted bug in its existing `titlecase` function: it
   silently collapses runs of consecutive whitespace and drops leading and
   trailing whitespace, which directly contradicts the spacing-preservation
   contract its own docstring documents, and the existing tests do not cover
   it. The bug is real and manifest: the review prompt has the reviewer walk
   each existing function's documented contract and run an actual reproduction
   before blocking. If the reviewer finds the bug, it must include the
   reproduction in its finding. A model can miss the bug on a given run.
5. **Fix.** If review blocks the change, Lucius applies the requested fix and
   adds a regression test.
6. **Ship.** Before anything is declared shipped, the demo verifies the work:
   it requires real changes in the worktree, runs the sample test suite, and
   requires the commit to produce a non-empty diff. Only then is the change
   committed locally and a pull-request-style summary printed from the real
   diffstat. There is no remote and no push: the "ship" is a real local
   commit, never a fabricated one.

At the end it prints the measured run time and a pointer to
[`../INSTALL.md`](../INSTALL.md) for pointing Alfred at your own repos.

## How long it takes

The demo makes sequential `claude` calls for planning, building, reviewing,
and, when needed, fixing. Run time depends on the selected model, provider
latency, and whether review requests a fix. The closing line reports the
measured time.

The plan step is a one-shot summary with no tool use, so it uses a small model
by default. Build, review, and fix use the configured default model.

## Failure behavior

- If the `claude` CLI is missing, it says so and points you at the installer.
- If a model call fails mid-run, it stops at that step and tells you which
  one, rather than pretending it shipped.
- If the engine reports success but leaves the worktree unchanged, the ship
  step refuses to commit and the run fails. The same rule applies if the sample test
  suite fails after the change, or if the commit would produce an empty diff.
- If the reviewer returns prose without an explicit verdict token, the run
  fails at the review step. A missing verdict is never treated as approval.
- If the review pass does not flag the planted bug on a given run, it says so
  and still ships the reviewed change. Re-run to see the catch.

## Flags

| Flag | Effect |
| --- | --- |
| `--keep` | Keep the throwaway demo repo instead of deleting it, and print its path so you can inspect the real diff. |
| `--yes` | Auto-approve the plan gate without waiting for Enter. Useful for a scripted or recorded run. |
| `--timeout N` | Per-step engine wall-clock ceiling in seconds (default 90). |

## Environment overrides

| Variable | Effect |
| --- | --- |
| `ALFRED_DEMO_MODEL` | Force one model for every step. |
| `ALFRED_DEMO_FAST_MODEL` | Override the fast model used for the plan step (default `haiku`). |
| `ALFRED_DEMO_VERBOSE` | Print per-step engine notes to stderr. |
| `CLAUDE_BIN` | Path to the `claude` binary if it is not on `PATH` as `claude`. |

## After the demo

The demo is a taste of one run on a toy repo. The real fleet runs unattended
against your own repos, opens real pull requests on GitHub, and holds work at
the approval rules you configure. Start with [`../INSTALL.md`](../INSTALL.md),
then use Alfred Desktop to choose repos, roster names, and schedule, or use
`alfred-init` to configure agents, repos, schedule, and Slack.

## Public tour recording

Maintainers can rebuild the public desktop tour from the repository fixture:

```sh
npm --prefix clients/desktop run capture:tour
```

The command records the synthetic contract data in
`clients/desktop/e2e/alfred-api.fixture.ts`. It writes the MP4 and poster to
`docs/media/` and `site/public/media/`. Do not record a live Alfred runtime,
operator account, Slack workspace, or repository for public media.
