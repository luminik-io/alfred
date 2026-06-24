# Benchmarks

A reproducible way to answer one honest question: **is your fleet getting
better or worse at shipping code, and what does it cost against your
subscription quota?**

This is a **self-benchmark**. It measures your install against its own
past runs (before/after) and reports honest absolute numbers. It is
explicitly **not** a competitive "Alfred beats tool X" claim. There is no
leaderboard here, and every number is read back out of telemetry the fleet
already captured, not fabricated.

The harness is two pieces:

- `lib/benchmark.py` - the reader. Pure stdlib, no LLM calls, no network.
  It turns telemetry on disk into the four metric families below.
- `bin/alfred-benchmark.py` (`alfred benchmark`) - the CLI wrapper:
  print the fixed task suite, run a report, emit JSON.

## What it measures, and where each number comes from

Every metric is harnessed from telemetry the fleet writes anyway. Nothing
here adds new instrumentation.

| Family | Metric | Source on disk |
|---|---|---|
| **Throughput** | PRs opened | `pr_opened` events in `state/<codename>/events/<firing_id>.jsonl` |
| | time to first PR | `firing_started` -> first `pr_opened` timestamp span |
| | median time to PR | median of all observed start -> PR spans |
| **Quality** | merge rate | merged PRs / PRs opened (merged count supplied via `--prs-merged`; merge state lives in the brain/GitHub, not the per-firing log) |
| | CI pass first try | PRs whose firing reached `checks_done` with no `fix_pushed` after the PR opened, over all PRs |
| | human-edit before merge | PRs that needed a `fix_pushed` follow-up commit, over all PRs (proxy for "a human had to edit before merge") |
| | review findings per PR | summed `review_posted` findings / PRs opened |
| **Reliability** | success rate | `successes_today` / (`successes_today` + `failures_today`) from the spend ledger |
| | fallback rate | firings with an `llm_fallback` event / firings observed |
| | self-heal rate | recoverable firings (had a fallback or loop signal) that still opened a PR, over recoverable firings |
| | loop incidents | count of `error_loop_detected` events |
| **Efficiency** | tokens in / out / cache | summed `message.usage` blocks per assistant turn in the stream-JSON transcript (the same field the live dashboard reads in `lib/server/usage.py`) |
| | cache hit rate | `cache_read` / (`input_tokens` + `cache_creation` + `cache_read`) |
| | turns, turns per PR | `turns_today` from the ledger, divided by PRs opened |

Telemetry sources, in one place:

- **Spend ledger** - `state/<codename>/spend-YYYY-MM-DD.json`
  (`SpendState`): firings, successes, failures, turns, cost.
- **Per-firing event log** - `state/<codename>/events/<firing_id>.jsonl`
  (`EventLog`): the typed `firing_started` / `pr_opened` / `llm_fallback`
  / `review_posted` / `checks_done` / `fix_pushed` / `error_loop_detected`
  spine.
- **Stream-JSON transcript** -
  `state/transcripts/<codename>/<YYYY-MM>/<firing_id>.jsonl`: per-turn
  `message.usage` token counters.

Reads are tolerant: a missing file, a torn JSONL tail, an unparseable
timestamp, or a firing with no PR is skipped, never raised. Every rate has
an explicit, non-fabricated denominator and degrades to `0.0` (or `-` for
a missing time) when there is nothing to divide by, so an empty run reports
honest zeros, never a guess.

## The fixed task suite

Reproducibility comes from running the **same representative coding tasks**
against the **same seed repo** every time. The built-in suite:

| task_id | kind | what it asks for |
|---|---|---|
| `fix-flaky-test` | fix | make an order-dependent test deterministic |
| `add-small-endpoint` | feature | add one read-only endpoint + a test |
| `refactor-function` | refactor | split a long function, no behaviour change |
| `add-unit-test` | test | cover one un-covered branch |
| `tighten-validation` | fix | reject an invalid input + a test |

Each task is the kind of bounded change a team hands a junior engineer: a
focused fix, a small additive feature, a mechanical refactor, a test. Print
the suite with `alfred benchmark show-suite`, or write it to a file to edit
or version it:

```
alfred benchmark write-suite ./bench-suite.json
# edit, then:
alfred benchmark report --suite-file ./bench-suite.json
```

Point the suite at your own seed repo (the placeholder is
`acme-org/your-repo`) by filing these as issues there and letting the fleet
pick them up the way it picks up any issue.

## How to run it

The harness deliberately **does not** invoke the model itself. It defines
the suite and reads the result. That separation is what keeps it
deterministic and offline-testable.

1. **Capture a baseline.** Pick a seed repo. File the suite tasks as
   issues (or use `write-suite` and your own intake). Let the fleet run
   them the normal way. The runner writes its normal telemetry.

2. **Read the baseline back.**

   ```
   alfred benchmark report --label before
   ```

   Optionally restrict to the codenames that did the work
   (`--codename lucius`) and pass the merged-PR count you observed
   (`--prs-merged N`), since merge state is not in the per-firing log.

