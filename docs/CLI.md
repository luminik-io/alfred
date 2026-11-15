# Alfred CLI: inspection commands and subcommand index

This page is not a full CLI reference. It lists every `bin/alfred` subcommand
in one line, then documents a few of them in detail: `alfred status`,
`alfred agent`, `alfred slack-listener`, `alfred metrics`, `alfred usage`,
the engine and model controls, `alfred logs`, and `alfred evidence`. For any
other subcommand, run `alfred <command> --help`.

`alfred metrics` and `alfred logs` are read-only tools for inspecting what
the fleet wrote to disk. They never mutate state; they read
`$ALFRED_STATE_DIR` (default `$ALFRED_HOME/state`, default `~/.alfred/state`)
and print the result. Both are thin wrappers around `bin/alfred-metrics.py`
and `bin/alfred-logs.py`, which call library functions in `lib/metrics.py` and
`lib/transcripts.py`.

## All subcommands

Generated from `python3 bin/alfred --help`:

- `agents`: list configured agents.
- `enable`: list an agent in the runner gate file.
- `disable`: remove an agent from the runner gate file.
- `enabled-agents`: print the list of enabled codenames.
- `agent`: manage operator-defined runtime agents.
- `skills`: manage curated skill packs.
- `pause`: stop scheduled firings for an agent.
- `resume`: reverse a pause.
- `run`: kick a single agent now (one-shot).
- `dry-run`: simulate one agent firing without scheduler or external side effects.
- `clear-lock`: diagnose and clear stale agent locks.
- `status`: show local fleet health.
- `doctor`: run host preflight checks for the configured fleet.
- `labels`: bootstrap GitHub labels on fleet repos.
- `pr`: run the GitHub-native merge gate on a pull request.
- `engine` (alias `provider`): manage per-agent engines.
- `model`: manage per-agent model overrides.
- `claude`: manage Claude Code account routing.
- `codex`: inspect Codex CLI availability/auth.
- `opencode`: inspect OpenCode CLI availability/auth.
- `setup-token`: mint a long-lived Claude OAuth token for scheduled firings.
- `setup-architect`: configure architect parent-repo and approval-gate settings.
- `architect`: manage architect cross-repo coordination.
- `auth`: inspect Claude Code, Codex, and OpenCode readiness.
- `metrics`: weekly fleet rollup of firings/cost/turns.
- `benchmark`: reproducible self-benchmark from existing telemetry (throughput/quality/etc.).
- `usage`: local Claude Code + Codex subscription usage and remaining limits.
- `logs`: inspect stream-JSON transcripts for one agent.
- `evidence`: manage saved evidence for one local run.
- `brain`: inspect and seed local fleet-brain memory.
- `memory`: inspect Alfred's unified memory plane.
- `code-memory`: inspect, index, or serve code-structure memory.
- `code-map`: build, export, or query Alfred's local JSON code graph.
- `capabilities`: inspect local code graph, token, and skill capabilities.
- `batteries`: list or toggle Alfred's opt-in enhancements (memory, compression, code graph).
- `github-poll`: poll GitHub issues/PRs into fleet-brain.
- `mcp`: serve read-only Alfred MCP tools.
- `demo`: watch the team plan, build, catch a bug, and ship with a supported engine.
- `serve`: open the read-only localhost dashboard.
- `schedule`: inspect or edit configured agent cadences.
- `assign`: route an existing issue to the right agent lane.
- `spec`: create and lint specs for agent work.
- `goal`: manage the durable goal ledger.
- `slack-listener` (alias `slack`): run or test the Slack planning listener.
- `slack-thread-sync`: post fleet progress (claimed/PR/CI/merged) to issue-filing Slack threads.
- `shipped`: summarize shipped GitHub activity.

## Configuration

| Env var | Purpose | Default |
| --- | --- | --- |
| `ALFRED_STATE_DIR` | Explicit state-root override | unset |
| `ALFRED_HOME` | Runtime root containing `state/` | `~/.alfred` |

