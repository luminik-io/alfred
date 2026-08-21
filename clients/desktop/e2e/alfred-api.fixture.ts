import { expect, type Page, type Request, type Route } from "playwright/test";

import type { SetupBatteryManifest } from "../src/types";

export const CONTRACT_TOKEN = "contract-token";

type ApiMode = "empty" | "onboarding" | "ready" | "workflow";

type RecordedRequest = {
  body: unknown;
  headers: Record<string, string>;
  method: string;
  path: string;
};

type SyntheticStreamRequest = RecordedRequest & {
  origin: string;
  url: string;
};

const installedFixtures = new WeakMap<Page, AlfredApiFixture>();

const agent = {
  codename: "architect",
  display_name: "Batman",
  role_title: "Architect",
  purpose: "Plans and coordinates changes across repositories.",
  last_firing_id: null,
  last_run_at: "2026-08-17T20:15:00Z",
  status: "idle",
  last_summary: "Ready for the next request.",
  firings_today: 2,
  failures_today: 0,
  paused: false,
  paused_since: null,
  loaded: true,
};

const workflowAgents = [
  {
    ...agent,
    codename: "triage",
    display_name: "Triage",
    role_title: "Triage",
    purpose: "Sorts incoming work and records the next decision.",
    status: "live",
    last_summary: "Prepared the release issue for planning.",
    firings_today: 5,
  },
  {
    ...agent,
    codename: "architect",
    display_name: "Architect",
    role_title: "Architect",
    purpose: "Turns approved requests into an implementation plan.",
    status: "idle",
    last_summary: "Mapped the Desktop workflow surface.",
    firings_today: 3,
  },
  {
    ...agent,
    codename: "senior-dev",
    display_name: "Senior developer",
    role_title: "Senior developer",
    purpose: "Implements approved work in an isolated worktree.",
    status: "live",
    last_summary: "Running the Desktop browser contract.",
    firings_today: 7,
  },
  {
    ...agent,
    codename: "reviewer",
    display_name: "Reviewer",
    role_title: "Reviewer",
    purpose: "Checks the change, tests, and review evidence.",
    status: "error",
    last_summary: "One review check needs attention.",
    firings_today: 4,
    failures_today: 1,
  },
  {
    ...agent,
    codename: "automerge",
    display_name: "Merge gate",
    role_title: "Merge gate",
    purpose: "Merges only after policy and review checks pass.",
    status: "idle",
    last_summary: "Waiting for approval.",
    firings_today: 2,
    paused: true,
    paused_since: "2026-08-17T20:05:00Z",
    loaded: false,
  },
  {
    ...agent,
    codename: "fleet-doctor",
    display_name: "Fleet doctor",
    role_title: "Fleet health",
    purpose: "Checks schedules, worktrees, and runtime health.",
    status: "idle",
    last_summary: "All other runtime checks passed.",
    firings_today: 1,
  },
];

const plan = {
  plan_id: "42-plan",
  title: "Make the memory benchmark use the shipped provider chain",
  status: "awaiting approval",
  parent: "https://github.com/luminik-io/alfred/issues/42",
  affected_repos: "luminik-io/alfred",
  updated_at: "2026-08-17T20:00:00Z",
  path: "plans/42-plan.json",
  preview: "Use the provider chain that ships with Alfred and record exact benchmark evidence.",
  content: "## Plan\n\nRun the memory benchmark through Alfred's shipped provider chain.",
  source: "architect",
  readiness_score: 96,
  readiness_ok: true,
  revision_count: 1,
};

const card = (
  number: number,
  title: string,
  kind: "issue" | "pr",
  author: string,
  overrides: Record<string, unknown> = {},
) => ({
  repo: "luminik-io/alfred",
  number,
  title,
  url: `https://github.com/luminik-io/alfred/${kind === "pr" ? "pull" : "issues"}/${number}`,
  author,
  kind,
  timestamp: "2026-08-17T20:00:00Z",
  age_days: 0,
  is_draft: false,
  labels: [],
  ...overrides,
});

