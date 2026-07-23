import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FleetControlView } from "./FleetControlView";
import { parseFleetServiceState } from "../lib/fleetControl";
import type {
  AgentSummary,
  NativeCommandResult,
  SaveAgentModelResponse,
  ScheduledRun,
} from "../types";

const agentApiMocks = vi.hoisted(() => ({
  loadAgentModels: vi.fn(),
  saveAgentModel: vi.fn(),
}));

vi.mock("../api/agents", () => agentApiMocks);

// Render in the desktop-capable mode so the control buttons appear.
vi.mock("../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/client")>()),
  supportsNativeActions: () => true,
}));

function statusResult(stdout: string): NativeCommandResult {
  return {
    command: ["alfred", "status", "--json"],
    stdout,
    stderr: "",
    status: 0,
    success: true,
    pid: null,
    message: null,
  };
}

function agent(codename: string, overrides: Partial<AgentSummary> = {}): AgentSummary {
  return {
    codename,
    last_firing_id: null,
    last_run_at: "2026-05-30T10:00:00Z",
    status: "live",
    last_summary: "ok",
    firings_today: 1,
    ...overrides,
  };
}

const SERVICE = parseFleetServiceState(
  statusResult(
    JSON.stringify({
      agents: [
        { agent: "senior-dev", loaded: true, paused: false, paused_since: null },
        {
          agent: "test-engineer",
          loaded: false,
          paused: true,
          paused_since: "2026-05-30T09:00:00Z",
        },
      ],
    }),
  ),
);

const SCHEDULE: ScheduledRun[] = [
  {
    codename: "senior-dev",
    role: "Engineer",
    kind: "interval",
    cadence: "every 10m",
    next_fire_at: null,
    raw_schedule: "interval:600",
  },
  {
    codename: "test-engineer",
    role: "Test coverage",
    kind: "cron-daily",
    cadence: "daily at 08:00",
    next_fire_at: "2026-06-08T08:00:00+02:00",
    raw_schedule: "cron:8:00",
  },
];

const MODELS = {
  agents: ["senior-dev", "test-engineer"].map((codename) => ({
    agent: codename,
    claude: { resolved: null, persisted: null, source: "provider-default" as const },
    codex: { resolved: null, persisted: null, source: "provider-default" as const },
  })),
  count: 2,
};

function renderView(onRunLocalAction = vi.fn(), schedule: ScheduledRun[] = SCHEDULE) {
  render(
    <FleetControlView
      baseUrl="http://127.0.0.1:7010"
      modelRefreshVersion={1}
      agents={[agent("senior-dev"), agent("test-engineer")]}
      schedule={schedule}
      service={SERVICE}
      nativeBusy={null}
      onRunLocalAction={onRunLocalAction}
      onViewLogs={vi.fn()}
    />,
  );
  return onRunLocalAction;
}

// The agent detail is a slide-over drawer that opens on select. The workflow
// graph renders no measurable nodes in jsdom, so we open the drawer through the
// list view (its rows are real buttons) to reach the inspector controls.
async function openDrawer(
  user: ReturnType<typeof userEvent.setup>,
  codename: string,
) {
  await user.click(screen.getByRole("button", { name: /list view/i }));
  await user.click(screen.getByRole("button", { name: new RegExp(`select ${codename}`, "i") }));
}