`alfred metrics` and `alfred logs` do not accept `--state-dir`; set
`ALFRED_STATE_DIR` instead. The underlying scripts, `bin/alfred-metrics.py`
and `bin/alfred-logs.py`, accept `--state-dir PATH` when you run them
directly.

## `alfred status`

`alfred status` is the one-screen fleet health view. It prints a `global:` block
(host scheduler, global rate-limit block, Slack webhook cache age) followed by
one row per agent: scheduler load state (`yes`, `no`, `pause`, or `off`),
engine, last fired, today's fires/ok/fail, current fail streak, turns, and
state (`ok`, paused, in-flight, stale lock, awaiting approval, blocked, or
`stderr fresh`). Rows use the display name from the active theme. Pass
`--json` for machine-readable output or `--slack` for a Slack summary.

```text
alfred-status @ 2026-06-25 18:35:01
alfred_home=/Users/<you>/.alfred

global:
  scheduler: launchd
  global block: none
  slack webhook cache: 3.2h old

agent                  load  eng    fired    fires ok  fail streak turns  state
-------------------------------------------------------------------------------
Drake                  yes   hybrid 12m      8     7   1    0      52     ok
Lucius                 yes   hybrid 3m       12    9   3    0      78     in-flight
Ra's al Ghul           yes   hybrid 1h05m    9     9   0    0      123    ok
Bane                   yes   hybrid never    0     0   0    0      0      ok
Batman                 off   hybrid never    0     0   0    0      0      ok
Huntress               pause -      never    0     0   0    0      0      paused
```

The numbers above are illustrative. Your own output shows your configured
agents, real per-day spend, and the live sentinel states for your machine.

## `alfred agent`

Create and inspect operator-defined runtime agents. These are real scheduled
agents, not just display names. Alfred stores them in
`$ALFRED_HOME/state/custom-agents/custom-agents.json`; `bash deploy.sh` compiles
enabled rows into the same launchd/systemd scheduler units as the built-in
fleet.

```sh
alfred agent list
alfred agent list --json

alfred agent add release-captain \
  --display-name "Release Captain" \
  --role-title "Release coordinator" \
  --prompt "Review release readiness and summarize blockers for the operator." \
  --engine hybrid \
  --schedule 30m \
  --repo acme/api

alfred agent remove release-captain
bash deploy.sh
```

The generic custom runner, `bin/custom-agent.py`, invokes the configured prompt
through Claude, Codex, or hybrid routing and records normal Alfred telemetry:
locks, preflight, events, spend, runtime memory, and Slack summaries. It is
read-only by default. Use a dedicated `bin/<role>.py` when the role needs
deterministic code or PR creation beyond a prompted scheduled teammate.

## `alfred slack-listener`

Run the optional Slack-native planning listener. It listens over Socket Mode for
trusted DMs, app mentions, and replies in registered Alfred threads. It saves
planning drafts and follow-up context locally; it does not approve plans or
execute work.

```sh
alfred slack-listener run
alfred slack-listener once payload.json --trusted-user U0123ABCDEF --no-post
alfred slack-listener once - --trusted-user U0123ABCDEF --allow-ignored
```

`run` requires `SLACK_APP_TOKEN` or `ALFRED_SLACK_APP_TOKEN`, plus a bot token
resolved the same way as the approval gate. `once` is a local smoke-test mode
that processes one Slack event JSON payload from a file or stdin. Pass
`--trusted-user` one or more times to test without touching your shell env.

Runtime state:

```text
$ALFRED_HOME/state/slack-threads/
$ALFRED_HOME/state/slack-threads/feedback/
$ALFRED_HOME/state/planning-drafts/
$ALFRED_HOME/state/slack-listener/seen/
```

## `alfred metrics`

Roll up per-agent firings, cost, turns, tool-use, and Codex tokens across a
window. Reads:

- `<codename>/spend-YYYY-MM-DD.json`: per-day SpendState files
- `transcripts/<codename>/<YYYY-MM>/*.jsonl`: stream-JSON firings
- `codex/<codename>/<YYYY-MM>/*.stdout.txt`: Codex run stdout dumps

