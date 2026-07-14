import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SetupStatus, Snapshot } from "./types";

type AppMenuHandlers = {
  onNavigate: (destination: string) => void;
  onCommandPalette: () => void;
  onRefresh: () => void;
};

const appMenuMocks = vi.hoisted(() => ({
  handlers: null as AppMenuHandlers | null,
  unlisten: vi.fn(),
}));

vi.mock("./lib/appMenuEvents", () => ({
  listenAppMenuEvents: (handlers: AppMenuHandlers) => {
    appMenuMocks.handlers = handlers;
    return Promise.resolve(appMenuMocks.unlisten);
  },
}));

// Mock the api module so App's named `loadSetupStatus` import binds to a mock we
// control (a plain vi.spyOn would miss the binding App captured at import time,
// since the App module is cached across the dynamic imports below).
const loadSetupStatusMock = vi.fn<() => Promise<SetupStatus>>();
vi.mock("./api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api/client")>()),
  supportsNativeActions: () => false,
}));
vi.mock("./api/setup", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api/setup")>()),
  loadSetupStatus: (...args: unknown[]) => loadSetupStatusMock(...(args as [])),
}));

// Stub the heavy screen components down to identifiable markers so the test can
// assert which screen the initial route lands on without rendering the whole
// app tree. AppShell just renders its children.
vi.mock("./components/layout/AppShell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="app-shell">{children}</div>
  ),
}));
vi.mock("./components/ReviewView", () => ({
  ReviewView: () => <div data-testid="inbox-screen">Inbox</div>,
}));
vi.mock("./components/OnboardingView", () => ({
  OnboardingView: ({ onFinish }: { onFinish: (destination: "home" | "compose") => void }) => (
    <div data-testid="onboarding-screen">
      Onboarding
      <button type="button" onClick={() => onFinish("home")}>Finish to Inbox</button>
      <button type="button" onClick={() => onFinish("compose")}>Finish to Ask</button>
    </div>
  ),
}));
// Keep the remaining lazy-ish surfaces cheap.
vi.mock("./components/CommandPalette", () => ({ CommandPalette: () => null }));
vi.mock("./components/CustomThemeEditor", () => ({ CustomThemeEditor: () => null }));

vi.mock("./lib/useTheme", () => ({
  useTheme: () => ({
    toggle: vi.fn(),
    themeName: "alfred",
    setThemeName: vi.fn(),
    mode: "dark",
    setMode: vi.fn(),
  }),
}));
vi.mock("./lib/useRosterTheme", () => ({
  useRosterTheme: () => ({
    rosterTheme: "batman",
    customNames: {},
    setRosterTheme: vi.fn(),
    saveCustomNames: vi.fn(),
    saveError: null,
  }),
}));

const useAlfredMock = vi.fn();
vi.mock("./hooks/useAlfred", () => ({
  useAlfred: () => useAlfredMock(),
}));

function makeSnapshot(): Snapshot {
  return {
    status: {
      agents: [],
      total_today: 0,
      reliability: { status: "ok" },
    },
    schedule: [],
    plans: [],
    firings: [],
    trustedSlack: null,
  } as unknown as Snapshot;
}

function makeSetupStatus(overrides: Partial<SetupStatus> = {}): SetupStatus {
  return {
    github: { ok: true, account: "octocat", detail: "Signed in." },
    engines: [{ name: "claude", installed: true, path: "/opt/homebrew/bin/claude" }],
    engine_ready: true,
    repos: { selected: ["acme-org/api"], count: 1, keys: [], repo_checkouts: [] },
    demo: { present: false },
    first_run: {
      version: 1,
      ready: true,
      status: "ready",
      headline: "Ready for the first real run.",
      summary: {
        required_ready: 7,
        required_total: 7,
        recommended_ready: 0,
        recommended_total: 0,
        optional_ready: 0,
        optional_total: 0,
        blockers: [],
      },
      checks: [],
    },
    install: {
      agents_conf_present: true,
      scheduled_runs: 3,
      initialized: true,
    } as SetupStatus["install"],
    ready: true,
    ...overrides,
  };
}

