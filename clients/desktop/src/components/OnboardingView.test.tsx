import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as apiClient from "../api/client";
import * as apiSetup from "../api/setup";
import * as apiSlack from "../api/slack";
import { OnboardingView } from "./OnboardingView";
import type {
  SetupPlaybooksResponse,
  SetupReposResponse,
  SetupRepoCheckout,
  SetupBatteryManifest,
  SetupStatus,
  TrustedSlackUsersResponse,
} from "../types";

const VERIFIED_CHECKOUT: SetupRepoCheckout = {
  repo: "octocat/web",
  path: "/workspace/web",
  source: "map",
  exists: true,
  is_git_repo: true,
  github_remote_name: "origin",
  github_remote_repo: "octocat/web",
  identity_matches: true,
  ready: true,
  reason: null,
};

function makeStatus(overrides: Partial<SetupStatus> = {}): SetupStatus {
  const firstRun: SetupStatus["first_run"] = {
    version: 1,
    ready: false,
    status: "needs_action",
    headline: "Setup needs action.",
    summary: {
      required_ready: 0,
      required_total: 7,
      recommended_ready: 0,
      recommended_total: 0,
      optional_ready: 0,
      optional_total: 0,
      blockers: ["engine", "github", "repos"],
    },
    checks: [],
  };
  return {
    github: { ok: true, account: "octocat", detail: "Signed in to GitHub as octocat." },
    engines: [
      { name: "claude", installed: true, path: "/opt/homebrew/bin/claude" },
      { name: "codex", installed: false, path: null },
    ],
    engine_ready: true,
    code_memory: {
      enabled: true,
      autofetch: true,
      binary: {
        resolved: false,
        path: null,
        source: "none",
        configured: null,
      },
      version_pin: "v0.8.1",
      repo: "DeusData/codebase-memory-mcp",
      index_dir: "/tmp/.alfred/state/code-memory",
      index_present: false,
      repos: { configured: [], count: 0 },
      detail:
        "Code-memory binary is not installed yet; Alfred can fetch the pinned release on first explicit use.",
    },
    repos: {
      selected: [],
      count: 0,
      keys: ["ALFRED_QUEUE_REPOS", "ALFRED_SHIPPED_REPOS"],
      repo_checkouts: [],
    },
    demo: { present: false },
    ready: false,
    ...overrides,
    first_run: overrides.first_run ?? firstRun,
  };
}

function makeIndexedStatus(): SetupStatus {
  const status = makeStatus();
  if (!status.code_memory) throw new Error("test setup requires code-memory status");
  return {
    ...status,
    code_memory_coverage: {
      ready: true,
      covered: ["octocat/web"],
      missing: [],
    },
    code_memory: {
      ...status.code_memory,
      index_present: true,
      repos: {
        configured: ["web"],
        configured_existing: ["web"],
        selected: ["web"],
        count: 1,
      },
      detail: "Code graph is ready.",
    },
  };
}

function makeReadyStatus(overrides: Partial<SetupStatus> = {}): SetupStatus {
  return makeStatus({
    repos: {
      selected: ["octocat/web"],
      count: 1,
      keys: ["ALFRED_QUEUE_REPOS", "ALFRED_SHIPPED_REPOS"],
      repo_checkouts: [VERIFIED_CHECKOUT],
    },
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
    ...overrides,
  });
}

function makeInstall(overrides: Partial<NonNullable<SetupStatus["install"]>> = {}): NonNullable<SetupStatus["install"]> {
  const base: NonNullable<SetupStatus["install"]> = {
    alfred_home: "/tmp/alfred-home",
    env_path: "/tmp/alfred-home/.env",
    env_present: true,
    server_token_present: true,
    agents_conf_path: "/tmp/alfred-home/launchd/agents.conf",
    agents_conf_present: true,
    scheduled_runs: 3,
    selected_repos_env_present: true,
    slack_configured: false,
    memory_configured: false,
    initialized: true,
    items: [
      {
        key: "home",
        label: "Runtime home",
        ok: true,
        detail: "Found /tmp/alfred-home",
        path: "/tmp/alfred-home",
      },
      {
        key: "agents",
        label: "Scheduled fleet",
        ok: true,
        detail: "3 configured scheduled runs in agents.conf",
        path: "/tmp/alfred-home/launchd/agents.conf",
      },
      {
        key: "repos",
        label: "Repository scope",
        ok: true,
        detail: "1 selected repos in ALFRED_QUEUE_REPOS, ALFRED_SHIPPED_REPOS",
        path: "/tmp/alfred-home/.env",
      },
      {
        key: "slack",
        label: "Slack approvals",
        ok: false,
        detail: "Optional. Not configured yet.",
        path: null,
        optional: true,
      },
      {
        key: "memory",
        label: "Memory layer",
        ok: true,
        detail: "Using embedded SQLite hybrid memory defaults.",
        path: null,
      },
      {
        key: "token",
        label: "Desktop mutation token",
        ok: true,
        detail: "Runtime token is present for desktop actions.",
        path: "/tmp/alfred-home/state",
      },
    ],
  };
  return { ...base, ...overrides };
}

const REPOS: SetupReposResponse = {
  repos: [
    {
      name_with_owner: "octocat/web",
      description: "The marketing site",
      is_private: false,
      is_fork: false,
      updated_at: "2026-06-01T00:00:00Z",
      selected: false,
    },
    {
      name_with_owner: "octocat/api",
      description: null,
      is_private: true,
      is_fork: false,
      updated_at: "2026-06-02T00:00:00Z",
      selected: false,
    },
  ],
  selected: [],
  repo_checkouts: [],
};

const PLAYBOOKS: SetupPlaybooksResponse = {
  playbooks: [
    { key: "triage-prs", title: "Triage open PRs every night", summary: "Review open PRs nightly." },
    { key: "fix-failing-ci", title: "Fix failing CI", summary: "Diagnose and fix a failing check." },
  ],
};

const TRUSTED_EMPTY: TrustedSlackUsersResponse = {
  operator_user_id: null,
  users: [],
  state_path: "/tmp/trusted.json",
};

function defaultRosterProps() {
  return {
    rosterTheme: "batman" as const,
    customNames: { names: {}, roles: {} },
    rosterSaveError: null,
    onRosterThemeChange: vi.fn(),
    onEditCustomTheme: vi.fn(),
    onSaveCustomNames: vi.fn(async () => undefined),
  };
}

