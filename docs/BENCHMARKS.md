# Benchmarks

A reproducible way to answer one measured question: **is your fleet getting
better or worse at shipping code, and how efficiently does it use each
turn?**

This is a **self-benchmark**. It measures your install against its own
past runs (before/after) and reports absolute values from captured telemetry.
It is not a competitive "Alfred beats tool X" claim, and it has no leaderboard.

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
a missing time) when there is nothing to divide by. An empty run reports
explicit zeros instead of estimates.

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
```

Keep the before/after pair together so the delta is legible. Do not turn it
into a "beats X" claim. The measured trend applies to your install.

## Starter-skill A/B

The starter-skill benchmark answers one narrow question: does a skill improve
the task result enough to justify adding it to every matching role prompt?

Run the built-in suite:

```sh
# List task names without a model call.
alfred benchmark skills --show-suite

# Run every paired task with the local Codex CLI.
alfred benchmark skills --engine codex --output skill-results.json

# Limit a diagnostic rerun to one or more skills.
alfred benchmark skills --skill review-security --skill add-observability
```

Each fixture has a seed repository and a grader outside that repository. The
harness copies the seed into a fresh temporary Git repository for each arm. The
baseline arm receives only the task prompt. The skill arm receives the same
prompt plus the named `SKILL.md`. The agent cannot read the grader. The grader
checks behavior, regressions, and review findings with normal code and test
commands. No model grades another model.

The JSON report records:

- the fixture digest, engine, engine version, and optional model override;
- pass, regression, and review-finding results for each arm;
- turns, prompt bytes, input, cached-input, and output tokens;
- elapsed time and agent exit status.

The report does not keep agent output, reasoning, source snapshots, or local
paths. The v0.8.0 gate is deliberately strict. A starter skill must pass every
skill-assisted task, must not reduce pass rate, and must not increase
regressions or review findings. At least one deterministic quality measure must
improve.

This paired design follows the useful parts of
[SkillsBench](https://arxiv.org/abs/2602.12670),
[SWE-Skills-Bench](https://arxiv.org/abs/2603.15401), and
[SkillLearnBench](https://arxiv.org/abs/2604.20087): compare against a no-skill
arm, use task-level acceptance checks, record efficiency separately, and allow
a skill to fail the gate. SWE-Skills-Bench is an important warning here: most
skills in that study did not improve pass rate, and some made results worse.
Alfred therefore does not treat a longer prompt or a plausible checklist as
evidence.

### v0.8.0 measured result

The release run used Codex CLI 0.145.0 with its default model selection and one
paired repetition per task. Each starter candidate ran on two held-out tasks.
The three skills that passed are `spec-to-issues`, `review-security`, and
`add-observability`. The full task rows and efficiency counters are in:

- [`skill-ab-starters-v0.8.0.json`](benchmarks/skill-ab-starters-v0.8.0.json)
- [`skill-ab-security-v0.8.0.json`](benchmarks/skill-ab-security-v0.8.0.json)
- [`skill-ab-nonstarters-v0.8.0.json`](benchmarks/skill-ab-nonstarters-v0.8.0.json)

| Skill | Distinct tasks | Baseline pass | Skill pass | Mean finding change | Starter |
|---|---:|---:|---:|---:|---|
| `spec-to-issues` | 2 | 0% | 100% | -3.50 | yes |
| `review-security` | 2 | 0% | 100% | -2.00 | yes |
| `add-observability` | 2 | 50% | 100% | -0.50 | yes |
| `write-tests` | 1 | 100% | 100% | 0.00 | no |
| `migrate-dependency` | 1 | 0% | 100% | -1.00 | no, one task only |
| `changelog-and-release-notes` | 1 | 0% | 100% | -1.00 | no, one task only |

`write-tests` passed its task in both arms with no deterministic quality gain.
`migrate-dependency` and `changelog-and-release-notes` improved one task each,
but did not meet the two-task evidence floor. All three remain available by
name but are not part of `alfred skills install --starter` and are not offered
to roles automatically.

This is a small release gate, not a general claim about every repository or
model. Re-run it when a skill, grader, coding CLI, or default model changes.

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

- **memory ON** uses the shipped `sqlite,fleet` chain, seeded with the lessons
  the fleet has already "learned" about the seed repo. Both stores run in
  memory, so the benchmark cannot read or change operator state. It injects
  recalled lessons through the live firing path (`format_memory_context`).
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

1. **Pick or write a fixture.** Two fixtures ship in-tree, each with three
   parts: `lessons.json` (the lessons the fleet "learned", including
   distractors), `tasks.json` (the paired tasks with their mistake/success
   markers and the relevant lesson id), and `repo/` (a tiny deterministic
   sample repo an engine can edit). The default `tests/fixtures/mem-bench/`
   (N=4) re-tempts generic Python gotchas and proves the harness; the harder
   `tests/fixtures/mem-bench-hard/` (N=10) plants repo-specific conventions a
   model cannot guess without memory and is the headline fixture. Point at
   either with `--fixture DIR`, or at your own.
2. **Capture a baseline** with `--engine <name> --label before --json`.
3. **Change something** - the recall limit, a prompt, or the provider passed to
   the Python benchmark API.
4. **Re-run** with `--label after --json` and compare. Same suite, same seed
   repo, same seeded lessons: the delta is the memory signal.

### Caveats (read before quoting a number)

- **Marker fidelity is a limitation.** The mistake/success verdict is a
  regex match against solver output. A marker that is too loose or too tight
  mis-scores a task. Markers live in `tasks.json`; audit them for your fixture.
- **The benchmark uses lexical SQLite recall.** It exercises the zero-daemon
  shipped default without optional dense embeddings. BM25 ranks overlapping
  terms; it does not infer semantic similarity. Precision therefore depends on
  fixture wording and the recall limit. Every report names its provider.
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
  memory backend: <sqlite,fleet | custom provider label>
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

### Provider recall-quality gate

Run the provider-only fixture before you change retrieval signals or defaults:

```sh
alfred benchmark memory-recall
alfred benchmark memory-recall --json > memory-recall-before.json
alfred benchmark memory-recall --fixture ./my-recall-fixture
```

This command uses the shipped `sqlite,fleet` chain in memory. It does not read
operator data. It does not call a model or use the network.

The built-in fixture covers seven cases:

- exact technical terms
- a wording variant for the same instruction
- repository scope
- a superseded temporal value
- a newer instruction that contradicts an older instruction
- expired guidance
- a true query miss

The report includes precision, recall, false-injection rate, true-miss rate,
provider latency, prompt bytes, and index/hydration measurements. It lists the
expected and recalled lesson IDs for each case. The JSON record includes the
fixture schema, fixture SHA-256 digest, provider chain, recall limit, fixed
8,000-character prompt budget, case category, and any provider or
context-format error.

The index/hydration measurements report:

- stored lesson-body bytes and searchable-text bytes
- index queries and final body-hydration queries
- body bytes returned for the final ranked lessons
- body bytes avoided compared with reading every stored body for every case

These are logical UTF-8 payload counts from the isolated fixture, not an
estimate of physical disk reads. SQLite ranks IDs and normalized searchable
text, then fetches the final lesson bodies in one batched query. The built-in
fixture currently avoids 2,857 of 3,101 possible body bytes, or 92.1%, while
keeping 100% precision and recall with zero false injections. Its searchable
text is 586 bytes for 443 bytes of stored lesson bodies because it also carries
tags. A summary index would be smaller, but this fixture does not show a recall
benefit that would justify changing the stored search surface.

Each case makes one provider call. Provider latency measures that call only.
Prompt bytes measure the same recalled lessons after the runtime memory
formatter applies the recorded limit and prompt budget. The benchmark does not
perform a second recall. It exits with status 2 if a provider member or the
formatter fails, even when the chain returns a fallback result.

This fixed fixture tests lexical provider behavior. It does not measure model
reasoning or the quality of arbitrary repositories and queries. Compare the
fixture digest before you compare results from separate runs.

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

How to interpret this result:

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
  fixture proves the harness produces a real, reproducible engine number; the
  [harder fixture below](#harder-fixture-result-engineclaude-n10) is the
  headline-grade run. Marker fidelity is the limiting factor
  (see caveats): a task counts as solved only on a literal success-marker match,
  so a correct-but-differently-spelled fix reads as "not solved", not "mistake".

Reproduce exactly from a repo checkout (burns real quota, no `ALFRED_HOME`
needed for the engine path):

```
uv run python bin/alfred-benchmark.py memory --engine claude --label real-v0.6.0 \
  --json > docs/benchmarks/mem-ab-real-v0.6.0.json