function baseAlfredReturn(overrides: Record<string, unknown> = {}) {
  const noop = vi.fn();
  const refresh = vi.fn(async () => true);
  return {
    baseUrl: "http://127.0.0.1:7010",
    snapshot: null,
    error: null,
    errorRaw: null,
    loading: false,
    busyPlanAction: null,
    busyMemoryAction: null,
    busyTrustedUser: null,
    busyQueue: null,
    noticeFor: () => null,
    nativeBusy: null,
    nativeResult: null,
    nativeError: null,
    nativeErrorRaw: null,
    clearNativeResult: noop,
    needsYou: [],
    fleetService: null,
    feed: [],
    unseenCount: 0,
    seenIds: new Set<string>(),
    markActivitySeen: noop,
    shipped: null,
    shippedState: "idle",
    shippedError: null,
    refreshShipped: noop,
    usage: null,
    usageState: "idle",
    refresh,
    runFollowupAction: noop,
    runPlanDecision: noop,
    runPlanDiscard: noop,
    runPlanIssueFile: noop,
    runQueueAction: noop,
    runMemoryCandidateAction: noop,
    addTrustedUser: noop,
    removeTrustedUser: noop,
    runLocalAction: noop,
    installCore: noop,
    startRuntime: noop,
    ...overrides,
  };
}

afterEach(() => {
  appMenuMocks.handlers = null;
  vi.clearAllMocks();
  window.history.replaceState(null, "", "/");
});

async function renderApp() {
  const { default: App } = await import("./App");
  return render(<App />);
}

