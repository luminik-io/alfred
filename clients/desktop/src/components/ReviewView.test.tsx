import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";

import { ReviewView } from "./ReviewView";
import type { AttentionItem } from "../lib/uiTypes";
import type { AgentSummary, FiringRecord, ShippedBoard, Snapshot, UsageResponse } from "../types";

vi.mock("../lib/links", () => ({
  openExternal: vi.fn(),
}));

function usage(overrides: Partial<UsageResponse> = {}): UsageResponse {
  return {
    available: true,
    kind: "subscription",
    source: "native",
    block: {
      start_at: "2026-06-02T10:00:00Z",
      reset_at: "2026-06-02T15:00:00Z",
      minutes_to_reset: 125,
      is_active: true,
      total_tokens: 142_200_916,
      cost_usd: 109.9,
      entries: 1021,
      token_counts: { input: 1, output: 2, cache_creation: 3, cache_read: 4 },
      projection: { total_tokens: 1_063_656_501, total_cost_usd: 822.07, remaining_minutes: 175 },
      burn_rate: { tokens_per_minute: 3_544_059, cost_per_hour: 164.34 },
      models: ["claude-opus-4-8"],
    },
    codex: {
      latest_day: { date: "2026-06-02", total_tokens: 75_778, cost_usd: 0.32, input_tokens: 62_886, output_tokens: 92 },
      totals: { total_tokens: 4_211_983_837, cost_usd: 3276.2 },
    },
    weekly: null,
    ...overrides,
  };
}

// Most tests only care about a lane; render with a benign available-usage panel
// and an idle state unless the test overrides them.
function renderReview(
  props: Partial<ComponentProps<typeof ReviewView>> = {},
) {
  return render(
    <ReviewView
      snapshot={snapshot()}
      needsYou={[]}
      shipped={null}
      usage={usage()}
      usageState="idle"
      onSwitch={vi.fn()}
      {...props}
    />,
  );
}

function snapshot(overrides: Partial<Snapshot> = {}): Snapshot {
  return {
    loadedAt: new Date("2026-06-02T12:00:00Z"),
    shipped: null,
    schedule: [],
    status: { agents: [], total_today: 3, reliability: { status: "ok" } },
    actions: {
      status: "ok",
      actions: [],
      failure_patterns: [],
      stale_workers: [],
      promotion_suggestions: [],
    },
    memoryCandidates: { rows: [] },
    firings: [],
    plans: [],
    trustedSlack: null,
    ...overrides,
  };
}

function agent(overrides: Partial<AgentSummary> = {}): AgentSummary {
  return {
    codename: "lucius",
    last_firing_id: null,
    last_run_at: null,
    status: "idle",
    last_summary: "no firings yet",
    firings_today: 0,
    paused: false,
    paused_since: null,
    loaded: true,
    ...overrides,
  };
}

function firing(overrides: Partial<FiringRecord> = {}): FiringRecord {
  return {
    firing_id: "f1",
    codename: "lucius",
    started_at: "2026-06-02T11:00:00Z",
    ended_at: null,
    status: "running",
    summary: "Working on the CSV export.",
    transcript_path: null,
    events_path: "",
    ...overrides,
  };
}

function board(overrides: Partial<ShippedBoard> = {}): ShippedBoard {
  return {
    generated_at: "2026-06-02T12:00:00Z",
    lookback_days: 14,
    repos: ["your-org/api"],
    columns: { queued: [], in_progress: [], shipped: [] },
    counts: { queued: 0, in_progress: 0, shipped: 0 },
    errors: [],
    ...overrides,
  };
}

const needsYouItem: AttentionItem = {
  id: "plan-1",
  label: "Draft",
  title: "Approve the export plan",
  detail: "Review before Alfred starts.",
  tone: "info",
  targetTab: "pipeline",
  icon: "plan",
};

