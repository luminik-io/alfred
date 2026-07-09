# Memory providers

Alfred ships a single-host memory layer: a runner can call
`memory.recall(...)` before a firing to surface lessons earlier
firings learned, and `memory.reflect(...)` afterwards to file new
ones. The default chain is `sqlite,fleet`: the embedded SQLite hybrid store
gives semantic-quality recall with **no daemon** (no Redis, no Ollama), while
FleetBrain keeps the local operational ledger and review queue. Redis Agent
Memory Server stays a fully supported opt-in for operators who want it
(`ALFRED_MEMORY_PROVIDERS=redis,fleet`).

Nothing is sent to a hosted memory service. Anonymous aggregate usage counts
are on by default; opt out with `alfred telemetry off`.

## The zero-daemon default: SQLite hybrid recall

The `sqlite` provider (`lib/memory/sqlite_hybrid.py`) is a single SQLite file
under the state root (`$ALFRED_HOME/memory-hybrid.db`) that does what Redis AMS
did for recall, without a running service. It stores every promoted lesson and
retrieves them with a **hybrid** strategy that degrades in clean tiers:

| Tier | Requires | How it ranks |
|---|---|---|
| **Lexical (default)** | nothing beyond stdlib SQLite | FTS5 full-text index, BM25 relevance. Falls back to `LIKE` substring matching if the SQLite build lacks FTS5, so recall never hard-fails. |
| **Dense (opt-in)** | `ALFRED_MEMORY_SQLITE_DENSE=1` + the optional `sqlite-vec` extension + a reachable Ollama embedder | a `vec0` vector table, k-nearest-neighbour over `mxbai-embed-large` embeddings (Alfred's existing embedding config). |

When both arms run they are fused with **Reciprocal Rank Fusion** (RRF):
`score(id) = Σ 1 / (k + rank)` over each arm's ranked list, `k` default 60. A
lesson both arms rank highly rises above one only a single arm found. With only
the lexical arm, the fused order is exactly the BM25 order.

**The dense arm is optional and degrades cleanly.** If `sqlite-vec` is not
installed (`pip install "alfred-os[vector]"`) or the Ollama embedder is
unreachable, the store silently uses lexical-only ranking. Lexical-only is the
true zero-dependency default: a fresh install gets working recall with nothing
running.

The store is a first-class **read AND write** target. The
capture -> judge -> promote pipeline writes each promoted lesson here (with a
deterministic id, so a re-promote upserts), and the revert / retire / decay
levers `forget` it here, exactly as they did against Redis AMS. `fleet-brain.db`
still owns candidates, firing logs, the graph, and review state; the hybrid file
owns only the promoted, recall-able lessons, so it can be reset or rebuilt
without touching the operational ledger.

### SQLite hybrid knobs

```sh
# Where the recall store lives (default $ALFRED_HOME/memory-hybrid.db).
ALFRED_MEMORY_SQLITE_DB=${ALFRED_HOME}/memory-hybrid.db
# Arm the dense arm (default off = lexical-only, zero dependencies).
ALFRED_MEMORY_SQLITE_DENSE=0
# RRF constant k (default 60) and per-arm candidate pool before fusion.
ALFRED_MEMORY_SQLITE_RRF_K=60
ALFRED_MEMORY_SQLITE_POOL=50
# Dense embeddings reuse the AMS embedding config:
#   ALFRED_AMS_EMBEDDING_MODEL, ALFRED_AMS_EMBEDDING_DIM, ALFRED_AMS_OLLAMA_BASE_URL
```

For the **code-structure** layer (where a symbol lives, who calls it, what a
change breaks, who owns a file) see [CODE_MEMORY.md](CODE_MEMORY.md). It is a
separate read-only MCP layer (codebase-memory-mcp) that complements the
semantic lessons and the operational graph rather than replacing them.

## Recall gating

Recalled lessons are gated before they are injected into a firing's prompt.
Anything below `ALFRED_MEMORY_RECALL_THRESHOLD` (an AMS similarity in `[0, 1]`,
higher is stricter) is dropped, and near-duplicate lesson bodies are collapsed
so the same lesson is never injected twice. This reuses the AMS relevance score
rather than always injecting whatever recall returned. The default threshold is
`0.0`, which preserves the historical inject-everything behavior; raise it to
suppress weakly related lessons. Lessons whose backend reports no score are
never dropped by the threshold (the gate cannot judge them), so providers
without scores keep their existing behavior.

This doc covers the **provider layer** above the brain: how to chain memory
backends so agents recall semantic lessons from Redis while FleetBrain keeps the
local queue, ledger, and review state.

## When to use this

Most users can leave the default alone. Reach for the provider layer when one
of these is true:

- You maintain your own personal knowledge base (notes app with a
  CLI, a local search index, a vector store you built years ago) and
  want Alfred firings to consult it as a fallback for older context.
- You want to disable runtime recall and reflection without ripping out the
  call sites (set `ALFRED_MEMORY_PROVIDERS=null`).
- You're writing a custom provider for a downstream fleet, such as a
  team wiki shim, and want to chain it behind Redis or FleetBrain.
- You run Redis Agent Memory Server on a different loopback port or host and
  want Alfred to use that endpoint.

## The Protocol

Providers implement a tiny Protocol (`lib/memory/__init__.py`):

```python
class MemoryProvider(Protocol):
    name: str

    def recall(
        self,
        *,
        query: str | None = None,
        codename: str | None = None,
        repo: str | None = None,
        limit: int = 5,
    ) -> list[Lesson]: ...

    def reflect(
        self,
        *,
        codename: str,
        repo: str,
        body: str,
        tags: Iterable[str] | None = None,
        severity: Severity = "info",
        firing_id: str | None = None,
        created_at: datetime | None = None,
    ) -> Lesson: ...
```

Runners depend on the Protocol, never on a concrete class.
Read-only providers raise `NotImplementedError` from `reflect`; the
chain wrapper catches it and tries the next writer.

## Built-in providers

| Name | File | Writable? | Notes |
|---|---|---|---|
| `sqlite` (alias `sqlite_hybrid`) | `lib/memory/sqlite_hybrid.py` | yes | **Default** zero-daemon recall store. FTS5 lexical + optional `sqlite-vec` dense, fused with RRF. Single SQLite file, no service. |
| `redis` | `lib/memory/redis_agent_memory.py` | yes | Opt-in semantic memory client backed by Redis Agent Memory Server (needs the daemon + Ollama). Use with `ALFRED_MEMORY_PROVIDERS=redis,fleet`. |
| `fleet` | `lib/memory/providers.py` | yes | Local operational ledger and review queue. SQLite under `$ALFRED_HOME`. |
| `gbrain` | `lib/memory/gbrain_stub.py` | no | Optional subprocess shim into a personal knowledge base CLI. Not bundled functionality. |
| `null` | `lib/memory/providers.py` | no | No-op. `recall` returns `[]`, `reflect` raises. Used when `ALFRED_MEMORY_PROVIDERS=null` or the env var is explicitly empty. |

### Which provider stores a promoted lesson?

The promote path writes to the **first dedicated recall store** named in
`ALFRED_MEMORY_PROVIDERS` (`sqlite` or `redis`), resolved by
`memory.config.load_lesson_writer`. So the default (`sqlite,fleet`) writes to
the embedded SQLite store, while `redis,fleet` writes to Redis exactly as
before. `fleet` is the candidate ledger, not a recall store, so it is never the
write target; if a chain names no recall store at all, promotion falls back to
the zero-daemon SQLite store so a lesson is never silently lost.

## Configuration

Two env vars drive the chain:

```sh
# Consult order. Comma-separated. Whitespace and case insensitive.
# Unset default -> sqlite,fleet (zero-daemon). Opt into Redis with redis,fleet.
ALFRED_MEMORY_PROVIDERS=sqlite,fleet

# Optional: path to a personal knowledge base CLI.
# Read by gbrain_stub; the binary is invoked with a JSON payload on
# stdin and must emit a JSON list of lessons on stdout.
ALFRED_GBRAIN_BIN=/usr/local/bin/gbrain

# Redis Agent Memory Server. Leave URL unset to use ALFRED_AMS_HOST/PORT.
ALFRED_REDIS_MEMORY_URL=http://127.0.0.1:8088
ALFRED_REDIS_MEMORY_NAMESPACE=alfred
ALFRED_REDIS_MEMORY_USER_ID=local-user
ALFRED_REDIS_MEMORY_TOKEN=
ALFRED_REDIS_MEMORY_SEARCH_MODE=semantic

# Bundled local server defaults.
ALFRED_AMS_HOST=127.0.0.1
ALFRED_AMS_PORT=8088
ALFRED_AMS_REDIS_URL=redis://127.0.0.1:6379/0
ALFRED_AMS_EMBEDDING_MODEL=ollama/mxbai-embed-large
ALFRED_AMS_EMBEDDING_DIM=1024
ALFRED_AMS_GENERATION_MODEL=ollama/llama3.2:1b
```

Sample shell config for adding a read-only personal knowledge base behind the
default memory stack:

```sh
export ALFRED_MEMORY_PROVIDERS=redis,fleet,gbrain
export ALFRED_GBRAIN_BIN=/usr/local/bin/gbrain
```

Sample shell config for "memory off":

```sh
export ALFRED_MEMORY_PROVIDERS=null
```

Sample shell config for a custom Agent Memory Server endpoint:

```sh
export ALFRED_MEMORY_PROVIDERS=redis,fleet
export ALFRED_REDIS_MEMORY_URL=http://127.0.0.1:9090
export ALFRED_REDIS_MEMORY_NAMESPACE=alfred
```

Keep `fleet` in the chain unless you are deliberately running without the local
review queue and operational ledger. The default reflection mode stores
agent-proposed memories as FleetBrain candidates first; the LLM judge then saves
safe ones autonomously unless `ALFRED_AUTO_PROMOTE=0` opts out (see
`docs/FLEET_BRAIN.md`). Redis is the promoted lesson store; FleetBrain is the
queue, ledger, and recall fallback.

Redis Agent Memory runs as a pure vector store. The bundled server has every
server-side LLM text process turned off (discrete-memory extraction, topic
extraction, NER, working-memory summarization), client writes pass
`deduplicate=False`, and periodic compaction is disabled, because the only local
generation model (`llama3.2:1b`) is too weak for that work and corrupts the
store. The real intelligence (the LLM judge that gates auto-save, plus
candidate-side dedup) lives upstream in Python; Redis just stores and retrieves
the embeddings.

### Resilience and tuning knobs

The Redis Agent Memory client is fault-tolerant: it retries transient failures
and trips a circuit breaker so a flaky or down AMS never blocks a firing (recall
then falls back to FleetBrain). The auto-promote path has its own budget knobs so
the LLM judge cannot run away with cost. Every value below has a working default,
so none of these are required; set them only to tune. Names and defaults are read
from `lib/memory/redis_agent_memory.py`, `lib/fleet_brain/__init__.py`, and
`lib/memory_judge.py`.

The **read (recall) path** carries a separate, lower budget from writes. Recall
runs inline before a firing and its result is optional (a miss falls back to
FleetBrain), so it must never pay the full write-path retry cost: a
dead-but-not-yet-tripped AMS would otherwise cost `timeout_s * (max_retries + 1)`
(~6s by default) on every recall. Recall uses `ALFRED_REDIS_MEMORY_RECALL_TIMEOUT_S`
and `ALFRED_REDIS_MEMORY_RECALL_MAX_RETRIES` instead, while reflect / promote
writes keep the full `ALFRED_REDIS_MEMORY_TIMEOUT_S` + `ALFRED_REDIS_MEMORY_MAX_RETRIES`
budget. All four still share the one circuit breaker.

| Variable | Default | What it controls |
|---|---|---|
| `ALFRED_REDIS_MEMORY_TIMEOUT_S` | `2.0` | Per-request AMS HTTP timeout for WRITES (reflect / promote / forget), in seconds. |
| `ALFRED_REDIS_MEMORY_MAX_RETRIES` | `2` | Retry attempts for a transient WRITE failure before giving up on that call. |
| `ALFRED_REDIS_MEMORY_RECALL_TIMEOUT_S` | `1.0` | Per-request AMS HTTP timeout for the read (recall) path, in seconds. Lower so recall never blocks a firing. |
| `ALFRED_REDIS_MEMORY_RECALL_MAX_RETRIES` | `0` | Retry attempts for a transient RECALL failure. Default 0: a recall miss falls back to FleetBrain rather than retrying. |
| `ALFRED_REDIS_MEMORY_BREAKER_THRESHOLD` | `5` | Consecutive failures that trip the circuit breaker and short-circuit further AMS calls. |
| `ALFRED_REDIS_MEMORY_BREAKER_COOLDOWN_S` | `30` | Seconds the breaker stays open before it allows a probe request again. |
| `ALFRED_AUTO_PROMOTE_THRESHOLD` | `0.5` | Minimum candidate confidence to consider for auto-promotion (the LLM judge is the real decider above this bar). |
| `ALFRED_AUTO_PROMOTE_NO_JUDGE_THRESHOLD` | `0.9` | Confidence floor used instead when the LLM judge is off, so default-confidence candidates are not promoted with no review. |
| `ALFRED_AUTO_PROMOTE_MAX_PER_RUN` | `5` | Cap on successful auto-promotions per run. |
| `ALFRED_AUTO_PROMOTE_MAX_JUDGE_CALLS` | `25` | Per-run judge-call budget (never below `MAX_PER_RUN`); bounds cost since rejected or duplicate candidates still cost a judge call. |
| `ALFRED_AUTO_PROMOTE_JUDGE_TIMEOUT` | `120` | Per-call LLM judge timeout, in seconds. |

The `ALFRED_AUTO_PROMOTE`, `ALFRED_AUTO_PROMOTE_KILL`, and
`ALFRED_AUTO_PROMOTE_LLM_JUDGE` on/off switches are covered in
`docs/FLEET_BRAIN.md`.

`ALFRED_MEMORY_REFLECTION_MODE` controls how model-generated reflections are
stored:

| Mode | Behavior |
|---|---|
| `candidate` | Default. Queue reviewable FleetBrain candidates. |
| `direct` | Write through the provider chain immediately. Redis is first in the default chain. |
| `off` | Skip runtime reflection. Recall still works. |

Check the local server:

```sh
alfred memory doctor
alfred memory doctor --json
alfred brain ams-status
alfred brain redis-status
alfred brain ams-status --json
```

Use `alfred memory doctor` first when debugging setup. It checks the provider
chain, Redis Agent Memory, FleetBrain, code-memory, code-map freshness, and the
read-only MCP tools in one report. The `alfred brain ...` commands are narrower
provider-specific probes.

Mirror reviewed local lessons into Redis explicitly:

```sh
alfred brain redis-sync --dry-run
alfred brain redis-sync --codename senior-dev --repo your-org/api
```

The sync path only reads trusted lessons from the fleet-brain. It does not
upload raw transcripts, event logs, or unreviewed memory candidates.

## How chaining works

`ChainedMemoryProvider` consults providers in declared order:

1. **`recall`** asks every provider, logs and skips failures, deduplicates by
   lesson id, then round-robins the merged results in provider order. One flaky
   backend cannot break the firing, and later read-only providers can still add
   context when Redis has useful hits.
2. **`reflect`** writes to the first provider that does not raise
   `NotImplementedError`. Read-only providers earlier in the chain
   are skipped silently. Runner-generated memories use the reflection mode above
   before they call into the provider chain.

Worked trace for `ALFRED_MEMORY_PROVIDERS=redis,fleet,gbrain`:

```
firing "senior-dev" starts, asks memory.recall(codename="senior-dev", repo="acme-org/api"):
  -> redis.recall(...) returns [Lesson("GraphQL schema lives in src/schema.graphql")]
  -> fleet.recall(...) returns [Lesson("Keep schema PRs small")]
  -> gbrain.recall(...) returns [Lesson("older notes about acme-org/api auth")]
  -> chain returns a merged, deduplicated list bounded by the caller's limit

firing finishes, queues a memory candidate (default candidate mode):
  -> FleetBrain stores the proposed lesson as a candidate
  -> alfred brain auto-promote lets an LLM judge save safe and
     behavior-changing candidates autonomously unless ALFRED_AUTO_PROMOTE=0
     opts out (see docs/FLEET_BRAIN.md); promotion routes the lesson toward Redis
  -> alfred brain redis-sync back-fills older promoted lessons into Redis

if ALFRED_MEMORY_REFLECTION_MODE=direct:
  -> redis.reflect(...) writes the lesson to Agent Memory Server first in the
     chain, with FleetBrain behind it for firings, candidates, and reliability rows
```

## Writing a custom provider

Drop a new file under `lib/memory/`, implement the Protocol, and
register it:

```python
# lib/memory/team_wiki.py
from dataclasses import dataclass

@dataclass
class TeamWikiProvider:
    name: str = "team_wiki"

    def recall(self, *, query=None, codename=None, repo=None, limit=5):
        # call your wiki API, map results to Lesson objects
        ...

    def reflect(self, **_):
        raise NotImplementedError("team_wiki is read-only")
```

Then in `lib/memory/config.py`:

```python
from .team_wiki import TeamWikiProvider

PROVIDER_REGISTRY["team_wiki"] = lambda env: TeamWikiProvider()
```

Now `ALFRED_MEMORY_PROVIDERS=redis,fleet,team_wiki` works.

## Privacy and scope

- The `gbrain` provider is an optional personal knowledge
  base. It is **not** bundled with Alfred. The shim only knows the
  path you configure; if the binary is missing, recall
  returns empty and the chain keeps working.
- Nothing in the default memory layer phones home. Redis Agent Memory Server
  binds to loopback, and FleetBrain is a SQLite file under `$ALFRED_HOME`.
- `alfred brain redis-sync` remains available for carrying older reviewed
  FleetBrain lessons into Redis.
- Read-only providers cannot exfiltrate FleetBrain. Writes flow
  the other direction (to the first writer in the chain), never out
  to gbrain.

## Deferred

- **Cross-provider result ranking.** Redis Agent Memory handles semantic recall.
  A later chain can rank Redis, FleetBrain, and read-only provider results
  together before prompt injection.
- **Reflect-everywhere.** Today `reflect` writes to the first
  writable provider only. A "broadcast" mode that fans the write
  out to every writer is intentionally out of scope until users prove
  they want Redis and FleetBrain written on every firing.
- **Per-provider limits.** `limit` is passed verbatim to every
  provider in the chain; a smarter chain could split the budget.
- **Cache.** No caching between calls. Each provider is hit fresh on
  every `recall`. Good enough for a single-host Alfred install.