describe("App initial route gating", () => {
  it("lands on onboarding when the runtime is unreachable (fresh machine)", async () => {
    useAlfredMock.mockReturnValue(
      baseAlfredReturn({ snapshot: null, error: "connection refused" }),
    );
    await renderApp();
    expect(await screen.findByTestId("onboarding-screen")).toBeInTheDocument();
    expect(screen.queryByTestId("app-shell")).not.toBeInTheDocument();
    expect(screen.queryByTestId("inbox-screen")).not.toBeInTheDocument();
    expect(document.querySelector(".alfred-first-run > [data-tauri-drag-region]")).toBeInTheDocument();
  });

  it("keeps native refresh locked until first-run onboarding is complete", async () => {
    const refresh = vi.fn(async () => true);
    useAlfredMock.mockReturnValue(
      baseAlfredReturn({ snapshot: null, error: "connection refused", refresh }),
    );
    await renderApp();
    expect(await screen.findByTestId("onboarding-screen")).toBeInTheDocument();

    appMenuMocks.handlers?.onRefresh();

    expect(refresh).not.toHaveBeenCalled();
  });

  it("allows native refresh after setup reaches the ready state", async () => {
    const refresh = vi.fn(async () => true);
    useAlfredMock.mockReturnValue(
      baseAlfredReturn({ snapshot: makeSnapshot(), error: null, refresh }),
    );
    loadSetupStatusMock.mockResolvedValue(makeSetupStatus());
    await renderApp();
    expect(await screen.findByTestId("inbox-screen")).toBeInTheDocument();

    appMenuMocks.handlers?.onRefresh();

    expect(refresh).toHaveBeenCalledOnce();
  });

  it("overlays native action results without adding them to onboarding flow", async () => {
    useAlfredMock.mockReturnValue(
      baseAlfredReturn({
        snapshot: null,
        error: "connection refused",
        nativeResult: {
          command: ["alfred", "install"],
          stdout: "",
          stderr: "",
          status: 0,
          success: true,
          pid: null,
          message: "Installed Alfred",
        },
      }),
    );
    await renderApp();

    const result = await screen.findByText("Installed Alfred");
    expect(result.closest(".alfred-first-run__result")).toBeInTheDocument();
    expect(screen.getByTestId("onboarding-screen")).toBeInTheDocument();
  });

  it("keeps the startup gate draggable while setup readiness is loading", async () => {
    useAlfredMock.mockReturnValue(baseAlfredReturn({ snapshot: makeSnapshot(), error: null }));
    loadSetupStatusMock.mockReturnValue(new Promise(() => {}));
    await renderApp();

    expect(screen.getByText("Checking this Mac")).toBeInTheDocument();
    expect(document.querySelector(".alfred-startup > [data-tauri-drag-region]")).toBeInTheDocument();
  });

  it("lands on onboarding when connected but setup is not complete", async () => {
    useAlfredMock.mockReturnValue(baseAlfredReturn({ snapshot: makeSnapshot(), error: null }));
    loadSetupStatusMock.mockResolvedValue(
      makeSetupStatus({
        first_run: {
          ...makeSetupStatus().first_run,
          ready: false,
          status: "needs_action",
          headline: "1 required setup item needs action.",
          summary: {
            ...makeSetupStatus().first_run.summary,
            required_ready: 6,
            blockers: ["engine"],
          },
        },
      }),
    );
    await renderApp();
    expect(await screen.findByTestId("onboarding-screen")).toBeInTheDocument();
    expect(screen.queryByTestId("app-shell")).not.toBeInTheDocument();
    expect(screen.queryByTestId("inbox-screen")).not.toBeInTheDocument();
  });

  it("lands on onboarding when canonical readiness reports a local checkout blocker", async () => {
    useAlfredMock.mockReturnValue(baseAlfredReturn({ snapshot: makeSnapshot(), error: null }));
    loadSetupStatusMock.mockResolvedValue(
      makeSetupStatus({
        first_run: {
          ...makeSetupStatus().first_run,
          ready: false,
          status: "needs_action",
          headline: "1 required setup item needs action.",
          summary: {
            ...makeSetupStatus().first_run.summary,
            required_ready: 6,
            blockers: ["repo_local_paths"],
          },
        },
      }),
    );
    await renderApp();
    expect(await screen.findByTestId("onboarding-screen")).toBeInTheDocument();
    expect(screen.queryByTestId("app-shell")).not.toBeInTheDocument();
    expect(screen.queryByTestId("inbox-screen")).not.toBeInTheDocument();
  });

  it("lands on onboarding when canonical setup status cannot be read", async () => {
    useAlfredMock.mockReturnValue(baseAlfredReturn({ snapshot: makeSnapshot(), error: null }));
    loadSetupStatusMock.mockRejectedValue(new Error("runtime warming up"));
    await renderApp();
    expect(await screen.findByTestId("onboarding-screen")).toBeInTheDocument();
    expect(screen.queryByTestId("app-shell")).not.toBeInTheDocument();
  });

  it("lands on the Inbox when connected and setup is complete", async () => {
    useAlfredMock.mockReturnValue(baseAlfredReturn({ snapshot: makeSnapshot(), error: null }));
    loadSetupStatusMock.mockResolvedValue(makeSetupStatus());
    await renderApp();
    expect(await screen.findByTestId("inbox-screen")).toBeInTheDocument();
    expect(screen.getByTestId("app-shell")).toBeInTheDocument();
    expect(screen.queryByTestId("onboarding-screen")).not.toBeInTheDocument();
  });

  it("promotes a returning complete install after the runtime reconnects", async () => {
    let alfred = baseAlfredReturn({ snapshot: null, error: "connection refused" });
    useAlfredMock.mockImplementation(() => alfred);
    loadSetupStatusMock.mockResolvedValue(makeSetupStatus());
    const { default: App } = await import("./App");
    const view = render(<App />);

    expect(await screen.findByTestId("onboarding-screen")).toBeInTheDocument();

    alfred = baseAlfredReturn({ snapshot: makeSnapshot(), error: null });
    view.rerender(<App />);

    expect(await screen.findByTestId("app-shell")).toBeInTheDocument();
    expect(screen.getByTestId("inbox-screen")).toBeInTheDocument();
    expect(screen.queryByTestId("onboarding-screen")).not.toBeInTheDocument();
  });

  it("mounts the application shell only after onboarding finishes", async () => {
    let alfred = baseAlfredReturn({ snapshot: null, error: "connection refused" });
    const refresh = vi.fn(async () => {
      alfred = baseAlfredReturn({ snapshot: makeSnapshot(), error: null, refresh });
      return true;
    });
    alfred = baseAlfredReturn({ snapshot: null, error: "connection refused", refresh });
    useAlfredMock.mockImplementation(() => alfred);
    await renderApp();
    expect(await screen.findByTestId("onboarding-screen")).toBeInTheDocument();
    expect(screen.queryByTestId("app-shell")).not.toBeInTheDocument();

    await userEvent.setup().click(screen.getByRole("button", { name: "Finish to Inbox" }));

    expect(refresh).toHaveBeenCalledWith("http://127.0.0.1:7010");
    expect(await screen.findByTestId("app-shell")).toBeInTheDocument();
    expect(screen.getByTestId("inbox-screen")).toBeInTheDocument();
    expect(screen.queryByTestId("onboarding-screen")).not.toBeInTheDocument();
  });

  it("keeps onboarding open when the final runtime refresh fails", async () => {
    const refresh = vi.fn(async () => false);
    useAlfredMock.mockReturnValue(
      baseAlfredReturn({ snapshot: null, error: "connection refused", refresh }),
    );
    await renderApp();

    await userEvent.setup().click(screen.getByRole("button", { name: "Finish to Inbox" }));

    expect(refresh).toHaveBeenCalledWith("http://127.0.0.1:7010");
    expect(screen.getByTestId("onboarding-screen")).toBeInTheDocument();
    expect(screen.queryByTestId("app-shell")).not.toBeInTheDocument();
  });
});
