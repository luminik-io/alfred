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

**Reserved codenames.** Auto-discovery (when you do not pass `--codename`)
walks the top level of the state dir and treats `transcripts`, `codex`,
`fleet`, and `engines` as infrastructure trees, not agents, so it skips
them. Do not name an agent any of these: under auto-discovery its event
log is invisible to the harness (a `--verbose` run logs a debug notice when
a reserved name with an `events/` dir is skipped). If you must scan one of
these directories, name it explicitly with `--codename <name>`, which
bypasses the reserved list.

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
   (`--codename senior-dev`) and pass the merged-PR count you observed
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

## Memory A/B: the repeated-mistake-rate

The telemetry reader above answers "is the fleet getting better or worse". A
second, separate benchmark answers a question no chat-recall leaderboard asks:
**when a repo has already taught the fleet a lesson, does durable memory stop
the next firing from repeating the mistake that lesson was about?**

This is the benchmark category Alfred owns: coding-fleet memory, measured by the
**repeated-mistake-rate**, not chat recall. LongMemEval and LoCoMo score whether
a model can retrieve a fact from a long conversation. They do not score whether
memory changes what an agent *does* to a codebase. That behavioural delta is the
whole point of fleet memory, and it is what this A/B measures.

Run it:

```
# Offline, deterministic, no model, no quota - proves the harness and prints
# an ILLUSTRATIVE result you can read the shape of:
alfred benchmark memory --stub

# A real memory-ON vs memory-OFF A/B (burns real quota):
alfred benchmark memory --engine claude
alfred benchmark memory --engine claude --json > mem-after.json

# Just the paired task suite:
alfred benchmark memory --show-suite
```

### How the A/B is built

The *same* task suite runs twice against the *same* seed repo and the *same*
seeded lessons. The only variable between the two arms is memory:

- **memory ON** uses a provider seeded with the lessons the fleet has already
  "learned" about the seed repo (the real in-memory FleetBrain, or your
  configured provider chain), and injects recalled lessons through the exact
  path a live firing uses (`format_memory_context`).
- **memory OFF** uses `NullMemoryProvider`: it recalls nothing and injects
  nothing. It is a true no-memory control, not memory-with-an-empty-store.

Each suite task is a bounded coding change that *re-tempts a specific known
mistake* the seeded lesson warns about (a naive `datetime.now()`, a bare
`except: pass`, a mutable default argument, an N+1 query). A task's output is
judged deterministically: declared `mistake_markers` (regexes) mean the known
mistake was repeated; `success_markers` with no mistake mean it was solved.
There is **no LLM judge** in the loop, so the verdict is reproducible.

### Metrics, and the exact denominator of each

| Metric | Definition | Denominator |
|---|---|---|
| **repeated-mistake-rate** (headline) | mistakes repeated on the arm | **N** = suite tasks flagged `repeats_known_mistake` (a control task never counts). `None` when N = 0 |
| task success rate | tasks solved (success marker, no mistake marker) | tasks attempted |
| tokens / turns | summed engine cost, plus per-task figures | tasks attempted |
| retrieval **recall** of the right lesson | relevant lessons recalled | total relevant lessons, over tasks that declare one. `None` only when no task declares a relevant lesson |
| retrieval **precision** of the right lesson | relevant lessons recalled | all lessons recalled for those tasks. `None` when nothing was recalled (memory-OFF) |

The report always prints **N**, the per-arm rates, the **delta** (`off - on`),
and a per-task table (did each task repeat its mistake, off vs on). It never
prints a solo "memory is X% better" number: the headline is meaningless without
the N it was measured over and the per-task breakdown behind it.

### Reproducibility

1. **Pick or write a fixture.** The built-in fixture lives at
   `tests/fixtures/mem-bench/` and has three parts: `lessons.json` (the lessons
   the fleet "learned", including distractors), `tasks.json` (the paired tasks
   with their mistake/success markers and the relevant lesson id), and `repo/`
   (a tiny deterministic sample repo an engine can edit). Point at your own with
   `--fixture DIR`.