```

### Harder fixture result (engine:claude, N=10)

The base fixture above re-tempts generic Python gotchas, which a capable model
often avoids without memory; its value is proving the harness on a real engine.
The **harder fixture** (`tests/fixtures/mem-bench-hard/`) is built so memory is
the *only* way to get the convention right: every task's correct behaviour is a
**repo-specific convention that is unguessable from the repo** - route work
through the team's internal `acme_*` platform helpers (HTTP, logging, config,
ids, clock, JSON, DB unit-of-work, shell, preconditions) and the project error
type, instead of the stdlib or `requests` default any model would reach for.
The helpers live in a separately-installed platform package, the fixture repo
never names them (a unit test enforces that mechanically), and the rule exists
only in the seeded lessons. Each task also demands a code-only final reply so
the deterministic markers grade code, not commentary about alternatives.

This is a **real** `--engine claude` run, both arms, real quota. The full
machine-readable record is committed at
[`docs/benchmarks/mem-ab-hard-real-v0.6.1.json`](benchmarks/mem-ab-hard-real-v0.6.1.json).

```
Memory A/B run                     (REAL result: engine:claude, harder fixture)
  label:        real-hard-v0.6.1
  seed repo:    acme-org/widgets   (tests/fixtures/mem-bench-hard/repo)
  memory backend: fleet-local (in-memory SQLite FleetBrain, recency + literal recall)
  solver:       engine:claude   (claude CLI 2.1.181)
  N (tasks that re-tempt a learned mistake): 10   (+2 control tasks)

  repeated-mistake-rate     memory OFF: 80%    memory ON: 0%     delta: +80 pts
  task success rate         memory OFF: 8.3%   memory ON: 91.7%  delta: +83.3 pts
  retrieval precision/recall (ON only):  33.3% / 100%
  tokens in / turns         memory OFF: 294,652/59   memory ON: 240,235/34

  per-task (mistake repeated?  off / on):
    http-client-wrapper     off=no   on=no
    project-error-type      off=yes  on=no
    structured-logger       off=yes  on=no
    config-access           off=yes  on=no
    id-generation           off=yes  on=no
    current-time            off=yes  on=no
    response-serialization  off=yes  on=no
    db-write-uow            off=yes  on=no
    shell-exec              off=yes  on=no
    precondition-check      off=yes  on=no
    add-docstring (control) off=no   on=no
    add-type-hint (control) off=no   on=no