function renderOnboarding(props: Partial<React.ComponentProps<typeof OnboardingView>> = {}) {
  return render(
    <OnboardingView
      baseUrl="http://127.0.0.1:7010"
      loading={false}
      connected
      canRun
      nativeBusy={null}
      onConnectServer={vi.fn()}
      onInstallCore={vi.fn()}
      onStartRuntime={vi.fn()}
      onRunLocalAction={vi.fn(async () => null)}
      onFinish={vi.fn()}
      onRefreshBoard={vi.fn(async () => undefined)}
      {...defaultRosterProps()}
      {...props}
    />,
  );
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

// The stepper buttons are the reliable way to reach a given step from any state.
async function gotoStep(user: ReturnType<typeof userEvent.setup>, stepName: RegExp) {
  await user.click(await screen.findByRole("button", { name: stepName }));
}

async function selectWebCheckout(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByText("octocat/web");
  await user.click(screen.getByRole("checkbox", { name: /octocat\/web/i }));
  await user.type(
    screen.getByRole("textbox", { name: /local checkout for octocat\/web/i }),
    "/workspace/web",
  );
}

beforeEach(() => {
  vi.spyOn(apiClient, "supportsNativeActions").mockReturnValue(true);
  vi.spyOn(apiClient, "supportsMutations").mockReturnValue(true);
  vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(makeStatus());
  vi.spyOn(apiSetup, "loadSetupRepos").mockResolvedValue(REPOS);
  vi.spyOn(apiSetup, "loadSetupBatteries").mockResolvedValue({
    version: 1,
    summary: { included: 4, enabled: 0, available: 0, not_installed: 5, total: 9 },
    batteries: [],
  });
  vi.spyOn(apiSetup, "loadSetupPlaybooks").mockResolvedValue(PLAYBOOKS);
  vi.spyOn(apiSlack, "loadTrustedSlackUsers").mockResolvedValue(TRUSTED_EMPTY);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("OnboardingView eight-step takeover", () => {
  it("fails closed when the runtime omits checkout readiness", async () => {
    const malformed = makeStatus({
      repos: {
        selected: ["octocat/web"],
        count: 1,
        keys: ["ALFRED_QUEUE_REPOS", "ALFRED_SHIPPED_REPOS"],
        repo_checkouts: [],
      },
    });
    delete (malformed.repos as Partial<SetupStatus["repos"]>).repo_checkouts;
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(malformed);

    renderOnboarding();

    expect(await screen.findByText(/let's get you set up/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /get started/i })).toBeInTheDocument();
  });

  it("opens on the welcome step with the mental model and no-terminal framing", async () => {
    renderOnboarding();
    expect(
      await screen.findByText(/let's get you set up/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/checks this Mac, connects to GitHub, and ends on a real result/i),
    ).toBeInTheDocument();
    // The trust differentiator is on the first screen, not buried.
    expect(
      screen.getByText(/runs on the claude max and codex pro subscriptions you already pay for/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/no api keys/i)).toBeInTheDocument();
    // The persistent stepper shows all eight steps.
    expect(screen.getByRole("button", { name: /^welcome$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^tools$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^github$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^repositories$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^team$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^slack$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^first request$/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /^set up alfred$/i, level: 1 })).toHaveClass(
      "sr-only",
    );
    const shell = document.querySelector(".alfred-onboarding-shell");
    expect(shell?.firstElementChild).toBe(
      screen.getByRole("navigation", { name: /onboarding progress/i }),
    );
  });

  it("starts a full desktop install from the fresh-machine welcome action", async () => {
    const onInstallCore = vi.fn();
    renderOnboarding({ connected: false, canRun: true, onInstallCore });
    const user = userEvent.setup();

    expect(await screen.findByRole("button", { name: /^tools$/i })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /continue setup/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^continue$/i })).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/onboarding navigation/i)).not.toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: /install alfred/i }));

    expect(onInstallCore).toHaveBeenCalledTimes(1);
  });

  it("requires a server path when native installation is unavailable", async () => {
    renderOnboarding({ connected: false, canRun: false });

    expect(await screen.findByRole("button", { name: /^tools$/i })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /get started/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /install alfred/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /i have a server running/i })).toBeInTheDocument();
  });

  it("shows detected existing install inventory on the welcome step", async () => {
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      makeStatus({
        install: makeInstall(),
        repos: {
          selected: ["octocat/web"],
          count: 1,
          keys: ["ALFRED_QUEUE_REPOS", "ALFRED_SHIPPED_REPOS"],
          repo_checkouts: [VERIFIED_CHECKOUT],
        },
      }),
    );
    renderOnboarding();

    expect(await screen.findByText(/found an alfred setup on this mac/i)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /review your setup/i })).not.toBeInTheDocument();
    expect(screen.getAllByText("/tmp/alfred-home").length).toBeGreaterThan(0);
    expect(screen.getByText(/3 configured scheduled runs in agents\.conf/i)).toBeInTheDocument();
    expect(screen.getByText(/optional\. not configured yet/i)).toBeInTheDocument();
    expect(screen.getByText(/ready to use/i)).toBeInTheDocument();
  });

  it("marks inventory-proven steps done for a detected install, not 0 of 7", async () => {
    // The contradiction: an existing install reads "ready to use" while the stepper
    // said "0 of 7 done" because completion was gated on the user re-walking the
    // wizard. With an install detected, steps the runtime proves complete
    // (engine, github, repos, team) must show as done so the stepper agrees with
    // the inventory.
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      makeStatus({
        install: makeInstall(),
        github: { ok: true, account: "octocat", detail: "Signed in as octocat." },
        engine_ready: true,
        repos: {
          selected: ["octocat/web"],
          count: 1,
          keys: ["ALFRED_QUEUE_REPOS", "ALFRED_SHIPPED_REPOS"],
          repo_checkouts: [VERIFIED_CHECKOUT],
        },
      }),
    );
    renderOnboarding();

    // On the welcome step of a detected install, the stepper must not read 0 done.
    expect(await screen.findByText(/found an alfred setup on this mac/i)).toBeInTheDocument();
    const progress = await screen.findByLabelText(/of 8 onboarding steps complete/i);
    const match = /(\d+) of 8 onboarding steps complete/.exec(progress.getAttribute("aria-label") || "");
    expect(match).not.toBeNull();
    expect(Number(match?.[1])).toBeGreaterThan(0);
  });

  it("keeps a fresh first run honest at 0 done until the user walks the wizard", async () => {
    // No existing install: even if engine/gh/repos are pre-detected, a brand-new
    // first run opens on Welcome with nothing marked done so the flow never feels
    // skipped. The cursor still governs completion here.
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      makeStatus({
        install: makeInstall({ initialized: false }),
        github: { ok: true, account: "octocat", detail: "Signed in as octocat." },
        engine_ready: true,
        repos: {
          selected: ["octocat/web"],
          count: 1,
          keys: ["ALFRED_QUEUE_REPOS", "ALFRED_SHIPPED_REPOS"],
          repo_checkouts: [VERIFIED_CHECKOUT],
        },
      }),
    );
    renderOnboarding();

    const progress = await screen.findByLabelText(/of 8 onboarding steps complete/i);
    expect(progress.getAttribute("aria-label")).toMatch(/^0 of 8/);
  });

  it("keeps the stepper first while setup inventory is loading", async () => {
    const pending = deferred<SetupStatus>();
    vi.spyOn(apiSetup, "loadSetupStatus").mockReturnValue(pending.promise);
    renderOnboarding();

    const shell = document.querySelector(".alfred-onboarding-shell");
    expect(shell?.firstElementChild).toBe(
      screen.getByRole("navigation", { name: /onboarding progress/i }),
    );
    expect(screen.queryByText(/checking setup/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /checking this mac/i })).not.toBeInTheDocument();

    pending.resolve(makeStatus());
    await waitFor(() => expect(screen.getByText(/let's get you set up/i)).toBeInTheDocument());
  });

  it("keeps the stepper first when no server is connected", () => {
    renderOnboarding({ connected: false });

    const shell = document.querySelector(".alfred-onboarding-shell");
    expect(shell?.firstElementChild).toBe(
      screen.getByRole("navigation", { name: /onboarding progress/i }),
    );
    expect(screen.getByRole("heading", { name: /set up alfred/i, level: 1 })).toHaveClass(
      "sr-only",
    );
    expect(screen.queryByText(/checking setup/i)).not.toBeInTheDocument();
  });

  it("clears displayed welcome inventory while a new server URL is loading", async () => {
    const newRequest = deferred<SetupStatus>();
    vi.spyOn(apiSetup, "loadSetupStatus")
      .mockResolvedValueOnce(
        makeStatus({ install: makeInstall({ alfred_home: "/tmp/old-alfred-home" }) }),
      )
      .mockReturnValueOnce(newRequest.promise);

    const view = renderOnboarding({ baseUrl: "http://127.0.0.1:7010" });
    expect(await screen.findByText("/tmp/old-alfred-home")).toBeInTheDocument();

    view.rerender(
      <OnboardingView
        baseUrl="http://127.0.0.1:7011"
        loading={false}
        connected
        canRun
        nativeBusy={null}
        onConnectServer={vi.fn()}
        onInstallCore={vi.fn()}
        onStartRuntime={vi.fn()}
        onRunLocalAction={vi.fn(async () => null)}
        onFinish={vi.fn()}
        onRefreshBoard={vi.fn(async () => undefined)}
        {...defaultRosterProps()}
      />,
    );

    await waitFor(() => {
      expect(screen.queryByText("/tmp/old-alfred-home")).not.toBeInTheDocument();
    });

    newRequest.resolve(
      makeStatus({ install: makeInstall({ alfred_home: "/tmp/new-alfred-home" }) }),
    );
    expect(await screen.findByText("/tmp/new-alfred-home")).toBeInTheDocument();
  });

  it("ignores stale welcome inventory reads after the server URL changes", async () => {
    const oldRequest = deferred<SetupStatus>();
    const newRequest = deferred<SetupStatus>();
    vi.spyOn(apiSetup, "loadSetupStatus")
      .mockReturnValueOnce(oldRequest.promise)
      .mockReturnValueOnce(newRequest.promise);

    const view = renderOnboarding({ baseUrl: "http://127.0.0.1:7010" });
    view.rerender(
      <OnboardingView
        baseUrl="http://127.0.0.1:7011"
        loading={false}
        connected
        canRun
        nativeBusy={null}
        onConnectServer={vi.fn()}
        onInstallCore={vi.fn()}
        onStartRuntime={vi.fn()}
        onRunLocalAction={vi.fn(async () => null)}
        onFinish={vi.fn()}
        onRefreshBoard={vi.fn(async () => undefined)}
        {...defaultRosterProps()}
      />,
    );

    newRequest.resolve(
      makeStatus({ install: makeInstall({ alfred_home: "/tmp/new-alfred-home" }) }),
    );
    expect(await screen.findByText("/tmp/new-alfred-home")).toBeInTheDocument();

    oldRequest.resolve(
      makeStatus({ install: makeInstall({ alfred_home: "/tmp/old-alfred-home" }) }),
    );
    await waitFor(() => {
      expect(screen.queryByText("/tmp/old-alfred-home")).not.toBeInTheDocument();
    });
  });

  it("welcome 'Get started' moves to the tools step", async () => {
    // Engine not ready yet, so Tools does not auto-advance and the user sees it.
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      makeStatus({
        engine_ready: false,
        github: { ok: false, account: null, detail: "Not signed in to GitHub." },
      }),
    );
    renderOnboarding();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /get started/i }));
    expect(screen.getByText(/no api keys needed/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /check my tools/i })).toBeInTheDocument();
  });

  it("welcome dev shortcut 'I have a server running' jumps to GitHub", async () => {
    renderOnboarding({ connected: false });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /i have a server running/i }));
    expect(screen.getAllByText(/connect github/i).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /^tools$/i })).not.toBeDisabled();
    expect(screen.getByLabelText(/local server url/i)).toBeVisible();
  });

  it("restores honest Welcome progress after abandoning the server route", async () => {
    renderOnboarding({ connected: false });
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /i have a server running/i }));
    await user.click(screen.getByRole("button", { name: /^back$/i }));
    await user.click(screen.getByRole("button", { name: /^welcome$/i }));

    const stepper = screen.getByRole("navigation", { name: /onboarding progress/i });
    expect(within(stepper).getByRole("button", { current: "step" })).toHaveAccessibleName(
      /welcome/i,
    );
    expect(within(stepper).getByLabelText(/onboarding steps complete/i)).toHaveTextContent(
      /0 of 8/i,
    );
    expect(within(stepper).getByRole("button", { name: /^tools$/i })).toBeDisabled();
    expect(screen.queryByLabelText(/onboarding navigation/i)).not.toBeInTheDocument();
  });

  it("completes the server route only after connection and keeps Welcome current on revisit", async () => {
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      makeStatus({
        github: { ok: false, account: null, detail: "Not signed in to GitHub." },
      }),
    );
    const props: React.ComponentProps<typeof OnboardingView> = {
      baseUrl: "http://127.0.0.1:7010",
      loading: false,
      connected: false,
      canRun: true,
      nativeBusy: null,
      onConnectServer: vi.fn(),
      onInstallCore: vi.fn(),
      onStartRuntime: vi.fn(),
      onRunLocalAction: vi.fn(async () => null),
      onFinish: vi.fn(),
      onRefreshBoard: vi.fn(async () => undefined),
      ...defaultRosterProps(),
    };
    const view = render(<OnboardingView {...props} />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /i have a server running/i }));
    expect(screen.getByLabelText(/onboarding steps complete/i)).toHaveTextContent(/0 of 8/i);
    view.rerender(<OnboardingView {...props} connected />);
    await waitFor(() =>
      expect(screen.getByLabelText(/onboarding steps complete/i)).toHaveTextContent(/1 of 8/i),
    );

    await user.click(screen.getByRole("button", { name: /^welcome$/i }));
    view.rerender(<OnboardingView {...props} />);
    const stepper = screen.getByRole("navigation", { name: /onboarding progress/i });
    expect(within(stepper).getByRole("button", { current: "step" })).toHaveAccessibleName(
      /welcome/i,
    );
    expect(within(stepper).getByLabelText(/onboarding steps complete/i)).toHaveTextContent(
      /1 of 8/i,
    );
  });

  it("clears the server shortcut after footer navigation leaves GitHub", async () => {
    const props: React.ComponentProps<typeof OnboardingView> = {
      baseUrl: "http://127.0.0.1:7010",
      loading: false,
      connected: false,
      canRun: true,
      nativeBusy: null,
      onConnectServer: vi.fn(),
      onInstallCore: vi.fn(),
      onStartRuntime: vi.fn(),
      onRunLocalAction: vi.fn(async () => null),
      onFinish: vi.fn(),
      onRefreshBoard: vi.fn(async () => undefined),
      ...defaultRosterProps(),
    };
    const view = render(<OnboardingView {...props} />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /i have a server running/i }));
    expect(screen.getByLabelText(/local server url/i)).toBeVisible();
    // Mark this visit as manual so readiness does not auto-advance before the
    // footer path under test can run.
    await user.click(screen.getByRole("button", { name: /^github$/i }));
    view.rerender(<OnboardingView {...props} connected />);
    await waitFor(() => expect(screen.getByRole("button", { name: /^continue$/i })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: /^continue$/i }));
    expect(screen.getByRole("textbox", { name: /search repositories/i })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^back$/i }));
    expect(screen.getByLabelText(/local server url/i)).not.toBeVisible();
  });

  it("clears the server shortcut when the user returns to native installation", async () => {
    const onInstallCore = vi.fn();
    const props: React.ComponentProps<typeof OnboardingView> = {
      baseUrl: "http://127.0.0.1:7010",
      loading: false,
      connected: false,
      canRun: true,
      nativeBusy: null,
      onConnectServer: vi.fn(),
      onInstallCore,
      onStartRuntime: vi.fn(),
      onRunLocalAction: vi.fn(async () => null),
      onFinish: vi.fn(),
      onRefreshBoard: vi.fn(async () => undefined),
      ...defaultRosterProps(),
    };
    const view = render(<OnboardingView {...props} />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /i have a server running/i }));
    expect(screen.getByLabelText(/local server url/i)).toBeVisible();
    await user.click(screen.getByRole("button", { name: /^welcome$/i }));
    await user.click(screen.getByRole("button", { name: /install alfred/i }));
    expect(onInstallCore).toHaveBeenCalledTimes(1);

    view.rerender(<OnboardingView {...props} connected />);
    await user.click(screen.getByRole("button", { name: /^github$/i }));
    expect(screen.getByLabelText(/local server url/i)).not.toBeVisible();
  });

  it("detects CLIs via a native auth probe on the tools step", async () => {
    const onRunLocalAction = vi.fn();
    // Keep gh NOT signed in so auto-advance does not skip past Tools immediately.
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      makeStatus({
        engine_ready: false,
        github: { ok: false, account: null, detail: "Not signed in to GitHub." },
      }),
    );
    renderOnboarding({ onRunLocalAction });
    const user = userEvent.setup();
    await gotoStep(user, /^tools$/i);
    await user.click(screen.getByRole("button", { name: /check my tools/i }));
    expect(onRunLocalAction).toHaveBeenCalledWith({ action: "auth_status", refreshAfter: true });
  });

  it("refreshes canonical setup readiness when a native tool check finishes", async () => {
    const loadStatus = vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      makeStatus({
        engine_ready: false,
        github: { ok: false, account: null, detail: "Not signed in to GitHub." },
      }),
    );
    const props: React.ComponentProps<typeof OnboardingView> = {
      baseUrl: "http://127.0.0.1:7010",
      loading: false,
      connected: true,
      canRun: true,
      nativeBusy: "auth_status",
      onConnectServer: vi.fn(),
      onInstallCore: vi.fn(),
      onStartRuntime: vi.fn(),
      onRunLocalAction: vi.fn(async () => null),
      onFinish: vi.fn(),
      onRefreshBoard: vi.fn(async () => undefined),
      ...defaultRosterProps(),
    };
    const view = render(<OnboardingView {...props} />);
    await waitFor(() => expect(loadStatus).toHaveBeenCalledTimes(1));

    view.rerender(<OnboardingView {...props} nativeBusy={null} />);

    await waitFor(() => expect(loadStatus).toHaveBeenCalledTimes(2));
  });

  it("refreshes canonical setup readiness after a native install reconnects", async () => {
    const loadStatus = vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(makeStatus());
    const props: React.ComponentProps<typeof OnboardingView> = {
      baseUrl: "http://127.0.0.1:7010",
      loading: false,
      connected: false,
      canRun: true,
      nativeBusy: "install_core",
      onConnectServer: vi.fn(),
      onInstallCore: vi.fn(),
      onStartRuntime: vi.fn(),
      onRunLocalAction: vi.fn(async () => null),
      onFinish: vi.fn(),
      onRefreshBoard: vi.fn(async () => undefined),
      ...defaultRosterProps(),
    };
    const view = render(<OnboardingView {...props} />);

    view.rerender(<OnboardingView {...props} nativeBusy={null} />);
    expect(loadStatus).not.toHaveBeenCalled();

    view.rerender(<OnboardingView {...props} connected nativeBusy={null} />);
    await waitFor(() => expect(loadStatus).toHaveBeenCalledTimes(1));
  });

  it("surfaces code-memory readiness on the tools step", async () => {
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      makeStatus({
        github: { ok: false, account: null, detail: "Not signed in to GitHub." },
        code_memory: {
          enabled: true,
          autofetch: true,
          binary: {
            resolved: true,
            path: "/opt/alfred/bin/codebase-memory-mcp",
            source: "cache",
            configured: null,
          },
          version_pin: "v0.8.1",
          repo: "DeusData/codebase-memory-mcp",
          index_dir: "/opt/alfred/state/code-memory",
          index_present: true,
          repos: { configured: ["api", "web"], count: 2 },
          detail: "Code-memory binary and index are present.",
        },
        capability_plane: {
          version: 1,
          summary: { ready: 3, actionable: 0, disabled: 0, total: 3 },
          capabilities: [
            {
              key: "code_graph",
              title: "Code graph memory",
              category: "memory",
              recommended: true,
              state: "ready",
              installed: true,
              enabled: true,
              detail: "Code graph index is ready for selected repos.",
              detected: {},
              install_hint: "Run `alfred code-memory doctor`, then `alfred code-memory index`.",
              source: {
                source: "DeusData/codebase-memory-mcp",
                url: "https://github.com/DeusData/codebase-memory-mcp",
                license: "MIT",
              },
            },
            {
              key: "context_compression",
              title: "Context governor",
              category: "tokens",
              recommended: true,
              state: "ready",
              installed: true,
              enabled: true,
              detail: "Alfred's built-in context governor is active for every agent firing.",
              detected: { env_key: "ALFRED_CONTEXT_GOVERNOR" },
              install_hint: "Unset ALFRED_CONTEXT_GOVERNOR or set it to 1 to re-enable.",
              source: {
                source: "Alfred context governor, headroomlabs-ai/headroom",
                url: "https://github.com/headroomlabs-ai/headroom",
                license: "Apache-2.0",
              },
            },
            {
              key: "engineering_skills",
              title: "Engineering skill packs",
              category: "skills",
              recommended: true,
              state: "ready",
              installed: true,
              enabled: true,
              detail: "At least one engineering skill pack is installed.",
              detected: {},
              install_hint: "Install gstack and the Vercel/Addy agent-skill packs.",
              source: { source: "garrytan/gstack", url: "https://github.com/garrytan/gstack" },
            },
          ],
        },
      }),
    );
    renderOnboarding();
    const user = userEvent.setup();
    await gotoStep(user, /^tools$/i);

    expect(await screen.findByText(/^code memory$/i)).toBeInTheDocument();
    expect(screen.getByText(/code-memory binary and index are present/i)).toBeInTheDocument();
    expect(screen.getByText(/local capabilities/i)).toBeInTheDocument();
    expect(screen.getByText(/3 of 3 ready/i)).toBeInTheDocument();
    expect(screen.getByText(/code graph index is ready for selected repos/i)).toBeInTheDocument();
    expect(screen.getByText(/built-in context governor is active/i)).toBeInTheDocument();
    expect(screen.queryByText(/run `headroom doctor`/i)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Alfred context governor/i })).toHaveAttribute(
      "href",
      "https://github.com/headroomlabs-ai/headroom",
    );
    await user.click(screen.getByText(/advanced: code-memory probe/i));
    expect(screen.getByText(/DeusData\/codebase-memory-mcp@v0.8.1/i)).toBeInTheDocument();
    expect(screen.getByText(/configured repos/i)).toBeInTheDocument();
    expect(screen.getByText(/api, web/i)).toBeInTheDocument();
  });

  it("labels disabled optional capabilities without calling setup ready", async () => {
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      makeStatus({
        github: { ok: false, account: null, detail: "Not signed in to GitHub." },
        capability_plane: {
          version: 1,
          summary: { ready: 0, actionable: 0, disabled: 1, total: 1 },
          capabilities: [
            {
              key: "context_compression",
              title: "Context governor",
              category: "tokens",
              recommended: false,
              state: "disabled",
              installed: false,
              enabled: false,
              detail: "Context governor is optional for this install.",
              detected: {},
              install_hint: "Set ALFRED_CONTEXT_GOVERNOR=1 if you want prompt budgeting.",
              source: { source: "headroomlabs-ai/headroom" },
            },
          ],
        },
      }),
    );
    renderOnboarding();
    const user = userEvent.setup();
    await gotoStep(user, /^tools$/i);

    expect(await screen.findByText(/local capabilities/i)).toBeInTheDocument();
    expect(screen.getByText(/0 of 1 ready/i)).toBeInTheDocument();
    expect(screen.getByText(/^optional$/i)).toBeInTheDocument();
    expect(screen.getByText(/set ALFRED_CONTEXT_GOVERNOR=1/i)).toBeInTheDocument();
    expect(screen.queryByText(/^ready$/i)).not.toBeInTheDocument();
  });

  it("handles older code-memory payloads without repo metadata", async () => {
    const incompleteCodeMemory = { ...makeStatus().code_memory! };
    delete incompleteCodeMemory.repos;
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      makeStatus({
        github: { ok: false, account: null, detail: "Not signed in to GitHub." },
        code_memory: {
          ...incompleteCodeMemory,
          binary: {
            resolved: true,
            path: "/opt/alfred/bin/codebase-memory-mcp",
            source: "cache",
            configured: null,
          },
          index_present: true,
          detail: "Code-memory binary and index are present.",
        },
      }),
    );
    renderOnboarding();
    const user = userEvent.setup();
    await gotoStep(user, /^tools$/i);

    expect(await screen.findByText(/code-memory binary and index are present/i)).toBeInTheDocument();
    await user.click(screen.getByText(/advanced: code-memory probe/i));
    expect(screen.getByText(/auto-discovered repos/i)).toBeInTheDocument();
    expect(screen.getByText(/none found yet/i)).toBeInTheDocument();
  });

  it("shows an honest empty state when no engine is found", async () => {
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      makeStatus({
        engine_ready: false,
        github: { ok: false, account: null, detail: "Not signed in to GitHub." },
      }),
    );
    renderOnboarding();
    const user = userEvent.setup();
    await gotoStep(user, /^tools$/i);
    expect(await screen.findByText(/no engine found yet/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /install claude code/i })).toBeInTheDocument();
  });

  it("shows 'Signed in' on the GitHub step and never asks for a token paste", async () => {
    // Opening GitHub deliberately from the stepper does not auto-advance away, so a
    // signed-in user can still read the confirmation.
    renderOnboarding();
    const user = userEvent.setup();
    await gotoStep(user, /^github$/i);
    expect(await screen.findByText(/signed in to github as octocat/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/token/i)).not.toBeInTheDocument();
  });

  it("auto-advances through Tools and GitHub from the forward flow when both are detected", async () => {
    // engine_ready true + github ok: Get started lands on Tools, which
    // auto-advances to GitHub, which auto-advances to Repositories.
    renderOnboarding();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /get started/i }));
    expect(await screen.findByRole("textbox", { name: /search repositories/i })).toBeInTheDocument();
  });

  it("shows recommended capability repairs without blocking repository setup", async () => {
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      makeStatus({
        github: { ok: true, account: "octocat", detail: "Signed in to GitHub as octocat." },
        engine_ready: true,
        capability_plane: {
          version: 1,
          summary: { ready: 1, actionable: 1, disabled: 0, total: 2 },
          capabilities: [
            {
              key: "code_graph",
              title: "Code graph memory",
              category: "memory",
              recommended: true,
              state: "needs_index",
              installed: true,
              enabled: true,
              detail: "Code graph binary is present; run an index.",
              detected: {},
              install_hint: "Run `alfred code-memory doctor`.",
              source: { source: "DeusData/codebase-memory-mcp" },
            },
            {
              key: "context_compression",
              title: "Context governor",
              category: "tokens",
              recommended: true,
              state: "ready",
              installed: true,
              enabled: true,
              detail: "Alfred's built-in context governor is active for every agent firing.",
              detected: {},
              install_hint: "Unset ALFRED_CONTEXT_GOVERNOR or set it to 1.",
              source: { source: "Alfred context governor" },
            },
          ],
        },
      }),
    );
    renderOnboarding();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /get started/i }));

    expect(await screen.findByRole("textbox", { name: /search repositories/i })).toBeInTheDocument();
    await gotoStep(user, /^tools$/i);
    expect(await screen.findByText(/local capabilities/i)).toBeInTheDocument();
    expect(screen.getByText(/1 of 2 ready, 1 to finish/i)).toBeInTheDocument();
    expect(screen.getByText(/^needs attention$/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^continue$/i })).toBeEnabled();
  });

  it("starts native GitHub web sign-in and polls until setup reports connected", async () => {
    const pending = makeStatus({
      github: { ok: false, account: null, detail: "Not signed in to GitHub." },
    });
    const loadStatus = vi
      .spyOn(apiSetup, "loadSetupStatus")
      .mockResolvedValueOnce(pending)
      .mockResolvedValueOnce(makeStatus());
    const onRunLocalAction = vi.fn(async () => ({
      command: ["gh", "auth", "login", "--web"],
      stdout: "",
      stderr: "",
      status: null,
      success: true,
      pid: 42,
      message: "GitHub sign-in started. Enter the one-time code in your browser.",
      github_auth: {
        device_url: "https://github.com/login/device",
        device_code: "ABCD-1234",
        poll_interval_ms: 250,
        timeout_ms: 1_000,
      },
    }));
    renderOnboarding({ onRunLocalAction });
    const user = userEvent.setup();
    await gotoStep(user, /^github$/i);

    await user.click(await screen.findByRole("button", { name: /sign in with github/i }));

    expect(onRunLocalAction).toHaveBeenCalledWith({ action: "github_auth_login" });
    await waitFor(() => expect(loadStatus).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.getByText(/signed in to github as octocat/i)).toBeInTheDocument(),
    );
  });

  it("ignores GitHub auth poll inventory after same-url disconnect and reconnect", async () => {
    const pending = makeStatus({
      github: { ok: false, account: null, detail: "Not signed in to GitHub." },
    });
    const pollStatus = deferred<SetupStatus>();
    const loadStatus = vi
      .spyOn(apiSetup, "loadSetupStatus")
      .mockResolvedValue(pending)
      .mockResolvedValueOnce(pending)
      .mockReturnValueOnce(pollStatus.promise);
    const onRunLocalAction = vi.fn(async () => ({
      command: ["gh", "auth", "login", "--web"],
      stdout: "",
      stderr: "",
      status: null,
      success: true,
      pid: 42,
      message: "GitHub sign-in started. Enter the one-time code in your browser.",
      github_auth: {
        device_url: "https://github.com/login/device",
        device_code: "ABCD-1234",
        poll_interval_ms: 250,
        timeout_ms: 1_000,
      },
    }));
    const props = {
      baseUrl: "http://127.0.0.1:7010",
      loading: false,
      canRun: true,
      nativeBusy: null,
      nativeResult: null,
      onConnectServer: vi.fn(),
      onInstallCore: vi.fn(),
      onStartRuntime: vi.fn(),
      onRunLocalAction,
      onFinish: vi.fn(),
      onRefreshBoard: vi.fn(async () => undefined),
      ...defaultRosterProps(),
    };
    const view = render(<OnboardingView {...props} connected />);
    const user = userEvent.setup();
    await gotoStep(user, /^github$/i);

    await user.click(await screen.findByRole("button", { name: /sign in with github/i }));
    await waitFor(() => expect(loadStatus).toHaveBeenCalledTimes(2));

    view.rerender(<OnboardingView {...props} connected={false} />);
    view.rerender(<OnboardingView {...props} connected />);
    await waitFor(() => expect(loadStatus).toHaveBeenCalledTimes(3));
    expect(await screen.findByText(/GitHub sign-in was interrupted/i)).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /sign in with github/i })).toBeEnabled(),
    );

    pollStatus.resolve(
      makeStatus({
        install: makeInstall({ alfred_home: "/tmp/stale-alfred-home" }),
      }),
    );

    await waitFor(() => {
      expect(screen.queryByText("/tmp/stale-alfred-home")).not.toBeInTheDocument();
      expect(screen.queryByText(/found an alfred setup on this mac/i)).not.toBeInTheDocument();
    });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /sign in with github/i })).toBeEnabled(),
    );
    await user.click(screen.getByRole("button", { name: /sign in with github/i }));
    expect(onRunLocalAction).toHaveBeenCalledTimes(2);
  });

  it("reenables GitHub sign-in when stale native auth resolves after reconnect", async () => {
    const pending = makeStatus({
      github: { ok: false, account: null, detail: "Not signed in to GitHub." },
    });
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(pending);
    const nativeAuth = deferred<Awaited<ReturnType<React.ComponentProps<typeof OnboardingView>["onRunLocalAction"]>>>();
    const onRunLocalAction = vi.fn(() => nativeAuth.promise);
    const props = {
      baseUrl: "http://127.0.0.1:7010",
      loading: false,
      canRun: true,
      nativeBusy: null,
      nativeResult: null,
      onConnectServer: vi.fn(),
      onInstallCore: vi.fn(),
      onStartRuntime: vi.fn(),
      onRunLocalAction,
      onFinish: vi.fn(),
      onRefreshBoard: vi.fn(async () => undefined),
      ...defaultRosterProps(),
    };
    const view = render(<OnboardingView {...props} connected />);
    const user = userEvent.setup();
    await gotoStep(user, /^github$/i);

    await user.click(await screen.findByRole("button", { name: /sign in with github/i }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /starting/i })).toBeDisabled(),
    );

    view.rerender(<OnboardingView {...props} connected={false} />);
    view.rerender(<OnboardingView {...props} connected />);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /sign in with github/i })).toBeEnabled(),
    );
    expect(screen.getByText(/GitHub sign-in was interrupted/i)).toBeInTheDocument();

    nativeAuth.resolve({
      command: ["gh", "auth", "login", "--web"],
      stdout: "",
      stderr: "",
      status: null,
      success: true,
      pid: 42,
      message: "GitHub sign-in started. Enter the one-time code in your browser.",
      github_auth: {
        device_url: "https://github.com/login/device",
        device_code: "ABCD-1234",
        poll_interval_ms: 250,
        timeout_ms: 1_000,
      },
    });

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /sign in with github/i })).toBeEnabled(),
    );
    expect(screen.queryByRole("button", { name: /waiting for github/i })).not.toBeInTheDocument();
  });

  it("falls back to copy-paste gh auth + recheck in browser mode", async () => {
    vi.spyOn(apiClient, "supportsNativeActions").mockReturnValue(false);
    const refetch = vi
      .spyOn(apiSetup, "loadSetupStatus")
      .mockResolvedValue(
        makeStatus({
          engine_ready: false,
          github: { ok: false, account: null, detail: "Not signed in to GitHub." },
        }),
      );
    renderOnboarding({ canRun: false });
    const user = userEvent.setup();
    await gotoStep(user, /^github$/i);
    expect(screen.queryByRole("button", { name: /sign in with github/i })).not.toBeInTheDocument();
    await user.click(screen.getByText(/advanced: terminal fallback/i));
    expect(screen.getByText("gh auth login --web")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /recheck github/i }));
    await waitFor(() => expect(refetch).toHaveBeenCalled());
  });

  it("loads, picks, and saves repositories leading with name + description", async () => {
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(makeIndexedStatus());
    const save = vi.spyOn(apiSetup, "saveSetupRepos").mockResolvedValue({
      ok: true,
      repos: ["octocat/web"],
      repo_checkouts: [VERIFIED_CHECKOUT],
      env_path: "/home/.alfred/.env",
      keys: ["ALFRED_QUEUE_REPOS", "ALFRED_SHIPPED_REPOS"],
    });
    const onRunLocalAction = vi.fn(async () => ({
      command: ["alfred", "code-memory", "index"],
      stdout: "",
      stderr: "",
      status: 0,
      success: true,
      pid: 1,
      message: "Code graph indexed.",
    }));
    renderOnboarding({ onRunLocalAction });
    const user = userEvent.setup();

    // Forward flow auto-advances Tools + GitHub (both detected) onto Repositories.
    await user.click(await screen.findByRole("button", { name: /get started/i }));
    await screen.findByRole("textbox", { name: /search repositories/i });
    // Leads with the short name and the description, with the full slug present.
    await waitFor(() => expect(screen.getByText("web")).toBeInTheDocument());
    expect(screen.getByText(/the marketing site/i)).toBeInTheDocument();
    expect(screen.getByText("octocat/web")).toBeInTheDocument();
    // Private badge on the private repo.
    expect(screen.getByText(/private/i)).toBeInTheDocument();

    await selectWebCheckout(user);
    await user.click(screen.getByRole("button", { name: /save 1 selected/i }));

    await waitFor(() =>
      expect(save).toHaveBeenCalledWith(
        "http://127.0.0.1:7010",
        ["octocat/web"],
        [{ repo: "octocat/web", path: "/workspace/web" }],
      ),
    );
    expect(onRunLocalAction).toHaveBeenCalledWith({
      action: "code_memory_index",
      refreshAfter: true,
    });
    await waitFor(() =>
      expect(
        screen.getByText(/saved and verified 1 repository, then built the code graph/i),
      ).toBeInTheDocument(),
    );
  });

  it("filters repositories and chooses a checkout with the native folder picker", async () => {
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(makeIndexedStatus());
    const pickFolder = vi
      .spyOn(apiSetup, "pickSetupRepoFolder")
      .mockResolvedValue("/workspace/api");
    renderOnboarding();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /get started/i }));
    const search = await screen.findByRole("textbox", { name: /search repositories/i });
    await user.type(search, "api");

    expect(screen.getByText("octocat/api")).toBeInTheDocument();
    expect(screen.queryByText("octocat/web")).not.toBeInTheDocument();

    await user.click(screen.getByRole("checkbox", { name: /octocat\/api/i }));
    await user.click(
      screen.getByRole("button", { name: /choose checkout folder for octocat\/api/i }),
    );

    expect(pickFolder).toHaveBeenCalledWith(undefined);
    expect(screen.getByRole("textbox", { name: /local checkout for octocat\/api/i })).toHaveValue(
      "/workspace/api",
    );
    expect(screen.getByRole("button", { name: /save 1 selected/i })).toBeEnabled();
  });

  it("keeps path entry available but disables the folder picker outside the native app", async () => {
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(makeIndexedStatus());
    renderOnboarding({ canRun: false });
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /get started/i }));
    await user.click(await screen.findByRole("checkbox", { name: /octocat\/web/i }));

    expect(screen.getByRole("textbox", { name: /local checkout for octocat\/web/i })).toBeEnabled();
    expect(
      screen.getByRole("button", { name: /choose checkout folder for octocat\/web/i }),
    ).toBeDisabled();
  });

  it("clears an existing repository scope", async () => {
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      makeStatus({
        repos: {
          selected: ["octocat/web"],
          count: 1,
          keys: ["ALFRED_QUEUE_REPOS", "ALFRED_SHIPPED_REPOS"],
          repo_checkouts: [VERIFIED_CHECKOUT],
        },
      }),
    );
    vi.spyOn(apiSetup, "loadSetupRepos").mockResolvedValue({
      ...REPOS,
      selected: ["octocat/web"],
      repo_checkouts: [VERIFIED_CHECKOUT],
    });
    const save = vi.spyOn(apiSetup, "saveSetupRepos").mockResolvedValue({
      ok: true,
      repos: [],
      repo_checkouts: [],
      env_path: "/home/.alfred/.env",
      keys: ["ALFRED_QUEUE_REPOS", "ALFRED_SHIPPED_REPOS"],
    });
    renderOnboarding();
    const user = userEvent.setup();

    await gotoStep(user, /^repositories$/i);
    await user.click(await screen.findByRole("checkbox", { name: /octocat\/web/i }));
    await user.click(screen.getByRole("button", { name: /clear repository scope/i }));

    expect(save).toHaveBeenCalledWith("http://127.0.0.1:7010", [], []);
    expect(await screen.findByText(/cleared repository scope/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^continue$/i })).toBeDisabled();
  });

  it("cannot clear an existing scope before the repository list loads", async () => {
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      makeStatus({
        repos: {
          selected: ["octocat/web"],
          count: 1,
          keys: ["ALFRED_QUEUE_REPOS", "ALFRED_SHIPPED_REPOS"],
          repo_checkouts: [VERIFIED_CHECKOUT],
        },
      }),
    );
    const repos = deferred<SetupReposResponse>();
    vi.spyOn(apiSetup, "loadSetupRepos").mockReturnValue(repos.promise);
    renderOnboarding();
    const user = userEvent.setup();

    await gotoStep(user, /^repositories$/i);

    expect(screen.getByRole("button", { name: /clear repository scope/i })).toBeDisabled();
    repos.resolve({
      ...REPOS,
      selected: ["octocat/web"],
      repo_checkouts: [VERIFIED_CHECKOUT],
    });
    expect(await screen.findByRole("checkbox", { name: /octocat\/web/i })).toBeChecked();
  });

  it("surfaces a graph failure without blocking the saved repository scope", async () => {
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(makeStatus());
    vi.spyOn(apiSetup, "saveSetupRepos").mockResolvedValue({
      ok: true,
      repos: ["octocat/web"],
      repo_checkouts: [VERIFIED_CHECKOUT],
      env_path: "/home/.alfred/.env",
      keys: ["ALFRED_QUEUE_REPOS", "ALFRED_SHIPPED_REPOS"],
    });
    const onRunLocalAction = vi.fn(async () => ({
      command: ["alfred", "code-memory", "index"],
      stdout: "",
      stderr: "",
      status: 0,
      success: true,
      pid: 1,
      message: "No local repositories found.",
    }));
    renderOnboarding({ onRunLocalAction });
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /get started/i }));
    await selectWebCheckout(user);
    await user.click(screen.getByRole("button", { name: /save 1 selected/i }));

    expect(await screen.findByText(/no code graph was produced/i)).toBeInTheDocument();
    expect(screen.queryByText(/built the code graph/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^continue$/i })).toBeEnabled();
  });

  it("retries code-graph indexing without saving repository scope again", async () => {
    let graphReady = false;
    vi.spyOn(apiSetup, "loadSetupStatus").mockImplementation(async () =>
      graphReady ? makeIndexedStatus() : makeStatus(),
    );
    const save = vi.spyOn(apiSetup, "saveSetupRepos").mockResolvedValue({
      ok: true,
      repos: ["octocat/web"],
      repo_checkouts: [VERIFIED_CHECKOUT],
      env_path: "/home/.alfred/.env",
      keys: ["ALFRED_QUEUE_REPOS", "ALFRED_SHIPPED_REPOS"],
    });
    const onRunLocalAction = vi.fn(async () => {
      if (onRunLocalAction.mock.calls.length > 1) graphReady = true;
      return {
        command: ["alfred", "code-memory", "index"],
        stdout: "",
        stderr: "",
        status: 0,
        success: true,
        pid: 1,
        message: "Code graph indexed.",
      };
    });
    renderOnboarding({ onRunLocalAction });
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /get started/i }));
    await selectWebCheckout(user);
    await user.click(screen.getByRole("button", { name: /save 1 selected/i }));

    await user.click(await screen.findByRole("button", { name: /retry code graph/i }));

    await waitFor(() => expect(onRunLocalAction).toHaveBeenCalledTimes(2));
    expect(save).toHaveBeenCalledTimes(1);
    expect(await screen.findByText(/code graph now covers/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /retry code graph/i })).not.toBeInTheDocument();
  });

  it("shows exact GitHub remote mismatches beside the selected checkout", async () => {
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(makeStatus());
    vi.spyOn(apiSetup, "saveSetupRepos").mockRejectedValue(
      new apiSetup.SetupRepoCheckoutValidationError([
        {
          ...VERIFIED_CHECKOUT,
          path: "/workspace/api",
          github_remote_repo: "octocat/api",
          identity_matches: false,
          ready: false,
          reason: "remote_mismatch",
        },
      ]),
    );
    renderOnboarding();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /get started/i }));
    await selectWebCheckout(user);
    await user.click(screen.getByRole("button", { name: /save 1 selected/i }));

    expect(
      await screen.findByText(/origin points to octocat\/api, not octocat\/web/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^continue$/i })).toBeDisabled();
  });

  it("does not accept a same-basename graph for a different repository", async () => {
    const staleStatus = makeIndexedStatus();
    staleStatus.code_memory_coverage = {
      ready: false,
      covered: ["other/web"],
      missing: ["octocat/web"],
    };
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(staleStatus);
    vi.spyOn(apiSetup, "saveSetupRepos").mockResolvedValue({
      ok: true,
      repos: ["octocat/web"],
      repo_checkouts: [VERIFIED_CHECKOUT],
      env_path: "/home/.alfred/.env",
      keys: ["ALFRED_QUEUE_REPOS", "ALFRED_SHIPPED_REPOS"],
    });
    const onRunLocalAction = vi.fn(async () => ({
      command: ["alfred", "code-memory", "index"],
      stdout: "",
      stderr: "",
      status: 0,
      success: true,
      pid: 1,
      message: "Code graph indexed.",
    }));
    renderOnboarding({ onRunLocalAction });
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /get started/i }));
    await selectWebCheckout(user);
    await user.click(screen.getByRole("button", { name: /save 1 selected/i }));

    expect(await screen.findByText(/does not cover every selected repository/i)).toBeInTheDocument();
    expect(screen.queryByText(/built the code graph/i)).not.toBeInTheDocument();
  });

  it("saves repositories without invoking code memory when Graphify is selected", async () => {
    const graphifyStatus = makeStatus({
      capability_plane: {
        version: 1,
        summary: { ready: 1, actionable: 0, disabled: 0, total: 1 },
        capabilities: [
          {
            key: "code_graph",
            title: "Code graph memory",
            category: "memory",
            recommended: true,
            state: "ready",
            installed: true,
            enabled: true,
            detail: "Graphify is ready.",
            detected: { engine: "graphify", graph_present: true },
            install_hint: "",
            source: { source: "graphifyy" },
          },
        ],
      },
    });
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(graphifyStatus);
    vi.spyOn(apiSetup, "saveSetupRepos").mockResolvedValue({
      ok: true,
      repos: ["octocat/web"],
      repo_checkouts: [VERIFIED_CHECKOUT],
      env_path: "/home/.alfred/.env",
      keys: ["ALFRED_QUEUE_REPOS", "ALFRED_SHIPPED_REPOS"],
    });
    const onRunLocalAction = vi.fn(async () => ({
      command: ["alfred", "code-memory", "index"],
      stdout: "",
      stderr: "",
      status: 0,
      success: true,
      pid: 1,
      message: "Code graph indexed.",
    }));
    renderOnboarding({ onRunLocalAction });
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /get started/i }));
    await selectWebCheckout(user);
    await user.click(screen.getByRole("button", { name: /save 1 selected/i }));

    await waitFor(() =>
      expect(screen.getByText(/saved and verified 1 repository\./i)).toBeInTheDocument(),
    );
    expect(onRunLocalAction).not.toHaveBeenCalled();
  });

  it("keeps a browser-only repository save complete when graph indexing cannot run", async () => {
    vi.spyOn(apiSetup, "saveSetupRepos").mockResolvedValue({
      ok: true,
      repos: ["octocat/web"],
      repo_checkouts: [VERIFIED_CHECKOUT],
      env_path: "/home/.alfred/.env",
      keys: ["ALFRED_QUEUE_REPOS", "ALFRED_SHIPPED_REPOS"],
    });
    renderOnboarding({ canRun: false });
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /get started/i }));
    await selectWebCheckout(user);
    await user.click(screen.getByRole("button", { name: /save 1 selected/i }));

    expect(await screen.findByText(/open the desktop app to build the code graph/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^continue$/i })).toBeEnabled();
  });

  it("blocks the repo step until GitHub is connected", async () => {
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      makeStatus({
        engine_ready: false,
        github: { ok: false, account: null, detail: "Not signed in to GitHub." },
      }),
    );
    renderOnboarding();
    const user = userEvent.setup();
    await gotoStep(user, /^repositories$/i);
    expect(screen.getByText(/connect github first/i)).toBeInTheDocument();
  });

  it("keeps first-job actions and the final exit locked until required setup is ready", async () => {
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      makeStatus({
        engine_ready: false,
        github: { ok: false, account: null, detail: "Not signed in to GitHub." },
      }),
    );
    renderOnboarding();
    const user = userEvent.setup();

    await gotoStep(user, /^first request$/i);
    expect(await screen.findByText(/finish tools, github, and repositories/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/onboarding steps complete/i)).toHaveTextContent(/0 of 8/i);
    expect(screen.getAllByRole("button", { name: /use this/i })[0]).toBeDisabled();
    expect(screen.getByRole("button", { name: /show me a sample first/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /write a brief in ask/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /go to inbox/i })).toBeDisabled();
  });

  it("honors server first-run blockers after engine, GitHub, and repos are ready", async () => {
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      makeReadyStatus({
        first_run: {
          version: 1,
          ready: false,
          status: "needs_action",
          headline: "2 required setup items need action.",
          summary: {
            required_ready: 5,
            required_total: 7,
            recommended_ready: 0,
            recommended_total: 0,
            optional_ready: 0,
            optional_total: 0,
            blockers: ["queue_coverage", "repo_local_paths"],
          },
          checks: [],
        },
      }),
    );
    renderOnboarding();
    const user = userEvent.setup();

    await gotoStep(user, /^first request$/i);
    expect(screen.getAllByRole("button", { name: /use this/i })[0]).toBeDisabled();
    expect(screen.getByRole("button", { name: /show me a sample first/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /go to inbox/i })).toBeDisabled();
  });

  it("requires a first job before the onboarding footer exits to Inbox", async () => {
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(makeReadyStatus());
    vi.spyOn(apiSetup, "seedSetupDemo").mockResolvedValue({ seeded: true });
    renderOnboarding();
    const user = userEvent.setup();

    await gotoStep(user, /^first request$/i);
    const finish = await screen.findByRole("button", { name: /go to inbox/i });
    expect(finish).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /show me a sample first/i }));
    await waitFor(() => expect(finish).toBeEnabled());
  });

  it("lets the user choose the agent naming theme before Slack", async () => {
    const onRosterThemeChange = vi.fn();
    const onEditCustomTheme = vi.fn();
    renderOnboarding({
      rosterTheme: "transformers",
      onRosterThemeChange,
      onEditCustomTheme,
    });
    const user = userEvent.setup();
    await gotoStep(user, /^team$/i);

    const stepper = screen.getByRole("navigation", { name: /onboarding progress/i });
    expect(within(stepper).getByRole("button", { current: "step" })).toHaveAccessibleName(
      /^team$/i,
    );
    expect(within(stepper).getByLabelText("0 of 8 onboarding steps complete")).toBeInTheDocument();
    expect(screen.getByText(/active roster/i)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /transformers/i })).toBeInTheDocument();
    expect(screen.getByText("Optimus Prime")).toBeInTheDocument();
    expect(
      screen.getByText(/roles, permissions, schedules, labels, worktrees, and merge gates stay unchanged/i),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /customize/i }));
    expect(onEditCustomTheme).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("combobox", { name: /roster theme/i }));
    await user.click(await screen.findByRole("option", { name: /justice league/i }));
    expect(onRosterThemeChange).toHaveBeenCalledWith("justice-league");

    // Completing a stepper-selected step records that step, not a fabricated
    // linear prefix and not a permanently frozen zero.
    await user.click(screen.getByRole("button", { name: /^continue$/i }));
    expect(within(stepper).getByRole("button", { current: "step" })).toHaveAccessibleName(
      /^slack$/i,
    );
    expect(within(stepper).getByLabelText("1 of 8 onboarding steps complete")).toBeInTheDocument();
  });

  it("records a battery configured inside a stepper-selected step", async () => {
    const manifest: SetupBatteryManifest = {
      version: 1,
      summary: { total: 1 },
      batteries: [
        {
          id: "dense-embeddings",
          name: "Dense embeddings",
          category: "memory",
          what: "A vector recall arm.",
          how_it_helps: "Finds relevant lessons across different wording.",
          builtin: false,
          default_on: false,
          status: "available",
          configured: false,
          enabled: false,
          installed: true,
          requires_daemon: false,
          service: "Ollama",
          install_kind: "pip-extra",
          install_hint: "Install the vector extra.",
          pip_extra: "vector",
          env_keys: ["ALFRED_MEMORY_SQLITE_DENSE"],
          docs: "docs/MEMORY_PROVIDERS.md",
        },
      ],
    };
    vi.spyOn(apiSetup, "loadSetupBatteries").mockResolvedValue(manifest);
    vi.spyOn(apiSetup, "saveSetupBattery").mockResolvedValue({
      ok: true,
      battery: "dense-embeddings",
      configured: true,
      enabled: true,
      env_path: "/home/.alfred/.env",
      keys: ["ALFRED_MEMORY_SQLITE_DENSE"],
      manifest: {
        ...manifest,
        batteries: [{ ...manifest.batteries[0], configured: true, enabled: true }],
      },
    });
    renderOnboarding({ canRun: false });
    const user = userEvent.setup();

    await gotoStep(user, /^batteries$/i);
    await user.click(await screen.findByRole("switch", { name: /enable dense embeddings/i }));

    await waitFor(() =>
      expect(screen.getByLabelText(/onboarding steps complete/i)).toHaveTextContent(/1 of 8/i),
    );
  });

  it("treats Slack as optional and skippable, advancing to the first request", async () => {
    renderOnboarding();
    const user = userEvent.setup();
    await gotoStep(user, /^slack$/i);
    expect(screen.getByText(/want approvals and questions in slack/i)).toBeInTheDocument();
    // Skip is a first-class button, not a tiny link.
    await user.click(screen.getByRole("button", { name: /skip for now/i }));
    await waitFor(() =>
      expect(screen.getByText(/pick something for alfred to do first/i)).toBeInTheDocument(),
    );
  });

  it("lets a Dev add a trusted Slack approver", async () => {
    const add = vi.spyOn(apiSlack, "addTrustedSlackUser").mockResolvedValue({
      operator_user_id: null,
      users: [
        { user_id: "U999", sources: ["onboarding"], added_at: null, added_by: null, can_remove: true },
      ],
      state_path: "/tmp/trusted.json",
      added: true,
    });
    renderOnboarding();
    const user = userEvent.setup();
    await gotoStep(user, /^slack$/i);
    await user.click(screen.getByText(/add a slack approver/i));
    await user.type(screen.getByLabelText(/slack user id/i), "U999");
    await user.click(screen.getByRole("button", { name: /^trust$/i }));
    await waitFor(() => expect(add).toHaveBeenCalledWith("http://127.0.0.1:7010", "U999"));
    await waitFor(() => expect(screen.getByText("U999")).toBeInTheDocument());
    expect(screen.getByLabelText(/onboarding steps complete/i)).toHaveTextContent(/1 of 8/i);
  });

  it("composes a starter spec into a real first request and lands on Ask", async () => {
    const compose = vi.spyOn(apiSetup, "composeSetupPlaybook").mockResolvedValue({
      ok: true,
      playbook: "triage-prs",
      draft_id: "compose-x",
      saved_path: "/p.json",
      title: "Nightly: triage open pull requests",
      repos: ["octocat/web"],
      readiness: { ok: false, score: 0.4 },
    });
    const onFinish = vi.fn();
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(makeReadyStatus());
    renderOnboarding({ onFinish });
    const user = userEvent.setup();

    await gotoStep(user, /^first request$/i);
    await waitFor(() =>
      expect(screen.getByText(/triage open prs every night/i)).toBeInTheDocument(),
    );
    const card = screen.getByText(/triage open prs every night/i).closest("[data-slot='card']");
    await user.click(within(card as HTMLElement).getByRole("button", { name: /use this/i }));

    await waitFor(() => expect(compose).toHaveBeenCalledWith("http://127.0.0.1:7010", "triage-prs"));
    await waitFor(() => expect(onFinish).toHaveBeenCalledWith("compose"));
  });

  it("keeps a drafted starter request single-flight while the app opens Ask", async () => {
    const compose = vi.spyOn(apiSetup, "composeSetupPlaybook").mockResolvedValue({
      ok: true,
      playbook: "triage-prs",
      draft_id: "compose-x",
      saved_path: "/p.json",
      title: "Nightly: triage open pull requests",
      repos: ["octocat/web"],
      readiness: { ok: false, score: 0.4 },
    });
    const finish = deferred<boolean>();
    const onFinish = vi.fn(() => finish.promise);
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(makeReadyStatus());
    renderOnboarding({ onFinish });
    const user = userEvent.setup();

    await gotoStep(user, /^first request$/i);
    const card = (await screen.findByText(/triage open prs every night/i)).closest(
      "[data-slot='card']",
    );
    const button = within(card as HTMLElement).getByRole("button", { name: /use this/i });
    await user.click(button);

    await waitFor(() => expect(onFinish).toHaveBeenCalledWith("compose"));
    expect(button).toBeDisabled();
    await user.click(button);
    expect(compose).toHaveBeenCalledTimes(1);

    finish.resolve(true);
  });

  it("seeds a labelled demo lifecycle and lands on a populated Inbox", async () => {
    const seed = vi.spyOn(apiSetup, "seedSetupDemo").mockResolvedValue({ seeded: true });
    const onFinish = vi.fn();
    const onRefreshBoard = vi.fn(async () => undefined);
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(makeReadyStatus());
    renderOnboarding({ onFinish, onRefreshBoard });
    const user = userEvent.setup();

    await gotoStep(user, /^first request$/i);
    await user.click(await screen.findByRole("button", { name: /show me a sample first/i }));
    await waitFor(() => expect(seed).toHaveBeenCalledWith("http://127.0.0.1:7010"));
    await waitFor(() => expect(onRefreshBoard).toHaveBeenCalledWith({ demo: true }));
    // The sample is not a one-way door: an "Open Inbox" control lands the user on
    // the populated board only when they choose to.
    await user.click(await screen.findByRole("button", { name: /open inbox/i }));
    await waitFor(() => expect(onFinish).toHaveBeenCalledWith("home"));
  });

  it("clears the seeded sample and flips the board back out of demo mode", async () => {
    vi.spyOn(apiSetup, "seedSetupDemo").mockResolvedValue({ seeded: true });
    const clear = vi.spyOn(apiSetup, "clearSetupDemo").mockResolvedValue({ cleared: true });
    const onRefreshBoard = vi.fn(async () => undefined);
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(makeReadyStatus());
    renderOnboarding({ onRefreshBoard });
    const user = userEvent.setup();

    await gotoStep(user, /^first request$/i);
    await user.click(await screen.findByRole("button", { name: /show me a sample first/i }));
    // Once seeded, the step surfaces a visible clear control instead of stranding
    // the user in demo mode.
    const clearButton = await screen.findByRole("button", { name: /clear sample data/i });
    await user.click(clearButton);
    await waitFor(() => expect(clear).toHaveBeenCalledWith("http://127.0.0.1:7010"));
    await waitFor(() => expect(onRefreshBoard).toHaveBeenCalledWith({ demo: false }));
    // The clear returns the step to its pre-seed offer.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /show me a sample first/i })).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: /go to inbox/i })).toBeDisabled();
  });

  it("shows the clear-sample exit when the server already reports demo present", async () => {
    // Simulate a remount after the sample was seeded in a prior mount (open
    // Inbox, reload, navigate back). The in-component seed flag has reset, but
    // the server still reports demo.present, so the step must derive the
    // "Clear sample data" exit from server truth rather than strand the user.
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      makeReadyStatus({ demo: { present: true } }),
    );
    renderOnboarding();
    const user = userEvent.setup();

    await gotoStep(user, /^first request$/i);
    // No seed click in this mount, yet the clear control is present.
    expect(
      await screen.findByRole("button", { name: /clear sample data/i }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /show me a sample first/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /go to inbox/i })).toBeEnabled();
  });

  it("relocks the Inbox after clearing server-reported demo state even when refresh fails", async () => {
    vi.spyOn(apiSetup, "clearSetupDemo").mockResolvedValue({ cleared: true });
    const onRefreshBoard = vi.fn(async () => {
      throw new Error("board refresh failed");
    });
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      makeReadyStatus({ demo: { present: true } }),
    );
    renderOnboarding({ onRefreshBoard });
    const user = userEvent.setup();

    await gotoStep(user, /^first request$/i);
    await user.click(await screen.findByRole("button", { name: /clear sample data/i }));

    await waitFor(() => expect(onRefreshBoard).toHaveBeenCalledWith({ demo: false }));
    expect(screen.getByRole("button", { name: /go to inbox/i })).toBeDisabled();
    expect(screen.getByText(/cleared the sample data/i)).toBeInTheDocument();
    expect(screen.queryByText(/could not clear the sample/i)).not.toBeInTheDocument();
  });

  it("does not let Enter bypass a missing required engine", async () => {
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      makeStatus({
        engine_ready: false,
        github: { ok: false, account: null, detail: "Not signed in to GitHub." },
      }),
    );
    renderOnboarding();
    const user = userEvent.setup();
    // Land on Tools via the stepper.
    await gotoStep(user, /^tools$/i);
    expect(screen.getByRole("button", { name: /check my tools/i })).toBeInTheDocument();
    const section = screen.getByLabelText(/set up alfred/i);
    fireEvent.keyDown(section, { key: "Enter" });
    expect(screen.getByRole("button", { name: /check my tools/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^continue$/i })).toBeDisabled();
  });

  it("does not expose application navigation before setup is complete", async () => {
    renderOnboarding();
    expect(screen.queryByRole("button", { name: /advanced setup/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: /primary/i })).not.toBeInTheDocument();
  });

  it("degrades mutating steps gracefully off-Tauri with a clear note", async () => {
    // The token-less dev preview: neither native actions nor token-gated HTTP
    // mutations are available, so the mutating steps show the read-only note.
    vi.spyOn(apiClient, "supportsNativeActions").mockReturnValue(false);
    vi.spyOn(apiClient, "supportsMutations").mockReturnValue(false);
    renderOnboarding({ canRun: false });
    const user = userEvent.setup();
    await gotoStep(user, /^first request$/i);
    await waitFor(() =>
      expect(screen.getByText(/triage open prs every night/i)).toBeInTheDocument(),
    );
    expect(screen.getAllByText(/desktop app/i).length).toBeGreaterThan(0);
    // The demo seed control is disabled in the browser preview.
    expect(screen.getByRole("button", { name: /show me a sample first/i })).toBeDisabled();
  });

  it("surfaces a setup-status read error without blanking the steps", async () => {
    vi.spyOn(apiSetup, "loadSetupStatus").mockRejectedValue(new Error("boom"));
    renderOnboarding();
    expect(await screen.findByText(/manual fallback/i)).toBeInTheDocument();
    // The welcome step still renders.
    expect(screen.getByText(/let's get you set up/i)).toBeInTheDocument();
  });

  it("tracks progress in the stepper as steps complete", async () => {
    // gh + engine ready: the forward flow auto-advances through Tools + GitHub,
    // so the progress label reflects real completion.
    renderOnboarding();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /get started/i }));
    expect(await screen.findByRole("textbox", { name: /search repositories/i })).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByLabelText(/onboarding steps complete/i)).toBeInTheDocument(),
    );
  });

  it("renders a persistent numbered stepper with current and upcoming states", async () => {
    // Engine + gh not detected so nothing auto-advances and Welcome stays current.
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      makeStatus({
        engine_ready: false,
        github: { ok: false, account: null, detail: "Not signed in to GitHub." },
      }),
    );
    renderOnboarding();
    const stepper = await screen.findByRole("navigation", { name: /onboarding progress/i });
    // The active node carries aria-current="step" and is the Welcome node.
    const current = within(stepper).getByRole("button", { current: "step" });
    expect(current).toHaveAccessibleName(/welcome/i);
    // All seven numbered nodes are present and queryable by their bare labels.
    for (const label of [
      /^welcome$/i,
      /^tools$/i,
      /^github$/i,
      /^repositories$/i,
      /^team$/i,
      /^slack$/i,
      /^first request$/i,
    ]) {
      expect(within(stepper).getByRole("button", { name: label })).toBeInTheDocument();
    }
  });

  it("marks detected steps done in the stepper", async () => {
    // engine_ready + github ok: once the forward flow lands on Repositories,
    // Welcome, Tools, and GitHub read as done (aria-current moves to repos).
    renderOnboarding();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /get started/i }));
    await screen.findByRole("textbox", { name: /search repositories/i });
    const stepper = screen.getByRole("navigation", { name: /onboarding progress/i });
    const current = within(stepper).getByRole("button", { current: "step" });
    expect(current).toHaveAccessibleName(/repositories/i);
    // The completion count reflects the three detected-done steps.
    expect(within(stepper).getByLabelText(/onboarding steps complete/i)).toHaveTextContent(
      /3 of 8/i,
    );
  });

  it("opens on Welcome at 0 of 8 done even when tools, gh and repos are pre-detected", async () => {
    // Regression for the broken progress logic: on a fresh launch where Claude
    // Code is installed, gh is already signed in, and repos are already saved,
    // the stepper used to show "3 of 7 done" while the user was still on step 1
    // (Welcome). The count must reflect where the user actually is, so a step the
    // user has not reached never reads done even when its signal is satisfied.
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      makeStatus({
        engine_ready: true,
        github: { ok: true, account: "octocat", detail: "Signed in to GitHub as octocat." },
        repos: {
          selected: ["octocat/web"],
          count: 1,
          keys: ["ALFRED_QUEUE_REPOS", "ALFRED_SHIPPED_REPOS"],
          repo_checkouts: [],
        },
      }),
    );
    renderOnboarding();
    const stepper = await screen.findByRole("navigation", { name: /onboarding progress/i });
    // The active node is Welcome and nothing reads done.
    const current = within(stepper).getByRole("button", { current: "step" });
    expect(current).toHaveAccessibleName(/welcome/i);
    await waitFor(() =>
      expect(within(stepper).getByLabelText(/onboarding steps complete/i)).toHaveTextContent(
        /0 of 8/i,
      ),
    );
  });

  it("moves Back and Continue through the footer", async () => {
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      makeStatus({
        engine_ready: false,
        github: { ok: false, account: null, detail: "Not signed in to GitHub." },
      }),
    );
    renderOnboarding();
    const user = userEvent.setup();
    // From Welcome, Continue advances to Tools.
    await user.click(await screen.findByRole("button", { name: /^continue$/i }));
    expect(screen.getByRole("button", { name: /check my tools/i })).toBeInTheDocument();
    // Back returns to Welcome.
    await user.click(screen.getByRole("button", { name: /^back$/i }));
    expect(screen.getByText(/let's get you set up/i)).toBeInTheDocument();
    // Back is disabled on the first step.
    expect(screen.getByRole("button", { name: /^back$/i })).toBeDisabled();
  });
});