```sh
alfred metrics                          # last 7 days, per-agent
alfred metrics --since 14d              # last 14 days
alfred metrics --since 48h              # last 48h (rounds up to days)
alfred metrics --codename senior-dev        # one codename only
alfred metrics --by-day                 # daily totals instead of per-agent
alfred metrics --json                   # machine-readable
```

`--since` accepts `7`, `7d`, `48h`, `2w`, `1m`. `--days N` overrides
`--since` when both are passed.

### Example output

```
alfred-metrics - last 7 days @ 2026-05-23 12:17 UTC

codename               firings  ok    fail  turns   codex  ctok     cost     tools   edits  reads  top tool               skills
--------------------------------------------------------------------------------------------------------------------------------
senior-dev             12       9     3     78      2      4500     $1.42    34      6      11     Bashx14                review x3
planner                8        7     1     52      0      0        $0.96    21      0      9      Readx9                 -
your-agent             3        2     1     14      0      0        $0.42    2       0      1      Readx1                 -
--------------------------------------------------------------------------------------------------------------------------------
TOTAL                  23       18    5     144     2      4500     $2.80    57      6      21

fleet success rate: 78.3%  (over 23 completed firings; 0 no-ops not counted)
```

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success. An unparseable `--since` prints a warning, falls back to 7 days, and still exits 0. |
| 1 | `--codename` names an agent with no state, while other agents do have state |
| 2 | State directory missing, or an argparse usage error |

## `alfred usage`

Report your real Claude and Codex subscription headroom for the
rolling 5-hour and weekly limit windows. Alfred drives Claude Code and Codex
through their local subscription CLIs rather than API keys, so there is no
billing API to query. `alfred usage` instead reads the state files those CLIs
persist on disk (under `~/.claude` and `~/.codex` by default) and reports usage
and remaining headroom. Numbers are never fabricated: a provider whose local
state cannot be read is reported as unavailable with a reason, and any single
limit figure the CLI does not persist is shown as `-` (`null` in JSON).

```sh
alfred usage            # human-readable table
alfred usage --json     # machine-readable {"claude": {...}, "codex": {...}}
```

### Example output

```
Claude Code:
    5-hour   used 41%    remaining 59%    resets in 2h13m  (at 2026-06-14T18:30:00Z)
    weekly   used 22%    remaining 78%    resets in 3d     (at 2026-06-17T00:00:00Z)
    plan     max

Codex:
    unavailable - no local Codex session state found under ~/.codex
```

`alfred usage` exposes, from the command line, the same headroom the desktop
client's capacity rail reads over the live `GET /api/v1/usage` endpoint. It
always exits 0: an absent local CLI
state is a valid, reportable condition, not a command failure. Scripts can read
the `available` flag in the `--json` payload to detect that case.

## Engine and model controls

```sh
alfred engine status [codename]
alfred engine doctor [codename]
alfred engine set <codename> <claude|codex|opencode|hybrid>
alfred model status [codename]
alfred model set <codename> <claude|codex|opencode> <model>
alfred model clear <codename> <claude|codex|opencode>
```

`engine doctor` checks the selected binary, minimum version, required
non-interactive commands, and authentication without starting a model turn.
OpenCode models use `provider/model`. See [engine routing](ENGINE_ROUTING.md)
and [OpenCode](OPENCODE.md).

## `alfred logs`

Inspect stream-JSON transcripts for one codename. Three modes:

```sh
alfred logs <codename>                                  # summary of last 10 firings
alfred logs <codename> --last 25                        # last 25 firings
alfred logs <codename> --show-tool-calls                # tool-call rollup
alfred logs <codename> --firing-id <id>                 # dump one firing
alfred logs <codename> --firing-id <id> --show-tool-calls
                                                        # tool calls for one firing
alfred logs <codename> --json                           # machine-readable
```

### Summary mode