```

How to interpret this result:

- **The headline is +80 pts over N=10.** Without memory the engine reached for
  the obvious default on eight of ten tasks (`ValueError`, `logging.getLogger`,
  `os.environ`, `uuid.uuid4`, `datetime.now`, `json.dumps`, `session.commit()`,
  `subprocess.run`) - exactly the planted mistakes. With the seeded lessons
  recalled and injected, it used the mandated internal helper on all ten.
- **The two OFF-arm "no" rows are not wins for the no-memory arm.** On
  `http-client-wrapper` and `precondition-check` the OFF engine avoided the
  specific planted pattern (for example, a different HTTP client than
  `requests.get`) but still missed the required convention, so it failed the
  task; OFF task success is 1/12. A "mistake not repeated" row only counts as
  good when the task also succeeds.
- **Memory ON was also cheaper on this run**: 240k tokens / 34 turns vs
  295k / 59, because the injected lesson removed exploration. Cost figures are
  one run's measurement, not a guarantee.
- **Retrieval: recall 100%, precision 33.3%.** The right lesson reached every
  ON-arm prompt; precision reflects the four distractor lessons the fixture
  seeds and the local FleetBrain's recency backfill in the top-3.
- **N = 10 is still a fixture, not a population.** It is materially larger and
  harder than the base run, and the planted conventions are the kind real repos
  have, but do not quote the delta without the N and the per-task rows.

Reproduce exactly from a repo checkout (burns real quota; roughly 10 minutes,
24 engine firings):

```
uv run python bin/alfred-benchmark.py memory --engine claude \
  --fixture tests/fixtures/mem-bench-hard --label real-hard-v0.6.1 \
  --json > docs/benchmarks/mem-ab-hard-real-v0.6.1.json
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
  memory backend: sqlite,fleet (in-memory shipped default; lexical SQLite recall)
  solver:         stub (deterministic; reacts only to whether the lesson text
                  reached the injected prompt)
  N (tasks that re-tempt a learned mistake): 4   (+1 control task)

  repeated-mistake-rate     memory OFF: 100%   memory ON: 0%    delta: +100 pts
  task success rate         memory OFF: 20%    memory ON: 100%
  retrieval precision/recall (ON only):  100% / 100%
  tokens in / turns         memory OFF: 5,000/25   memory ON: 5,000/25

  per-task (mistake repeated?  off / on):
    tz-naive-datetime       off=yes  on=no
    swallow-exceptions      off=yes  on=no
    mutable-default-arg     off=yes  on=no
    n-plus-one-query        off=yes  on=no
    add-docstring (control) off=no   on=no