const sampleBoard = {
  sample: true,
  generated_at: "2026-08-17T20:15:00Z",
  lookback_days: 14,
  repos: ["luminik-io/alfred"],
  columns: {
    queued: [card(621, "Add OpenCode capability probe", "issue", "architect")],
    in_progress: [
      card(683, "Record engine settings for every run", "pr", "senior-dev", {
        is_draft: true,
        labels: ["agent:authored", "harness:codex"],
        agent_evidence: ["label:agent:authored", "branch:senior-dev/run-evidence"],
        github_evidence: {
          head_sha: "dc759788a0300891d6c21718a9e4fa8d23f69cf7",
          review_state: "REVIEW_REQUIRED",
          checks: [
            { name: "Desktop client", status: "SUCCESS" },
            { name: "Public metadata", status: "SUCCESS" },
            { name: "Independent review", status: "PENDING" },
          ],
          changed_files: [
            "clients/desktop/src/components/LogsView.tsx",
            "lib/agent_runner/process.py",
            "lib/server/run_evidence.py",
          ],
          changed_file_count: 3,
          changed_file_count_incomplete: false,
          commit_count: 1,
          commit_count_incomplete: false,
          latest_reviews: [{ author: "reviewer", state: "COMMENTED" }],
        },
      }),
      card(623, "Verify isolated worktree cleanup", "issue", "reviewer"),
    ],
    shipped: [
      card(617, "Harden macOS runner selection", "pr", "senior-dev", {
        agent_evidence: ["label:agent:authored"],
      }),
      card(618, "Make code-memory tests deterministic", "pr", "reviewer", {
        agent_evidence: ["label:agent:authored"],
      }),
    ],
    awaiting_approval: [],
  },
  counts: { queued: 1, in_progress: 2, shipped: 2, awaiting_approval: 0 },
};

const emptyBoard = {
  sample: false,
  generated_at: "2026-08-17T20:15:00Z",
  lookback_days: 14,
  repos: ["example/workspace"],
  columns: {
    queued: [],
    in_progress: [],
    shipped: [],
    awaiting_approval: [],
  },
  counts: { queued: 0, in_progress: 0, shipped: 0, awaiting_approval: 0 },
};