describe("FleetControlView", () => {
  beforeEach(() => {
    agentApiMocks.loadAgentModels.mockReset();
    agentApiMocks.saveAgentModel.mockReset();
    agentApiMocks.loadAgentModels.mockResolvedValue(MODELS);
    // Roster view mode persists in localStorage; reset so each test starts on
    // the workflow default regardless of order.
    try {
      window.localStorage.clear();
    } catch {
      // jsdom without storage: nothing to reset.
    }
  });

  it("defaults to the workflow view and toggles to the dense list", async () => {
    renderView();
    const user = userEvent.setup();
    const workflow = screen.getByRole("button", { name: /workflow view/i });
    const list = screen.getByRole("button", { name: /list view/i });
    expect(workflow).toHaveAttribute("aria-pressed", "true");
    expect(list).toHaveAttribute("aria-pressed", "false");

    await user.click(list);
    expect(list).toHaveAttribute("aria-pressed", "true");
    expect(workflow).toHaveAttribute("aria-pressed", "false");
    // Selecting an agent works in the list view.
    await user.click(screen.getByRole("button", { name: /select bane/i }));
    expect(screen.getByRole("button", { name: /^Resume$/i })).toBeInTheDocument();
  });

  it("shows selected-agent controls and switches to a paused agent", async () => {
    renderView();
    const user = userEvent.setup();

    // Nothing is open until an agent is selected, so no controls show yet.
    expect(screen.queryByRole("button", { name: /^Pause$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Resume$/i })).not.toBeInTheDocument();

    // Open the running agent's drawer: its Pause control shows, Resume does not.
    await openDrawer(user, "lucius");
    expect(screen.getByRole("button", { name: /^Pause$/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Resume$/i })).not.toBeInTheDocument();

    // Select the paused agent: the drawer swaps to show Resume.
    await user.click(screen.getByRole("button", { name: /select bane/i }));
    expect(screen.getByRole("button", { name: /^Resume$/i })).toBeInTheDocument();
    // The list row and the drawer both surface the paused state.
    expect(screen.getAllByText(/paused since/i).length).toBeGreaterThan(0);
  });

  it("defaults selection to an llm-error agent before a running agent", async () => {
    render(
      <FleetControlView
        baseUrl="http://127.0.0.1:7010"
        modelRefreshVersion={1}
        agents={[
          agent("senior-dev", { status: "live" }),
          agent("test-engineer", { status: "llm-error" }),
        ]}
        schedule={SCHEDULE}
        service={SERVICE}
        nativeBusy={null}
        onRunLocalAction={vi.fn()}
        onViewLogs={vi.fn()}
      />,
    );
    const user = userEvent.setup();

    // The errored, paused agent is the default selection: its list row is the
    // one marked current, ahead of the live agent.
    await user.click(screen.getByRole("button", { name: /list view/i }));
    expect(screen.getByRole("button", { name: /select bane/i })).toHaveAttribute(
      "aria-current",
      "true",
    );
  });

  it("reads paused state from the polled summary without a CLI service map", async () => {
    render(
      <FleetControlView
        baseUrl="http://127.0.0.1:7010"
        modelRefreshVersion={1}
        agents={[
          agent("senior-dev", { paused: false, loaded: true }),
          agent("test-engineer", {
            paused: true,
            loaded: false,
            paused_since: "2026-05-30T09:00:00Z",
          }),
        ]}
        schedule={[]}
        service={{}}
        nativeBusy={null}
        onRunLocalAction={vi.fn()}
        onViewLogs={vi.fn()}
      />,
    );
    const user = userEvent.setup();

    await openDrawer(user, "lucius");
    expect(screen.getByRole("button", { name: /^Pause$/i })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /select bane/i }));
    expect(screen.getByRole("button", { name: /^Resume$/i })).toBeInTheDocument();
    // The list row and the drawer both surface the paused state.
    expect(screen.getAllByText(/paused since/i).length).toBeGreaterThan(0);
  });

  it("renders the human agent role and purpose above the runtime codename", async () => {
    render(
      <FleetControlView
        baseUrl="http://127.0.0.1:7010"
        modelRefreshVersion={1}
        agents={[
          agent("senior-dev", {
            display_name: "Lucius",
            role_title: "Senior Developer",
            purpose: "Ships scoped implementation issues as pull requests.",
          }),
        ]}
        schedule={[]}
        service={{}}
        nativeBusy={null}
        onRunLocalAction={vi.fn()}
        onViewLogs={vi.fn()}
      />,
    );
    const user = userEvent.setup();
    await openDrawer(user, "lucius");

    // Name and the plain role label render as separate elements now (the role
    // is shown explicitly, independent of the themed name), in both the list
    // row and the drawer.
    expect(screen.getAllByText("Lucius").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Senior Developer").length).toBeGreaterThan(0);
    expect(
      screen.getAllByText("Ships scoped implementation issues as pull requests.").length,
    ).toBeGreaterThan(0);
    expect(screen.getByTitle("Runtime codename: senior-dev")).toHaveTextContent("senior-dev");
  });

  it("runs dry-run immediately without confirmation", async () => {
    const onRun = renderView();
    const user = userEvent.setup();
    await openDrawer(user, "lucius");
    await user.click(screen.getAllByRole("button", { name: /Dry-run/i })[0]);
    expect(onRun).toHaveBeenCalledWith(
      expect.objectContaining({ action: "dry_run", refreshAfter: true }),
    );
  });

  it("sets an agent schedule from a cadence menu", async () => {
    const onRun = renderView();
    const user = userEvent.setup();
    await openDrawer(user, "lucius");

    await user.click(screen.getByRole("combobox", { name: /schedule senior-dev/i }));
    await user.click(screen.getByRole("option", { name: /every 20 min/i }));
    await user.click(screen.getByRole("button", { name: /set senior-dev schedule/i }));

    expect(onRun).toHaveBeenCalledWith(
      expect.objectContaining({
        action: "schedule",
        target: "senior-dev",
        cadence: "20m",
        refreshAfter: true,
      }),
    );
  });

  it("requires confirmation before a state-changing pause", async () => {
    const onRun = renderView();
    const user = userEvent.setup();
    await openDrawer(user, "lucius");

    await user.click(screen.getByRole("button", { name: /^Pause$/i }));
    // Nothing dispatched yet; a confirm dialog appears instead.
    expect(onRun).not.toHaveBeenCalled();
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /yes, pause/i }));
    expect(onRun).toHaveBeenCalledWith(
      expect.objectContaining({ action: "pause", target: "senior-dev", refreshAfter: true }),
    );
  });

  it("cancels a pending action without dispatching", async () => {
    const onRun = renderView();
    const user = userEvent.setup();
    await openDrawer(user, "lucius");

    await user.click(screen.getByRole("button", { name: /^Pause$/i }));
    await user.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onRun).not.toHaveBeenCalled();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("moves focus to the affirmative button and closes on Escape", async () => {
    const onRun = renderView();
    const user = userEvent.setup();
    await openDrawer(user, "lucius");

    await user.click(screen.getByRole("button", { name: /^Pause$/i }));
    // Focus lands on the destructive affirmative so the confirm is keyboard-ready.
    const affirm = screen.getByRole("button", { name: /yes, pause/i });
    expect(affirm).toHaveFocus();

    // Escape cancels without dispatching.
    await user.keyboard("{Escape}");
    expect(onRun).not.toHaveBeenCalled();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("deep-links to that agent's logs from its card", async () => {
    const onViewLogs = vi.fn();
    render(
      <FleetControlView
        baseUrl="http://127.0.0.1:7010"
        modelRefreshVersion={1}
        agents={[agent("senior-dev")]}
        schedule={[]}
        service={{}}
        nativeBusy={null}
        onRunLocalAction={vi.fn()}
        onViewLogs={onViewLogs}
      />,
    );
    const user = userEvent.setup();
    await openDrawer(user, "lucius");
    await user.click(screen.getByRole("button", { name: /^Logs$/i }));
    expect(onViewLogs).toHaveBeenCalledWith("senior-dev");
  });

  it("saves a per-agent provider model from the detail drawer", async () => {
    agentApiMocks.saveAgentModel.mockResolvedValue({
      ok: true,
      agent: "senior-dev",
      provider: "claude",
      selection: { resolved: "opus", persisted: "opus", source: "state" },
    });
    renderView();
    const user = userEvent.setup();
    await openDrawer(user, "lucius");

    await waitFor(() => expect(agentApiMocks.loadAgentModels).toHaveBeenCalled());
    await user.type(screen.getByLabelText("Claude"), "opus");
    await user.click(
      screen.getByRole("button", { name: /save claude model for senior-dev/i }),
    );

    expect(agentApiMocks.saveAgentModel).toHaveBeenCalledWith(
      "http://127.0.0.1:7010",
      "senior-dev",
      "claude",
      "opus",
    );
    await waitFor(() => expect(screen.getByText("Active: opus")).toBeInTheDocument());
  });

  it("ignores a save response from a previously selected runtime", async () => {
    let resolveSave!: (value: SaveAgentModelResponse) => void;
    agentApiMocks.saveAgentModel.mockReturnValue(
      new Promise((resolve) => {
        resolveSave = resolve;
      }),
    );
    agentApiMocks.loadAgentModels
      .mockResolvedValueOnce(MODELS)
      .mockResolvedValueOnce({
        agents: [
          {
            agent: "senior-dev",
            claude: { resolved: "runtime-b", persisted: "runtime-b", source: "state" },
            codex: { resolved: null, persisted: null, source: "provider-default" },
          },
        ],
        count: 1,
      });

    const props = {
      modelRefreshVersion: 1,
      agents: [agent("senior-dev")],
      schedule: SCHEDULE,
      service: SERVICE,
      nativeBusy: null,
      onRunLocalAction: vi.fn(),
      onViewLogs: vi.fn(),
    };
    const { rerender } = render(
      <FleetControlView baseUrl="http://runtime-a" {...props} />,
    );
    const user = userEvent.setup();
    await openDrawer(user, "lucius");
    await user.type(screen.getByLabelText("Claude"), "runtime-a");
    await user.click(
      screen.getByRole("button", { name: /save claude model for senior-dev/i }),
    );

    rerender(<FleetControlView baseUrl="http://runtime-b" {...props} />);
    await waitFor(() => expect(screen.getByText("Active: runtime-b")).toBeInTheDocument());

    await act(async () => {
      resolveSave({
        ok: true,
        agent: "senior-dev",
        provider: "claude",
        selection: { resolved: "runtime-a", persisted: "runtime-a", source: "state" },
      });
      await Promise.resolve();
    });
    expect(screen.getByText("Active: runtime-b")).toBeInTheDocument();
    expect(screen.queryByText("Active: runtime-a")).not.toBeInTheDocument();
  });

  it("does not let an older inventory refresh overwrite a completed save", async () => {
    let resolveSave!: (value: SaveAgentModelResponse) => void;
    let resolveRefresh!: (value: typeof MODELS) => void;
    agentApiMocks.saveAgentModel.mockReturnValue(
      new Promise((resolve) => {
        resolveSave = resolve;
      }),
    );
    agentApiMocks.loadAgentModels
      .mockResolvedValueOnce(MODELS)
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolveRefresh = resolve;
        }),
      );

    const props = {
      baseUrl: "http://runtime-a",
      agents: [agent("senior-dev")],
      schedule: SCHEDULE,
      service: SERVICE,
      nativeBusy: null,
      onRunLocalAction: vi.fn(),
      onViewLogs: vi.fn(),
    };
    const { rerender } = render(
      <FleetControlView modelRefreshVersion={1} {...props} />,
    );
    const user = userEvent.setup();
    await openDrawer(user, "lucius");
    await waitFor(() => expect(agentApiMocks.loadAgentModels).toHaveBeenCalledTimes(1));
    await user.type(screen.getByLabelText("Claude"), "new-model");
    await user.click(
      screen.getByRole("button", { name: /save claude model for senior-dev/i }),
    );

    rerender(<FleetControlView modelRefreshVersion={2} {...props} />);
    await waitFor(() => expect(agentApiMocks.loadAgentModels).toHaveBeenCalledTimes(2));

    await act(async () => {
      resolveSave({
        ok: true,
        agent: "senior-dev",
        provider: "claude",
        selection: { resolved: "new-model", persisted: "new-model", source: "state" },
      });
      await Promise.resolve();
    });
    expect(await screen.findByText("Active: new-model")).toBeInTheDocument();

    await act(async () => {
      resolveRefresh(MODELS);
      await Promise.resolve();
    });
    expect(screen.getByText("Active: new-model")).toBeInTheDocument();
  });

  it("does not carry an unsaved model draft to another agent", async () => {
    renderView();
    const user = userEvent.setup();
    await openDrawer(user, "lucius");
    await waitFor(() => expect(agentApiMocks.loadAgentModels).toHaveBeenCalled());
    await user.type(screen.getByLabelText("Claude"), "unsaved-lucius-model");

    await user.click(screen.getByRole("button", { name: /select bane/i }));

    expect(screen.getByLabelText("Claude")).toHaveValue("");
    expect(
      screen.getByRole("button", { name: /save claude model for test-engineer/i }),
    ).toBeDisabled();
  });

  it("hides model controls for an agent absent from engine inventory", async () => {
    agentApiMocks.loadAgentModels.mockResolvedValue({
      agents: [MODELS.agents[0]],
      count: 1,
    });
    renderView();
    const user = userEvent.setup();
    await openDrawer(user, "lucius");
    await waitFor(() => expect(screen.getByLabelText("Claude")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: /select bane/i }));

    expect(screen.queryByRole("heading", { name: "Models" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Claude")).not.toBeInTheDocument();
  });

  it("clears model inventory when the replacement runtime cannot load", async () => {
    agentApiMocks.loadAgentModels
      .mockResolvedValueOnce({
        agents: [
          {
            agent: "senior-dev",
            claude: { resolved: "runtime-a", persisted: "runtime-a", source: "state" },
            codex: { resolved: "codex-a", persisted: "codex-a", source: "state" },
          },
        ],
        count: 1,
      })
      .mockRejectedValueOnce(new Error("runtime B inventory unavailable"));

    const props = {
      modelRefreshVersion: 1,
      agents: [agent("senior-dev")],
      schedule: SCHEDULE,
      service: SERVICE,
      nativeBusy: null,
      onRunLocalAction: vi.fn(),
      onViewLogs: vi.fn(),
    };
    const { rerender } = render(
      <FleetControlView baseUrl="http://runtime-a" {...props} />,
    );
    const user = userEvent.setup();
    await openDrawer(user, "lucius");
    await waitFor(() => expect(screen.getByText("Active: runtime-a")).toBeInTheDocument());
    expect(screen.getByText("Active: codex-a")).toBeInTheDocument();

    rerender(<FleetControlView baseUrl="http://runtime-b" {...props} />);

    expect(screen.queryByText("Active: runtime-a")).not.toBeInTheDocument();
    expect(screen.queryByText("Active: codex-a")).not.toBeInTheDocument();
    expect(await screen.findByText("runtime B inventory unavailable")).toBeInTheDocument();
    expect(screen.queryByText("Active: runtime-a")).not.toBeInTheDocument();
    expect(screen.queryByText("Active: codex-a")).not.toBeInTheDocument();
  });

  it("reloads model sources after a same-runtime snapshot refresh", async () => {
    agentApiMocks.loadAgentModels
      .mockResolvedValueOnce({
        agents: [
          {
            agent: "senior-dev",
            claude: { resolved: "runtime-a", persisted: "runtime-a", source: "state" },
            codex: { resolved: null, persisted: null, source: "provider-default" },
          },
        ],
        count: 1,
      })
      .mockResolvedValueOnce({
        agents: [
          {
            agent: "senior-dev",
            claude: {
              resolved: "fleet-opus",
              persisted: "runtime-a",
              source: "fleet-environment",
            },
            codex: { resolved: null, persisted: null, source: "provider-default" },
          },
        ],
        count: 1,
      });

    const props = {
      baseUrl: "http://runtime-a",
      agents: [agent("senior-dev")],
      schedule: SCHEDULE,
      service: SERVICE,
      nativeBusy: null,
      onRunLocalAction: vi.fn(),
      onViewLogs: vi.fn(),
    };
    const { rerender } = render(
      <FleetControlView modelRefreshVersion={1} {...props} />,
    );
    const user = userEvent.setup();
    await openDrawer(user, "lucius");
    await waitFor(() => expect(screen.getByText("Active: runtime-a")).toBeInTheDocument());

    rerender(<FleetControlView modelRefreshVersion={2} {...props} />);

    await waitFor(() => expect(agentApiMocks.loadAgentModels).toHaveBeenCalledTimes(2));
    expect(
      await screen.findByText("Fleet override: fleet-opus. Saved: runtime-a"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Active: runtime-a")).not.toBeInTheDocument();
  });

  it("clears a model load error after a successful same-runtime refresh", async () => {
    agentApiMocks.loadAgentModels
      .mockRejectedValueOnce(new Error("temporary model inventory failure"))
      .mockResolvedValueOnce({
        agents: [
          {
            agent: "senior-dev",
            claude: { resolved: "recovered", persisted: "recovered", source: "state" },
            codex: { resolved: null, persisted: null, source: "provider-default" },
          },
        ],
        count: 1,
      });

    const props = {
      baseUrl: "http://runtime-a",
      agents: [agent("senior-dev")],
      schedule: SCHEDULE,
      service: SERVICE,
      nativeBusy: null,
      onRunLocalAction: vi.fn(),
      onViewLogs: vi.fn(),
    };
    const { rerender } = render(
      <FleetControlView modelRefreshVersion={1} {...props} />,
    );
    const user = userEvent.setup();
    await openDrawer(user, "lucius");
    expect(await screen.findByText("temporary model inventory failure")).toBeInTheDocument();

    rerender(<FleetControlView modelRefreshVersion={2} {...props} />);

    expect(await screen.findByText("Active: recovered")).toBeInTheDocument();
    expect(screen.queryByText("temporary model inventory failure")).not.toBeInTheDocument();
  });
});