2. **Capture a baseline** with `--engine <name> --label before --json`.
3. **Change something** - the memory provider, the recall limit, a prompt.
4. **Re-run** with `--label after --json` and compare. Same suite, same seed
   repo, same seeded lessons: the delta is the memory signal.

### Caveats (read before quoting a number)

- **Marker fidelity is the honest limit.** The mistake/success verdict is a
  regex match against solver output. A marker that is too loose or too tight
  mis-scores a task. Markers live in `tasks.json`; audit them for your fixture.
- **The local FleetBrain fallback recalls by recency, not semantics.** The
  literal-substring match surfaces a lesson whose body contains the task's
  recall query, then backfills by recency up to the limit. Per-task *semantic*
  discrimination is the Redis Agent Memory layer's job (see
  [`MEMORY_PROVIDERS.md`](MEMORY_PROVIDERS.md)); a fixture that leans on the
  local fallback measures recency retrieval, and precision reflects the
  distractor share in the top-K. Say which backend a result used.
- **`--stub` numbers are illustrative, not a result.** The stub solver is
  deterministic and reacts only to whether the lesson text reached the prompt.
  It exercises the harness (recall, injection, scoring) with no model; it is
  **not** evidence about any real engine. Only `--engine` runs produce a real
  result.
- **N is small by design.** The fixture is a handful of tasks. Report N; do not
  extrapolate a 4-task delta into a population claim.

### LongMemEval-S is an optional secondary check only

If you want an external comparability point, LongMemEval-S can be run as a
*secondary* chat-recall sanity check - "does the memory layer at least retrieve
facts as well as a standard recall benchmark". It is **never the headline**. The
headline for coding-fleet memory is the repeated-mistake-rate above, because
chat-recall accuracy does not tell you whether memory changed what the fleet
*did* to the code. Keep any LongMemEval-S number in a clearly separate
"secondary comparability" row, not next to the repeated-mistake-rate.

### Results template (illustrative until you run it)