const sampleBatteries: SetupBatteryManifest = {
  version: 1,
  summary: { included: 2, enabled: 1, available: 1, not_installed: 1, total: 5 },
  batteries: [
    {
      id: "sqlite-memory",
      name: "Built-in memory",
      category: "memory",
      what: "An embedded lesson store that needs no service.",
      how_it_helps: "Keeps useful lessons available for later runs.",
      builtin: true,
      default_on: true,
      setup_group: "included",
      status: "included",
      configured: true,
      enabled: true,
      installed: true,
      requires_daemon: false,
      service: "",
      install_kind: "included",
      install_hint: "",
      pip_extra: "",
      env_keys: [],
      docs: "docs/MEMORY_PROVIDERS.md",
      version: "bundled with Alfred",
      license: "MIT",
      source_url: "https://github.com/luminik-io/alfred",
      integrity: "installed with the signed Alfred package",
      install_command: "included with Alfred",
      check_command: "alfred batteries list --json",
      disable_command: "not disableable",
      remove_command: "removed with Alfred",
    },
    {
      id: "tool-compactor",
      name: "Tool-output compactor",
      category: "compression",
      what: "A built-in filter for verbose command and test output.",
      how_it_helps: "Leaves more context for the work itself.",
      builtin: true,
      default_on: true,
      setup_group: "included",
      status: "included",
      configured: true,
      enabled: true,
      installed: true,
      requires_daemon: false,
      service: "",
      install_kind: "included",
      install_hint: "",
      pip_extra: "",
      env_keys: [],
      docs: "docs/COMPRESSION.md",
      version: "bundled with Alfred",
      license: "MIT",
      source_url: "https://github.com/luminik-io/alfred",
      integrity: "installed with the signed Alfred package",
      install_command: "included with Alfred",
      check_command: "alfred batteries list --json",
      disable_command: "not disableable",
      remove_command: "removed with Alfred",
    },
    {
      id: "code-memory-mcp",
      name: "Codebase memory",
      category: "code-graph",
      what: "A local code graph for structural search and impact checks.",
      how_it_helps: "Helps agents find the right code before they edit it.",
      builtin: false,
      default_on: true,
      setup_group: "included",
      status: "enabled",
      configured: true,
      enabled: true,
      installed: true,
      requires_daemon: false,
      service: "",
      install_kind: "autofetch",
      install_hint: "alfred code-memory install",
      pip_extra: "",
      env_keys: ["ALFRED_CODE_MEMORY_MCP"],
      docs: "docs/CODE_MEMORY.md",
      version: "v0.8.1",
      license: "MIT",
      source_url: "https://github.com/DeusData/codebase-memory-mcp",
      integrity: "release checksums.txt verified with SHA-256",
      install_command: "alfred batteries install code-memory-mcp --yes",
      check_command: "alfred code-memory doctor",
      disable_command: "alfred batteries disable code-memory-mcp --yes",
      remove_command: "alfred batteries remove code-memory-mcp --yes",
    },
    {
      id: "headroom-compression",
      name: "Headroom compression",
      category: "compression",
      what: "An optional compressor that uses the same gates as the built-in compactor.",
      how_it_helps: "Lets you compare Headroom with the built-in compactor before enabling it.",
      builtin: false,
      default_on: false,
      setup_group: "optional-local",
      status: "available",
      configured: false,
      enabled: false,
      installed: true,
      requires_daemon: false,
      service: "",
      install_kind: "pip-extra",
      install_hint: "Install headroom-ai into the Python interpreter that runs Alfred.",
      pip_extra: "",
      env_keys: ["ALFRED_COMPRESSION_ENGINE", "ALFRED_HEADROOM_AUTOFETCH"],
      docs: "docs/COMPRESSION.md",
      version: "headroom-ai==0.29.0",
      license: "Apache-2.0",
      source_url: "https://pypi.org/project/headroom-ai/0.29.0/",
      integrity: "Python package index artifact hashes",
      install_command: "alfred batteries install headroom-compression --yes",
      check_command: "alfred batteries list --json",
      disable_command: "alfred batteries disable headroom-compression --yes",
      remove_command: "alfred batteries remove headroom-compression --yes",
    },
    {
      id: "pgvector",
      name: "Postgres + pgvector",
      category: "memory",
      what: "An optional Postgres-backed semantic lesson store.",
      how_it_helps: "Shares memory across a larger installation.",
      builtin: false,
      default_on: false,
      setup_group: "external-service",
      status: "not_installed",
      configured: false,
      enabled: false,
      installed: false,
      requires_daemon: true,
      service: "Postgres",
      install_kind: "daemon",
      install_hint: "Run Postgres with pgvector, then set the connection URL.",
      pip_extra: "pgvector",
      env_keys: ["ALFRED_MEMORY_PG_DSN"],
      docs: "docs/MEMORY_PROVIDERS.md",
      version: "psycopg>=3.1; pgvector>=0.2; Postgres operator-managed",
      license: "psycopg LGPL-3.0; pgvector-python MIT; pgvector PostgreSQL",
      source_url: "https://github.com/pgvector/pgvector",
      integrity: "Python package index artifact hashes; database is operator-managed",
      install_command: "follow docs/MEMORY_PROVIDERS.md; Alfred does not install databases",
      check_command: "alfred batteries list --json",
      disable_command: "alfred batteries disable pgvector --yes",
      remove_command: "remove the operator-managed database after disabling it",
    },
  ],
};

const samplePlaybooks = {
  playbooks: [
    {
      key: "fix-failing-test",
      title: "Fix a failing test",
      summary: "Trace one reproducible failure, repair it, and prove the fix.",
    },
    {
      key: "review-pull-request",
      title: "Review a pull request",
      summary: "Check a pull request for correctness, risk, and missing tests.",
    },
  ],
};

const emptyDraft = {
  title: "",
  problem: "",
  user: "",
  current_behavior: "",
  desired_behavior: "",
  repos: ["example/workspace"],
  acceptance_criteria: [],
  test_plan: "",
  out_of_scope: "",
  rollout: "",
  open_questions: "",
  operator_notes: "",
};

export class AlfredApiFixture {
  readonly requests: RecordedRequest[] = [];
  readonly unknownRequests: string[] = [];
  readonly protocolErrors: string[] = [];
  private planApproved = false;

  constructor(
    private readonly page: Page,
    private readonly mode: ApiMode,
  ) {}

  private get isReady(): boolean {
    return this.mode === "ready" || this.mode === "workflow";
  }

