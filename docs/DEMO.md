# The demo

`alfred demo` is the fastest way to see what Alfred does. It runs the whole
loop once, on a throwaway repo, and asks nothing of you except a working
`claude` CLI.

```sh
alfred demo
```

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
   to approve, or type `n` to decline. This is the same control you keep over
   the real fleet: nothing proceeds without your say-so.
3. **Build.** Lucius implements the plan directly in the worktree.
4. **Review.** Ra's al Ghul reviews the change adversarially. The sample
   project ships with a subtle planted bug in its existing `titlecase`
   function (it silently collapses runs of consecutive whitespace, and the
   existing tests do not cover it). The bug is real and manifest: the review
   prompt requires the reviewer to run an actual reproduction before blocking,
   so the catch is verified, not recited.
5. **Fix.** Lucius applies the fix the reviewer demanded and adds a
   regression test.
6. **Ship.** Before anything is declared shipped, the demo verifies the work:
   it requires real changes in the worktree, runs the sample test suite, and
   requires the commit to produce a non-empty diff. Only then is the change
   committed locally and a pull-request-style summary printed from the real
   diffstat. There is no remote and no push: the "ship" is a real local
   commit, never a fabricated one.

At the end it prints the measured run time and a pointer to
[`../INSTALL.md`](../INSTALL.md) for pointing Alfred at your own repos.

## How long it takes

The demo makes four real, sequential `claude` calls (plan, build, review,
fix), so it is bounded by real model latency, not by a canned script.
Expect roughly two to three minutes of run time on a typical connection. The
closing line reports the actual measured time for your run, honestly.

The read-only reasoning steps (plan and review) run on a small fast model by
default to keep the run tight; the code-editing steps (build and fix) use the
default model so the shipped change is reliable.

## It is honest by construction

The demo never fakes success (the fleet's core product rule: real progress
only).

- If the `claude` CLI is missing, it says so and points you at the installer.
- If a model call fails mid-run, it stops at that step and tells you which
  one, rather than pretending it shipped.
- If the engine reports success but leaves the worktree unchanged, the ship
  step refuses to commit and the run fails honestly. Same if the sample test
  suite fails after the change, or if the commit would produce an empty diff.
- If the reviewer returns prose without an explicit verdict token, the run
  fails at the review step. A missing verdict is never treated as approval.
- If the review pass happens not to flag the planted bug on a given run
  (it must verify a real reproduction before blocking), it says so plainly
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
| `ALFRED_DEMO_FAST_MODEL` | Override the fast model used for the plan and review steps (default `haiku`). |
| `ALFRED_DEMO_VERBOSE` | Print per-step engine notes to stderr. |
| `CLAUDE_BIN` | Path to the `claude` binary if it is not on `PATH` as `claude`. |

## After the demo

The demo is a taste of one run on a toy repo. The real fleet runs unattended
against your own repos, opens real pull requests on GitHub, and holds work at
the approval rules you configure. Start with [`../INSTALL.md`](../INSTALL.md),
then `alfred-init` to choose agents, repos, codenames, and Slack.
