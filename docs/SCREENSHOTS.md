# Screenshots

Alfred's desktop app (and the same UI served in a browser via `alfred serve`)
runs entirely on your machine. It is theme-aware, so every screen is shown here
in both light and dark.

## The tour

A short recording of the whole surface end to end: the decide inbox, asking a
question, the agent workflow graph, a live roster re-skin, the learnings view,
and the work board with real merged pull requests.

<p align="center">
  <a href="media/alfred-tour.mp4"><img src="media/alfred-tour.webp" alt="Alfred product tour" width="820"></a>
</p>

[Watch the full tour (MP4, 55s)](media/alfred-tour.mp4). Every frame is live
local state from a running `alfred serve`, nothing mocked.

## Real work, on camera

Alfred runs its own fleet against this repo, `luminik-io/alfred`. These frames
are from that live setup: the fleet plans, builds, reviews, and opens pull
requests on its own codebase, including merged work like
[`#528`](https://github.com/luminik-io/alfred/pull/528).

| Ask explains the loop | Work board, runs in flight |
|---|---|
| ![Ask explains the loop](images/real/ask-explains-the-loop.png) | ![Work board with runs in flight](images/real/work-board-live.png) |

| Workflow graph mid-run | A pull request the fleet opened |
|---|---|
| ![Workflow graph mid-run](images/real/workflow-graph-firing.png) | ![The fleet-authored pull request](images/real/pr-528.png) |

## Ask: from a question to a plan to a pull request

The Ask surface is where a plain-English request becomes real work. Ask a
question and Alfred answers; describe a change and it shapes a plan you can file
as a GitHub issue for the fleet to build, review, and ship. No API keys: Alfred
runs on the Claude and Codex subscriptions you already pay for.

| Light | Dark |
|---|---|
| ![Ask, light](images/ask-light.png) | ![Ask, dark](images/ask-dark.png) |

## Setup: guided install to a working fleet

Guided, conversational onboarding checks your machine, connects to GitHub,
picks your team, and ends on a real result you can see. Scheduled agents can run
in the background; gated plans and shipping stop for your approval.

| Light | Dark |
|---|---|
| ![Setup screen, light theme](images/setup-light.png) | ![Setup screen, dark theme](images/setup-dark.png) |

## The loop these screens drive

Behind the UI, a request runs the full engineering loop on your own machine:

```
plan  ->  approve  ->  build  ->  review (adversarial)  ->  fix  ->  ship
```

You can watch the whole loop end to end on a throwaway repo, with no GitHub,
Slack, or tokens, by running:

```sh
alfred demo
```

It plans a feature, waits for your approval, builds it, reviews it (the reviewer
is prompted to find real problems), applies the fix the reviewer demanded, runs
the tests, and ends on a PR-style summary.