  async install(): Promise<void> {
    await this.installStreamingFetch();
    await this.page.route("**/*", async (route) => {
      const request = route.request();
      const url = new URL(request.url());

      if (request.resourceType() === "document" && url.pathname === "/") {
        const response = await route.fetch();
        const html = await response.text();
        await route.fulfill({
          response,
          body: html.replace(
            "</head>",
            `<meta name="alfred-token" content="${CONTRACT_TOKEN}"></head>`,
          ),
        });
        return;
      }

      if (!url.pathname.startsWith("/api/")) {
        await route.continue();
        return;
      }

      this.record(request, url);
      if (!this.validatePrivilegedRequest(request.method(), url, request.headers())) {
        await route.fulfill({
          status: 403,
          contentType: "application/json",
          body: JSON.stringify({ error: "Contract request failed authentication." }),
        });
        return;
      }
      const handled = await this.handle(route, request, url);
      if (!handled) {
        const signature = `${request.method()} ${url.pathname}${url.search}`;
        this.unknownRequests.push(signature);
        await route.fulfill({
          status: 501,
          contentType: "application/json",
          body: JSON.stringify({ error: `Unhandled contract route: ${signature}` }),
        });
      }
    });
  }

  find(method: string, path: string): RecordedRequest | undefined {
    return this.requests.find((request) => request.method === method && request.path === path);
  }

  assertNoUnknownRequests(): void {
    expect(this.unknownRequests, "every desktop API call must be declared by the fixture").toEqual(
      [],
    );
    expect(
      this.protocolErrors,
      "every privileged desktop API call must carry the launch token and same-origin proof",
    ).toEqual([]);
  }

  async releaseStream(): Promise<void> {
    const released = await this.page.evaluate(() => {
      const contractWindow = window as typeof window & {
        __releaseAlfredContractStream?: () => void;
      };
      if (!contractWindow.__releaseAlfredContractStream) return false;
      contractWindow.__releaseAlfredContractStream();
      return true;
    });
    expect(released, "the Ask stream must reach its first incremental frame").toBe(true);
  }

  private record(request: Request, url: URL): void {
    let body: unknown = null;
    const raw = request.postData();
    if (raw) {
      try {
        body = JSON.parse(raw) as unknown;
      } catch {
        body = raw;
      }
    }
    this.requests.push({
      body,
      headers: request.headers(),
      method: request.method(),
      path: `${url.pathname}${url.search}`,
    });
  }