The table below is a **template with placeholders**, not a result. Fill it from
one `alfred benchmark memory --engine <name> --json`. Until a real run fills it,
leave it marked illustrative - do not paste stub numbers here as if they were a
result. A real `--engine claude` run of this template is filled in under
[Real-engine result](#real-engine-result-v060-engineclaude) below.

```
Memory A/B run                     (ILLUSTRATIVE until a real --engine run fills it)
  label:        <before | after | ...>
  seed repo:    tests/fixtures/mem-bench/repo   (or your fixture)
  memory backend: <fleet-local (recency) | redis+fleet (semantic)>
  solver:       <engine:claude | engine:codex>
  N (tasks that re-tempt a learned mistake): <n>

  repeated-mistake-rate     memory OFF: <%>     memory ON: <%>     delta: <+pts>
  task success rate         memory OFF: <%>     memory ON: <%>
  retrieval precision/recall (ON only):  <%> / <%>
  tokens in / turns         memory OFF: <n>/<n>  memory ON: <n>/<n>

  per-task (mistake repeated?  off / on):
    <task_id>               off=<yes|no>  on=<yes|no>
    ...

  secondary comparability (optional, NOT the headline):
    LongMemEval-S recall@<k>: <%>
```

Keep the OFF/ON pair together so the delta is legible, always next to N and the
per-task rows.

### Real-engine result (v0.6.0, engine:claude)

This is a **real** `--engine claude` run of the template above, not the stub. It
ran the built-in fixture through the live `claude` CLI on both arms and burned
real quota. The full machine-readable record is committed at
[`docs/benchmarks/mem-ab-real-v0.6.0.json`](benchmarks/mem-ab-real-v0.6.0.json).

```
Memory A/B run                     (REAL result: engine:claude, built-in fixture)
  label:        real-v0.6.0
  seed repo:    acme-org/widgets   (tests/fixtures/mem-bench/repo)
  memory backend: fleet-local (in-memory SQLite FleetBrain, recency + literal recall)
  solver:       engine:claude   (claude CLI 2.1.181)
  N (tasks that re-tempt a learned mistake): 4   (+1 control task)

  repeated-mistake-rate     memory OFF: 50%    memory ON: 0%     delta: +50 pts
  task success rate         memory OFF: 40%    memory ON: 80%    delta: +40 pts
  retrieval precision/recall (ON only):  33.3% / 100%
  tokens in / turns         memory OFF: 117,585/17   memory ON: 109,537/19

  per-task (mistake repeated?  off / on):
    tz-naive-datetime       off=yes  on=no
    swallow-exceptions      off=no   on=no
    mutable-default-arg     off=yes  on=no
    n-plus-one-query        off=no   on=no
    add-docstring (control) off=no   on=no
```

How to read it honestly:

- **The headline moved by +50 pts on this run.** The isolated memory-OFF arm
  repeated two of four known mistakes (`tz-naive-datetime` and
  `mutable-default-arg`); the memory-ON arm repeated none. Every attempt ran in a
  fresh temporary copy of the fixture, so neither arm inherited files written by
  an earlier task. This is a real result for N=4, not a population claim.
- **Task success moved from 40% to 80%.** Both repeated mistakes also missed their
  success markers in the OFF arm. With memory ON, all four mistake tasks reached
  their success markers; the docstring control missed in both arms.
- **Retrieval itself worked.** On the ON arm the right lesson was recalled for
  all four mistake tasks (recall 100%), with precision 33.3% because the fixture
  seeds two distractor lessons alongside each relevant one and the local
  FleetBrain fallback recalls by recency once the literal match is exhausted. So
  the behavioural delta is paired with verified delivery of the relevant lesson
  to every memory-ON prompt.
- **N = 4 is tiny by design.** Do not extrapolate a 4-task delta either way. This
  fixture proves the harness produces a real, reproducible engine number; a
  larger, harder fixture is still needed before generalizing the delta. Marker
  fidelity is the honest limit
  (see caveats): a task counts as solved only on a literal success-marker match,
  so a correct-but-differently-spelled fix reads as "not solved", not "mistake".

Reproduce exactly from a repo checkout (burns real quota, no `ALFRED_HOME`
needed for the engine path):

```
uv run python bin/alfred-benchmark.py memory --engine claude --label real-v0.6.0 \
  --json > docs/benchmarks/mem-ab-real-v0.6.0.json
```

### Offline-fixture result (stub solver, no engine)

The numbers below are the **actual output of `alfred benchmark memory --stub`**
against this repo's built-in fixture (`tests/fixtures/mem-bench/`). They are a
real result **of the harness**, not of any engine: the stub solver runs no
model, makes no network call, and burns no quota. Read them as "the harness,
recall, injection and scoring all work end to end, and the fixture is
well-formed", not as evidence about `claude` or `codex`. For a real engine
result, run `--engine <name>` and fill the template above; those numbers replace
these as the headline.

```
Memory A/B run                     (OFFLINE FIXTURE result: stub solver, no engine)
  seed repo:      acme-org/widgets   (tests/fixtures/mem-bench)
  memory backend: fleet-local (in-memory SQLite FleetBrain, recency + literal recall)
  solver:         stub (deterministic; reacts only to whether the lesson text
                  reached the injected prompt)
  N (tasks that re-tempt a learned mistake): 4   (+1 control task)

  repeated-mistake-rate     memory OFF: 100%   memory ON: 0%    delta: +100 pts
  task success rate         memory OFF: 20%    memory ON: 100%
  retrieval precision/recall (ON only):  33.3% / 100%
  tokens in / turns         memory OFF: 5,000/25   memory ON: 5,000/25

  per-task (mistake repeated?  off / on):
    tz-naive-datetime       off=yes  on=no
    swallow-exceptions      off=yes  on=no
    mutable-default-arg     off=yes  on=no
    n-plus-one-query        off=yes  on=no
    add-docstring (control) off=no   on=no
```

How to read it honestly:

- The **+100 pt** delta is the ceiling the stub is built to show: the fixture
  lesson signal always reaches the prompt on the ON arm and never on the OFF
  arm, so the ON arm follows every lesson and the OFF arm repeats every mistake.
  A real engine will not be this clean; the value of the stub run is that the
  harness, recall, injection and marker scoring are all exercised for real.
- **Retrieval precision is 33.3%, not 100%**, because the fixture seeds
  distractor lessons and the local FleetBrain fallback recalls by recency once
  the literal match is exhausted, so the top-K carries irrelevant lessons
  alongside the right one. Recall of the right lesson is 100% (it is always in
  the top-K). This is the recency-retrieval caveat above, visible in a number.
- **Cost is arm-equal** (5,000 tokens / 25 turns both sides) because the stub
  assigns a fixed synthetic cost; only a real engine measures true token/turn
  cost, and only there is a cost delta meaningful.
- **N = 4 is tiny by design.** Do not extrapolate a 4-task fixture delta into a
  population claim. Report N.

Reproduce exactly with `uv run python bin/alfred-benchmark.py memory --stub`
(or `--json` for the machine-readable record these numbers were read from).

## Compression: builtin #453 vs headroom

A third benchmark answers a different question: **on the verbose output a firing
actually produces (grep dumps, JSON blobs, build logs), how much context does
each compression engine save?** It runs the *same* real payloads through the
built-in #453 compactor and through the optional headroom engine (see
[COMPRESSION.md](COMPRESSION.md)) and reports the token-reduction ratio for each.

