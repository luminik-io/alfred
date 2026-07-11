<!-- GENERATED FILE - do not edit by hand.
     Regenerate with `bin/alfred-config-doc.py` after changing
     `lib/alfred_config.py`. `--check` fails CI on drift. -->

# Alfred configuration reference

Every environment variable Alfred reads is declared once in the typed
registry at `lib/alfred_config.py`. This page is generated from it.

- Declared variables: **377**
- Operator-facing (in `.env.example`): **68**
- Internal / experimental: **309**

Operator-facing vars also appear, with their defaults, in
`.env.example`. Internal vars are listed here for completeness; they
are experimental, deep-tuning, or set by Alfred itself at runtime.

## Runtime, paths, and repo scope

| Variable | Type | Default | Scope | Description |
| --- | --- | --- | --- | --- |
| `ALFRED_HOME` | path | `~/.alfred` | operator | Root of the Alfred runtime (state/, worktrees/, lib/, bin/). |
| `ALFRED_REPO` | path |  | internal | Path to the Alfred source checkout; falls back to ALFRED_HOME. |
| `ALFRED_SOURCE_DIR` | path |  | internal | Alternate Alfred source dir, checked after ALFRED_REPO. |
| `ALFRED_PYTHON` | path |  | internal | Explicit Python interpreter override for agent-launch. |
| `ALFRED_WORKSPACE_SUBDIR` | str | `product` | internal | Subdirectory under WORKSPACE_ROOT holding per-repo checkouts. |
| `ALFRED_STATE_DIR` | path |  | internal | Explicit override for the state/ directory (defaults under ALFRED_HOME). |
| `ALFRED_DRY_RUN` | bool | `0` | internal | Run the firing without side effects; narrates each boundary. |
| `ALFRED_DOCTOR` | bool | `0` | internal | Preflight-only doctor mode: exit 0 with a sentinel instead of doing work. |
| `ALFRED_NONINTERACTIVE` | bool | `0` | internal | Force non-interactive behaviour (no prompts). |
| `ALFRED_REPO_LOCAL_MAP` | str |  | internal | Comma-separated repo=path overrides mapping bare repo slugs to local dirs. |
| `ALFRED_PUBLIC_REPO_ALLOWLIST` | list |  | internal | Comma-separated repos that may be treated as public. |
| `ALFRED_PUBLIC_OPERATOR` | str |  | internal | Operator handle used in public-facing artifacts. |
| `ALFRED_SELF_REPO` | str |  | internal | Canonical self repo slug (Alfred's own repo). |
| `ALFRED_INTAKE_PROFILE` | str |  | internal | Selects the intake profile used to scope a fresh setup. |
| `ALFRED_AGENT_HOOKS` | bool | `0` | internal | Enable agent lifecycle hooks; a manual-debug seam. |
| `ALFRED_AGENT_NOTIFICATIONS` | bool | `0` | internal | Enable agent desktop/CLI notifications. |
| `ALFRED_AGENTS_CONF` | path |  | internal | Path to an explicit agents configuration file. |
| `ALFRED_CONNECTORS_CONFIG` | path | `examples/connectors.yaml` | internal | Path to the connectors config used by connector-sync. |
| `ALFRED_GH_BIN` | path |  | internal | Path to the gh CLI (falls back to GH_BIN then PATH). |
| `ALFRED_DISABLE_CHECKOUT_SYNC` | bool | `0` | internal | Skip the per-firing repo checkout sync. |
| `ALFRED_DISABLE_CLAUDE_AUTH_REPAIR` | bool | `0` | internal | Disable the automatic claude auth repair step. |
| `ALFRED_CLAUDE_PROJECTS_DIR` | path |  | internal | Override for the Claude Code projects directory. |
| `ALFRED_CLAUDE_USAGE_LIMITS_FILE` | path |  | internal | Path to a file describing Claude usage limits. |
| `ALFRED_CODEX_SESSIONS_DIR` | path |  | internal | Override for the Codex sessions directory. |
| `GH_ORG` | str |  | operator | GitHub org/user owning the fleet's product repos. |
| `GH_BIN` | path |  | internal | Path to the gh CLI (fallback for ALFRED_GH_BIN). |
| `OPERATOR_NAME` | str |  | operator | Display name shown in agent prompts. |
| `OPERATOR_EMAIL` | str |  | operator | Operator email referenced in some prompts. |
| `OPERATOR_GH_HANDLE` | str |  | operator | Operator GitHub login when distinct from GH_ORG. |
| `WORKSPACE_ROOT` | path | `~/code` | operator | Parent directory of per-repo product checkouts. |
| `WORKSPACE_SUBDIR` | str | `product` | operator | Subdirectory under WORKSPACE_ROOT holding checkouts. |
| `CLAUDE_BIN` | path |  | operator | Absolute path to the claude CLI (defaults to PATH). |
| `CLAUDE_CODE_OAUTH_TOKEN` | secret |  | operator | Long-lived OAuth token so launchd/systemd can authenticate claude. |

## Memory providers and code intelligence

| Variable | Type | Default | Scope | Description |
| --- | --- | --- | --- | --- |
| `ALFRED_MEMORY_PROVIDERS` | list |  | operator | Ordered comma-separated list of active memory providers. |
| `ALFRED_MEMORY_MCP` | bool | `1` | operator | Expose the memory MCP server; disable with 0. |
| `ALFRED_MCP_ALLOW_RAW_MEMORY` | bool | `0` | internal | Allow raw (unjudged) memory writes through the MCP. |
| `ALFRED_MEMORY_RECALL_THRESHOLD` | float |  | operator | Minimum similarity for a recalled memory to be injected. |
| `ALFRED_MEMORY_EXTRACT` | bool | `0` | internal | Arm post-firing memory extraction; off touches nothing. |
| `ALFRED_MEMORY_EXTRACT_TIMEOUT` | int | `180` | internal | Timeout in seconds for the memory-extraction LLM call. |
| `ALFRED_MEMORY_MAX_LESSONS` | int |  | internal | Cap on lessons pulled from the recall store per firing. |
| `ALFRED_MEMORY_ANCHOR_RECALL` | bool | `0` | internal | Enable anchor-based recall. |
| `ALFRED_MEMORY_TYPED_RECALL` | bool | `0` | internal | Enable typed (category-aware) recall. |
| `ALFRED_MEMORY_DELTA` | bool | `0` | internal | Enable delta memory tracking. |
| `ALFRED_MEMORY_RANK` | bool | `0` | internal | Enable weighted memory re-ranking. |
| `ALFRED_MEMORY_RANK_W_RELEVANCE` | float |  | internal | Weight of relevance in memory ranking. |
| `ALFRED_MEMORY_RANK_W_ROI` | float |  | internal | Weight of ROI in memory ranking. |
| `ALFRED_MEMORY_RANK_W_RECENCY` | float |  | internal | Weight of recency in memory ranking. |
| `ALFRED_MEMORY_RANK_W_REUSE` | float |  | internal | Weight of reuse count in memory ranking. |
| `ALFRED_MEMORY_DECAY_HALFLIFE_DAYS` | float |  | internal | Half-life in days for memory recency decay. |
| `ALFRED_MEMORY_CONSOLIDATE` | bool | `0` | internal | Arm destructive memory consolidation (opt-in). |
| `ALFRED_MEMORY_CONSOLIDATE_SEMANTIC` | bool | `0` | internal | Arm semantic (embedding-based) consolidation. |
| `ALFRED_MEMORY_CONSOLIDATE_SIM_THRESHOLD` | float |  | internal | Similarity threshold above which memories consolidate. |
| `ALFRED_MEMORY_REFLECTION_MODE` | enum (direct/candidate/off) | `candidate` | internal | Reflection write mode. |
| `ALFRED_PLANNING_MEMORY` | bool | `1` | internal | Use memory during planning; disable to turn off. |
| `ALFRED_PLANNING_MEMORY_CANDIDATES` | bool | `1` | internal | Surface planning memory candidates; disable to turn off. |
| `ALFRED_MEMORY_SQLITE_DB` | path |  | operator | SQLite memory database path. |
| `ALFRED_MEMORY_SQLITE_DENSE` | bool | `0` | operator | Enable dense (sqlite-vec) retrieval in the SQLite provider. |
| `ALFRED_MEMORY_SQLITE_POOL` | int | `50` | operator | Per-arm candidate pool size before fusion (SQLite). |
| `ALFRED_MEMORY_SQLITE_RRF_K` | int | `60` | operator | RRF constant k for SQLite hybrid fusion. |
| `ALFRED_MEMORY_PG_DSN` | secret |  | internal | Postgres DSN for the pgvector memory provider. |
| `ALFRED_MEMORY_PG_DENSE` | bool | `1` | internal | Enable dense retrieval in the Postgres provider. |
| `ALFRED_MEMORY_PG_INDEX` | enum (hnsw/ivfflat) | `hnsw` | internal | Vector index kind for pgvector. |
| `ALFRED_MEMORY_PG_POOL` | int | `50` | internal | Per-arm candidate pool before fusion (Postgres). |
| `ALFRED_MEMORY_PG_RRF_K` | int | `60` | internal | RRF constant k for Postgres hybrid fusion. |
| `ALFRED_MEMORY_PG_TABLE_PREFIX` | str |  | internal | Optional table-name prefix for the Postgres provider. |
| `ALFRED_REDIS_MEMORY_URL` | str | `http://127.0.0.1:8088` | operator | Base URL of the Redis agent-memory server. |
| `ALFRED_REDIS_MEMORY_TOKEN` | secret |  | internal | Auth token for the Redis memory server. |
| `ALFRED_REDIS_MEMORY_USER_ID` | str |  | internal | User id scoping Redis memory reads/writes. |
| `ALFRED_REDIS_MEMORY_NAMESPACE` | str | `alfred` | operator | Namespace prefix for Redis memory keys. |
| `ALFRED_REDIS_MEMORY_SEARCH_MODE` | enum (semantic/lexical/hybrid) | `semantic` | operator | Search mode for Redis memory recall. |
| `ALFRED_REDIS_MEMORY_TIMEOUT_S` | float |  | internal | Request timeout in seconds for Redis memory calls. |
| `ALFRED_REDIS_MEMORY_RECALL_TIMEOUT_S` | float |  | internal | Recall-specific timeout in seconds for Redis memory. |
| `ALFRED_REDIS_MEMORY_MAX_RETRIES` | int | `2` | internal | Max retries for Redis memory calls. |
| `ALFRED_REDIS_MEMORY_RECALL_MAX_RETRIES` | int |  | internal | Max retries for Redis memory recall specifically. |
| `ALFRED_REDIS_MEMORY_BREAKER_THRESHOLD` | int | `5` | internal | Failures before the Redis memory circuit breaker trips. |
| `ALFRED_REDIS_MEMORY_BREAKER_COOLDOWN_S` | int | `30` | internal | Cooldown in seconds for the Redis memory breaker. |
| `ALFRED_AMS_HOST` | str | `127.0.0.1` | operator | Host the local AMS server binds to. |
| `ALFRED_AMS_PORT` | int | `8088` | operator | Port the local AMS server binds to. |
| `ALFRED_AMS_REDIS_URL` | str | `redis://127.0.0.1:6379/0` | operator | Redis URL backing the AMS server. |
| `ALFRED_AMS_TOKEN` | secret |  | internal | Auth token for the AMS server (fallback for REDIS_MEMORY_TOKEN). |
| `ALFRED_AMS_AUTH_MODE` | str | `disabled` | internal | Auth mode for the AMS server. |
| `ALFRED_AMS_EMBEDDING_MODEL` | str | `ollama/mxbai-embed-large` | operator | Embedding model AMS uses for dense recall. |
| `ALFRED_AMS_EMBEDDING_DIM` | int | `1024` | operator | Embedding dimension for AMS dense recall. |
| `ALFRED_AMS_GENERATION_MODEL` | str | `ollama/llama3.2:1b` | operator | Generation model AMS uses for summarisation. |
| `ALFRED_AMS_OLLAMA_BASE_URL` | str |  | internal | Ollama base URL for local AMS embedding/generation. |
| `ALFRED_AMS_LONG_TERM_MEMORY` | bool |  | internal | Toggle AMS long-term memory storage. |
| `ALFRED_AMS_FORGETTING` | bool |  | internal | Toggle AMS forgetting/eviction. |
| `ALFRED_AMS_COMPACTION_INTERVAL_S` | int |  | internal | AMS compaction interval in seconds. |
| `ALFRED_AMS_UVX_SPEC` | str |  | internal | uvx spec used to launch the AMS server. |
| `ALFRED_CODE_MEMORY_MCP` | bool | `1` | operator | Expose the code-memory MCP server (on by default when the binary is installed; set 0 to disable). |
| `ALFRED_CODE_MEMORY_AUTOFETCH` | bool | `1` | operator | Auto-fetch code memory before a firing (on by default; set 0 to disable). |
| `ALFRED_CODE_MEMORY_BIN` | path |  | operator | Path override for the code-memory binary. |
| `ALFRED_CODE_MEMORY_REPO` | str | `DeusData/codebase-memory-mcp` | operator | Repo slug providing the code-memory index source. |
| `ALFRED_CODE_MEMORY_REPOS` | list |  | operator | Comma-separated repos the code-memory index covers. |
| `ALFRED_CODE_MEMORY_INDEX_DIR` | path |  | operator | Directory holding the code-memory index. |
| `ALFRED_CODE_MEMORY_HOME` | path |  | internal | Home directory for the code-memory tool. |
| `ALFRED_CODE_MEMORY_VERSION` | str | `v0.8.1` | operator | Pinned code-memory tool version. |
| `ALFRED_CODE_MEMORY_DISCOVERY_LIMIT` | int |  | internal | Max files the code-memory discovery pass scans. |
| `ALFRED_CODE_MEMORY_CONNECT_TIMEOUT_S` | int | `10` | internal | Connect timeout in seconds for the code-memory server. |
| `ALFRED_CODE_MEMORY_FETCH_TIMEOUT_S` | int | `120` | internal | Fetch timeout in seconds for the code-memory server. |
| `ALFRED_GRAPHIFY_MCP` | bool | `0` | operator | Expose the graphify MCP server. |
| `ALFRED_GRAPHIFY_BIN` | path |  | operator | Path override for the graphify binary. |
| `ALFRED_GRAPHIFY_GRAPH` | path | `graphify-out/graph.json` | operator | Path to the graphify graph JSON. |
| `ALFRED_GRAPHIFY_FALLBACK` | str |  | operator | Fallback provider when graphify is unavailable (e.g. code-memory). |
| `ALFRED_GRAPH_DENSIFY` | bool | `1` | internal | Enable graph densification projection; on by default. |
| `ALFRED_GBRAIN_BIN` | path |  | internal | Path to an external graph-brain binary. |
| `ALFRED_CODE_MAP_REPOS` | list |  | operator | Comma-separated repos included in code-map indexing. |
| `ALFRED_CODE_MAP_BACKEND_REPO` | str |  | internal | Local dir name of the backend repo for code-map. |
| `ALFRED_CODE_MAP_SIDECAR_REPO` | str |  | internal | Local dir name of the sidecar repo for code-map. |
| `ALFRED_CODE_MAP_CLIENT_REPOS` | list |  | internal | Comma-separated frontend/mobile repos for code-map. |
| `ALFRED_CODE_MAP_MAX_FILES` | int | `2000` | internal | Per-repo source-file cap for graph indexing. |

## Engine selection, quotas, retries

| Variable | Type | Default | Scope | Description |
| --- | --- | --- | --- | --- |
| `ALFRED_ENGINE` | enum (claude/codex/hybrid) | `hybrid` | operator | Fleet-wide engine override for testing (claude/codex/hybrid). |
| `ALFRED_MAX_STEPS` | int | `200` | internal | Hard ceiling on lifecycle steps per firing (clamped 1..100000). |
| `ALFRED_LOOP_DETECT` | bool | `1` | internal | Enable repeated-action loop detection; set 0 to disable. |
| `ALFRED_LOOP_WINDOW` | int | `3` | internal | Window size for loop detection (clamped 2..50). |
| `ALFRED_ENGINE_QUOTA_DEFAULT_HOURS` | int | `5` | internal | Default provider quota window in hours (codex 5-hour block). |
| `ALFRED_BREAKER_THRESHOLD` | int | `5` | internal | Consecutive failures before the circuit breaker trips (1..100). |
| `ALFRED_BREAKER_COOLDOWN_SECONDS` | int | `300` | internal | Circuit-breaker cooldown in seconds (1..86400). |
| `ALFRED_FAIL_STREAK_THRESHOLD` | int | `5` | internal | Failure streak that halts a runner across the fleet. |
| `ALFRED_RETRY_AFTER_MAX_SECONDS` | int | `300` | internal | Ceiling for honoring a Retry-After header (1..86400). |
| `ALFRED_RETRY_BASE_SECONDS` | int | `2` | internal | Base delay for exponential retry backoff (1..600). |
| `ALFRED_RETRY_CAP_SECONDS` | int | `60` | internal | Cap for the exponential retry window (1..3600). |
| `ALFRED_TRANSIENT_MAX_RETRIES` | int | `3` | internal | Retries for transient errors; 0 disables retry (0..20). |
| `ALFRED_LLM_MAX_RETRIES` | int |  | internal | Max retries for direct LLM helper calls (ALFRED_LLM_* family). |
| `ALFRED_LLM_BACKOFF_BASE_S` | float |  | internal | Base backoff seconds for LLM helper retries. |
| `ALFRED_LLM_BACKOFF_MAX_S` | float |  | internal | Max backoff seconds for LLM helper retries. |
| `ALFRED_LLM_TIMEOUT_PER_REQUEST_S` | float |  | internal | Per-request timeout in seconds for LLM helper calls. |
| `ALFRED_BENCHMARK_TURN_BUDGET_CLAUDE_MAX_5X` | int |  | internal | Per-plan turn budget override for the claude-max-5x benchmark tier. |

## Context batteries (governor, read-delta, digests)

| Variable | Type | Default | Scope | Description |
| --- | --- | --- | --- | --- |
| `ALFRED_MEMORY_INJECT_MAX_CHARS` | int |  | internal | Character budget for injected memory context. |
| `ALFRED_MEMORY_INJECT_OPS` | bool | `1` | internal | Split ops vs codebase memory on inject; on by default. |
| `ALFRED_CONTEXT_GOVERNOR` | bool | `0` | operator | Enable the context governor. |
| `ALFRED_CONTEXT_MAX_CHARS` | int |  | internal | Max characters kept before context governance. |
| `ALFRED_CONTEXT_MAX_BYTES` | int |  | internal | Max bytes kept before context governance. |
| `ALFRED_CONTEXT_HEAD_CHARS` | int |  | internal | Head characters preserved by the context governor. |
| `ALFRED_CONTEXT_TAIL_CHARS` | int |  | internal | Tail characters preserved by the context governor. |
| `ALFRED_READ_DELTA` | bool | `0` | internal | Enable read-delta (re-read only changed regions). |
| `ALFRED_READ_DELTA_CONTEXT` | int |  | internal | Context lines around a read-delta change. |
| `ALFRED_READ_DELTA_MAX_CHARS` | int |  | internal | Max characters emitted per read-delta. |
| `ALFRED_READ_DELTA_MAX_RATIO` | float |  | internal | Max diff-to-file ratio before read-delta falls back to a full read. |
| `ALFRED_OUTPUT_COMPACTOR` | bool | `1` | internal | Compact large tool output; opt out with 0. |
| `ALFRED_OUTPUT_COMPACTOR_MIN_BYTES` | int |  | internal | Minimum output size before compaction kicks in. |
| `ALFRED_OUTPUT_COMPACTOR_MAX_BYTES` | int |  | internal | Maximum compacted output size. |
| `ALFRED_OUTPUT_COMPACTOR_HEAD_LINES` | int |  | internal | Head lines kept when compacting tool output. |
| `ALFRED_OUTPUT_COMPACTOR_TAIL_LINES` | int |  | internal | Tail lines kept when compacting tool output. |
| `ALFRED_OUTPUT_COMPACTOR_TOOLS` | list |  | internal | Comma-separated tools the output compactor applies to. |
| `ALFRED_TOOL_DIGEST` | bool | `1` | internal | Digest verbose tool schemas; opt out with 0. |
| `ALFRED_TOOL_DIGEST_MIN_CHARS` | int |  | internal | Below this size, tool output passes through un-digested. |
| `ALFRED_SKILLS_INJECT` | bool | `1` | internal | Inject skill headers into the prompt; on by default. |
| `ALFRED_SKILLS_DIR` | path |  | internal | Directory holding skill definitions. |
| `ALFRED_SKELETON_PRIMING` | bool | `0` | internal | Prime the model with a repo skeleton; off by default. |
| `ALFRED_SKELETON_MAX_FILES` | int |  | internal | Max files included in the skeleton priming pass. |
| `ALFRED_SKELETON_MAX_SIGNATURE_LINES` | int |  | internal | Max signature lines per file in skeleton priming. |
| `ALFRED_REPO_PROFILE` | bool | `0` | internal | Emit a deterministic repo profile into context; off by default. |
| `ALFRED_REPO_PROFILE_MAX_CHARS` | int |  | internal | Character budget for the repo profile. |
| `ALFRED_GOAL_WIRING` | bool | `0` | internal | Wire active-goal context into firings (opt-in). |

## Context compression (headroom, condenser)

| Variable | Type | Default | Scope | Description |
| --- | --- | --- | --- | --- |
| `ALFRED_CONTEXT_COMPRESSION` | bool | `0` | operator | Enable headroom-based context compression. |
| `ALFRED_COMPRESSION_ENGINE` | enum (headroom) |  | operator | Selects the context-compression engine (e.g. headroom). |
| `ALFRED_CONDENSER_ENABLED` | bool | `0` | internal | Enable the conversation condenser. |
| `ALFRED_CONDENSER_MODEL` | str |  | internal | Model used to summarise when condensing (keep cheap). |
| `ALFRED_CONDENSER_KEEP_FIRST` | int |  | internal | Number of leading turns the condenser keeps verbatim. |
| `ALFRED_CONDENSER_KEEP_LAST` | int |  | internal | Number of trailing turns the condenser keeps verbatim. |
| `ALFRED_CONDENSER_TRIGGER_TURNS` | int |  | internal | Turn count that triggers condensation. |
| `ALFRED_CONDENSER_TRIGGER_CHARS` | int |  | internal | Character count that triggers condensation. |
| `ALFRED_CONDENSER_MAX_SUMMARY_CHARS` | int |  | internal | Max characters in a condenser summary. |
| `ALFRED_HEADROOM_BIN` | path |  | internal | Path override for the headroom binary. |
| `ALFRED_HEADROOM_MODEL` | str |  | internal | Model headroom uses for compression. |
| `ALFRED_HEADROOM_AUTOFETCH` | bool | `0` | internal | Auto-fetch headroom compression before a firing. |
| `ALFRED_HEADROOM_AUTOFETCH_CMD` | str |  | internal | Command (shlex-split) headroom runs to auto-fetch. |
| `ALFRED_HEADROOM_COMPRESS_CMD` | str |  | internal | Command headroom runs to compress the transcript. |
| `ALFRED_HEADROOM_MESSAGE_ROLE` | str |  | internal | Explicit message role headroom assigns to compressed context. |

## Slack transport, converse, and bridge

| Variable | Type | Default | Scope | Description |
| --- | --- | --- | --- | --- |
| `SLACK_WEBHOOK_URL` | secret |  | operator | Direct Slack webhook URL (simplest transport). |
| `SLACK_WEBHOOK_SECRET_ID` | str | `alfred/slack-webhook` | operator | AWS Secrets Manager id for the Slack webhook. |
| `SLACK_WEBHOOK_SECRET_REGION` | str | `us-east-1` | operator | AWS region for the Slack webhook secret. |
| `SLACK_BOT_TOKEN` | secret |  | operator | Slack bot token for Block Kit / threaded posts. |
| `SLACK_BOT_TOKEN_SECRET_ID` | str | `alfred/slack-bot-token` | operator | AWS Secrets Manager id for the Slack bot token. |
| `SLACK_BOT_TOKEN_SECRET_REGION` | str |  | operator | AWS region for the Slack bot-token secret. |
| `SLACK_HOME_CHANNEL` | str | `alfred` | operator | Default Slack channel for fleet posts. |
| `SLACK_MIN_HOURS` | int |  | internal | Minimum hours between certain Slack notifications. |
| `ALFRED_SLACK_APP_TOKEN` | secret |  | operator | Slack app-level token for Socket Mode. |
| `ALFRED_SLACK_BOT_USER_ID` | str |  | internal | Bot user id used to detect self-mentions. |
| `ALFRED_SLACK_BOT_TOKEN_SECRET_ID` | str |  | internal | Alfred-scoped Secrets Manager id for the bot token. |
| `ALFRED_SLACK_BOT_TOKEN_SECRET_REGION` | str |  | internal | Alfred-scoped region for the bot-token secret. |
| `ALFRED_SLACK_BOT_TOKEN_CACHE` | path |  | internal | Path to the on-disk Slack bot-token cache. |
| `ALFRED_SLACK_NATIVE_SENDS` | bool | `0` | internal | Prefer native Slack API sends over webhooks. |
| `ALFRED_SLACK_CONVERSE_ENABLED` | bool | `0` | operator | Enable the Slack converse (chat) surface. |
| `ALFRED_SLACK_CONVERSE_ENGINE` | enum (claude/codex/hybrid) |  | operator | Engine backing Slack converse (claude/codex/hybrid). |
| `ALFRED_SLACK_CONVERSE_CHANNELS` | list |  | operator | Comma-separated channels where converse is active. |
| `ALFRED_SLACK_CONVERSE_TIMEOUT` | int |  | internal | Timeout in seconds for a converse turn. |
| `ALFRED_SLACK_CONVERSE_THREAD_CONTEXT` | int |  | internal | How much thread context converse includes. |
| `ALFRED_SLACK_CONVERSE_STREAM_THROTTLE` | float |  | internal | Throttle in seconds between streamed converse updates. |
| `ALFRED_SLACK_AMBIENT` | bool | `0` | internal | Enable ambient (unmentioned) Slack engagement; off by default. |
| `ALFRED_SLACK_AMBIENT_CHANNELS` | list |  | internal | Comma-separated channels where ambient engagement is allowed. |
| `ALFRED_SLACK_MEMORY_CANDIDATES` | bool | `1` | internal | Surface Slack-derived memory candidates; disable to turn off. |
| `ALFRED_SLACK_RUN_CODENAMES` | list |  | internal | Comma-separated agent codenames runnable from Slack. |
| `ALFRED_SLACK_MAX_TOTAL_BACKOFF_SECONDS` | float |  | internal | Cap on total Slack post backoff in seconds. |
| `ALFRED_SLACK_RECONNECT_BASE_BACKOFF_S` | float | `1.0` | internal | Base backoff for Slack listener reconnects. |
| `ALFRED_SLACK_RECONNECT_MAX_BACKOFF_S` | float | `30.0` | internal | Max backoff for Slack listener reconnects. |
| `ALFRED_SLACK_RECONNECT_CHECK_INTERVAL_S` | float | `15.0` | internal | Interval between Slack listener reconnect checks. |
| `ALFRED_SLACK_THREAD_SYNC_INTERVAL_S` | float |  | internal | Interval in seconds between Slack thread-status syncs. |
| `ALFRED_OPERATOR_SLACK_USER_ID` | str |  | operator | Slack user id of the operator (naming + trust). |
| `ALFRED_TRUSTED_SLACK_USER_IDS` | list |  | operator | Comma-separated Slack user ids treated as trusted collaborators. |
| `ALFRED_INTENT_ROUTER_ENABLED` | bool | `0` | internal | Enable the LLM intent router for ambient messages. |
| `ALFRED_INTENT_ROUTER_ENGINE` | str |  | internal | Engine used by the intent router. |
| `ALFRED_INTENT_ROUTER_MIN_CONFIDENCE` | float |  | internal | Minimum confidence before the intent router acts. |
| `ALFRED_INTENT_ROUTER_TIMEOUT` | int |  | internal | Timeout in seconds for the intent router. |
| `ALFRED_CONVERSE_OPERATIONAL_GROUNDING` | bool | `0` | internal | Ground converse replies in live operational state. |
| `ALFRED_CONVERSE_POLL_SECONDS` | float | `0.04` | internal | Poll interval for the converse loop. |
| `ALFRED_COMPOSE_CONVERSE_ENGINE` | str |  | internal | Engine override for composed converse replies. |
| `ALFRED_PLAN_THREAD_ANSWER_ENGINE` | str |  | internal | Engine used to answer plan-thread questions. |
| `ALFRED_PLAN_THREAD_ANSWER_TIMEOUT` | int |  | internal | Timeout in seconds for plan-thread answers. |
| `ALFRED_BRIDGE_ENABLED` | bool | `0` | operator | Enable the Slack-to-issue bridge. |
| `ALFRED_BRIDGE_LABEL` | str |  | internal | Pickup label applied to issues created by the bridge. |
| `ALFRED_BRIDGE_REPOS` | list |  | internal | Comma-separated repos the bridge may open issues in. |
| `ALFRED_BRIDGE_MIN_READINESS_SCORE` | float |  | internal | Minimum readiness score before the bridge creates an issue. |
| `ALFRED_BRIDGE_APPROVAL_PHRASES` | list |  | internal | Comma/semicolon separated phrases that approve a bridge issue. |

## Serve API and status cache

| Variable | Type | Default | Scope | Description |
| --- | --- | --- | --- | --- |
| `ALFRED_SERVE_UI_DIST` | path |  | internal | Directory of the built serve UI to host. |
| `ALFRED_STATUS_AUTH_TTL_SECONDS` | int | `60` | internal | TTL in seconds for the authenticated status cache. |
| `ALFRED_STATUS_SLOW_TTL_SECONDS` | int | `1800` | internal | TTL in seconds for the slow (heavy) status cache. |
| `ALFRED_SSE_HEARTBEAT_SECONDS` | float | `15.0` | internal | Heartbeat interval for server-sent-event streams. |

## Scheduler, cleanup, disk guardian, backup

| Variable | Type | Default | Scope | Description |
| --- | --- | --- | --- | --- |
| `ALFRED_MERGE_REQUIRE_APPROVAL` | bool | `1` | operator | Automerge only merges PRs an operator approved on GitHub (fail-closed gate). |
| `ALFRED_MERGE_MIN_APPROVALS` | int | `1` | operator | Approving reviews required when GitHub branch protection does not decide it. |
| `ALFRED_RECOVERY_MAX_ATTEMPTS` | int | `1` | operator | Bounded recovery turns to fix a failed push/CI/merge-gate step before HOLD (0 disables). |
| `ALFRED_LAUNCH_DIR` | path | `~/Library/LaunchAgents` | internal | Directory launchd plists are installed into. |
| `ALFRED_LAUNCHD_LABEL_PREFIX` | str | `alfred` | internal | Reverse-DNS label prefix for launchd plists. |
| `ALFRED_SYSTEMD_USER_DIR` | path | `~/.config/systemd/user` | internal | Directory systemd --user units are installed into. |
| `ALFRED_MIN_FREE_DISK_GB` | float | `3.0` | operator | Absolute free-disk floor in GB before firings back off. |
| `ALFRED_MIN_FREE_DISK_PCT` | float | `5.0` | operator | Relative free-disk floor in percent before firings back off. |
| `ALFRED_DISK_SLACK_MIN_HOURS` | int | `6` | operator | Throttle window in hours for the disk-low Slack warning. |
| `ALFRED_CLEANUP_AUTODISCOVER` | bool | `1` | operator | Auto-discover .worktrees pools to sweep; opt out with 0. |
| `ALFRED_CLEANUP_SCHEDULED_RECLAIM` | bool | `0` | operator | Run dev-cache/Docker reclaim on the daily pass. |
| `ALFRED_CLEANUP_EXTRA_PATHS` | list |  | internal | Extra worktree-pool paths for cleanup to sweep. |
| `ALFRED_CLEANUP_MAX_AGE_HOURS` | int | `48` | internal | Age threshold in hours for normal cleanup. |
| `ALFRED_CLEANUP_EMERGENCY_MAX_AGE_HOURS` | int | `1` | internal | Age threshold in hours for emergency cleanup. |
| `ALFRED_CLEANUP_TMP_PREFIXES` | list |  | internal | Temp-dir prefixes cleanup is allowed to remove. |
| `ALFRED_EMERGENCY_SKIP_DEV_CACHES` | bool | `0` | internal | Skip dev-cache reclaim during emergency cleanup. |
| `ALFRED_EMERGENCY_SKIP_DOCKER` | bool | `0` | internal | Skip Docker reclaim during emergency cleanup. |
| `ALFRED_EMERGENCY_EVENTS_RETENTION_DAYS` | int | `3` | internal | Events retention in days under emergency cleanup. |
| `ALFRED_EMERGENCY_TRANSCRIPT_RETENTION_DAYS` | int | `3` | internal | Transcript retention in days under emergency cleanup. |
| `ALFRED_EVENTS_RETENTION_DAYS` | int | `30` | internal | Events retention in days for normal cleanup. |
| `ALFRED_TRANSCRIPT_RETENTION_DAYS` | int | `30` | internal | Transcript retention in days for normal cleanup. |
| `ALFRED_SPEND_RETENTION_DAYS` | int | `90` | internal | Spend-ledger retention in days. |
| `ALFRED_PREFLIGHT_FORCE_SLACK` | bool | `0` | internal | Force the Slack preflight even when recently checked. |
| `ALFRED_PREFLIGHT_SLACK_MIN_MINUTES` | int | `60` | internal | Minimum minutes between Slack preflight checks. |
| `ALFRED_PRE_PUSH_TIMEOUT_S` | int | `900` | internal | Timeout in seconds for the pre-push hook. |
| `ALFRED_HOOK_REPOS` | list |  | internal | Space-separated repo dirs to install the pre-push hook into. |
| `ALFRED_HOOK_SOURCE` | path |  | internal | Path to the canonical pre-push hook source. |
| `ALFRED_DEPENDENCY_WARNING_TTL_S` | int | `21600` | internal | TTL in seconds for the missing-dependency warning. |
| `ALFRED_BACKUP_DEST` | str |  | internal | s3://bucket/prefix upload target for cold backups. |
| `ALFRED_BACKUP_AWS_PROFILE` | str |  | internal | AWS profile for cold-backup uploads. |
| `ALFRED_BACKUP_KEEP` | int |  | internal | Number of cold backups to retain. |
| `ALFRED_BACKUP_LOCAL_ONLY` | bool | `0` | internal | Keep the cold backup local, skipping upload. |
| `ALFRED_BACKUP_OUTPUT_DIR` | path |  | internal | Local directory for cold-backup output. |
| `ALFRED_BACKUP_PRUNE` | bool | `1` | internal | Prune old cold backups after a successful run. |
| `ALFRED_BACKUP_STAMP` | str |  | internal | Explicit timestamp stamp for a cold backup. |
| `ALFRED_BACKUP_CONFIRM_CMD` | str |  | internal | Command run to confirm a cold backup completed. |

## Proof telemetry

| Variable | Type | Default | Scope | Description |
| --- | --- | --- | --- | --- |
| `ALFRED_TELEMETRY_ENABLED` | bool | `1` | operator | Emit proof telemetry; opt out with 0. |
| `ALFRED_TELEMETRY_URL` | str |  | operator | Override the telemetry ingest URL. |
| `ALFRED_DEFAULT_TELEMETRY_URL` | str |  | internal | Hosted default telemetry URL; set empty to disable the default. |
| `ALFRED_TELEMETRY_TOKEN` | secret |  | internal | Optional shared ingest token for hosted telemetry. |
| `ALFRED_TELEMETRY_TRUSTED_TOKEN` | secret |  | internal | Server-trust token proving a telemetry payload is first-party. |

## Per-agent caps, repos, engines

| Variable | Type | Default | Scope | Description |
| --- | --- | --- | --- | --- |
| `ALFRED_MORNING_BRIEF_AGENTS` | list |  | internal | Comma-separated agents included in the morning brief. |
| `ALFRED_MORNING_BRIEF_REPOS` | list |  | internal | Comma-separated repos included in the morning brief. |
| `ALFRED_REVIEWER_REPOS` | list |  | internal | Repos the reviewer agent covers. |
| `ALFRED_REVIEWER_SPECS_REPOS` | list |  | internal | Repos the reviewer treats as spec repos. |
| `ALFRED_REVIEWER_DIFF_CAP` | int | `4000` | internal | Diff-line cap for a standard review. |
| `ALFRED_REVIEWER_DIFF_CAP_SPECS` | int | `8000` | internal | Diff-line cap for a spec review. |
| `ALFRED_REVIEWER_REVIEW_CAP` | int | `30` | internal | Daily cap on reviews performed. |
| `ALFRED_REVIEWER_TURN_CAP` | int | `800` | internal | Daily turn cap for the reviewer. |
| `ALFRED_REVIEWER_MAX_TURNS` | int |  | internal | Per-firing max turns for the reviewer (min 40). |
| `ALFRED_REVIEWER_TIMEOUT` | int | `900` | internal | Per-review timeout in seconds (min 60). |
| `ALFRED_REVIEWER_FALLBACK_TIMEOUT` | int | `1800` | internal | Fallback review timeout in seconds (min 60). |
| `ALFRED_FIXER_REPOS` | list |  | internal | Repos the fixer agent covers. |
| `ALFRED_FIXER_REVIEW_AGENT` | str | `reviewer` | internal | Codename of the review agent the fixer re-triggers. |
| `ALFRED_FIXER_ESCALATE_AFTER` | int | `3` | internal | No-commit attempts before the fixer escalates. |
| `ALFRED_FIXER_TURN_CAP` | int | `600` | internal | Daily turn cap for the fixer. |
| `ALFRED_FIXER_MAX_TURNS` | int |  | internal | Per-firing max turns for the fixer (min 25). |
| `ALFRED_PLANNER_REPOS` | list |  | internal | Repos the planner agent covers. |
| `ALFRED_PLANNER_DAILY_ISSUE_CAP` | int |  | internal | Daily cap on issues the planner may open. |
| `ALFRED_PLANNER_MAX_TURNS` | int |  | internal | Per-firing max turns for the planner (min 40). |
| `ALFRED_TRIAGE_REPOS` | list |  | internal | Repos the triage agent covers. |
| `ALFRED_TRIAGE_DAILY_CAP` | int | `50` | internal | Daily cap on triage actions. |
| `ALFRED_TRIAGE_TURN_CAP` | int | `600` | internal | Daily turn cap for triage. |
| `ALFRED_TRIAGE_MAX_TURNS` | int |  | internal | Per-firing max turns for triage (min 20). |
| `ALFRED_TRIAGE_TOUCHED_TTL_DAYS` | int | `7` | internal | Days a triaged item is remembered as touched. |
| `ALFRED_SENIOR_DEV_REPOS` | list |  | internal | Repos the senior-dev agent covers. |
| `ALFRED_SENIOR_DEV_TURN_CAP` | int | `5000` | internal | Daily turn cap for senior-dev. |
| `ALFRED_SENIOR_DEV_MAX_TURNS` | int |  | internal | Per-firing max turns for senior-dev (min 40). |
| `ALFRED_SENIOR_DEV_SELFASSESS_MAX_TURNS` | int |  | internal | Max turns for the senior-dev self-assessment pass (min 1). |
| `ALFRED_TEST_ENGINEER_REPOS` | list |  | internal | Repos the test-engineer agent covers. |
| `ALFRED_TEST_ENGINEER_MAX_TURNS` | int |  | internal | Per-firing max turns for the test-engineer (min 40). |
| `ALFRED_AUTOMERGE_REPOS` | list |  | internal | Repos eligible for auto-merge. |
| `ALFRED_AUTOMERGE_FIX_AGENT` | str | `fixer` | internal | Codename of the fix agent auto-merge escalates to. |
| `ALFRED_AUTOMERGE_REVIEW_AGENT` | str | `reviewer` | internal | Codename of the review agent auto-merge waits on. |
| `ALFRED_AUTOMERGE_MIN_AGE_MIN` | int | `30` | internal | Minimum PR age in minutes before auto-merge. |
| `ALFRED_CURATOR_MAX_ITEMS` | int | `8` | internal | Cap on findings the curator shows in Slack. |
| `ALFRED_CLAIM_SWEEP_REPOS` | list |  | internal | Repos the claim sweep runs over. |
| `ALFRED_CLAIM_MAX_AGE_HOURS` | int | `4` | internal | Max age in hours before a stale claim is swept. |
| `ALFRED_QUEUE_REPOS` | list |  | internal | Assignment repo allowlist for issue queueing. |
| `ALFRED_GITHUB_POLL_REPOS` | list |  | internal | Repos the GitHub poller watches. |
| `ALFRED_IN_PROGRESS_REQUIRE_AGENT_EVIDENCE` | bool |  | internal | Require agent evidence before marking an issue in-progress. |
| `ALFRED_ARCHITECT_APPROVAL_MAX_AGE_HOURS` | int | `24` | internal | Max age in hours for an architect approval to stay valid. |
| `ALFRED_SPEC_PLANNER_REPOS` | list |  | internal | Repos the spec planner covers. |
| `ALFRED_SPEC_PLANNER_SPEC_DIR` | path |  | internal | Directory holding specs for the spec planner. |
| `ALFRED_SPEC_PLANNER_DAILY_BUNDLE_CAP` | int |  | internal | Daily cap on bundles the spec planner emits. |
| `ALFRED_SPEC_INTERROGATOR_PROMPT` | path |  | internal | Prompt override for the spec interrogator. |
| `ALFRED_SELF_PROOF_REPOS` | list |  | internal | Repos the self-proof pass covers. |
| `ALFRED_SELF_PROOF_SELF_REPO` | str |  | internal | Self repo slug for self-proof. |
| `ALFRED_SELF_PROOF_EXCLUDED_AUTHORS` | list |  | internal | Authors excluded from self-proof attribution. |
| `ALFRED_SHIPPED_REPOS` | list |  | internal | Repos the shipped board tracks. |
| `ALFRED_SHIPPED_SUMMARY_REPOS` | list |  | operator | Shared fallback repos for shipped summaries. |
| `ALFRED_SHIPPED_SUMMARY_DAILY_REPOS` | list |  | internal | Repos for the daily shipped summary. |
| `ALFRED_SHIPPED_SUMMARY_WEEKLY_REPOS` | list |  | internal | Repos for the weekly shipped summary. |
| `ALFRED_SHIPPED_SUMMARY_AGENT_LABELS` | list |  | internal | Labels marking agent-shipped work in summaries. |
| `ALFRED_SHIPPED_SUMMARY_QUERY_LIMIT` | int |  | internal | Query limit for shipped-summary lookups. |
| `ALFRED_SHIPPED_AGENT_AUTHORS` | list |  | internal | Authors counted as agents on the shipped board. |
| `ALFRED_SHIPPED_AGENT_LABELS` | list |  | internal | Labels marking agent-shipped PRs. |
| `ALFRED_SHIPPED_AGENT_BRANCH_PREFIXES` | list |  | internal | Branch prefixes marking agent-shipped work. |
| `ALFRED_SHIPPED_QUEUE_INCLUDE_LABELS` | list |  | internal | Labels a shipped item must have to be included (* for all). |
| `ALFRED_SHIPPED_QUEUE_EXCLUDE_LABELS` | list |  | internal | Labels that exclude an item from the shipped queue. |
| `ALFRED_AUTO_PROMOTE` | bool | `1` | internal | Enable memory auto-promotion; 0 disables save/skip decisions. |
| `ALFRED_AUTO_PROMOTE_KILL` | bool | `0` | internal | Kill switch that fails auto-promotion closed. |
| `ALFRED_AUTO_PROMOTE_LLM_JUDGE` | bool | `1` | internal | Use the LLM judge for auto-promotion; falsy disables it. |
| `ALFRED_AUTO_PROMOTE_JUDGE_TIMEOUT` | int | `120` | internal | Timeout in seconds for the auto-promote judge call. |
| `ALFRED_AUTO_PROMOTE_THRESHOLD` | float |  | internal | Score threshold for auto-promotion. |
| `ALFRED_AUTO_PROMOTE_NO_JUDGE_THRESHOLD` | float |  | internal | Score threshold used when the judge is disabled. |
| `ALFRED_AUTO_PROMOTE_MAX_PER_RUN` | int |  | internal | Max memories auto-promoted per run. |
| `ALFRED_AUTO_PROMOTE_MAX_JUDGE_CALLS` | int |  | internal | Max judge calls per auto-promote run. |
| `ALFRED_RUBRIC_GATE` | bool | `0` | operator | Grade the build against an issue-derived rubric and revise before opening a PR. |
| `ALFRED_RUBRIC` | str |  | internal | Inline rubric or rubric name enabling graded output. |
| `ALFRED_RUBRIC_GRADER_ENGINE` | str |  | internal | Engine used by the rubric grader. |
| `ALFRED_RUBRIC_MAX_ITERATIONS` | int | `1` | operator | Max rubric revision passes before opening the PR (1..10). |
| `ALFRED_PLANNING_ASSISTANT_ENGINE` | str |  | internal | Fallback engine for the planning assistant. |
| `ALFRED_PLANNING_ASSISTANT_TIMEOUT` | int | `180` | internal | Timeout in seconds for the planning assistant. |
| `ALFRED_ISSUE_SUMMARY_ENABLED` | bool | `0` | internal | Enable LLM issue summaries. |
| `ALFRED_ISSUE_SUMMARY_ENGINE` | str |  | internal | Engine used for issue summaries. |
| `ALFRED_ISSUE_SUMMARY_MAX_CHARS` | int |  | internal | Character cap for an issue summary (~360). |
| `ALFRED_ISSUE_SUMMARY_TIMEOUT` | int |  | internal | Timeout in seconds for an issue summary. |
| `ALFRED_PR_EVIDENCE` | bool | `1` | internal | Attach test/PR evidence; on by default. |
| `LABEL_STATE_SWEEP_REPOS` | list |  | internal | Repos the label-state sweep runs over. |
| `LABEL_STATE_SKIP_DEDUP_CHECK` | bool | `0` | internal | Skip the label-state dedup check. |

## Ops integrations (E2E, ops-watch, scrub)

| Variable | Type | Default | Scope | Description |
| --- | --- | --- | --- | --- |
| `ALFRED_AWS_PROFILE` | str |  | internal | AWS profile for Secrets Manager and other AWS calls. |
| `ALFRED_SECRETS_BACKEND` | enum (aws) |  | internal | Set to 'aws' to enable the AWS Secrets Manager backend. |
| `ALFRED_ARTIFACT_BUCKET` | str |  | internal | S3 bucket for uploaded run artifacts. |
| `ALFRED_E2E_RUNNER_TARGET_URL` | str |  | internal | Target URL the E2E runner exercises. |
| `ALFRED_E2E_RUNNER_TESTS_DIR` | path |  | internal | Directory of E2E tests to run. |
| `ALFRED_E2E_RUNNER_SECRET_ID` | str | `alfred/huntress/test-account` | internal | Secrets Manager id for the E2E test account. |
| `ALFRED_E2E_RUNNER_AWS_PROFILE` | str |  | internal | AWS profile for E2E runner AWS calls. |
| `ALFRED_E2E_RUNNER_S3_BUCKET` | str |  | internal | S3 bucket receiving E2E artifacts. |
| `ALFRED_E2E_RUNNER_ECS_CLUSTER` | str |  | internal | ECS cluster the E2E runner targets. |
| `ALFRED_E2E_RUNNER_ECS_SERVICES` | list |  | internal | Comma-separated ECS services for the E2E runner. |
| `ALFRED_E2E_RUNNER_DEPLOY_REF_REPO` | str |  | internal | Repo whose deploy ref the E2E runner checks. |
| `ALFRED_OPS_WATCH_AWS_PROFILE` | str |  | internal | AWS profile for ops-watch. |
| `ALFRED_OPS_WATCH_ECS_CLUSTER` | str |  | internal | Staging ECS cluster ops-watch monitors. |
| `ALFRED_OPS_WATCH_SERVICES` | list |  | internal | Comma-separated service=repo:branch entries for ops-watch. |
| `ALFRED_OPS_WATCH_SENTRY_ORG` | str |  | internal | Sentry org ops-watch queries. |
| `ALFRED_OPS_WATCH_SENTRY_SECRET_ID` | str | `alfred/sentry-api-token` | internal | Secrets Manager id for the Sentry API token. |
| `ALFRED_NIGHTLY_NPM_REPOS` | list |  | internal | Semicolon-separated npm nightly entries. |
| `ALFRED_NIGHTLY_ADVISORY_REPOS` | list |  | internal | Semicolon-separated advisory nightly entries. |
| `ALFRED_SCRUB_NAMES` | list |  | internal | Comma-separated names the scrubber redacts. |
| `ALFRED_SCRUB_NAMES_FILE` | path |  | internal | Path to a file of names for the scrubber. |
| `ALFRED_SCRUB_EXTRA_PATTERNS` | path |  | internal | Path to extra scrub patterns. |
| `ALFRED_SLOP_RULES` | path |  | internal | Path to slop-detector rules. |
| `ALFRED_SLOP_TARGET_PATH` | path | `.` | internal | Default target path for the slop detector. |
| `ALFRED_FLEET_BRAIN_DB` | path |  | internal | Path to the local fleet-brain counts database. |
| `ALFRED_FLEET_OVERLAY` | str | `fleet_overlay` | internal | Name of the private fleet overlay module (silently absent by default). |

## Onboarding and theming

| Variable | Type | Default | Scope | Description |
| --- | --- | --- | --- | --- |
| `ALFRED_ONBOARDING_ENGINE` | str |  | internal | Engine override for the conversational onboarding. |
| `ALFRED_ONBOARDING_PROMPT` | path |  | internal | Prompt override for onboarding. |
| `ALFRED_THEME_BUILDER_ENGINE` | str |  | internal | Engine override for the chat theme builder. |
| `ALFRED_THEME_BUILDER_PROMPT` | path |  | internal | Prompt override for the theme builder. |

## Internal and process-set

| Variable | Type | Default | Scope | Description |
| --- | --- | --- | --- | --- |
| `ALFRED_LIB` | path |  | internal | Derived lib/ path under ALFRED_HOME; set by launch scripts, not by operators. |
| `ALFRED_FIRING_ID` | str |  | internal | Per-firing id set by the scheduler; required by the read-delta ledger. |
| `ALFRED_MEMORY_REFLECTIONS_JSON` | str |  | internal | Marker key for the reflections JSON block; not an operator knob. |
| `ALFRED_DEMO_MODEL` | str |  | internal | Force a specific model for the demo. |
| `ALFRED_DEMO_FAST_MODEL` | str | `haiku` | internal | Fast model used by the demo. |
| `ALFRED_DEMO_VERBOSE` | bool | `0` | internal | Verbose demo engine output. |
| `ALFRED_BRAIN_LOG_LEVEL` | enum (DEBUG/INFO/WARNING/ERROR) | `WARNING` | internal | Log level for the brain process. |
| `ALFRED_DISK_EMERGENCY_IN_PROGRESS` | bool | `0` | internal | Reentrancy guard set while emergency disk cleanup runs. |