  private async handle(
    route: Route,
    request: Request,
    url: URL,
  ): Promise<boolean> {
    const method = request.method();
    const path = url.pathname;
    const matches = (pathname: string, search = "") =>
      path === pathname && url.search === search;

    if (method === "POST" && matches("/api/conversation/control")) {
      await this.fulfill(route, {
        handled: false,
        action: "unknown",
        text: "",
        detail: "",
        actor_user_id: "desktop",
      });
      return true;
    }

    if (method === "POST" && matches("/api/plans/42-plan/decision")) {
      this.planApproved = true;
      await this.fulfill(route, {
        plan_id: "42-plan",
        issue_number: 42,
        decision: "approve",
        status: "approved",
        marker_path: "state/approvals/42.approved",
      });
      return true;
    }

    if (method !== "GET") return false;

    if (matches("/api/status")) {
      await this.fulfill(route, {
        agents:
          this.mode === "workflow"
            ? workflowAgents
            : this.mode === "ready"
              ? [agent]
              : [],
        total_today: this.mode === "workflow" ? 22 : this.mode === "ready" ? 2 : 0,
        reliability: { status: "ok", actions: [], failure_patterns: [] },
        metrics: {
          spend_usd: null,
          firings: this.mode === "workflow" ? 22 : this.mode === "ready" ? 2 : 0,
          successes: this.mode === "workflow" ? 21 : this.mode === "ready" ? 2 : 0,
          failures: 0,
          agents_with_spend: 0,
        },
        intake_profile: "technical",
        setup_repos: {
          selected: this.isReady ? ["example/workspace"] : [],
          count: this.isReady ? 1 : 0,
        },
      });
      return true;
    }
    if (matches("/api/actions")) {
      await this.fulfill(route, {
        status: "ok",
        actions: [],
        failure_patterns: [],
        stale_workers: [],
        promotion_suggestions: [],
      });
      return true;
    }
    if (matches("/api/memory/candidates", "?limit=20")) {
      await this.fulfill(route, { rows: [] });
      return true;
    }
    if (matches("/api/memory/lessons", "?limit=30")) {
      await this.fulfill(route, {
        rows:
          this.isReady
            ? [
                {
                  id: "lesson:fixture-boundary",
                  codename: "senior-dev",
                  repo: "example/workspace",
                  body: "Keep fixture data separate from operator data.",
                  tags: ["privacy", "testing"],
                  severity: "info",
                  created_at: "2026-08-17T19:45:00Z",
                  firing_id: "senior-dev-visual-contract",
                  match_reason: "Active lesson for example/workspace.",
                  recall_provider: "sqlite",
                  provenance:
                    "pr:https://github.com/example/workspace/pull/42",
                  valid_until: "2026-10-23T19:45:00Z",
                  memory_status: "active",
                  age_seconds: 86400,
                  scope: {
                    codename: "senior-dev",
                    repo: "example/workspace",
                  },
                  ops: false,
                },
              ]
            : [],
      });
      return true;
    }
    if (matches("/api/firings", "?limit=14")) {
      await this.fulfill(route, {
        rows:
          this.isReady
            ? [
                {
                  firing_id: "senior-dev-visual-contract",
                  codename: "senior-dev",
                  started_at: "2026-08-17T19:40:00Z",
                  ended_at: "2026-08-17T19:44:00Z",
                  status: "ok",
                  summary: "Completed the Desktop visual contract",
                  transcript_path: null,
                  events_path: "state/fixtures/visual-contract.jsonl",
                  evidence: {
                    schema_version: 1,
                    run_id: "senior-dev-visual-contract",
                    agent: "senior-dev",
                    event_count: 8,
                    facts: [
                      {
                        kind: "issue",
                        source: "github",
                        event_type: "issue_picked",
                        event_seq: 2,
                        data: { repo: "example/workspace", number: 42 },
                      },
                      {
                        kind: "engine_session",
                        source: "engine",
                        event_type: "llm_invoke_done",
                        event_seq: 4,
                        data: {
                          engine: "opencode",
                          session_id: "session_fixture_42",
                          turns: 7,
                        },
                      },
                      {
                        kind: "approval",
                        source: "operator",
                        event_type: "plan_approved",
                        event_seq: 5,
                        data: { decision: "approved" },
                      },
                      {
                        kind: "pull_request",
                        source: "github",
                        event_type: "pr_opened",
                        event_seq: 7,
                        data: {
                          repo: "example/workspace",
                          number: 42,
                        },
                      },
                    ],
                    artifacts: [
                      {
                        kind: "events",
                        status: "available",
                        path: "state/fixtures/visual-contract.jsonl",
                      },
                      {
                        kind: "transcript",
                        status: "available",
                        path: "state/fixtures/visual-contract.events.jsonl",
                      },
                      {
                        kind: "imported_session",
                        status: "available",
                        path: "state/imports/senior-dev/visual-contract/stdout.txt",
                      },
                    ],
                  },
                  timeline: {
                    headline: "Completed the Desktop visual contract",
                    severity: "ok",
                    error: null,
                    outcome: "All layout checks passed.",
                    steps: [
                      {
                        kind: "selected",
                        label: "Selected the Desktop visual contract",
                        detail: "example/workspace",
                        tone: "info",
                        ts: "2026-08-17T19:40:00Z",
                      },
                      {
                        kind: "checks",
                        label: "Ran layout and responsive checks",
                        detail: "All checks passed",
                        tone: "ok",
                        ts: "2026-08-17T19:43:00Z",
                      },
                      {
                        kind: "completed",
                        label: "Prepared the review evidence",
                        detail: "Ready for operator review",
                        tone: "ok",
                        ts: "2026-08-17T19:44:00Z",
                      },
                    ],
                  },
                },
              ]
            : [],
      });
      return true;
    }
    if (matches("/api/plans", "?limit=14")) {
      await this.fulfill(route, {
        rows: this.isReady && !this.planApproved ? [plan] : [],
      });
      return true;
    }
    if (matches("/api/slack/trusted-users")) {
      await this.fulfill(route, {
        operator_user_id: null,
        users:
          this.isReady
            ? [
                {
                  user_id: "UTEAM12345",
                  sources: ["state"],
                  added_at: "2026-08-17T19:45:00Z",
                  added_by: "desktop",
                  can_remove: true,
                },
              ]
            : [],
        state_path: "state/slack-trusted-users.json",
      });
      return true;
    }
    if (matches("/api/schedule")) {
      await this.fulfill(route, {
        runs:
          this.isReady
            ? [
                {
                  codename: "architect",
                  role: "architect",
                  display_name: "Batman",
                  role_title: "Architect",
                  purpose: agent.purpose,
                  kind: "interval",
                  cadence: "every 30m",
                  next_fire_at: null,
                  raw_schedule: "30m",
                },
              ]
            : [],
      });
      return true;
    }
    if (matches("/api/shipped", "?days=14")) {
      await this.fulfill(route, this.isReady ? sampleBoard : emptyBoard);
      return true;
    }
    if (matches("/api/usage")) {
      await this.fulfill(route, {
        available: true,
        kind: "subscription",
        source: "fixture",
        block: null,
        codex: {
          latest_day: {
            date: "2026-08-17",
            total_tokens: 82_400,
            cost_usd: null,
            input_tokens: 70_200,
            output_tokens: 12_200,
          },
          totals: { total_tokens: 1_240_000, cost_usd: null },
          quota: {
            primary: { used_percent: 28, resets_at: "2026-08-17T23:00:00Z" },
            secondary: { used_percent: 39, resets_at: "2026-08-22T20:00:00Z" },
            plan_type: "sample",
          },
        },
        limits: {
          source: "fixture",
          updated_at: "2026-08-17T20:15:00Z",
          five_hour: {
            utilization: 28,
            remaining_percent: 72,
            resets_at: "2026-08-17T23:00:00Z",
            minutes_to_reset: 165,
          },
          seven_day: {
            utilization: 39,
            remaining_percent: 61,
            resets_at: "2026-08-22T20:00:00Z",
            minutes_to_reset: 7_185,
          },
          seven_day_sonnet: null,
          seven_day_opus: null,
          extra_usage: null,
        },
        weekly: null,
      });
      return true;
    }
    if (matches("/api/setup/status")) {
      await this.fulfill(route, this.setupStatus());
      return true;
    }
    if (matches("/api/setup/batteries")) {
      await this.fulfill(route, sampleBatteries);
      return true;
    }
    if (matches("/api/setup/playbooks")) {
      await this.fulfill(route, samplePlaybooks);
      return true;
    }
    if (matches("/api/roster-theme")) {
      await this.fulfill(route, {
        theme: "batman",
        custom_names: {},
        custom_roles: {},
        updated_at: null,
      });
      return true;
    }
    if (matches("/api/custom-agents", "?include_prompt=1")) {
      await this.fulfill(route, {
        version: 1,
        path: "state/custom-agents.json",
        agents: [],
        count: 0,
        enabled_count: 0,
        disabled_count: 0,
        updated_at: null,
      });
      return true;
    }
    if (matches("/api/agent-models")) {
      await this.fulfill(route, {
        agents: [
          {
            agent: "architect",
            claude: { resolved: "opus", persisted: null, source: "provider-default" },
            codex: { resolved: "gpt-5", persisted: null, source: "provider-default" },
            opencode: {
              resolved: "anthropic/claude-sonnet-4-5",
              persisted: null,
              source: "provider-default",
            },
          },
        ],
        count: 1,
      });
      return true;
    }
    if (path === "/api/code-intelligence") {
      const selectedRepo = url.searchParams.get("repo");
      const queryPath = url.searchParams.get("path");
      const analyzed =
        this.isReady && Boolean(selectedRepo && queryPath);
      await this.fulfill(route, {
        schema: "alfred.code-intelligence.v1",
        generated_at: "2026-08-17T20:15:00Z",
        repos:
          this.isReady
            ? [
                {
                  name: "example/workspace",
                  head_sha: "0123456789abcdef",
                  summary: {
                    files: 128,
                    symbols: 840,
                    imports: 412,
                    languages: { TypeScript: 82, Python: 46 },
                    truncated: false,
                  },
                  endpoint_count: 14,
                  route_count: 10,
                  api_call_count: 18,
                  contract_drift_count: 0,
                },
              ]
            : [],
        repo_count: this.isReady ? 1 : 0,
        contract_drift_count: 0,
        selected_repo: analyzed ? selectedRepo : null,
        query_path: analyzed ? queryPath : null,
        impact: analyzed
          ? {
              kind: "impact-brief",
              repo: selectedRepo,
              path: queryPath,
              matched_file: queryPath,
              match_status: "exact",
              head_sha: "0123456789abcdef",
              language: "typescript",
              level: "high",
              reasons: ["route contract", "direct dependents"],
              summary: "The indexed file owns one route and has two direct dependents.",
              counts: {
                symbols: 3,
                direct_dependents: 2,
                direct_dependencies: 1,
                contract_surfaces: 1,
                contract_drift: 1,
                nearby_files: 2,
              },
              symbols: [
                { name: "registerRoutes", line: 18 },
                { name: "readWorkspace", line: 42 },
                { name: "writeResponse", line: 67 },
              ],
              direct_dependents: [
                { path: "src/server/app.ts", via: "./routes", kind: "import" },
                { path: "tests/routes.test.ts", via: "route setup", kind: "test" },
              ],
              direct_dependencies: [
                { path: "src/server/store.ts", via: "./store", kind: "import" },
              ],
              contract_surfaces: [
                {
                  kind: "route",
                  method: "GET",
                  path: "/api/workspaces",
                  file: queryPath,
                },
              ],
              contract_drift: [
                {
                  method: "GET",
                  path: "/api/workspaces",
                  normalized: "/workspaces",
                  file: queryPath,
                },
              ],
              nearby_files: ["tests/routes.test.ts", "src/server/store.ts"],
              candidate_matches: [],
              next_checks: [
                "Run the route contract tests.",
                "Check callers before changing the response shape.",
              ],
            }
          : null,
      });
      return true;
    }
    return false;
  }