describe("OnboardingView conversational setup actions", () => {
  function makeNativeResult(
    overrides: Partial<import("../types").NativeCommandResult> = {},
  ): import("../types").NativeCommandResult {
    return {
      command: ["alfred"],
      stdout: "",
      stderr: "",
      status: 0,
      success: true,
      pid: 1,
      message: "ok",
      ...overrides,
    };
  }

  // Open the "Set it up by chatting" panel and send one message so the scripted
  // converse turn is issued.
  async function enterChatAndSend(user: ReturnType<typeof userEvent.setup>, text: string) {
    await user.click(await screen.findByRole("button", { name: /set it up by chatting/i }));
    const input = await screen.findByLabelText(/message alfred to set up/i);
    await user.type(input, text);
    await user.click(screen.getByRole("button", { name: /send/i }));
  }

  // A side-effectful action is parked behind an Approve button; click it so the
  // shared executor actually runs the step (the #415 human-gate).
  async function approveStep(user: ReturnType<typeof userEvent.setup>, buttonName: RegExp) {
    await user.click(await screen.findByRole("button", { name: buttonName }));
  }

  it("hands conversational repository selection to the local checkout step", async () => {
    const save = vi.spyOn(apiSetup, "saveSetupRepos").mockResolvedValue({
      ok: true,
      repos: ["octocat/web"],
      repo_checkouts: [VERIFIED_CHECKOUT],
      env_path: "/home/.alfred/.env",
      keys: ["ALFRED_QUEUE_REPOS", "ALFRED_SHIPPED_REPOS"],
    });
    vi.spyOn(apiSetup, "onboardingConverse").mockResolvedValueOnce({
      reply: "I can set up that repository.",
      action: { tool: "set_repos", args: { repos: ["octocat/web"] } },
      done: false,
    });
    const onRunLocalAction = vi.fn(async () => makeNativeResult());
    renderOnboarding({ onRunLocalAction });
    const user = userEvent.setup();

    await enterChatAndSend(user, "use octocat/web");
    await approveStep(user, /save repositories/i);

    expect(await screen.findByRole("textbox", { name: /search repositories/i })).toBeInTheDocument();
    expect(save).not.toHaveBeenCalled();
    expect(onRunLocalAction).not.toHaveBeenCalled();
  });

  it("carries a native Slack skip back into conversational completion", async () => {
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(makeReadyStatus());
    vi.spyOn(apiSetup, "onboardingConverse")
      .mockResolvedValueOnce({
        reply: "Keep the built-in batteries?",
        action: { tool: "skip_batteries", args: {} },
        done: false,
      })
      .mockResolvedValueOnce({
        reply: "Let's set up Slack locally.",
        action: { tool: "open_slack_setup", args: {} },
        done: false,
      })
      .mockResolvedValueOnce({
        reply: "Setup is ready.",
        action: { tool: "finish_setup", args: {} },
        done: true,
      });
    const onFinish = vi.fn();
    renderOnboarding({ onFinish });
    const user = userEvent.setup();

    await enterChatAndSend(user, "set up the optional services");
    await approveStep(user, /skip batteries/i);
    await approveStep(user, /open slack setup/i);

    expect(await screen.findByText(/want approvals and questions in slack/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/slack user id/i)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /skip for now/i }));
    await user.click(screen.getByRole("button", { name: /^welcome$/i }));
    await enterChatAndSend(user, "finish setup");
    await approveStep(user, /finish setup/i);

    expect(await screen.findByText(/pick something for alfred to do first/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/onboarding steps complete/i)).toHaveTextContent(/7 of 8/i);
    expect(onFinish).not.toHaveBeenCalled();
  });

  it("persists chat battery and Slack skips across a panel remount", async () => {
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(makeReadyStatus());
    vi.spyOn(apiSetup, "onboardingConverse")
      .mockResolvedValueOnce({
        reply: "Keep the built-in batteries?",
        action: { tool: "skip_batteries", args: {} },
        done: false,
      })
      .mockResolvedValueOnce({
        reply: "Skip Slack?",
        action: { tool: "skip_slack", args: {} },
        done: false,
      })
      .mockResolvedValueOnce({ reply: "Both choices are saved.", action: null, done: false })
      .mockResolvedValueOnce({
        reply: "Setup is ready.",
        action: { tool: "finish_setup", args: {} },
        done: true,
      });
    const onFinish = vi.fn();
    renderOnboarding({ onFinish });
    const user = userEvent.setup();

    await enterChatAndSend(user, "keep the defaults");
    await approveStep(user, /skip batteries/i);
    await approveStep(user, /skip slack/i);
    await user.click(await screen.findByRole("button", { name: /set up step by step/i }));
    expect(screen.getByLabelText(/onboarding steps complete/i)).toHaveTextContent(/2 of 8/i);
    await user.click(screen.getByRole("button", { name: /^welcome$/i }));
    await enterChatAndSend(user, "finish setup");
    await approveStep(user, /finish setup/i);

    expect(await screen.findByText(/pick something for alfred to do first/i)).toBeInTheDocument();
    expect(onFinish).not.toHaveBeenCalled();
  });

  it("reports a successful GitHub device flow to the model (Codex P2 fresh status)", async () => {
    // GitHub starts disconnected; the device flow succeeds and the poll lands on a
    // connected status. The executor must report success from the FRESH verdict,
    // not the stale pre-action render value.
    vi.spyOn(apiSetup, "loadSetupStatus")
      .mockResolvedValueOnce(
        makeStatus({ github: { ok: false, account: null, detail: "Not signed in to GitHub." } }),
      )
      .mockResolvedValue(
        makeStatus({ github: { ok: true, account: "octocat", detail: "Signed in as octocat." } }),
      );
    const onRunLocalAction = vi.fn(async () =>
      makeNativeResult({
        message: "GitHub sign-in started.",
        github_auth: {
          device_url: "https://github.com/login/device",
          device_code: "ABCD-1234",
          poll_interval_ms: 50,
          timeout_ms: 1_000,
        },
      }),
    );

    // First converse turn requests connect_github; capture the SECOND turn's
    // messages, which carry the threaded [setup] outcome note.
    let secondTurnMessages: { role: string; content: string }[] = [];
    const converse = vi
      .spyOn(apiSetup, "onboardingConverse")
      .mockResolvedValueOnce({
        reply: "Connecting GitHub.",
        action: { tool: "connect_github", args: {} },
        done: false,
      })
      .mockImplementationOnce(async (_base, request) => {
        secondTurnMessages = request.messages;
        return { reply: "Great, you are connected.", action: null, done: false };
      });

    renderOnboarding({ onRunLocalAction });
    const user = userEvent.setup();
    await enterChatAndSend(user, "connect github");

    // connect_github is side-effectful: approve it before it runs.
    await approveStep(user, /connect github/i);

    await waitFor(() => expect(converse).toHaveBeenCalledTimes(2));
    expect(onRunLocalAction).toHaveBeenCalledWith({ action: "github_auth_login" });
    // The outcome threaded back to the model reports success, not "did not complete".
    const note = secondTurnMessages.find((m) => m.content.includes("[setup] connect_github"));
    expect(note?.content).toContain("connect_github completed");
    expect(note?.content).not.toContain("did not complete");
  });

  it("persists the requested schedule through the native primitive (Codex P2)", async () => {
    // set_schedule with a daily cadence must call the native `alfred schedule set`
    // primitive for each scheduled agent, not just acknowledge it.
    vi.spyOn(apiSetup, "loadSchedule").mockResolvedValue({
      runs: [
        {
          codename: "architect",
          role: "architect",
          kind: "cron-daily",
          cadence: "daily at 09:00",
          next_fire_at: null,
          raw_schedule: "cron:9:0",
        },
        {
          codename: "senior-dev",
          role: "ops",
          kind: "interval",
          cadence: "every 20m",
          next_fire_at: null,
          raw_schedule: "interval:1200",
        },
      ],
    });
    const onRunLocalAction = vi.fn(async () => makeNativeResult({ message: "schedule set" }));
    let secondTurnMessages: { role: string; content: string }[] = [];
    const converse = vi
      .spyOn(apiSetup, "onboardingConverse")
      .mockResolvedValueOnce({
        reply: "Setting a daily cadence.",
        action: { tool: "set_schedule", args: { cadence: "daily" } },
        done: false,
      })
      .mockImplementationOnce(async (_base, request) => {
        secondTurnMessages = request.messages;
        return { reply: "Done.", action: null, done: false };
      });

    renderOnboarding({ onRunLocalAction });
    const user = userEvent.setup();
    await enterChatAndSend(user, "daily please");

    // set_schedule is side-effectful: approve it before it runs.
    await approveStep(user, /set schedule/i);

    await waitFor(() => expect(converse).toHaveBeenCalledTimes(2));
    // The cadence is mapped to the canonical schedule and written for each agent
    // through the SAME native primitive the Fleet view uses.
    expect(onRunLocalAction).toHaveBeenCalledWith({
      action: "schedule",
      target: "architect",
      cadence: "daily@09:00",
      refreshAfter: false,
    });
    expect(onRunLocalAction).toHaveBeenCalledWith({
      action: "schedule",
      target: "senior-dev",
      cadence: "daily@09:00",
      refreshAfter: false,
    });
    const note = secondTurnMessages.find((m) => m.content.includes("[setup] set_schedule"));
    expect(note?.content).toContain("set_schedule completed");
  });

  it("reports engine-present from fresh status on check_engine (Codex P2)", async () => {
    // check_engine must read the FRESH status the refresh fetched, not the stale
    // closure. Seed a fresh status that has an installed engine and assert the
    // outcome threaded to the model reports it found the engine.
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      makeStatus({
        engine_ready: true,
        engines: [
          { name: "claude", installed: true, path: "/opt/homebrew/bin/claude" },
          { name: "codex", installed: false, path: null },
        ],
      }),
    );
    let secondTurnMessages: { role: string; content: string }[] = [];
    const converse = vi
      .spyOn(apiSetup, "onboardingConverse")
      .mockResolvedValueOnce({
        reply: "Checking your tools.",
        action: { tool: "check_engine", args: {} },
        done: false,
      })
      .mockImplementationOnce(async (_base, request) => {
        secondTurnMessages = request.messages;
        return { reply: "Great.", action: null, done: false };
      });

    renderOnboarding();
    const user = userEvent.setup();
    // check_engine is read-only, so it auto-proceeds with no approval click.
    await enterChatAndSend(user, "start");

    await waitFor(() => expect(converse).toHaveBeenCalledTimes(2));
    const note = secondTurnMessages.find((m) => m.content.includes("[setup] check_engine"));
    expect(note?.content).toContain("check_engine completed");
    expect(note?.content).toContain("claude");
  });
});