describe("ReviewView", () => {
  it("offers the three lanes as in-page tabs, with capacity evidence pinned", () => {
    renderReview({ needsYou: [needsYouItem] });
    // Lanes are in-page tabs now (no long scroll); the right rail keeps the
    // useful capacity evidence in view without a duplicate cost strip.
    expect(screen.getByRole("tab", { name: /needs/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /running/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /shipped/i })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: /engine capacity/i })).toBeInTheDocument();
    // Real engine headroom is surfaced in the rail (Claude 5h window) without a
    // raw token wall.
    expect(screen.getByText(/5h window/i)).toBeInTheDocument();
    // A waiting decision opens on the Needs lane by default.
    expect(screen.getByRole("region", { name: /decisions/i })).toBeInTheDocument();
    expect(screen.getByText(/approve the export plan/i)).toBeInTheDocument();
  });

  it("follows the smart default lane as state loads, until the operator pins one", async () => {
    // Before the first snapshot resolves, the dashboard surfaces a transient
    // connection item, so decisions is truthy and Inbox opens on Needs.
    const { rerender } = render(
      <ReviewView
        snapshot={null}
        needsYou={[needsYouItem]}
        shipped={null}
        usage={usage()}
        usageState="idle"
        onSwitch={vi.fn()}
      />,
    );
    expect(screen.getByRole("tab", { name: /needs/i })).toHaveAttribute("aria-selected", "true");

    // A healthy snapshot with no real decisions arrives: the lane moves to
    // Running on its own instead of stranding Inbox on an empty Needs panel.
    rerender(
      <ReviewView
        snapshot={snapshot()}
        needsYou={[]}
        shipped={null}
        usage={usage()}
        usageState="idle"
        onSwitch={vi.fn()}
      />,
    );
    expect(screen.getByRole("tab", { name: /running/i })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: /needs/i })).toHaveAttribute("aria-selected", "false");

    // Once the operator manually picks a lane, respect it even as state changes.
    await userEvent.setup().click(screen.getByRole("tab", { name: /shipped/i }));
    expect(screen.getByRole("tab", { name: /shipped/i })).toHaveAttribute("aria-selected", "true");
    rerender(
      <ReviewView
        snapshot={snapshot()}
        needsYou={[needsYouItem]}
        shipped={null}
        usage={usage()}
        usageState="idle"
        onSwitch={vi.fn()}
      />,
    );
    expect(screen.getByRole("tab", { name: /shipped/i })).toHaveAttribute("aria-selected", "true");
  });

  it("shows an honest empty note when no schedule is surfaced", () => {
    renderReview();
    expect(screen.getByText(/no upcoming runs surfaced/i)).toBeInTheDocument();
    expect(screen.getByText(/could not read a launchd schedule/i)).toBeInTheDocument();
  });

  it("lists upcoming scheduled runs with a next-fire time or a cadence", () => {
    renderReview({
      snapshot: snapshot({
        schedule: [
          {
            codename: "bane",
            role: "Daily test author",
            kind: "cron-daily",
            cadence: "daily 02:00",
            next_fire_at: "2026-06-04T02:00:00",
            raw_schedule: "cron:2:00",
          },
          {
            codename: "lucius",
            role: "Single-repo engineer",
            kind: "interval",
            cadence: "every 10m",
            next_fire_at: null,
            raw_schedule: "interval:600",
          },
        ],
      }),
    });
    expect(screen.getByRole("list", { name: /upcoming scheduled runs/i })).toBeInTheDocument();
    expect(screen.getByText("bane")).toBeInTheDocument();
    // cron row shows a next-fire; interval row shows its cadence string.
    expect(screen.getByText(/^next/i)).toBeInTheDocument();
    expect(screen.getByText("every 10m")).toBeInTheDocument();
    // The empty-state note is gone once real runs render.
    expect(screen.queryByText(/no upcoming runs surfaced/i)).not.toBeInTheDocument();
  });

  it("shows running firings in the Running lane", () => {
    renderReview({ snapshot: snapshot({ firings: [firing()] }) });
    expect(screen.getByText("Alfred is working")).toBeInTheDocument();
    expect(screen.getByText(/1 run is active now/i)).toBeInTheDocument();
    expect(screen.getByText(/working on the csv export\./i)).toBeInTheDocument();
    expect(screen.queryByText(/all clear/i)).not.toBeInTheDocument();
  });

  it("renders shipped work as a plain-English digest", async () => {
    renderReview({
      shipped: board({
        columns: {
          queued: [],
          in_progress: [],
          shipped: [
            {
              repo: "your-org/api",
              number: 7,
              title: "feat: add CSV export",
              url: "https://github.com/your-org/api/pull/7",
              author: "lucius",
              kind: "pr",
              timestamp: "2026-06-02T11:00:00Z",
              age_days: 0,
              is_draft: false,
              labels: [],
            },
            {
              repo: "your-org/web",
              number: 8,
              title: "fix: simplify setup copy",
              url: "https://github.com/your-org/web/pull/8",
              author: "github-actions",
              kind: "pr",
              timestamp: "2026-06-02T10:30:00Z",
              age_days: 0,
              is_draft: false,
              labels: ["agent:large-feature"],
              agent_evidence: ["label:agent:large-feature"],
            },
          ],
        },
        counts: { queued: 0, in_progress: 0, shipped: 2 },
      }),
    });
    // Shipped is its own in-page lane now; open it first.
    await userEvent.setup().click(screen.getByRole("tab", { name: /shipped/i }));
    // Conventional-commit prefix stripped; reads as an outcome.
    expect(screen.getByText("Add CSV export.")).toBeInTheDocument();
    expect(screen.getByText("Simplify setup copy.")).toBeInTheDocument();
    expect(screen.getAllByText("Lucius").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Batman").length).toBeGreaterThan(0);
    expect(screen.getByText(/merged into api/i)).toBeInTheDocument();
    // Agent shown as a badge (asserted above), sentence no longer repeats it.
    expect(screen.getByText(/shipped and merged into web/i)).toBeInTheDocument();
  });

  it("re-skins the Shipped lane agent badges under the active roster theme", async () => {
    // A Lucius-authored shipped PR must read as the Justice-League persona
    // ("Superman") in the Shipped lane too, not the hardcoded Batman-cast name.
    renderReview({
      rosterTheme: "justice-league",
      shipped: board({
        columns: {
          queued: [],
          in_progress: [],
          shipped: [
            {
              repo: "your-org/api",
              number: 7,
              title: "feat: add CSV export",
              url: "https://github.com/your-org/api/pull/7",
              author: "lucius",
              kind: "pr",
              timestamp: "2026-06-02T11:00:00Z",
              age_days: 0,
              is_draft: false,
              labels: [],
            },
          ],
        },
        counts: { queued: 0, in_progress: 0, shipped: 1 },
      }),
    });
    await userEvent.setup().click(screen.getByRole("tab", { name: /shipped/i }));
    // Scope to the Shipped lane; the roles rail also re-skins lucius to Superman.
    const lane = within(screen.getByRole("region", { name: "Shipped" }));
    expect(lane.getByText("Superman")).toBeInTheDocument();
    expect(lane.queryByText("Lucius")).not.toBeInTheDocument();
  });

  it("keeps the runtime field for the un-overridden part of a partial custom theme", () => {
    // Operator customizes only Lucius's role, not the name. The name must keep
    // the runtime/server value ("Lux Fox"); only the role takes the custom label.
    const { container } = renderReview({
      rosterTheme: "custom",
      customNames: { names: {}, roles: { lucius: "Build lead" } },
      snapshot: snapshot({
        status: {
          agents: [
            agent({
              codename: "lucius",
              display_name: "Lux Fox",
              role_title: "Senior Developer",
              purpose: "Ships scoped implementation issues as pull requests.",
            }),
          ],
          total_today: 1,
          reliability: { status: "ok" },
        },
      }),
    });
    const panel = container.querySelector(".command-center__agent-route");
    const scope = within(panel as HTMLElement);
    // Name un-overridden: keeps the runtime display name.
    expect(scope.getByText("Lux Fox")).toBeInTheDocument();
    // Role overridden: takes the custom label, not the runtime role title.
    expect(scope.getByText("Build lead")).toBeInTheDocument();
    expect(scope.queryByText("Senior Developer")).not.toBeInTheDocument();
  });

  it("keeps the runtime role for a name-only custom override", () => {
    // Mirror case: only the name is customized, so the role must keep the
    // runtime/server value instead of being overwritten.
    const { container } = renderReview({
      rosterTheme: "custom",
      customNames: { names: { lucius: "Lux Fox" }, roles: {} },
      snapshot: snapshot({
        status: {
          agents: [
            agent({
              codename: "lucius",
              display_name: "Lucius",
              role_title: "Senior Developer",
              purpose: "Ships scoped implementation issues as pull requests.",
            }),
          ],
          total_today: 1,
          reliability: { status: "ok" },
        },
      }),
    });
    const panel = container.querySelector(".command-center__agent-route");
    const scope = within(panel as HTMLElement);
    expect(scope.getByText("Lux Fox")).toBeInTheDocument();
    // Role un-overridden: keeps the runtime role title.
    expect(scope.getByText("Senior Developer")).toBeInTheDocument();
  });

  it("re-skins the Agent roles panel under the active roster theme", () => {
    // The server reports the Batman-default display name/role for the fleet.
    // A Justice-League theme must re-skin the panel (Superman / Senior developer),
    // not surface the raw "Lucius" server label.
    const { container } = renderReview({
      rosterTheme: "justice-league",
      snapshot: snapshot({
        status: {
          agents: [
            agent({
              codename: "lucius",
              display_name: "Lucius",
              role_title: "Senior Developer",
              purpose: "Ships scoped implementation issues as pull requests.",
            }),
          ],
          total_today: 1,
          reliability: { status: "ok" },
        },
      }),
    });
    const panel = container.querySelector(".command-center__agent-route");
    expect(panel).not.toBeNull();
    const scope = within(panel as HTMLElement);
    expect(scope.getByText("Superman")).toBeInTheDocument();
    expect(scope.queryByText("Lucius")).not.toBeInTheDocument();
  });

  it("re-skins the fallback Agent roles panel when the server reports no agents", () => {
    const { container } = renderReview({
      rosterTheme: "justice-league",
      snapshot: snapshot({
        status: { agents: [], total_today: 0, reliability: { status: "ok" } },
      }),
    });
    const panel = container.querySelector(".command-center__agent-route");
    const scope = within(panel as HTMLElement);
    // Fallback seeds (batman / lucius / drake) re-skinned to the JL personas.
    expect(scope.getByText("Batman")).toBeInTheDocument();
    expect(scope.getByText("Superman")).toBeInTheDocument();
    expect(scope.getByText("Green Arrow")).toBeInTheDocument();
    expect(scope.queryByText("Lucius")).not.toBeInTheDocument();
    expect(scope.queryByText("Drake")).not.toBeInTheDocument();
  });

  it("keeps the Batman roster on the Agent roles panel when no theme is set", () => {
    const { container } = renderReview({
      snapshot: snapshot({
        status: { agents: [], total_today: 0, reliability: { status: "ok" } },
      }),
    });
    const panel = container.querySelector(".command-center__agent-route");
    const scope = within(panel as HTMLElement);
    expect(scope.getByText("Batman")).toBeInTheDocument();
    expect(scope.getByText("Lucius")).toBeInTheDocument();
    expect(scope.getByText("Drake")).toBeInTheDocument();
  });

  it("opens Running when queued board work exists alongside shipped proof", () => {
    renderReview({
      shipped: board({
        columns: {
          queued: [
            {
              repo: "your-org/api",
              number: 12,
              title: "Add billing report",
              url: "https://github.com/your-org/api/issues/12",
              author: "batman",
              kind: "issue",
              timestamp: "2026-06-02T11:45:00Z",
              age_days: 0,
              is_draft: false,
              labels: [],
            },
          ],
          in_progress: [],
          shipped: [
            {
              repo: "your-org/api",
              number: 7,
              title: "feat: add CSV export",
              url: "https://github.com/your-org/api/pull/7",
              author: "lucius",
              kind: "pr",
              timestamp: "2026-06-02T11:00:00Z",
              age_days: 0,
              is_draft: false,
              labels: [],
            },
          ],
        },
        counts: { queued: 1, in_progress: 0, shipped: 1 },
      }),
    });

    expect(screen.getByRole("tab", { name: /running/i })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("Alfred is working")).toBeInTheDocument();
    expect(screen.getByText(/1 request is queued or building/i)).toBeInTheDocument();
    expect(screen.getByText("Add billing report")).toBeInTheDocument();
    expect(screen.queryByText("Alfred is clear")).not.toBeInTheDocument();
  });

  it("counts active firings and active board threads together", () => {
    renderReview({
      snapshot: snapshot({ firings: [firing()] }),
      shipped: board({
        columns: {
          queued: [
            {
              repo: "your-org/api",
              number: 12,
              title: "Add billing report",
              url: "https://github.com/your-org/api/issues/12",
              author: "batman",
              kind: "issue",
              timestamp: "2026-06-02T11:45:00Z",
              age_days: 0,
              is_draft: false,
              labels: [],
            },
          ],
          in_progress: [],
          shipped: [],
        },
        counts: { queued: 1, in_progress: 0, shipped: 0 },
      }),
    });

    expect(screen.getByLabelText("2 new")).toBeInTheDocument();
    expect(screen.getByText(/1 run is active now/i)).toBeInTheDocument();
    expect(screen.getByText(/1 request is queued or building/i)).toBeInTheDocument();
  });

  it("uses uncapped board counts for live-work chrome", () => {
    const queued = Array.from({ length: 8 }, (_, index) => ({
      repo: "your-org/api",
      number: index + 1,
      title: `Queued request ${index + 1}`,
      url: `https://github.com/your-org/api/issues/${index + 1}`,
      author: "batman",
      kind: "issue",
      timestamp: "2026-06-02T11:45:00Z",
      age_days: 0,
      is_draft: false,
      labels: [],
    }));
    renderReview({
      shipped: board({
        columns: { queued, in_progress: [], shipped: [] },
        counts: { queued: 8, in_progress: 0, shipped: 0 },
      }),
    });

    expect(screen.getByLabelText("8 new")).toBeInTheDocument();
    expect(screen.getByText(/8 requests are queued or building/i)).toBeInTheDocument();
    const workingCard = screen.getByText("Working now").closest("article");
    expect(workingCard).not.toBeNull();
    expect(within(workingCard as HTMLElement).getByText("8")).toBeInTheDocument();
    expect(within(workingCard as HTMLElement).getByText(/follow live work/i)).toBeInTheDocument();
  });

  it("leads with the pause when the whole fleet is on hold, not queued/building activity", () => {
    // Every reported agent is paused: the board still lists queued work, but the
    // fleet cannot be actively working it, so the Inbox must say so and must not
    // claim the requests are "queued or building".
    const queued = Array.from({ length: 25 }, (_, index) => ({
      repo: "your-org/api",
      number: index + 1,
      title: `Queued request ${index + 1}`,
      url: `https://github.com/your-org/api/issues/${index + 1}`,
      author: "batman",
      kind: "issue",
      timestamp: "2026-06-02T11:45:00Z",
      age_days: 0,
      is_draft: false,
      labels: [],
    }));
    renderReview({
      snapshot: snapshot({
        status: {
          agents: [
            agent({ codename: "batman", paused: true, paused_since: "2026-06-01T00:00:00Z" }),
            agent({ codename: "lucius", paused: true, paused_since: "2026-06-01T00:00:00Z" }),
            agent({ codename: "drake", paused: true, paused_since: "2026-06-01T00:00:00Z" }),
          ],
          total_today: 0,
          reliability: { status: "ok" },
        },
      }),
      shipped: board({
        columns: { queued, in_progress: [], shipped: [] },
        counts: { queued: 25, in_progress: 0, shipped: 0 },
      }),
    });

    expect(screen.getByText("Fleet is paused")).toBeInTheDocument();
    // Honest lead line: paused + waiting, never "queued or building".
    expect(screen.getByText(/Fleet paused - all 3 agents on hold; 25 requests are waiting/i)).toBeInTheDocument();
    expect(screen.queryByText(/queued or building/i)).not.toBeInTheDocument();
    // "Working now" must read 0 while paused, and say the work is waiting, not running.
    const workingCard = screen.getByText("Working now").closest("article");
    expect(within(workingCard as HTMLElement).getByText("0")).toBeInTheDocument();
    expect(within(workingCard as HTMLElement).getByText(/paused/i)).toBeInTheDocument();
  });

  it("counts only genuinely running firings as Working now while paused (no stale board work)", () => {
    // A stale board card from a paused fleet must not inflate Working now. With
    // no live firing, Working now is 0 even though the board lists in-flight work.
    renderReview({
      snapshot: snapshot({
        status: {
          agents: [
            agent({ codename: "batman", paused: true }),
            agent({ codename: "lucius", paused: true }),
          ],
          total_today: 0,
          reliability: { status: "ok" },
        },
      }),
      shipped: board({
        columns: {
          queued: [],
          in_progress: [
            {
              repo: "your-org/api",
              number: 5,
              title: "Stale in-flight PR",
              url: "https://github.com/your-org/api/pull/5",
              author: "lucius",
              kind: "pr",
              timestamp: "2026-06-01T10:00:00Z",
              age_days: 1,
              is_draft: false,
              labels: ["agent:in-flight"],
            },
          ],
          shipped: [],
        },
        counts: { queued: 0, in_progress: 1, shipped: 0 },
      }),
    });

    const workingCard = screen.getByText("Working now").closest("article");
    expect(within(workingCard as HTMLElement).getByText("0")).toBeInTheDocument();
  });

  it("keeps the normal activity summary when only a minority of agents are paused", () => {
    // One of three paused is not "mostly paused": the fleet is still working, so
    // the honest summary keeps the queued/building line.
    renderReview({
      snapshot: snapshot({
        status: {
          agents: [
            agent({ codename: "batman", paused: false }),
            agent({ codename: "lucius", paused: false }),
            agent({ codename: "drake", paused: true }),
          ],
          total_today: 0,
          reliability: { status: "ok" },
        },
      }),
      shipped: board({
        columns: {
          queued: [
            {
              repo: "your-org/api",
              number: 12,
              title: "Add billing report",
              url: "https://github.com/your-org/api/issues/12",
              author: "batman",
              kind: "issue",
              timestamp: "2026-06-02T11:45:00Z",
              age_days: 0,
              is_draft: false,
              labels: [],
            },
          ],
          in_progress: [],
          shipped: [],
        },
        counts: { queued: 1, in_progress: 0, shipped: 0 },
      }),
    });

    expect(screen.queryByText(/Fleet is paused/i)).not.toBeInTheDocument();
    expect(screen.getByText(/1 request is queued or building/i)).toBeInTheDocument();
  });

  it("shows compact engine headroom without exposing token totals or dollars", () => {
    renderReview();
    const rail = screen.getByRole("region", { name: /engine capacity/i });
    // The Inbox rail is the compact headroom view: Claude 5h + weekly windows,
    // never a raw token wall or dollar figure.
    expect(within(rail).getByText(/5h window/i)).toBeInTheDocument();
    expect(within(rail).getByText(/weekly/i)).toBeInTheDocument();
    expect(rail.textContent).not.toMatch(/142/);
    expect(rail.textContent).not.toMatch(/\$/);
  });

  it("shows a compact usage-unavailable state when local usage cannot be read", () => {
    renderReview({
      usage: usage({ available: false, block: null, codex: null, error: "usage logs unavailable" }),
      usageState: "error",
    });
    const rail = screen.getByRole("region", { name: /engine capacity/i });
    // Honest degraded state: the panel says usage is unavailable rather than
    // inventing headroom, and the rest of the Review surface still renders.
    expect(within(rail).getByText(/usage unavailable/i)).toBeInTheDocument();
    expect(rail).toBeInTheDocument();
  });

  it("falls back the hero CTA to Open Work when nothing needs the user", async () => {
    // The redundant Ask card is gone (Ask lives in the sidebar). With no
    // decisions waiting, the hero's single CTA opens Work.
    const onSwitch = vi.fn();
    renderReview({ onSwitch, needsYou: [] });
    const user = userEvent.setup();
    expect(
      screen.queryByRole("button", { name: /ask alfred/i }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /open work/i }));
    expect(onSwitch).toHaveBeenCalledWith("pipeline");
  });

  it("points the hero CTA at the waiting decisions when some exist", async () => {
    // When decisions are waiting the hero CTA becomes the top decision and
    // switches the inbox to the Needs lane in place (no navigation away).
    const onSwitch = vi.fn();
    renderReview({
      onSwitch,
      needsYou: [
        {
          id: "a1",
          label: "Plan",
          title: "Approve the export plan",
          detail: "Batman needs a go-ahead.",
          tone: "info",
          icon: "plan",
          targetTab: "pipeline",
        },
      ],
    });
    const user = userEvent.setup();
    const cta = screen.getByRole("button", { name: /review the 1 waiting/i });
    await user.click(cta);
    // The lane CTA does not navigate; it pins the Needs lane within Inbox.
    expect(onSwitch).not.toHaveBeenCalled();
  });

  it("approves a waiting Batman plan in-place from the Needs-you card", async () => {
    const onPlanDecision = vi.fn();
    const plan = {
      plan_id: "13-plan",
      title: "Approve the export plan",
      status: "draft",
      parent: null,
      affected_repos: null,
      updated_at: null,
      path: "",
      preview: "",
      content: "",
      source: "batman",
      readiness_score: null,
      readiness_ok: null,
      revision_count: 0,
    };
    renderReview({
      snapshot: snapshot({ plans: [plan] }),
      needsYou: [{ ...needsYouItem, id: "plan-13-plan", planId: "13-plan" }],
      onPlanDecision,
    });

    expect(screen.getByRole("button", { name: /^approve/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^decline/i })).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: /^approve/i }));
    expect(onPlanDecision).toHaveBeenCalledWith(
      expect.objectContaining({ plan_id: "13-plan" }),
      "approve",
    );
  });
});