  private setupStatus(): Record<string, unknown> {
    const ready = this.mode !== "onboarding";
    return {
      github: {
        ok: ready,
        account: ready ? "example" : null,
        detail: ready ? "Signed in" : "Sign in required",
      },
      engines: [
        {
          name: "claude",
          display_name: "Claude Code",
          installed: ready,
          protocol_compatible: ready,
          ready,
          dispatchable: true,
          state: ready ? "ready" : "missing",
          detail: ready
            ? "Claude Code is compatible and signed in."
            : "Claude Code is not installed.",
          path: ready ? "/usr/local/bin/claude" : null,
          version: ready ? "Claude Code 2.1.0" : null,
          capabilities: ["text", "worktree-write"],
          failures: ready ? [] : ["missing_binary"],
        },
        {
          name: "codex",
          display_name: "Codex",
          installed: ready,
          protocol_compatible: ready,
          ready,
          dispatchable: true,
          state: ready ? "ready" : "missing",
          detail: ready ? "Codex is compatible and signed in." : "Codex is not installed.",
          path: ready ? "/usr/local/bin/codex" : null,
          version: ready ? "codex-cli 1.2.3" : null,
          capabilities: ["text", "worktree-write"],
          failures: ready ? [] : ["missing_binary"],
        },
      ],
      engine_ready: ready,
      repos: {
        selected: ready ? ["example/workspace"] : [],
        count: ready ? 1 : 0,
        keys: [],
        repo_checkouts: [],
      },
      queue: { ready, count: ready ? 1 : 0, covers_selected: ready, missing_selected: [] },
      demo: { present: false },
      first_run: {
        version: 1,
        ready,
        status: ready ? "ready" : "needs_action",
        headline: ready ? "Alfred is ready" : "Finish setup",
        summary: {
          required_ready: ready ? 3 : 0,
          required_total: 3,
          recommended_ready: 0,
          recommended_total: 0,
          optional_ready: 0,
          optional_total: 0,
          blockers: ready ? [] : ["github", "engine", "repos"],
        },
        checks: [],
      },
      ready,
    };
  }