3. **Change something** - a prompt, a model, a budget, an engine route.

4. **Re-run the suite, then read it back** with `--label after` and
   compare the two reports side by side. Same suite, same seed repo: the
   delta is the signal.

For a machine-readable record (to diff, chart, or feed a dashboard):

```
alfred benchmark report --label after --json > bench-after.json
```

Run against any state tree with `--state-dir`, so you can snapshot a run's
`state/` directory and benchmark it later, offline.

## Cost as a share of your subscription quota

Subscription-backed Claude Code does not bill per token. It draws from the
same usage pool your terminal sessions consume (see
[`CLAUDE_CODE.md`](CLAUDE_CODE.md), "Cost vs token-API mental model"). So
the honest cost unit is **not** dollars per PR. It is **what fraction of
your plan's daily budget one PR consumes**.

The harness frames cost as `turns per PR / daily plan turn budget`. The
plan budgets reuse the empirical turn-burn numbers from
[`CLAUDE_CODE.md`](CLAUDE_CODE.md): a typical small-issue firing burns
30-80 turns, a multi-file refactor 150+, and a continuous single-codename
cadence averages 2000-3500 turns/day, which is roughly a Pro day.

| Plan | Daily turn budget (sizing estimate) | Notes |
|---|---|---|
| Claude Pro | ~2,000 | one operator, occasional agent runs |
| Claude Max 5x | ~10,000 | continuous fleet, a few codenames |
| Claude Max 20x | ~40,000 | continuous fleet, many codenames |
| Codex Pro | ~4,000 | independent reviewer / fallback engine |

Worked example: a run that averages **60 turns per PR**:

| Plan | Daily turns | Turns/PR | % quota per PR |
|---|---|---|---|
| Claude Pro | 2,000 | 60 | **3.00%** |
| Claude Max 5x | 10,000 | 60 | **0.60%** |
| Claude Max 20x | 40,000 | 60 | **0.15%** |
| Codex Pro | 4,000 | 60 | **1.50%** |

Read that as: on Pro, one PR at this efficiency costs about 3% of a day's
budget, so the plan sustains roughly 30 such PRs a day before the cap
trips; on Max 20x, the same PR is 0.15%.

These budgets are **sizing estimates, not provider billing guarantees** -
Anthropic and OpenAI own the real reset behaviour and may change it. They
are config-overridable per plan:

```
ALFRED_BENCHMARK_TURN_BUDGET_CLAUDE_MAX_5X=12000 alfred benchmark report
```

(`ALFRED_BENCHMARK_TURN_BUDGET_<PLAN>` upper-cased; a non-numeric or
non-positive value is ignored so a typo can't zero a budget.)

## Results template

Copy this into a PR description or a tracking doc when you record a run.
Fill it from one `alfred benchmark report` (text or `--json`).

```
Benchmark run
  label:        <before | after | v0.5.0 | ...>
  seed repo:    <acme-org/your-repo>
  suite:        <built-in 5-task | path to custom suite>
  date:         <YYYY-MM-DD>
  codenames:    <which agents ran the suite>

Throughput
  PRs opened:            <n>
  time to first PR:      <m>
  median time to PR:     <m>

Quality
  PRs merged:            <m> / <opened>
  merge rate:            <%>
  CI pass first try:     <%>
  human-edit before merge: <%>
  review findings / PR:  <x.xx>

Reliability
  success rate:          <%>  (<completed> completed firings)
  fallback rate:         <%>
  self-heal rate:        <%>
  loop incidents:        <n>

Efficiency
  tokens in / out:       <n> / <n>
  cache hit rate:        <%>
  turns:                 <n>
  turns per PR:          <n>

Cost (subscription quota)
  Claude Pro:            <%> quota / PR
  Claude Max 5x:         <%> quota / PR
  Claude Max 20x:        <%> quota / PR
```

Keep the before/after pair together so the delta is legible. Do not turn it
into a "beats X" claim; the value is the honest trend on your own install.

## Feeding a future desktop Metrics view

`alfred benchmark report --json` already emits the exact shape a desktop
"Metrics" tab would render: the four families, the per-firing observations,
and the quota-cost rows under a single `quota_cost` key. The desktop app's
`alfred serve` API (see [`DESKTOP_CLIENT.md`](DESKTOP_CLIENT.md)) can shell
this command and render the JSON without any new aggregation logic. Wiring
that endpoint and the tab is a **follow-up**; the harness already produces
the contract it would consume, so no schema work is blocked on it.

## Testing the harness itself

The reader is covered by `tests/test_benchmark.py`. The model is fully
mocked there: the tests build a synthetic `state/` tree (spend ledger +
event logs + transcripts with `message.usage` blocks) under a temp dir and
assert the four families and the quota framing. **No LLM is called, no real
disk outside the temp dir is touched, and no quota is burned.** Run them
with the rest of the suite:

```
uv run pytest tests/test_benchmark.py
```