```

How to interpret this result:

- The **+100 pt** delta is the ceiling the stub is built to show: the fixture
  lesson signal always reaches the prompt on the ON arm and never on the OFF
  arm, so the ON arm follows every lesson and the OFF arm repeats every mistake.
  A real engine will not be this clean; the value of the stub run is that the
  harness, recall, injection and marker scoring are all exercised for real.
- **Retrieval precision and recall are 100% for this fixture.** The SQLite
  lexical arm returns the relevant lesson for all four eligible tasks. This is
  a small, wording-dependent fixture result, not a general retrieval claim.
- **Cost is arm-equal** (5,000 tokens / 25 turns both sides) because the stub
  assigns a fixed synthetic cost; only a real engine measures true token/turn
  cost, and only there is a cost delta meaningful.
- **N = 4 is tiny by design.** Do not extrapolate a 4-task fixture delta into a
  population claim. Report N.

Reproduce exactly with `uv run python bin/alfred-benchmark.py memory --stub`
(or `--json` for the machine-readable record these numbers were read from).

## Compression quality: raw, built-in, and Headroom

A third benchmark answers two questions about recorded grep, JSON, build-log,
and failed-test output: how much context does each engine remove, and which
required facts remain? It runs the same payloads through a raw-output control,
the built-in compactor, and the optional Headroom engine (see
[COMPRESSION.md](COMPRESSION.md)).

Run it:

```sh
# Human-readable table (offline, no model, no quota):
alfred benchmark compression

# Machine-readable:
alfred benchmark compression --json > compression-before.json

# Point at your own payloads:
alfred benchmark compression --fixture ./my-payloads
```

### What it measures and how it labels estimates

- **Same payloads for every arm.** The built-in arm runs
  `tool_compactor.compact_output` on each payload; the Headroom arm runs the
  Headroom engine on the identical input. Byte reduction is exact; token
  reduction uses `tiktoken` (cl100k_base) when installed and otherwise a
  deterministic `chars/4` estimate - and the report **labels which estimator
  produced the number**, so an estimate is never presented as truth.
- **Raw output is the control.** It always reports 0% reduction and must retain
  every declared fact. Built-in and Headroom results are compared with that
  same contract.
- **Required facts are explicit.** The fixture manifest marks failures, file
  paths, line numbers, test counts, and final command status. An engine passes
  a case only when every declared fact remains in its final output. Failed
  commands are measured through the same confirmed-failure valve used by real
  firings, so neither compressor receives or hides them.
- **Headroom is optional, and absence is explicit.** When Headroom cannot run
  in the environment executing the benchmark, its arm is marked
  `not-run` - never zero, never a fabricated ratio. The built-in arm still
  reports its real numbers. Only an engine that actually ran is scored.
- **Offline-testable.** The built-in arm and the token accounting are pure
  stdlib; the harness is unit-tested in `tests/test_compression_benchmark.py`
  with Headroom either unavailable (marked not-run) or mocked. No Headroom
  install and no network are required.

The built-in fixtures live in `tests/fixtures/compression/`. The
`quality-manifest.json` file binds required facts and exit status to the
recorded grep, JSON, build-log, and failed-test payloads.

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
Headroom arm is left for you to fill by installing headroom-ai and re-running -
this doc does not quote a headroom number the harness has not measured here.

On the current four-case quality manifest, the raw control and built-in engine
both retain every declared fact. The built-in mean includes the failed command
at 0% reduction because Alfred passes failed output through unchanged. Headroom
remains `not-run` unless the benchmark process can call a real Headroom
compression path. Token savings alone do not qualify an engine as the default.

Measured with the pinned Headroom 0.29.0 package in an isolated environment, all three
successful payloads used the built-in fallback and the failed payload used raw
passthrough. The selected Headroom arm therefore matched the built-in 70.5%
token reduction and 100% fact-retention result, but Headroom itself was the
effective compressor for 0 payloads. This result does not support changing the
default engine.

## Feeding a future desktop Metrics view

`alfred benchmark report --json` already emits the exact shape a desktop
"Metrics" tab would render: the four families, the per-firing observations,
and their observed efficiency values. The desktop app's `alfred serve` API
(see [`DESKTOP_CLIENT.md`](DESKTOP_CLIENT.md)) can shell this command and
render the JSON without any new aggregation logic. Wiring that endpoint and
the tab is a **follow-up**; the harness already produces the contract it
would consume, so no schema work is blocked on it.

## Testing the harness itself

The reader is covered by `tests/test_benchmark.py`. The model is fully
mocked there: the tests build a synthetic `state/` tree (spend ledger +
event logs + transcripts with `message.usage` blocks) under a temp dir and
assert the four families and observed turns per PR. **No LLM is called, no
real disk outside the temp dir is touched, and no subscription usage is
consumed.** Run them with the rest of the suite:

```
uv run pytest tests/test_benchmark.py
```

The memory A/B is covered by `tests/test_memory_benchmark.py`. It runs the full
A/B over the built-in fixture with the deterministic stub solver and the real
in-memory `sqlite,fleet` chain. Recall, injection, and every metric run for real;
only the engine is stubbed. **No LLM is called, no network is touched, and no
quota is burned.** The one path left uncovered is the real-engine solver
(`make_cli_engine_solver`), by design: exercising it needs a live model.

```
uv run pytest tests/test_memory_benchmark.py
```