  private async fulfill(route: Route, body: unknown): Promise<void> {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  }

  private streamResult(): Record<string, unknown> {
    return {
      draft_id: "ask-contract",
      saved_path: "state/plans/ask-contract.json",
      reply: "I found the relevant desktop protocol. What outcome should the test prove?",
      intent: "build",
      readiness: { score: 62, ready: false, missing: ["Expected outcome"] },
      done: false,
      draft: emptyDraft,
    };
  }

  private async installStreamingFetch(): Promise<void> {
    await this.page.exposeBinding(
      "__alfredContractStreamRequest",
      async (_source, payload: SyntheticStreamRequest) => {
        const url = new URL(payload.url);
        this.requests.push({
          body: payload.body,
          headers: payload.headers,
          method: payload.method,
          path: payload.path,
        });
        if (
          payload.method !== "POST" ||
          url.pathname !== "/api/compose/converse/stream" ||
          url.search !== ""
        ) {
          this.unknownRequests.push(`${payload.method} ${payload.path}`);
          return { ok: false, status: 501 };
        }
        const ok = this.validatePrivilegedRequest(
          payload.method,
          url,
          payload.headers,
          payload.origin,
        );
        return { ok, status: ok ? 200 : 403 };
      },
    );

    await this.page.addInitScript(
      ({ firstFrame, secondFrame, resultFrame }) => {
        type ContractWindow = typeof window & {
          __alfredContractStreamRequest: (
            payload: SyntheticStreamRequest,
          ) => Promise<{ ok: boolean; status: number }>;
          __releaseAlfredContractStream?: () => void;
        };
        const contractWindow = window as ContractWindow;
        const nativeFetch = window.fetch.bind(window);

        window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
          const inputRequest = input instanceof Request ? input : null;
          const rawUrl =
            typeof input === "string"
              ? input
              : inputRequest
                ? inputRequest.url
                : input.toString();
          const url = new URL(rawUrl, window.location.href);
          if (url.pathname !== "/api/compose/converse/stream") {
            return nativeFetch(input, init);
          }

          const headers = new Headers(inputRequest?.headers);
          new Headers(init?.headers).forEach((value, key) => headers.set(key, value));
          const method = (init?.method ?? inputRequest?.method ?? "GET").toUpperCase();
          const rawBody =
            typeof init?.body === "string"
              ? init.body
              : inputRequest
                ? await inputRequest.clone().text()
                : null;
          let body: unknown = null;
          if (rawBody) {
            try {
              body = JSON.parse(rawBody) as unknown;
            } catch {
              body = rawBody;
            }
          }
          const validation = await contractWindow.__alfredContractStreamRequest({
            body,
            headers: Object.fromEntries(headers.entries()),
            method,
            origin: window.location.origin,
            path: `${url.pathname}${url.search}`,
            url: url.toString(),
          });
          if (!validation.ok) {
            return new Response(JSON.stringify({ error: "Contract stream rejected." }), {
              status: validation.status,
              headers: { "content-type": "application/json" },
            });
          }

          const encoder = new TextEncoder();
          const bodyStream = new ReadableStream<Uint8Array>({
            start(controller) {
              controller.enqueue(encoder.encode(firstFrame));
              let released = false;
              contractWindow.__releaseAlfredContractStream = () => {
                if (released) return;
                released = true;
                controller.enqueue(encoder.encode(secondFrame));
                window.setTimeout(() => {
                  controller.enqueue(encoder.encode(resultFrame));
                  controller.close();
                  delete contractWindow.__releaseAlfredContractStream;
                }, 25);
              };
            },
          });
          return new Response(bodyStream, {
            status: 200,
            headers: { "content-type": "text/event-stream", "cache-control": "no-cache" },
          });
        };
      },
      {
        firstFrame:
          'event: token\ndata: {"text":"I found the relevant desktop protocol. "}\n\n',
        secondFrame:
          'event: token\ndata: {"text":"What outcome should the test prove?"}\n\n',
        resultFrame: `event: result\ndata: ${JSON.stringify(this.streamResult())}\n\n`,
      },
    );
  }

  private validatePrivilegedRequest(
    method: string,
    url: URL,
    headers: Record<string, string>,
    browserOrigin = headers.origin ?? headers.referer,
  ): boolean {
    const privilegedRead =
      method === "GET" &&
      url.pathname === "/api/custom-agents" &&
      url.search === "?include_prompt=1";
    if (method === "GET" && !privilegedRead) return true;

    const signature = `${method} ${url.pathname}${url.search}`;
    let valid = true;
    if (headers["x-alfred-token"] !== CONTRACT_TOKEN) {
      this.protocolErrors.push(`${signature} omitted the launch token`);
      valid = false;
    }
    let presentedOrigin = "";
    try {
      presentedOrigin = browserOrigin ? new URL(browserOrigin).origin : "";
    } catch {
      // Invalid Origin/Referer values fail the same-origin check below.
    }
    if (presentedOrigin !== url.origin) {
      this.protocolErrors.push(`${signature} was not same-origin`);
      valid = false;
    }
    return valid;
  }
}

export async function installAlfredApi(
  page: Page,
  mode: ApiMode = "ready",
): Promise<AlfredApiFixture> {
  const api = new AlfredApiFixture(page, mode);
  await api.install();
  installedFixtures.set(page, api);
  return api;
}

export function assertAlfredApiComplete(page: Page): void {
  installedFixtures.get(page)?.assertNoUnknownRequests();
}