```
alfred-logs senior-dev - last 3 firings
transcripts: ~/.alfred/state/transcripts/senior-dev/

firing_id              when                   subtype        turns  cost    tools  edits  top tools
---------------------------------------------------------------------------------------------------
2026-05-23-1417-abc    2026-05-23 14:17:00Z   success        4      $0.14   12     1      Bashx5, Readx4, Editx2 [skills: review]
2026-05-23-1305-def    2026-05-23 13:05:12Z   success        3      $0.09   8      0      Readx5, Bashx2
2026-05-22-2330-ghi    2026-05-22 23:30:01Z   error_max_turns 20     $0.43   45     0      Bashx18, Readx14
```

### Tool-call rollup

```
alfred-logs senior-dev --show-tool-calls - last 10 firings

tool          calls
------------------------
  Bash             47
  Read             32
  Edit             12
  Skill             3

skill invocations:
  /review                   2
  /qa                       1
```

### Firing dump

`--firing-id ID` pretty-prints a single transcript: system/user/assistant
events one per line, with tool-use blocks summarized inline.

```
[system] init
[assistant] Reading the package layout to understand what changed
[tool_use Read] /repo/your-backend/build.gradle.kts
[tool_use Bash] $ git log --oneline -10
[tool_use Skill] /review
[result] subtype=success turns=4 cost=$0.1400 stop_reason=end_turn
```

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success. An unknown codename also exits 0 with `no transcripts under ...` when no agent has transcripts yet. |
| 1 | `--firing-id` not found, or an unknown codename while other agents do have transcripts |
| 2 | State directory missing, or an argparse usage error |

## `alfred evidence`

Attach an existing Claude Code, Codex, or OpenCode transcript to one saved
Alfred run:

```sh
alfred evidence import \
  --agent reviewer \
  --run-id 2026-08-17-1042-abc \
  --repo owner/repo \
  --engine codex \
  --source ./session.txt
```

Alfred accepts the import only when the run event log records the exact
repository. It copies the file under
`$ALFRED_HOME/state/imports/<agent>/<run-id>/` and records the managed file
name, size, SHA-256 digest, engine, repository, and import time. It does not
record the source file name or directory. The original file stays in place.

Remove Alfred's managed copy with the same repository scope:

```sh
alfred evidence remove \
  --agent reviewer \
  --run-id 2026-08-17-1042-abc \
  --repo owner/repo
```

Both actions support `--json`. A conflicting import must be removed before a
different transcript can be attached to the run.

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success, including "already imported" and "no managed transcript found" |
| 2 | Any error: missing run or repository mismatch, missing source file, import conflict, or an argparse usage error |

## State directory layout

`alfred metrics`, `alfred logs`, and `alfred evidence` assume:

```
$ALFRED_HOME/state/
  <codename>/
    spend-YYYY-MM-DD.json
  transcripts/
    <codename>/
      <YYYY-MM>/
        <firing-id>.jsonl
  codex/
    <codename>/
      <YYYY-MM>/
        <firing-id>.stdout.txt
        <firing-id>.stderr.txt
        <firing-id>.last.md
  imports/
    <codename>/
      <run-id>/
        manifest.json
        transcript.jsonl   # Claude imports
        stdout.txt         # Codex imports
        events.jsonl       # OpenCode imports
```

Each import directory holds `manifest.json` plus one transcript file, named
for the engine passed to `--engine`.

The Claude invocation helpers in `lib/agent_runner/` write transcripts
here. The SpendState helper writes spend files on each firing. Hosts that
have not run any agent yet will have an empty or missing state directory.
`alfred metrics` and `alfred logs` exit 2 with a clear message when the state
directory is missing. When it exists but is empty, `alfred metrics` prints
`(no firings or transcripts in window)`.

## Library reuse

The aggregation logic is importable:

```python
from pathlib import Path
from metrics import fleet_metrics
from transcripts import default_state_dir, list_firings, transcript_summary

state = default_state_dir()
report = fleet_metrics(state, days=7)
for m in report.metrics:
    if m.tool_calls_total > 50:
        print(m.codename, m.tool_calls)

for firing in list_firings(state, "senior-dev")[:5]:
    s = transcript_summary(firing.path)
    print(firing.firing_id, s.tool_calls_by_name)
```

The CLIs intentionally render only what dataclasses expose. Extend the
dataclasses (or add new render formats) instead of growing the CLI code.
