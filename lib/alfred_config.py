"""Central registry of every ``ALFRED_*`` (and adjacent operator) config var.

This module is the single typed source of truth for the environment-variable
contract. Historically the ~360 ``ALFRED_*`` vars were read from hundreds of
scattered ``os.environ`` sites, and only a handful were documented in
``.env.example``. That sprawl is what this registry fixes:

* Every variable is declared once here as a :class:`ConfigVar` with a name,
  type, default, one-line description, category, and an ``operator`` flag
  (operator-facing vs internal).
* ``bin/alfred-config-doc.py`` regenerates ``.env.example`` (operator vars) and
  ``docs/CONFIG.md`` (full reference) from this registry, so the docs can never
  drift from the declared set again.
* ``tests/test_config_registry.py`` is the ratchet: it fails when a new
  ``ALFRED_*`` token appears in ``lib/`` or ``bin/`` that is not declared here
  (or explicitly listed as a non-var token), which stops future sprawl.

The registry does not replace the low-level env-read helpers in
``agent_runner/config.py`` (``env_int``, ``_truthy_env``, ...); it sits above
them as the schema + docs layer. The typed accessors at the bottom
(:func:`get_bool`, :func:`get_int`, :func:`get_str`, :func:`get_list`) read
``os.environ`` at call time and fall back to the registered default, so callers
that migrate onto them get one honest default per var.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["bool", "int", "float", "str", "enum", "list", "path", "secret"]

# Categories used to group vars in the generated docs. Keep this list and the
# per-var ``category`` values in sync; the doc generator orders sections by it.
CATEGORIES: tuple[str, ...] = (
    "runtime",
    "memory",
    "engine",
    "batteries",
    "compression",
    "slack",
    "server",
    "scheduler",
    "telemetry",
    "agents",
    "ops",
    "onboarding",
    "internal",
)

# Truthy set shared with agent_runner.config._truthy_env. Kept local so this
# module has no import dependency on agent_runner (avoids an import cycle: the
# runner imports the registry, not the other way round).
_TRUTHY = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class ConfigVar:
    """One declared environment variable."""

    name: str
    kind: Kind
    default: str | None
    category: str
    description: str
    # True => operator-facing (emitted into .env.example). False => internal,
    # experimental, or a process-set guard (docs/CONFIG.md only).
    operator: bool = False
    # Optional allowed values for enum-kind vars (documentation only).
    choices: tuple[str, ...] = field(default_factory=tuple)


def V(
    name: str,
    kind: Kind,
    default: str | None,
    category: str,
    description: str,
    *,
    operator: bool = False,
    choices: tuple[str, ...] = (),
) -> ConfigVar:
    return ConfigVar(name, kind, default, category, description, operator, choices)


# --------------------------------------------------------------------------
# The registry. Grouped by category for readability; order within a group is
# roughly alphabetical. Descriptions are one truthful line drawn from the code
# around each usage. ``operator=True`` marks the knobs an operator would set in
# ``$ALFRED_HOME/.env``; everything else is internal/experimental/deep-tuning.
# --------------------------------------------------------------------------
_VARS: tuple[ConfigVar, ...] = (
    # ---- runtime: home, workspace, paths, engine selection, run modes ----
    V(
        "ALFRED_HOME",
        "path",
        "~/.alfred",
        "runtime",
        "Root of the Alfred runtime (state/, worktrees/, lib/, bin/).",
        operator=True,
    ),
    V(
        "ALFRED_REPO",
        "path",
        None,
        "runtime",
        "Path to the Alfred source checkout; falls back to ALFRED_HOME.",
    ),
    V(
        "ALFRED_SOURCE_DIR",
        "path",
        None,
        "runtime",
        "Alternate Alfred source dir, checked after ALFRED_REPO.",
    ),
    V(
        "ALFRED_LIB",
        "path",
        None,
        "internal",
        "Derived lib/ path under ALFRED_HOME; set by launch scripts, not by operators.",
    ),
    V(
        "ALFRED_PYTHON",
        "path",
        None,
        "runtime",
        "Explicit Python interpreter override for agent-launch.",
    ),
    V(
        "ALFRED_WORKSPACE_SUBDIR",
        "str",
        "product",
        "runtime",
        "Subdirectory under WORKSPACE_ROOT holding per-repo checkouts.",
    ),
    V(
        "ALFRED_STATE_DIR",
        "path",
        None,
        "runtime",
        "Explicit override for the state/ directory (defaults under ALFRED_HOME).",
    ),
    V(
        "ALFRED_ENGINE",
        "enum",
        "hybrid",
        "engine",
        "Fleet-wide engine override for testing (claude/codex/hybrid).",
        operator=True,
        choices=("claude", "codex", "hybrid"),
    ),
    V(
        "ALFRED_DRY_RUN",
        "bool",
        "0",
        "runtime",
        "Run the firing without side effects; narrates each boundary.",
    ),
    V(
        "ALFRED_DOCTOR",
        "bool",
        "0",
        "runtime",
        "Preflight-only doctor mode: exit 0 with a sentinel instead of doing work.",
    ),
    V(
        "ALFRED_NONINTERACTIVE",
        "bool",
        "0",
        "runtime",
        "Force non-interactive behaviour (no prompts).",
    ),
    V(
        "ALFRED_FIRING_ID",
        "str",
        None,
        "internal",
        "Per-firing id set by the scheduler; required by the read-delta ledger.",
    ),
    V(
        "ALFRED_REPO_LOCAL_MAP",
        "str",
        None,
        "runtime",
        "Comma-separated repo=path overrides mapping bare repo slugs to local dirs.",
    ),
    V(
        "ALFRED_PUBLIC_REPO_ALLOWLIST",
        "list",
        None,
        "runtime",
        "Comma-separated repos that may be treated as public.",
    ),
    V(
        "ALFRED_PUBLIC_OPERATOR",
        "str",
        None,
        "runtime",
        "Operator handle used in public-facing artifacts.",
    ),
    V("ALFRED_SELF_REPO", "str", None, "runtime", "Canonical self repo slug (Alfred's own repo)."),
    V(
        "ALFRED_INTAKE_PROFILE",
        "str",
        None,
        "runtime",
        "Selects the intake profile used to scope a fresh setup.",
    ),
    V(
        "ALFRED_MAX_STEPS",
        "int",
        "200",
        "engine",
        "Hard ceiling on lifecycle steps per firing (clamped 1..100000).",
    ),
    V(
        "ALFRED_LOOP_DETECT",
        "bool",
        "1",
        "engine",
        "Enable repeated-action loop detection; set 0 to disable.",
    ),
    V(
        "ALFRED_LOOP_WINDOW",
        "int",
        "3",
        "engine",
        "Window size for loop detection (clamped 2..50).",
    ),
    V(
        "ALFRED_AGENT_HOOKS",
        "bool",
        "0",
        "runtime",
        "Enable agent lifecycle hooks; a manual-debug seam.",
    ),
    V(
        "ALFRED_AGENT_NOTIFICATIONS",
        "bool",
        "0",
        "runtime",
        "Enable agent desktop/CLI notifications.",
    ),
    V(
        "ALFRED_AGENTS_CONF",
        "path",
        None,
        "runtime",
        "Path to an explicit agents configuration file.",
    ),
    V(
        "ALFRED_CONNECTORS_CONFIG",
        "path",
        "examples/connectors.yaml",
        "runtime",
        "Path to the connectors config used by connector-sync.",
    ),
    V(
        "ALFRED_GH_BIN",
        "path",
        None,
        "runtime",
        "Path to the gh CLI (falls back to GH_BIN then PATH).",
    ),
    V(
        "ALFRED_AWS_PROFILE",
        "str",
        None,
        "ops",
        "AWS profile for Secrets Manager and other AWS calls.",
    ),
    V(
        "ALFRED_SECRETS_BACKEND",
        "enum",
        None,
        "ops",
        "Set to 'aws' to enable the AWS Secrets Manager backend.",
        choices=("aws",),
    ),
    V("ALFRED_ARTIFACT_BUCKET", "str", None, "ops", "S3 bucket for uploaded run artifacts."),
    V(
        "ALFRED_DISABLE_CHECKOUT_SYNC",
        "bool",
        "0",
        "runtime",
        "Skip the per-firing repo checkout sync.",
    ),
    V(
        "ALFRED_DISABLE_CLAUDE_AUTH_REPAIR",
        "bool",
        "0",
        "runtime",
        "Disable the automatic claude auth repair step.",
    ),
    V(
        "ALFRED_CLAUDE_PROJECTS_DIR",
        "path",
        None,
        "runtime",
        "Override for the Claude Code projects directory.",
    ),
    V(
        "ALFRED_CLAUDE_USAGE_LIMITS_FILE",
        "path",
        None,
        "runtime",
        "Path to a file describing Claude usage limits.",
    ),
    V(
        "ALFRED_CODEX_SESSIONS_DIR",
        "path",
        None,
        "runtime",
        "Override for the Codex sessions directory.",
    ),
    # ---- engine: quota, breaker, retry, llm retry knobs ----
    V(
        "ALFRED_ENGINE_QUOTA_DEFAULT_HOURS",
        "int",
        "5",
        "engine",
        "Default provider quota window in hours (codex 5-hour block).",
    ),
    V(
        "ALFRED_BREAKER_THRESHOLD",
        "int",
        "5",
        "engine",
        "Consecutive failures before the circuit breaker trips (1..100).",
    ),
    V(
        "ALFRED_BREAKER_COOLDOWN_SECONDS",
        "int",
        "300",
        "engine",
        "Circuit-breaker cooldown in seconds (1..86400).",
    ),
    V(
        "ALFRED_FAIL_STREAK_THRESHOLD",
        "int",
        "5",
        "engine",
        "Failure streak that halts a runner across the fleet.",
    ),
    V(
        "ALFRED_RETRY_AFTER_MAX_SECONDS",
        "int",
        "300",
        "engine",
        "Ceiling for honoring a Retry-After header (1..86400).",
    ),
    V(
        "ALFRED_RETRY_BASE_SECONDS",
        "int",
        "2",
        "engine",
        "Base delay for exponential retry backoff (1..600).",
    ),
    V(
        "ALFRED_RETRY_CAP_SECONDS",
        "int",
        "60",
        "engine",
        "Cap for the exponential retry window (1..3600).",
    ),
    V(
        "ALFRED_TRANSIENT_MAX_RETRIES",
        "int",
        "3",
        "engine",
        "Retries for transient errors; 0 disables retry (0..20).",
    ),
    V(
        "ALFRED_LLM_MAX_RETRIES",
        "int",
        None,
        "engine",
        "Max retries for direct LLM helper calls (ALFRED_LLM_* family).",
    ),
    V(
        "ALFRED_LLM_BACKOFF_BASE_S",
        "float",
        None,
        "engine",
        "Base backoff seconds for LLM helper retries.",
    ),
    V(
        "ALFRED_LLM_BACKOFF_MAX_S",
        "float",
        None,
        "engine",
        "Max backoff seconds for LLM helper retries.",
    ),
    V(
        "ALFRED_LLM_TIMEOUT_PER_REQUEST_S",
        "float",
        None,
        "engine",
        "Per-request timeout in seconds for LLM helper calls.",
    ),
    V(
        "ALFRED_BENCHMARK_TURN_BUDGET_CLAUDE_MAX_5X",
        "int",
        None,
        "engine",
        "Per-plan turn budget override for the claude-max-5x benchmark tier.",
    ),
    # ---- memory: providers, ranking, extraction, consolidation ----
    V(
        "ALFRED_MEMORY_PROVIDERS",
        "list",
        None,
        "memory",
        "Ordered comma-separated list of active memory providers.",
        operator=True,
    ),
    V(
        "ALFRED_MEMORY_MCP",
        "bool",
        "1",
        "memory",
        "Expose the memory MCP server; disable with 0.",
        operator=True,
    ),
    V(
        "ALFRED_MCP_ALLOW_RAW_MEMORY",
        "bool",
        "0",
        "memory",
        "Allow raw (unjudged) memory writes through the MCP.",
    ),
    V(
        "ALFRED_MEMORY_RECALL_THRESHOLD",
        "float",
        None,
        "memory",
        "Minimum similarity for a recalled memory to be injected.",
        operator=True,
    ),
    V(
        "ALFRED_MEMORY_EXTRACT",
        "bool",
        "0",
        "memory",
        "Arm post-firing memory extraction; off touches nothing.",
    ),
    V(
        "ALFRED_MEMORY_EXTRACT_TIMEOUT",
        "int",
        "180",
        "memory",
        "Timeout in seconds for the memory-extraction LLM call.",
    ),
    V(
        "ALFRED_MEMORY_MAX_LESSONS",
        "int",
        None,
        "memory",
        "Cap on lessons pulled from the recall store per firing.",
    ),
    V(
        "ALFRED_MEMORY_INJECT_MAX_CHARS",
        "int",
        None,
        "batteries",
        "Character budget for injected memory context.",
    ),
    V(
        "ALFRED_MEMORY_INJECT_OPS",
        "bool",
        "1",
        "batteries",
        "Split ops vs codebase memory on inject; on by default.",
    ),
    V(
        "ALFRED_MERGE_REQUIRE_APPROVAL",
        "bool",
        "1",
        "scheduler",
        "Automerge only merges PRs an operator approved on GitHub (fail-closed gate).",
        operator=True,
    ),
    V(
        "ALFRED_MERGE_MIN_APPROVALS",
        "int",
        "1",
        "scheduler",
        "Distinct exact-head approving reviews Alfred always requires; branch protection may require more.",
        operator=True,
    ),
    V("ALFRED_MEMORY_ANCHOR_RECALL", "bool", "0", "memory", "Enable anchor-based recall."),
    V("ALFRED_MEMORY_TYPED_RECALL", "bool", "0", "memory", "Enable typed (category-aware) recall."),
    V("ALFRED_MEMORY_DELTA", "bool", "0", "memory", "Enable delta memory tracking."),
    V("ALFRED_MEMORY_RANK", "bool", "0", "memory", "Enable weighted memory re-ranking."),
    V(
        "ALFRED_MEMORY_RANK_W_RELEVANCE",
        "float",
        None,
        "memory",
        "Weight of relevance in memory ranking.",
    ),
    V("ALFRED_MEMORY_RANK_W_ROI", "float", None, "memory", "Weight of ROI in memory ranking."),
    V(
        "ALFRED_MEMORY_RANK_W_RECENCY",
        "float",
        None,
        "memory",
        "Weight of recency in memory ranking.",
    ),
    V(
        "ALFRED_MEMORY_RANK_W_REUSE",
        "float",
        None,
        "memory",
        "Weight of reuse count in memory ranking.",
    ),
    V(
        "ALFRED_MEMORY_DECAY_HALFLIFE_DAYS",
        "float",
        None,
        "memory",
        "Half-life in days for memory recency decay.",
    ),
    V(
        "ALFRED_MEMORY_CONSOLIDATE",
        "bool",
        "0",
        "memory",
        "Arm destructive memory consolidation (opt-in).",
    ),
    V(
        "ALFRED_MEMORY_CONSOLIDATE_SEMANTIC",
        "bool",
        "0",
        "memory",
        "Arm semantic (embedding-based) consolidation.",
    ),
    V(
        "ALFRED_MEMORY_CONSOLIDATE_SIM_THRESHOLD",
        "float",
        None,
        "memory",
        "Similarity threshold above which memories consolidate.",
    ),
    V(
        "ALFRED_MEMORY_REFLECTION_MODE",
        "enum",
        "candidate",
        "memory",
        "Reflection write mode.",
        choices=("direct", "candidate", "off"),
    ),
    V(
        "ALFRED_MEMORY_REFLECTIONS_JSON",
        "str",
        None,
        "internal",
        "Marker key for the reflections JSON block; not an operator knob.",
    ),
    V(
        "ALFRED_PLANNING_MEMORY",
        "bool",
        "1",
        "memory",
        "Use memory during planning; disable to turn off.",
    ),
    V(
        "ALFRED_PLANNING_MEMORY_CANDIDATES",
        "bool",
        "1",
        "memory",
        "Surface planning memory candidates; disable to turn off.",
    ),
    # SQLite hybrid provider
    V(
        "ALFRED_MEMORY_SQLITE_DB",
        "path",
        None,
        "memory",
        "SQLite memory database path.",
        operator=True,
    ),
    V(
        "ALFRED_MEMORY_SQLITE_DENSE",
        "bool",
        "0",
        "memory",
        "Enable dense (sqlite-vec) retrieval in the SQLite provider.",
        operator=True,
    ),
    V(
        "ALFRED_MEMORY_SQLITE_POOL",
        "int",
        "50",
        "memory",
        "Per-arm candidate pool size before fusion (SQLite).",
        operator=True,
    ),
    V(
        "ALFRED_MEMORY_SQLITE_RRF_K",
        "int",
        "60",
        "memory",
        "RRF constant k for SQLite hybrid fusion.",
        operator=True,
    ),
    # Postgres/pgvector scale provider
    V(
        "ALFRED_MEMORY_PG_DSN",
        "secret",
        None,
        "memory",
        "Postgres DSN for the pgvector memory provider.",
    ),
    V(
        "ALFRED_MEMORY_PG_DENSE",
        "bool",
        "1",
        "memory",
        "Enable dense retrieval in the Postgres provider.",
    ),
    V(
        "ALFRED_MEMORY_PG_INDEX",
        "enum",
        "hnsw",
        "memory",
        "Vector index kind for pgvector.",
        choices=("hnsw", "ivfflat"),
    ),
    V(
        "ALFRED_MEMORY_PG_POOL",
        "int",
        "50",
        "memory",
        "Per-arm candidate pool before fusion (Postgres).",
    ),
    V(
        "ALFRED_MEMORY_PG_RRF_K",
        "int",
        "60",
        "memory",
        "RRF constant k for Postgres hybrid fusion.",
    ),
    V(
        "ALFRED_MEMORY_PG_TABLE_PREFIX",
        "str",
        None,
        "memory",
        "Optional table-name prefix for the Postgres provider.",
    ),
    # Redis / AMS memory client
    V(
        "ALFRED_REDIS_MEMORY_URL",
        "str",
        "http://127.0.0.1:8088",
        "memory",
        "Base URL of the Redis agent-memory server.",
        operator=True,
    ),
    V(
        "ALFRED_REDIS_MEMORY_TOKEN",
        "secret",
        None,
        "memory",
        "Auth token for the Redis memory server.",
    ),
    V(
        "ALFRED_REDIS_MEMORY_USER_ID",
        "str",
        None,
        "memory",
        "User id scoping Redis memory reads/writes.",
    ),
    V(
        "ALFRED_REDIS_MEMORY_NAMESPACE",
        "str",
        "alfred",
        "memory",
        "Namespace prefix for Redis memory keys.",
        operator=True,
    ),
    V(
        "ALFRED_REDIS_MEMORY_SEARCH_MODE",
        "enum",
        "semantic",
        "memory",
        "Search mode for Redis memory recall.",
        operator=True,
        choices=("semantic", "lexical", "hybrid"),
    ),
    V(
        "ALFRED_REDIS_MEMORY_TIMEOUT_S",
        "float",
        None,
        "memory",
        "Request timeout in seconds for Redis memory calls.",
    ),
    V(
        "ALFRED_REDIS_MEMORY_RECALL_TIMEOUT_S",
        "float",
        None,
        "memory",
        "Recall-specific timeout in seconds for Redis memory.",
    ),
    V(
        "ALFRED_REDIS_MEMORY_MAX_RETRIES",
        "int",
        "2",
        "memory",
        "Max retries for Redis memory calls.",
    ),
    V(
        "ALFRED_REDIS_MEMORY_RECALL_MAX_RETRIES",
        "int",
        None,
        "memory",
        "Max retries for Redis memory recall specifically.",
    ),
    V(
        "ALFRED_REDIS_MEMORY_BREAKER_THRESHOLD",
        "int",
        "5",
        "memory",
        "Failures before the Redis memory circuit breaker trips.",
    ),
    V(
        "ALFRED_REDIS_MEMORY_BREAKER_COOLDOWN_S",
        "int",
        "30",
        "memory",
        "Cooldown in seconds for the Redis memory breaker.",
    ),
    # AMS (Redis agent-memory-server) provisioning
    V(
        "ALFRED_AMS_HOST",
        "str",
        "127.0.0.1",
        "memory",
        "Host the local AMS server binds to.",
        operator=True,
    ),
    V(
        "ALFRED_AMS_PORT",
        "int",
        "8088",
        "memory",
        "Port the local AMS server binds to.",
        operator=True,
    ),
    V(
        "ALFRED_AMS_REDIS_URL",
        "str",
        "redis://127.0.0.1:6379/0",
        "memory",
        "Redis URL backing the AMS server.",
        operator=True,
    ),
    V(
        "ALFRED_AMS_TOKEN",
        "secret",
        None,
        "memory",
        "Auth token for the AMS server (fallback for REDIS_MEMORY_TOKEN).",
    ),
    V("ALFRED_AMS_AUTH_MODE", "str", "disabled", "memory", "Auth mode for the AMS server."),
    V(
        "ALFRED_AMS_EMBEDDING_MODEL",
        "str",
        "ollama/mxbai-embed-large",
        "memory",
        "Embedding model AMS uses for dense recall.",
        operator=True,
    ),
    V(
        "ALFRED_AMS_EMBEDDING_DIM",
        "int",
        "1024",
        "memory",
        "Embedding dimension for AMS dense recall.",
        operator=True,
    ),
    V(
        "ALFRED_AMS_GENERATION_MODEL",
        "str",
        "ollama/llama3.2:1b",
        "memory",
        "Generation model AMS uses for summarisation.",
        operator=True,
    ),
    V(
        "ALFRED_AMS_OLLAMA_BASE_URL",
        "str",
        None,
        "memory",
        "Ollama base URL for local AMS embedding/generation.",
    ),
    V(
        "ALFRED_AMS_LONG_TERM_MEMORY",
        "bool",
        None,
        "memory",
        "Toggle AMS long-term memory storage.",
    ),
    V("ALFRED_AMS_FORGETTING", "bool", None, "memory", "Toggle AMS forgetting/eviction."),
    V(
        "ALFRED_AMS_COMPACTION_INTERVAL_S",
        "int",
        None,
        "memory",
        "AMS compaction interval in seconds.",
    ),
    V("ALFRED_AMS_UVX_SPEC", "str", None, "memory", "uvx spec used to launch the AMS server."),
    # Code memory / graphify
    V(
        "ALFRED_CODE_MEMORY_MCP",
        "bool",
        "1",
        "memory",
        "Expose the code-memory MCP server (on by default when the binary is installed; set 0 to disable).",
        operator=True,
    ),
    V(
        "ALFRED_CODE_MEMORY_AUTOFETCH",
        "bool",
        "1",
        "memory",
        "Auto-fetch code memory before a firing (on by default; set 0 to disable).",
        operator=True,
    ),
    V(
        "ALFRED_CODE_MEMORY_BIN",
        "path",
        None,
        "memory",
        "Path override for the code-memory binary.",
        operator=True,
    ),
    V(
        "ALFRED_CODE_MEMORY_REPO",
        "str",
        "DeusData/codebase-memory-mcp",
        "memory",
        "Repo slug providing the code-memory index source.",
        operator=True,
    ),
    V(
        "ALFRED_CODE_MEMORY_REPOS",
        "list",
        None,
        "memory",
        "Comma-separated repos the code-memory index covers.",
        operator=True,
    ),
    V(
        "ALFRED_CODE_MEMORY_INDEX_DIR",
        "path",
        None,
        "memory",
        "Directory holding the code-memory index.",
        operator=True,
    ),
    V(
        "ALFRED_CODE_MEMORY_HOME",
        "path",
        None,
        "memory",
        "Home directory for the code-memory tool.",
    ),
    V(
        "ALFRED_CODE_MEMORY_VERSION",
        "str",
        "v0.8.1",
        "memory",
        "Pinned code-memory tool version.",
        operator=True,
    ),
    V(
        "ALFRED_CODE_MEMORY_DISCOVERY_LIMIT",
        "int",
        None,
        "memory",
        "Max files the code-memory discovery pass scans.",
    ),
    V(
        "ALFRED_CODE_MEMORY_CONNECT_TIMEOUT_S",
        "int",
        "10",
        "memory",
        "Connect timeout in seconds for the code-memory server.",
    ),
    V(
        "ALFRED_CODE_MEMORY_FETCH_TIMEOUT_S",
        "int",
        "120",
        "memory",
        "Fetch timeout in seconds for the code-memory server.",
    ),
    V(
        "ALFRED_GRAPHIFY_MCP",
        "bool",
        "0",
        "memory",
        "Expose the graphify MCP server.",
        operator=True,
    ),
    V(
        "ALFRED_GRAPHIFY_BIN",
        "path",
        None,
        "memory",
        "Path override for the graphify binary.",
        operator=True,
    ),
    V(
        "ALFRED_GRAPHIFY_GRAPH",
        "path",
        "graphify-out/graph.json",
        "memory",
        "Path to the graphify graph JSON.",
        operator=True,
    ),
    V(
        "ALFRED_GRAPHIFY_FALLBACK",
        "str",
        None,
        "memory",
        "Fallback provider when graphify is unavailable (e.g. code-memory).",
        operator=True,
    ),
    V(
        "ALFRED_GRAPH_DENSIFY",
        "bool",
        "1",
        "memory",
        "Enable graph densification projection; on by default.",
    ),
    V("ALFRED_GBRAIN_BIN", "path", None, "memory", "Path to an external graph-brain binary."),
    V(
        "ALFRED_CODE_MAP_REPOS",
        "list",
        None,
        "memory",
        "Comma-separated repos included in code-map indexing.",
        operator=True,
    ),
    V(
        "ALFRED_CODE_MAP_BACKEND_REPO",
        "str",
        None,
        "memory",
        "Local dir name of the backend repo for code-map.",
    ),
    V(
        "ALFRED_CODE_MAP_SIDECAR_REPO",
        "str",
        None,
        "memory",
        "Local dir name of the sidecar repo for code-map.",
    ),
    V(
        "ALFRED_CODE_MAP_CLIENT_REPOS",
        "list",
        None,
        "memory",
        "Comma-separated frontend/mobile repos for code-map.",
    ),
    V(
        "ALFRED_CODE_MAP_MAX_FILES",
        "int",
        "2000",
        "memory",
        "Per-repo source-file cap for graph indexing.",
    ),
    # ---- batteries: context governor, read-delta, compaction, digests ----
    V(
        "ALFRED_CONTEXT_GOVERNOR",
        "bool",
        "0",
        "batteries",
        "Enable the context governor.",
        operator=True,
    ),
    V(
        "ALFRED_CONTEXT_COMPRESSION",
        "bool",
        "0",
        "compression",
        "Enable headroom-based context compression.",
        operator=True,
    ),
    V(
        "ALFRED_CONTEXT_MAX_CHARS",
        "int",
        None,
        "batteries",
        "Max characters kept before context governance.",
    ),
    V(
        "ALFRED_CONTEXT_MAX_BYTES",
        "int",
        None,
        "batteries",
        "Max bytes kept before context governance.",
    ),
    V(
        "ALFRED_CONTEXT_HEAD_CHARS",
        "int",
        None,
        "batteries",
        "Head characters preserved by the context governor.",
    ),
    V(
        "ALFRED_CONTEXT_TAIL_CHARS",
        "int",
        None,
        "batteries",
        "Tail characters preserved by the context governor.",
    ),
    V(
        "ALFRED_READ_DELTA",
        "bool",
        "0",
        "batteries",
        "Enable read-delta (re-read only changed regions).",
    ),
    V(
        "ALFRED_READ_DELTA_CONTEXT",
        "int",
        None,
        "batteries",
        "Context lines around a read-delta change.",
    ),
    V(
        "ALFRED_READ_DELTA_MAX_CHARS",
        "int",
        None,
        "batteries",
        "Max characters emitted per read-delta.",
    ),
    V(
        "ALFRED_READ_DELTA_MAX_RATIO",
        "float",
        None,
        "batteries",
        "Max diff-to-file ratio before read-delta falls back to a full read.",
    ),
    V(
        "ALFRED_OUTPUT_COMPACTOR",
        "bool",
        "1",
        "batteries",
        "Compact large tool output; opt out with 0.",
    ),
    V(
        "ALFRED_OUTPUT_COMPACTOR_MIN_BYTES",
        "int",
        None,
        "batteries",
        "Minimum output size before compaction kicks in.",
    ),
    V(
        "ALFRED_OUTPUT_COMPACTOR_MAX_BYTES",
        "int",
        None,
        "batteries",
        "Maximum compacted output size.",
    ),
    V(
        "ALFRED_OUTPUT_COMPACTOR_HEAD_LINES",
        "int",
        None,
        "batteries",
        "Head lines kept when compacting tool output.",
    ),
    V(
        "ALFRED_OUTPUT_COMPACTOR_TAIL_LINES",
        "int",
        None,
        "batteries",
        "Tail lines kept when compacting tool output.",
    ),
    V(
        "ALFRED_OUTPUT_COMPACTOR_TOOLS",
        "list",
        None,
        "batteries",
        "Comma-separated tools the output compactor applies to.",
    ),
    V(
        "ALFRED_TOOL_DIGEST",
        "bool",
        "1",
        "batteries",
        "Digest verbose tool schemas; opt out with 0.",
    ),
    V(
        "ALFRED_TOOL_DIGEST_MIN_CHARS",
        "int",
        None,
        "batteries",
        "Below this size, tool output passes through un-digested.",
    ),
    V(
        "ALFRED_SKILLS_INJECT",
        "bool",
        "1",
        "batteries",
        "Inject skill headers into the prompt; on by default.",
    ),
    V("ALFRED_SKILLS_DIR", "path", None, "batteries", "Directory holding skill definitions."),
    V(
        "ALFRED_SKELETON_PRIMING",
        "bool",
        "0",
        "batteries",
        "Prime the model with a repo skeleton; off by default.",
    ),
    V(
        "ALFRED_SKELETON_MAX_FILES",
        "int",
        None,
        "batteries",
        "Max files included in the skeleton priming pass.",
    ),
    V(
        "ALFRED_SKELETON_MAX_SIGNATURE_LINES",
        "int",
        None,
        "batteries",
        "Max signature lines per file in skeleton priming.",
    ),
    V(
        "ALFRED_REPO_PROFILE",
        "bool",
        "0",
        "batteries",
        "Emit a deterministic repo profile into context; off by default.",
    ),
    V(
        "ALFRED_REPO_PROFILE_MAX_CHARS",
        "int",
        None,
        "batteries",
        "Character budget for the repo profile.",
    ),
    V(
        "ALFRED_GOAL_WIRING",
        "bool",
        "0",
        "batteries",
        "Wire active-goal context into firings (opt-in).",
    ),
    # ---- compression: engine, condenser, headroom ----
    V(
        "ALFRED_COMPRESSION_ENGINE",
        "enum",
        None,
        "compression",
        "Selects the context-compression engine (e.g. headroom).",
        operator=True,
        choices=("headroom",),
    ),
    V("ALFRED_CONDENSER_ENABLED", "bool", "0", "compression", "Enable the conversation condenser."),
    V(
        "ALFRED_CONDENSER_MODEL",
        "str",
        None,
        "compression",
        "Model used to summarise when condensing (keep cheap).",
    ),
    V(
        "ALFRED_CONDENSER_KEEP_FIRST",
        "int",
        None,
        "compression",
        "Number of leading turns the condenser keeps verbatim.",
    ),
    V(
        "ALFRED_CONDENSER_KEEP_LAST",
        "int",
        None,
        "compression",
        "Number of trailing turns the condenser keeps verbatim.",
    ),
    V(
        "ALFRED_CONDENSER_TRIGGER_TURNS",
        "int",
        None,
        "compression",
        "Turn count that triggers condensation.",
    ),
    V(
        "ALFRED_CONDENSER_TRIGGER_CHARS",
        "int",
        None,
        "compression",
        "Character count that triggers condensation.",
    ),
    V(
        "ALFRED_CONDENSER_MAX_SUMMARY_CHARS",
        "int",
        None,
        "compression",
        "Max characters in a condenser summary.",
    ),
    V("ALFRED_HEADROOM_BIN", "path", None, "compression", "Path override for the headroom binary."),
    V("ALFRED_HEADROOM_MODEL", "str", None, "compression", "Model headroom uses for compression."),
    V(
        "ALFRED_HEADROOM_AUTOFETCH",
        "bool",
        "0",
        "compression",
        "Auto-fetch headroom compression before a firing.",
    ),
    V(
        "ALFRED_HEADROOM_AUTOFETCH_CMD",
        "str",
        None,
        "compression",
        "Command (shlex-split) headroom runs to auto-fetch.",
    ),
    V(
        "ALFRED_HEADROOM_COMPRESS_CMD",
        "str",
        None,
        "compression",
        "Command headroom runs to compress the transcript.",
    ),
    V(
        "ALFRED_HEADROOM_MESSAGE_ROLE",
        "str",
        None,
        "compression",
        "Explicit message role headroom assigns to compressed context.",
    ),
    # ---- slack: transport, converse, listener, bridge, trust ----
    V(
        "SLACK_WEBHOOK_URL",
        "secret",
        None,
        "slack",
        "Direct Slack webhook URL (simplest transport).",
        operator=True,
    ),
    V(
        "SLACK_WEBHOOK_SECRET_ID",
        "str",
        "alfred/slack-webhook",
        "slack",
        "AWS Secrets Manager id for the Slack webhook.",
        operator=True,
    ),
    V(
        "SLACK_WEBHOOK_SECRET_REGION",
        "str",
        "us-east-1",
        "slack",
        "AWS region for the Slack webhook secret.",
        operator=True,
    ),
    V(
        "SLACK_BOT_TOKEN",
        "secret",
        None,
        "slack",
        "Slack bot token for Block Kit / threaded posts.",
        operator=True,
    ),
    V(
        "SLACK_BOT_TOKEN_SECRET_ID",
        "str",
        "alfred/slack-bot-token",
        "slack",
        "AWS Secrets Manager id for the Slack bot token.",
        operator=True,
    ),
    V(
        "SLACK_BOT_TOKEN_SECRET_REGION",
        "str",
        None,
        "slack",
        "AWS region for the Slack bot-token secret.",
        operator=True,
    ),
    V(
        "SLACK_HOME_CHANNEL",
        "str",
        "alfred",
        "slack",
        "Default Slack channel for fleet posts.",
        operator=True,
    ),
    V(
        "SLACK_MIN_HOURS",
        "int",
        None,
        "slack",
        "Minimum hours between certain Slack notifications.",
    ),
    V(
        "ALFRED_SLACK_APP_TOKEN",
        "secret",
        None,
        "slack",
        "Slack app-level token for Socket Mode.",
        operator=True,
    ),
    V(
        "ALFRED_SLACK_BOT_USER_ID",
        "str",
        None,
        "slack",
        "Bot user id used to detect self-mentions.",
    ),
    V(
        "ALFRED_SLACK_BOT_TOKEN_SECRET_ID",
        "str",
        None,
        "slack",
        "Alfred-scoped Secrets Manager id for the bot token.",
    ),
    V(
        "ALFRED_SLACK_BOT_TOKEN_SECRET_REGION",
        "str",
        None,
        "slack",
        "Alfred-scoped region for the bot-token secret.",
    ),
    V(
        "ALFRED_SLACK_BOT_TOKEN_CACHE",
        "path",
        None,
        "slack",
        "Path to the on-disk Slack bot-token cache.",
    ),
    V(
        "ALFRED_SLACK_NATIVE_SENDS",
        "bool",
        "0",
        "slack",
        "Prefer native Slack API sends over webhooks.",
    ),
    V(
        "ALFRED_SLACK_CONVERSE_ENABLED",
        "bool",
        "0",
        "slack",
        "Enable the Slack converse (chat) surface.",
        operator=True,
    ),
    V(
        "ALFRED_SLACK_CONVERSE_ENGINE",
        "enum",
        None,
        "slack",
        "Engine backing Slack converse (claude/codex/hybrid).",
        operator=True,
        choices=("claude", "codex", "hybrid"),
    ),
    V(
        "ALFRED_SLACK_CONVERSE_CHANNELS",
        "list",
        None,
        "slack",
        "Comma-separated channels where converse is active.",
        operator=True,
    ),
    V(
        "ALFRED_SLACK_CONVERSE_TIMEOUT",
        "int",
        None,
        "slack",
        "Timeout in seconds for a converse turn.",
    ),
    V(
        "ALFRED_SLACK_CONVERSE_THREAD_CONTEXT",
        "int",
        None,
        "slack",
        "How much thread context converse includes.",
    ),
    V(
        "ALFRED_SLACK_CONVERSE_STREAM_THROTTLE",
        "float",
        None,
        "slack",
        "Throttle in seconds between streamed converse updates.",
    ),
    V(
        "ALFRED_SLACK_AMBIENT",
        "bool",
        "0",
        "slack",
        "Enable ambient (unmentioned) Slack engagement; off by default.",
    ),
    V(
        "ALFRED_SLACK_AMBIENT_CHANNELS",
        "list",
        None,
        "slack",
        "Comma-separated channels where ambient engagement is allowed.",
    ),
    V(
        "ALFRED_SLACK_MEMORY_CANDIDATES",
        "bool",
        "1",
        "slack",
        "Surface Slack-derived memory candidates; disable to turn off.",
    ),
    V(
        "ALFRED_SLACK_RUN_CODENAMES",
        "list",
        None,
        "slack",
        "Comma-separated agent codenames runnable from Slack.",
    ),
    V(
        "ALFRED_SLACK_MAX_TOTAL_BACKOFF_SECONDS",
        "float",
        None,
        "slack",
        "Cap on total Slack post backoff in seconds.",
    ),
    V(
        "ALFRED_SLACK_RECONNECT_BASE_BACKOFF_S",
        "float",
        "1.0",
        "slack",
        "Base backoff for Slack listener reconnects.",
    ),
    V(
        "ALFRED_SLACK_RECONNECT_MAX_BACKOFF_S",
        "float",
        "30.0",
        "slack",
        "Max backoff for Slack listener reconnects.",
    ),
    V(
        "ALFRED_SLACK_RECONNECT_CHECK_INTERVAL_S",
        "float",
        "15.0",
        "slack",
        "Interval between Slack listener reconnect checks.",
    ),
    V(
        "ALFRED_SLACK_THREAD_SYNC_INTERVAL_S",
        "float",
        None,
        "slack",
        "Interval in seconds between Slack thread-status syncs.",
    ),
    V(
        "ALFRED_OPERATOR_SLACK_USER_ID",
        "str",
        None,
        "slack",
        "Slack user id of the operator (naming + trust).",
        operator=True,
    ),
    V(
        "ALFRED_TRUSTED_SLACK_USER_IDS",
        "list",
        None,
        "slack",
        "Comma-separated Slack user ids treated as trusted collaborators.",
        operator=True,
    ),
    V(
        "ALFRED_INTENT_ROUTER_ENABLED",
        "bool",
        "0",
        "slack",
        "Enable the LLM intent router for ambient messages.",
    ),
    V("ALFRED_INTENT_ROUTER_ENGINE", "str", None, "slack", "Engine used by the intent router."),
    V(
        "ALFRED_INTENT_ROUTER_MIN_CONFIDENCE",
        "float",
        None,
        "slack",
        "Minimum confidence before the intent router acts.",
    ),
    V(
        "ALFRED_INTENT_ROUTER_TIMEOUT",
        "int",
        None,
        "slack",
        "Timeout in seconds for the intent router.",
    ),
    V(
        "ALFRED_CONVERSE_OPERATIONAL_GROUNDING",
        "bool",
        "0",
        "slack",
        "Ground converse replies in live operational state.",
    ),
    V(
        "ALFRED_CONVERSE_POLL_SECONDS",
        "float",
        "0.04",
        "slack",
        "Poll interval for the converse loop.",
    ),
    V(
        "ALFRED_COMPOSE_CONVERSE_ENGINE",
        "str",
        None,
        "slack",
        "Engine override for composed converse replies.",
    ),
    V(
        "ALFRED_PLAN_THREAD_ANSWER_ENGINE",
        "str",
        None,
        "slack",
        "Engine used to answer plan-thread questions.",
    ),
    V(
        "ALFRED_PLAN_THREAD_ANSWER_TIMEOUT",
        "int",
        None,
        "slack",
        "Timeout in seconds for plan-thread answers.",
    ),
    # Slack -> issue bridge
    V(
        "ALFRED_BRIDGE_ENABLED",
        "bool",
        "0",
        "slack",
        "Enable the Slack-to-issue bridge.",
        operator=True,
    ),
    V(
        "ALFRED_BRIDGE_LABEL",
        "str",
        None,
        "slack",
        "Pickup label applied to issues created by the bridge.",
    ),
    V(
        "ALFRED_BRIDGE_REPOS",
        "list",
        None,
        "slack",
        "Comma-separated repos the bridge may open issues in.",
    ),
    V(
        "ALFRED_BRIDGE_MIN_READINESS_SCORE",
        "float",
        None,
        "slack",
        "Minimum readiness score before the bridge creates an issue.",
    ),
    V(
        "ALFRED_BRIDGE_APPROVAL_PHRASES",
        "list",
        None,
        "slack",
        "Comma/semicolon separated phrases that approve a bridge issue.",
    ),
    # ---- server: serve, status cache, sse ----
    V("ALFRED_SERVE_UI_DIST", "path", None, "server", "Directory of the built serve UI to host."),
    V(
        "ALFRED_STATUS_AUTH_TTL_SECONDS",
        "int",
        "60",
        "server",
        "TTL in seconds for the authenticated status cache.",
    ),
    V(
        "ALFRED_STATUS_SLOW_TTL_SECONDS",
        "int",
        "1800",
        "server",
        "TTL in seconds for the slow (heavy) status cache.",
    ),
    V(
        "ALFRED_SSE_HEARTBEAT_SECONDS",
        "float",
        "15.0",
        "server",
        "Heartbeat interval for server-sent-event streams.",
    ),
    # ---- telemetry: proof-of-work telemetry ingest/report ----
    V(
        "ALFRED_TELEMETRY_ENABLED",
        "bool",
        "1",
        "telemetry",
        "Emit proof telemetry; opt out with 0.",
        operator=True,
    ),
    V(
        "ALFRED_TELEMETRY_URL",
        "str",
        None,
        "telemetry",
        "Override the telemetry ingest URL.",
        operator=True,
    ),
    V(
        "ALFRED_DEFAULT_TELEMETRY_URL",
        "str",
        None,
        "telemetry",
        "Hosted default telemetry URL; set empty to disable the default.",
    ),
    V(
        "ALFRED_TELEMETRY_TOKEN",
        "secret",
        None,
        "telemetry",
        "Optional shared ingest token for hosted telemetry.",
    ),
    V(
        "ALFRED_TELEMETRY_TRUSTED_TOKEN",
        "secret",
        None,
        "telemetry",
        "Server-trust token proving a telemetry payload is first-party.",
    ),
    # ---- scheduler: launchd/systemd, cleanup, disk guardian, backup ----
    V(
        "ALFRED_LAUNCH_DIR",
        "path",
        "~/Library/LaunchAgents",
        "scheduler",
        "Directory launchd plists are installed into.",
    ),
    V(
        "ALFRED_LAUNCHD_LABEL_PREFIX",
        "str",
        "alfred",
        "scheduler",
        "Reverse-DNS label prefix for launchd plists.",
    ),
    V(
        "ALFRED_SYSTEMD_USER_DIR",
        "path",
        "~/.config/systemd/user",
        "scheduler",
        "Directory systemd --user units are installed into.",
    ),
    V(
        "ALFRED_MIN_FREE_DISK_GB",
        "float",
        "3.0",
        "scheduler",
        "Absolute free-disk floor in GB before firings back off.",
        operator=True,
    ),
    V(
        "ALFRED_MIN_FREE_DISK_PCT",
        "float",
        "5.0",
        "scheduler",
        "Relative free-disk floor in percent before firings back off.",
        operator=True,
    ),
    V(
        "ALFRED_DISK_SLACK_MIN_HOURS",
        "int",
        "6",
        "scheduler",
        "Throttle window in hours for the disk-low Slack warning.",
        operator=True,
    ),
    V(
        "ALFRED_CLEANUP_AUTODISCOVER",
        "bool",
        "1",
        "scheduler",
        "Auto-discover .worktrees pools to sweep; opt out with 0.",
        operator=True,
    ),
    V(
        "ALFRED_CLEANUP_SCHEDULED_RECLAIM",
        "bool",
        "0",
        "scheduler",
        "Run dev-cache/Docker reclaim on the daily pass.",
        operator=True,
    ),
    V(
        "ALFRED_CLEANUP_EXTRA_PATHS",
        "list",
        None,
        "scheduler",
        "Extra worktree-pool paths for cleanup to sweep.",
    ),
    V(
        "ALFRED_CLEANUP_MAX_AGE_HOURS",
        "int",
        "48",
        "scheduler",
        "Age threshold in hours for normal cleanup.",
    ),
    V(
        "ALFRED_CLEANUP_EMERGENCY_MAX_AGE_HOURS",
        "int",
        "1",
        "scheduler",
        "Age threshold in hours for emergency cleanup.",
    ),
    V(
        "ALFRED_CLEANUP_TMP_PREFIXES",
        "list",
        None,
        "scheduler",
        "Temp-dir prefixes cleanup is allowed to remove.",
    ),
    V(
        "ALFRED_EMERGENCY_SKIP_DEV_CACHES",
        "bool",
        "0",
        "scheduler",
        "Skip dev-cache reclaim during emergency cleanup.",
    ),
    V(
        "ALFRED_EMERGENCY_SKIP_DOCKER",
        "bool",
        "0",
        "scheduler",
        "Skip Docker reclaim during emergency cleanup.",
    ),
    V(
        "ALFRED_EMERGENCY_EVENTS_RETENTION_DAYS",
        "int",
        "3",
        "scheduler",
        "Events retention in days under emergency cleanup.",
    ),
    V(
        "ALFRED_EMERGENCY_TRANSCRIPT_RETENTION_DAYS",
        "int",
        "3",
        "scheduler",
        "Transcript retention in days under emergency cleanup.",
    ),
    V(
        "ALFRED_EVENTS_RETENTION_DAYS",
        "int",
        "30",
        "scheduler",
        "Events retention in days for normal cleanup.",
    ),
    V(
        "ALFRED_TRANSCRIPT_RETENTION_DAYS",
        "int",
        "30",
        "scheduler",
        "Transcript retention in days for normal cleanup.",
    ),
    V("ALFRED_SPEND_RETENTION_DAYS", "int", "90", "scheduler", "Spend-ledger retention in days."),
    V(
        "ALFRED_PREFLIGHT_FORCE_SLACK",
        "bool",
        "0",
        "scheduler",
        "Force the Slack preflight even when recently checked.",
    ),
    V(
        "ALFRED_PREFLIGHT_SLACK_MIN_MINUTES",
        "int",
        "60",
        "scheduler",
        "Minimum minutes between Slack preflight checks.",
    ),
    V(
        "ALFRED_PRE_PUSH_TIMEOUT_S",
        "int",
        "900",
        "scheduler",
        "Timeout in seconds for the pre-push hook.",
    ),
    V(
        "ALFRED_HOOK_REPOS",
        "list",
        None,
        "scheduler",
        "Space-separated repo dirs to install the pre-push hook into.",
    ),
    V(
        "ALFRED_HOOK_SOURCE",
        "path",
        None,
        "scheduler",
        "Path to the canonical pre-push hook source.",
    ),
    V(
        "ALFRED_DEPENDENCY_WARNING_TTL_S",
        "int",
        "21600",
        "scheduler",
        "TTL in seconds for the missing-dependency warning.",
    ),
    # Cold backup
    V(
        "ALFRED_BACKUP_DEST",
        "str",
        None,
        "scheduler",
        "s3://bucket/prefix upload target for cold backups.",
    ),
    V(
        "ALFRED_BACKUP_AWS_PROFILE",
        "str",
        None,
        "scheduler",
        "AWS profile for cold-backup uploads.",
    ),
    V("ALFRED_BACKUP_KEEP", "int", None, "scheduler", "Number of cold backups to retain."),
    V(
        "ALFRED_BACKUP_LOCAL_ONLY",
        "bool",
        "0",
        "scheduler",
        "Keep the cold backup local, skipping upload.",
    ),
    V(
        "ALFRED_BACKUP_OUTPUT_DIR",
        "path",
        None,
        "scheduler",
        "Local directory for cold-backup output.",
    ),
    V(
        "ALFRED_BACKUP_PRUNE",
        "bool",
        "1",
        "scheduler",
        "Prune old cold backups after a successful run.",
    ),
    V(
        "ALFRED_BACKUP_STAMP",
        "str",
        None,
        "scheduler",
        "Explicit timestamp stamp for a cold backup.",
    ),
    V(
        "ALFRED_BACKUP_CONFIRM_CMD",
        "str",
        None,
        "scheduler",
        "Command run to confirm a cold backup completed.",
    ),
    # ---- agents: per-agent caps, repos, engines, timeouts ----
    V(
        "ALFRED_MORNING_BRIEF_AGENTS",
        "list",
        None,
        "agents",
        "Comma-separated agents included in the morning brief.",
    ),
    V(
        "ALFRED_MORNING_BRIEF_REPOS",
        "list",
        None,
        "agents",
        "Comma-separated repos included in the morning brief.",
    ),
    V("ALFRED_REVIEWER_REPOS", "list", None, "agents", "Repos the reviewer agent covers."),
    V(
        "ALFRED_REVIEWER_SPECS_REPOS",
        "list",
        None,
        "agents",
        "Repos the reviewer treats as spec repos.",
    ),
    V("ALFRED_REVIEWER_DIFF_CAP", "int", "4000", "agents", "Diff-line cap for a standard review."),
    V(
        "ALFRED_REVIEWER_DIFF_CAP_SPECS",
        "int",
        "8000",
        "agents",
        "Diff-line cap for a spec review.",
    ),
    V("ALFRED_REVIEWER_REVIEW_CAP", "int", "30", "agents", "Daily cap on reviews performed."),
    V("ALFRED_REVIEWER_TURN_CAP", "int", "800", "agents", "Daily turn cap for the reviewer."),
    V(
        "ALFRED_REVIEWER_MAX_TURNS",
        "int",
        None,
        "agents",
        "Per-firing max turns for the reviewer (min 40).",
    ),
    V("ALFRED_REVIEWER_TIMEOUT", "int", "900", "agents", "Per-review timeout in seconds (min 60)."),
    V(
        "ALFRED_REVIEWER_FALLBACK_TIMEOUT",
        "int",
        "1800",
        "agents",
        "Fallback review timeout in seconds (min 60).",
    ),
    V("ALFRED_FIXER_REPOS", "list", None, "agents", "Repos the fixer agent covers."),
    V(
        "ALFRED_FIXER_REVIEW_AGENT",
        "str",
        "reviewer",
        "agents",
        "Codename of the review agent the fixer re-triggers.",
    ),
    V(
        "ALFRED_FIXER_ESCALATE_AFTER",
        "int",
        "3",
        "agents",
        "No-commit attempts before the fixer escalates.",
    ),
    V("ALFRED_FIXER_TURN_CAP", "int", "600", "agents", "Daily turn cap for the fixer."),
    V(
        "ALFRED_FIXER_MAX_TURNS",
        "int",
        None,
        "agents",
        "Per-firing max turns for the fixer (min 25).",
    ),
    V("ALFRED_PLANNER_REPOS", "list", None, "agents", "Repos the planner agent covers."),
    V(
        "ALFRED_PLANNER_DAILY_ISSUE_CAP",
        "int",
        None,
        "agents",
        "Daily cap on issues the planner may open.",
    ),
    V(
        "ALFRED_PLANNER_MAX_TURNS",
        "int",
        None,
        "agents",
        "Per-firing max turns for the planner (min 40).",
    ),
    V("ALFRED_TRIAGE_REPOS", "list", None, "agents", "Repos the triage agent covers."),
    V("ALFRED_TRIAGE_DAILY_CAP", "int", "50", "agents", "Daily cap on triage actions."),
    V("ALFRED_TRIAGE_TURN_CAP", "int", "600", "agents", "Daily turn cap for triage."),
    V(
        "ALFRED_TRIAGE_MAX_TURNS",
        "int",
        None,
        "agents",
        "Per-firing max turns for triage (min 20).",
    ),
    V(
        "ALFRED_TRIAGE_TOUCHED_TTL_DAYS",
        "int",
        "7",
        "agents",
        "Days a triaged item is remembered as touched.",
    ),
    V("ALFRED_SENIOR_DEV_REPOS", "list", None, "agents", "Repos the senior-dev agent covers."),
    V("ALFRED_SENIOR_DEV_TURN_CAP", "int", "5000", "agents", "Daily turn cap for senior-dev."),
    V(
        "ALFRED_SENIOR_DEV_MAX_TURNS",
        "int",
        None,
        "agents",
        "Per-firing max turns for senior-dev (min 40).",
    ),
    V(
        "ALFRED_SENIOR_DEV_SELFASSESS_MAX_TURNS",
        "int",
        None,
        "agents",
        "Max turns for the senior-dev self-assessment pass (min 1).",
    ),
    V(
        "ALFRED_TEST_ENGINEER_REPOS",
        "list",
        None,
        "agents",
        "Repos the test-engineer agent covers.",
    ),
    V(
        "ALFRED_TEST_ENGINEER_MAX_TURNS",
        "int",
        None,
        "agents",
        "Per-firing max turns for the test-engineer (min 40).",
    ),
    V("ALFRED_AUTOMERGE_REPOS", "list", None, "agents", "Repos eligible for auto-merge."),
    V(
        "ALFRED_AUTOMERGE_FIX_AGENT",
        "str",
        "fixer",
        "agents",
        "Codename of the fix agent auto-merge escalates to.",
    ),
    V(
        "ALFRED_AUTOMERGE_REVIEW_AGENT",
        "str",
        "reviewer",
        "agents",
        "Codename of the review agent auto-merge waits on.",
    ),
    V(
        "ALFRED_AUTOMERGE_MIN_AGE_MIN",
        "int",
        "30",
        "agents",
        "Minimum PR age in minutes before auto-merge.",
    ),
    V(
        "ALFRED_CURATOR_MAX_ITEMS",
        "int",
        "8",
        "agents",
        "Cap on findings the curator shows in Slack.",
    ),
    V("ALFRED_CLAIM_SWEEP_REPOS", "list", None, "agents", "Repos the claim sweep runs over."),
    V(
        "ALFRED_CLAIM_MAX_AGE_HOURS",
        "int",
        "4",
        "agents",
        "Max age in hours before a stale claim is swept.",
    ),
    V(
        "ALFRED_QUEUE_REPOS",
        "list",
        None,
        "agents",
        "Assignment repo allowlist for issue queueing.",
    ),
    V("ALFRED_GITHUB_POLL_REPOS", "list", None, "agents", "Repos the GitHub poller watches."),
    V(
        "ALFRED_IN_PROGRESS_REQUIRE_AGENT_EVIDENCE",
        "bool",
        None,
        "agents",
        "Require agent evidence before marking an issue in-progress.",
    ),
    V(
        "ALFRED_ARCHITECT_APPROVAL_MAX_AGE_HOURS",
        "int",
        "24",
        "agents",
        "Max age in hours for an architect approval to stay valid.",
    ),
    # Spec planner
    V("ALFRED_SPEC_PLANNER_REPOS", "list", None, "agents", "Repos the spec planner covers."),
    V(
        "ALFRED_SPEC_PLANNER_SPEC_DIR",
        "path",
        None,
        "agents",
        "Directory holding specs for the spec planner.",
    ),
    V(
        "ALFRED_SPEC_PLANNER_DAILY_BUNDLE_CAP",
        "int",
        None,
        "agents",
        "Daily cap on bundles the spec planner emits.",
    ),
    V(
        "ALFRED_SPEC_INTERROGATOR_PROMPT",
        "path",
        None,
        "agents",
        "Prompt override for the spec interrogator.",
    ),
    # Self-proof
    V("ALFRED_SELF_PROOF_REPOS", "list", None, "agents", "Repos the self-proof pass covers."),
    V("ALFRED_SELF_PROOF_SELF_REPO", "str", None, "agents", "Self repo slug for self-proof."),
    V(
        "ALFRED_SELF_PROOF_EXCLUDED_AUTHORS",
        "list",
        None,
        "agents",
        "Authors excluded from self-proof attribution.",
    ),
    # Shipped board / summary
    V("ALFRED_SHIPPED_REPOS", "list", None, "agents", "Repos the shipped board tracks."),
    V(
        "ALFRED_SHIPPED_SUMMARY_REPOS",
        "list",
        None,
        "agents",
        "Shared fallback repos for shipped summaries.",
        operator=True,
    ),
    V(
        "ALFRED_SHIPPED_SUMMARY_DAILY_REPOS",
        "list",
        None,
        "agents",
        "Repos for the daily shipped summary.",
    ),
    V(
        "ALFRED_SHIPPED_SUMMARY_WEEKLY_REPOS",
        "list",
        None,
        "agents",
        "Repos for the weekly shipped summary.",
    ),
    V(
        "ALFRED_SHIPPED_SUMMARY_AGENT_LABELS",
        "list",
        None,
        "agents",
        "Labels marking agent-shipped work in summaries.",
    ),
    V(
        "ALFRED_SHIPPED_SUMMARY_QUERY_LIMIT",
        "int",
        None,
        "agents",
        "Query limit for shipped-summary lookups.",
    ),
    V(
        "ALFRED_SHIPPED_AGENT_AUTHORS",
        "list",
        None,
        "agents",
        "Authors counted as agents on the shipped board.",
    ),
    V("ALFRED_SHIPPED_AGENT_LABELS", "list", None, "agents", "Labels marking agent-shipped PRs."),
    V(
        "ALFRED_SHIPPED_AGENT_BRANCH_PREFIXES",
        "list",
        None,
        "agents",
        "Branch prefixes marking agent-shipped work.",
    ),
    V(
        "ALFRED_SHIPPED_QUEUE_INCLUDE_LABELS",
        "list",
        None,
        "agents",
        "Labels a shipped item must have to be included (* for all).",
    ),
    V(
        "ALFRED_SHIPPED_QUEUE_EXCLUDE_LABELS",
        "list",
        None,
        "agents",
        "Labels that exclude an item from the shipped queue.",
    ),
    # Auto-promote (memory)
    V(
        "ALFRED_AUTO_PROMOTE",
        "bool",
        "1",
        "agents",
        "Enable memory auto-promotion; 0 disables save/skip decisions.",
    ),
    V(
        "ALFRED_AUTO_PROMOTE_KILL",
        "bool",
        "0",
        "agents",
        "Kill switch that fails auto-promotion closed.",
    ),
    V(
        "ALFRED_AUTO_PROMOTE_LLM_JUDGE",
        "bool",
        "1",
        "agents",
        "Use the LLM judge for auto-promotion; falsy disables it.",
    ),
    V(
        "ALFRED_AUTO_PROMOTE_JUDGE_TIMEOUT",
        "int",
        "120",
        "agents",
        "Timeout in seconds for the auto-promote judge call.",
    ),
    V(
        "ALFRED_AUTO_PROMOTE_THRESHOLD",
        "float",
        None,
        "agents",
        "Score threshold for auto-promotion.",
    ),
    V(
        "ALFRED_AUTO_PROMOTE_NO_JUDGE_THRESHOLD",
        "float",
        None,
        "agents",
        "Score threshold used when the judge is disabled.",
    ),
    V(
        "ALFRED_AUTO_PROMOTE_MAX_PER_RUN",
        "int",
        None,
        "agents",
        "Max memories auto-promoted per run.",
    ),
    V(
        "ALFRED_AUTO_PROMOTE_MAX_JUDGE_CALLS",
        "int",
        None,
        "agents",
        "Max judge calls per auto-promote run.",
    ),
    # Rubric grader
    V(
        "ALFRED_RUBRIC",
        "str",
        None,
        "agents",
        "Inline rubric or rubric name enabling graded output.",
    ),
    V("ALFRED_RUBRIC_GRADER_ENGINE", "str", None, "agents", "Engine used by the rubric grader."),
    V(
        "ALFRED_RUBRIC_MAX_ITERATIONS",
        "int",
        "3",
        "agents",
        "Max rubric improvement iterations (1..10).",
    ),
    # Planning assistant
    V(
        "ALFRED_PLANNING_ASSISTANT_ENGINE",
        "str",
        None,
        "agents",
        "Fallback engine for the planning assistant.",
    ),
    V(
        "ALFRED_PLANNING_ASSISTANT_TIMEOUT",
        "int",
        "180",
        "agents",
        "Timeout in seconds for the planning assistant.",
    ),
    # Issue summary
    V("ALFRED_ISSUE_SUMMARY_ENABLED", "bool", "0", "agents", "Enable LLM issue summaries."),
    V("ALFRED_ISSUE_SUMMARY_ENGINE", "str", None, "agents", "Engine used for issue summaries."),
    V(
        "ALFRED_ISSUE_SUMMARY_MAX_CHARS",
        "int",
        None,
        "agents",
        "Character cap for an issue summary (~360).",
    ),
    V(
        "ALFRED_ISSUE_SUMMARY_TIMEOUT",
        "int",
        None,
        "agents",
        "Timeout in seconds for an issue summary.",
    ),
    V("ALFRED_PR_EVIDENCE", "bool", "1", "agents", "Attach test/PR evidence; on by default."),
    # ---- ops: e2e runner, ops-watch, nightly, scrub ----
    V("ALFRED_E2E_RUNNER_TARGET_URL", "str", None, "ops", "Target URL the E2E runner exercises."),
    V("ALFRED_E2E_RUNNER_TESTS_DIR", "path", None, "ops", "Directory of E2E tests to run."),
    V(
        "ALFRED_E2E_RUNNER_SECRET_ID",
        "str",
        "alfred/huntress/test-account",
        "ops",
        "Secrets Manager id for the E2E test account.",
    ),
    V("ALFRED_E2E_RUNNER_AWS_PROFILE", "str", None, "ops", "AWS profile for E2E runner AWS calls."),
    V("ALFRED_E2E_RUNNER_S3_BUCKET", "str", None, "ops", "S3 bucket receiving E2E artifacts."),
    V("ALFRED_E2E_RUNNER_ECS_CLUSTER", "str", None, "ops", "ECS cluster the E2E runner targets."),
    V(
        "ALFRED_E2E_RUNNER_ECS_SERVICES",
        "list",
        None,
        "ops",
        "Comma-separated ECS services for the E2E runner.",
    ),
    V(
        "ALFRED_E2E_RUNNER_DEPLOY_REF_REPO",
        "str",
        None,
        "ops",
        "Repo whose deploy ref the E2E runner checks.",
    ),
    V("ALFRED_OPS_WATCH_AWS_PROFILE", "str", None, "ops", "AWS profile for ops-watch."),
    V(
        "ALFRED_OPS_WATCH_ECS_CLUSTER",
        "str",
        None,
        "ops",
        "Staging ECS cluster ops-watch monitors.",
    ),
    V(
        "ALFRED_OPS_WATCH_SERVICES",
        "list",
        None,
        "ops",
        "Comma-separated service=repo:branch entries for ops-watch.",
    ),
    V("ALFRED_OPS_WATCH_SENTRY_ORG", "str", None, "ops", "Sentry org ops-watch queries."),
    V(
        "ALFRED_OPS_WATCH_SENTRY_SECRET_ID",
        "str",
        "alfred/sentry-api-token",
        "ops",
        "Secrets Manager id for the Sentry API token.",
    ),
    V("ALFRED_NIGHTLY_NPM_REPOS", "list", None, "ops", "Semicolon-separated npm nightly entries."),
    V(
        "ALFRED_NIGHTLY_ADVISORY_REPOS",
        "list",
        None,
        "ops",
        "Semicolon-separated advisory nightly entries.",
    ),
    V("ALFRED_SCRUB_NAMES", "list", None, "ops", "Comma-separated names the scrubber redacts."),
    V("ALFRED_SCRUB_NAMES_FILE", "path", None, "ops", "Path to a file of names for the scrubber."),
    V("ALFRED_SCRUB_EXTRA_PATTERNS", "path", None, "ops", "Path to extra scrub patterns."),
    V("ALFRED_SLOP_RULES", "path", None, "ops", "Path to slop-detector rules."),
    V("ALFRED_SLOP_TARGET_PATH", "path", ".", "ops", "Default target path for the slop detector."),
    V(
        "ALFRED_FLEET_BRAIN_DB",
        "path",
        None,
        "ops",
        "Path to the local fleet-brain counts database.",
    ),
    V(
        "ALFRED_FLEET_OVERLAY",
        "str",
        "fleet_overlay",
        "ops",
        "Name of the private fleet overlay module (silently absent by default).",
    ),
    # ---- onboarding / theming ----
    V(
        "ALFRED_ONBOARDING_ENGINE",
        "str",
        None,
        "onboarding",
        "Engine override for the conversational onboarding.",
    ),
    V("ALFRED_ONBOARDING_PROMPT", "path", None, "onboarding", "Prompt override for onboarding."),
    V(
        "ALFRED_THEME_BUILDER_ENGINE",
        "str",
        None,
        "onboarding",
        "Engine override for the chat theme builder.",
    ),
    V(
        "ALFRED_THEME_BUILDER_PROMPT",
        "path",
        None,
        "onboarding",
        "Prompt override for the theme builder.",
    ),
    # ---- demo ----
    V("ALFRED_DEMO_MODEL", "str", None, "internal", "Force a specific model for the demo."),
    V("ALFRED_DEMO_FAST_MODEL", "str", "haiku", "internal", "Fast model used by the demo."),
    V("ALFRED_DEMO_VERBOSE", "bool", "0", "internal", "Verbose demo engine output."),
    # ---- brain / misc logging ----
    V(
        "ALFRED_BRAIN_LOG_LEVEL",
        "enum",
        "WARNING",
        "internal",
        "Log level for the brain process.",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    ),
    # ---- internal: process-set guards / sentinels ----
    V(
        "ALFRED_DISK_EMERGENCY_IN_PROGRESS",
        "bool",
        "0",
        "internal",
        "Reentrancy guard set while emergency disk cleanup runs.",
    ),
    # ---- non-ALFRED operator vars already shown in .env.example ----
    V(
        "GH_ORG",
        "str",
        None,
        "runtime",
        "GitHub org/user owning the fleet's product repos.",
        operator=True,
    ),
    V("GH_BIN", "path", None, "runtime", "Path to the gh CLI (fallback for ALFRED_GH_BIN)."),
    V(
        "OPERATOR_NAME",
        "str",
        None,
        "runtime",
        "Display name shown in agent prompts.",
        operator=True,
    ),
    V(
        "OPERATOR_EMAIL",
        "str",
        None,
        "runtime",
        "Operator email referenced in some prompts.",
        operator=True,
    ),
    V(
        "OPERATOR_GH_HANDLE",
        "str",
        None,
        "runtime",
        "Operator GitHub login when distinct from GH_ORG.",
        operator=True,
    ),
    V(
        "WORKSPACE_ROOT",
        "path",
        "~/code",
        "runtime",
        "Parent directory of per-repo product checkouts.",
        operator=True,
    ),
    V(
        "WORKSPACE_SUBDIR",
        "str",
        "product",
        "runtime",
        "Subdirectory under WORKSPACE_ROOT holding checkouts.",
        operator=True,
    ),
    V(
        "CLAUDE_BIN",
        "path",
        None,
        "runtime",
        "Absolute path to the claude CLI (defaults to PATH).",
        operator=True,
    ),
    V(
        "CLAUDE_CODE_OAUTH_TOKEN",
        "secret",
        None,
        "runtime",
        "Long-lived OAuth token so launchd/systemd can authenticate claude.",
        operator=True,
    ),
    V("LABEL_STATE_SWEEP_REPOS", "list", None, "agents", "Repos the label-state sweep runs over."),
    V("LABEL_STATE_SKIP_DEDUP_CHECK", "bool", "0", "agents", "Skip the label-state dedup check."),
)


# --------------------------------------------------------------------------
# Non-var tokens: strings that the discovery grep
# ``grep -rhoE "ALFRED_[A-Z0-9_]+" lib bin`` picks up but which are NOT
# standalone environment variables. Keeping them here (with a reason) lets the
# ratchet test stay an exact-match check: every discovered token must be either
# a registered var or listed here.
# --------------------------------------------------------------------------
NON_VAR_TOKENS: dict[str, str] = {
    # Dynamic-prefix families (the concrete key is built at runtime).
    "ALFRED_BENCHMARK_TURN_BUDGET_": "prefix for per-tier benchmark turn budgets",
    "ALFRED_CODE_MEMORY_SHA256_": "prefix for per-tag code-memory checksums",
    "ALFRED_SHIPPED_SUMMARY_": "infix for ALFRED_SHIPPED_SUMMARY_<PERIOD>_REPOS",
    # Doc/wildcard artifacts (appear only in comments/docstrings as *_*).
    "ALFRED_AMS_": "documentation wildcard 'ALFRED_AMS_*'",
    "ALFRED_CODE_MEMORY_": "documentation wildcard 'ALFRED_CODE_MEMORY_*'",
    "ALFRED_LLM_": "documentation wildcard 'ALFRED_LLM_*'",
    "ALFRED_SPEC_PLANNER_": "documentation wildcard 'ALFRED_SPEC_PLANNER_*'",
    "ALFRED_TELEMETRY_": "documentation wildcard 'ALFRED_TELEMETRY_*'",
    # Python/shell identifier fragments matched by the broad regex.
    "ALFRED_HOME_EXPLICIT": "python module variable _ALFRED_HOME_EXPLICIT",
    "ALFRED_HOME_PRESENT": "shell variable ORIGINAL_ALFRED_HOME_PRESENT",
    "ALFRED_INIT_MANAGED_ENV_KEYS": "python module constant, not an env read",
    "ALFRED_INIT_MANAGED_SCOPE_PATTERNS": "python module constant, not an env read",
    "ALFRED_INIT_BANNER_RE": "python module constant (compiled regex)",
    "ALFRED_ENV_BANNER": "python module constant (banner string)",
    "ALFRED_ENV_BANNER_RE": "python module constant (compiled regex)",
    "ALFRED_LIB": "derived lib path (shell/python var), not an operator env read",
    "ALFRED_INTRO": "python module constant (_ALFRED_INTRO greeting)",
    # Sentinel strings (agent reply markers / cache markers), not env vars.
    "ALFRED_CLAUDE_OK": "sentinel reply string in an engine liveness probe",
    "ALFRED_CODEX_OK": "sentinel reply string in an engine liveness probe",
    "ALFRED_DEP_LOOKUP_FAILED": "sentinel string __ALFRED_DEP_LOOKUP_FAILED__",
    "ALFRED_DEP_LOOKUP_FAILED_": "sentinel string fragment",
    "ALFRED_DEP_LOOKUP_FAILED__": "sentinel string __ALFRED_DEP_LOOKUP_FAILED__",
}


# Build lookup maps once at import.
REGISTRY: dict[str, ConfigVar] = {v.name: v for v in _VARS}


def all_vars() -> tuple[ConfigVar, ...]:
    """Every declared config var, in registry (declaration) order."""
    return _VARS


def get_var(name: str) -> ConfigVar | None:
    """Return the :class:`ConfigVar` for ``name`` or ``None`` if unknown."""
    return REGISTRY.get(name)


def operator_vars() -> tuple[ConfigVar, ...]:
    """Operator-facing vars (those emitted into ``.env.example``)."""
    return tuple(v for v in _VARS if v.operator)


def vars_by_category(category: str) -> tuple[ConfigVar, ...]:
    return tuple(v for v in _VARS if v.category == category)


def registered_names() -> frozenset[str]:
    """All names the ratchet treats as declared (vars + non-var tokens)."""
    return frozenset(REGISTRY) | frozenset(NON_VAR_TOKENS)


# --------------------------------------------------------------------------
# Typed accessors. These read os.environ at call time and fall back to the
# registered default, giving migrated call sites one honest default per var.
# --------------------------------------------------------------------------


def _raw(name: str, environ: dict[str, str] | None = None) -> str | None:
    env = environ if environ is not None else os.environ
    val = env.get(name)
    if val is not None:
        return val
    var = REGISTRY.get(name)
    return var.default if var is not None else None


def get_str(name: str, environ: dict[str, str] | None = None) -> str | None:
    """Return the string value (env override or registered default)."""
    return _raw(name, environ)


def get_bool(name: str, environ: dict[str, str] | None = None) -> bool:
    """Return a truthy-checked bool (``1/true/yes/on``), default-aware."""
    raw = _raw(name, environ)
    return raw is not None and raw.strip().lower() in _TRUTHY


def get_int(name: str, environ: dict[str, str] | None = None) -> int | None:
    """Return an int value, falling back to the registered default.

    An env value that fails to parse falls back to the default (the same
    forgiving spirit as ``agent_runner.config.env_int``), so a typo in the
    plist can never crash an import-time module constant. Returns ``None`` only
    when neither the env value nor the default parses.
    """
    env = environ if environ is not None else os.environ
    var = REGISTRY.get(name)
    for candidate in (env.get(name), var.default if var is not None else None):
        if candidate is None:
            continue
        try:
            return int(str(candidate).strip())
        except ValueError:
            continue
    return None


def require_int(name: str, environ: dict[str, str] | None = None) -> int:
    """Like :func:`get_int`, but for vars with a guaranteed integer default.

    Returns a plain ``int`` so callers can do arithmetic without a ``None``
    guard. If the value ever resolves to ``None`` (an unregistered var, or one
    with no parseable default), that is a config-wiring bug, so raise loudly
    instead of letting ``None`` leak into ``max``/comparisons downstream.
    """
    value = get_int(name, environ)
    if value is None:
        raise KeyError(f"{name} has no integer value or registered default")
    return value


def get_float(name: str, environ: dict[str, str] | None = None) -> float | None:
    """Return a float value, falling back to the registered default.

    Same forgiving fallback as :func:`get_int`. Returns ``None`` only when
    neither the env value nor the default parses.
    """
    env = environ if environ is not None else os.environ
    var = REGISTRY.get(name)
    for candidate in (env.get(name), var.default if var is not None else None):
        if candidate is None:
            continue
        try:
            return float(str(candidate).strip())
        except ValueError:
            continue
    return None


def get_list(
    name: str,
    environ: dict[str, str] | None = None,
    *,
    sep: str = ",",
) -> list[str]:
    """Return a de-blanked split list for a comma-separated var."""
    raw = _raw(name, environ)
    if not raw:
        return []
    return [item.strip() for item in raw.split(sep) if item.strip()]