Run it:

```sh
# Human-readable table (offline, no model, no quota):
alfred benchmark compression

# Machine-readable:
alfred benchmark compression --json > compression-before.json

# Point at your own payloads:
alfred benchmark compression --fixture ./my-payloads
```

### What it measures, honestly

- **Same payloads, both engines.** The built-in arm runs
  `tool_compactor.compact_output` on each payload; the headroom arm runs the
  headroom engine on the identical input. Byte reduction is exact; token
  reduction uses `tiktoken` (cl100k_base) when installed and otherwise a
  deterministic `chars/4` estimate - and the report **labels which estimator
  produced the number**, so an estimate is never presented as truth.
- **headroom is optional, and honestly reported.** When headroom is not
  installed in the environment running the benchmark, its arm is marked
  `not-run` - never zero, never a fabricated ratio. The built-in arm still
  reports its real numbers. Only an engine that actually ran is scored.
- **Offline-testable.** The built-in arm and the token accounting are pure
  stdlib; the harness is unit-tested in `tests/test_compression_benchmark.py`
  with headroom either absent (marked not-run) or mocked. No headroom install
  and no network are required.

The built-in fixtures live in `tests/fixtures/compression/` (`grep-symbols.txt`,
`data.json`, `log-build.txt`) - representative grep, JSON, and log tool output.

### Reference numbers (built-in arm, this repo's fixtures)

Measured with `alfred benchmark compression` on the built-in fixtures
(`tiktoken:cl100k_base`), the **built-in #453 compactor** alone reduces tokens by
roughly:

| payload | kind | builtin token reduction |
|---|---|---|
| `log-build.txt` | log | ~94% |
| `data.json` | json | ~98% |
| `grep-symbols.txt` | grep | ~90% |

These are the built-in engine's own numbers on high-redundancy fixtures; they
are a floor a solo install already gets with **zero** extra dependencies. The
headroom arm is left for you to fill by installing headroom-ai and re-running -
this doc does not quote a headroom number the harness has not measured here.

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

The memory A/B is covered by `tests/test_memory_benchmark.py`. It runs the full
A/B over the built-in fixture with the deterministic stub solver and a **real**
in-memory FleetBrain (SQLite `:memory:`), so recall, injection and every metric
are exercised for real - only the engine is stubbed. **No LLM is called, no
network is touched, and no quota is burned.** The one path left uncovered is the
real-engine solver (`make_cli_engine_solver`), by design: exercising it needs a
live model.

```
uv run pytest tests/test_memory_benchmark.py
```
