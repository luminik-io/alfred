import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as apiClient from "../api/client";
import * as apiSetup from "../api/setup";
import type { SetupStatus } from "../types";
import { SettingsView } from "./SettingsView";

function setupStatus(home: string, overrides: Partial<SetupStatus> = {}): SetupStatus {
  const base: SetupStatus = {
    github: { ok: true, account: "octocat", detail: "Signed in to GitHub as octocat." },
    engines: [{ name: "claude", installed: true, path: "/opt/homebrew/bin/claude" }],
    engine_ready: true,
    repos: {
      selected: ["octocat/web"],
      count: 1,
      keys: ["ALFRED_QUEUE_REPOS"],
      repo_checkouts: [],
    },
    demo: { present: false },
    ready: true,
    install: {
      alfred_home: home,
      env_path: `${home}/.env`,
      env_present: true,
      server_token_present: true,
      agents_conf_path: `${home}/launchd/agents.conf`,
      agents_conf_present: true,
      scheduled_runs: 1,
      selected_repos_env_present: true,
      slack_configured: false,
      memory_configured: false,
      initialized: true,
      items: [
        {
          key: "home",
          label: "Runtime home",
          ok: true,
          detail: `Found ${home}`,
          path: home,
        },
        {
          key: "env",
          label: "Configuration file",
          ok: true,
          detail: `Found ${home}/.env`,
          path: `${home}/.env`,
        },
      ],
    },
    first_run: {
      version: 1,
      ready: true,
      status: "ready",
      headline: "Ready for the first real run.",
      summary: {
        required_ready: 7,
        required_total: 7,
        recommended_ready: 1,
        recommended_total: 3,
        optional_ready: 0,
        optional_total: 2,
        blockers: [],
      },
      checks: [
        {
          key: "github",
          title: "GitHub auth",
          category: "auth",
          tier: "required",
          required: true,
          ready: true,
          state: "ready",
          detail: "Signed in.",
          action: "Run gh auth login.",
          path: null,
        },
        {
          key: "code_graph",
          title: "Code graph memory",
          category: "memory",
          tier: "recommended",
          required: false,
          ready: true,
          state: "ready",
          detail: "Code-memory binary and index are present.",
          action: "Run alfred code-memory doctor.",
          path: `${home}/state/code-memory`,
        },
      ],
    },
  };
  return { ...base, ...overrides };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function renderSettings(
  baseUrl: string,
  props: Partial<React.ComponentProps<typeof SettingsView>> = {},
) {
  return (
    <SettingsView
      baseUrl={baseUrl}
      loading={false}
      connected
      actionNotice={null}
      trustedSlack={null}
      busyTrustedUser={null}
      nativeBusy={null}
      themeName="alfred"
      mode="dark"
      onSelectTheme={vi.fn()}
      onSelectMode={vi.fn()}
      onAddTrustedUser={vi.fn()}
      onRemoveTrustedUser={vi.fn()}
      onRunLocalAction={vi.fn()}
      onInstallCore={vi.fn()}
      onStartRuntime={vi.fn()}
      onConnectServer={vi.fn()}
      {...props}
    />
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("SettingsView", () => {
  it("marks the surface ready only after runtime inventory settles", async () => {
    const request = deferred<SetupStatus>();
    vi.spyOn(apiClient, "supportsNativeActions").mockReturnValue(true);
    vi.spyOn(apiSetup, "loadSetupStatus").mockReturnValue(request.promise);

    render(renderSettings("http://127.0.0.1:7010"));

    const settings = screen.getByRole("region", { name: "Settings" });
    await waitFor(() => expect(settings).toHaveAttribute("data-ready", "false"));

    request.resolve(setupStatus("/tmp/alfred-home"));
    await waitFor(() => expect(settings).toHaveAttribute("data-ready", "true"));
  });

  it("keeps the surface unready when runtime inventory fails", async () => {
    vi.spyOn(apiClient, "supportsNativeActions").mockReturnValue(true);
    vi.spyOn(apiSetup, "loadSetupStatus").mockRejectedValue(
      new Error("inventory unavailable"),
    );

    render(renderSettings("http://127.0.0.1:7010"));

    expect(await screen.findByText(/inventory unavailable/i)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Settings" })).toHaveAttribute(
      "data-ready",
      "false",
    );
  });

  it("defaults diagnostics dry-run to the canonical senior-dev role", async () => {
    const user = userEvent.setup();
    const onRunLocalAction = vi.fn();
    vi.spyOn(apiClient, "supportsNativeActions").mockReturnValue(true);
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(setupStatus("/tmp/alfred-home"));

    render(renderSettings("http://127.0.0.1:7010", { onRunLocalAction }));

    await user.click(screen.getByRole("tab", { name: "Diagnostics" }));
    const input = await screen.findByLabelText("Dry-run agent");
    expect(input).toHaveValue("senior-dev");

    await user.click(screen.getByRole("button", { name: "Run dry-run" }));
    expect(onRunLocalAction).toHaveBeenCalledWith({
      action: "dry_run",
      target: "senior-dev",
      refreshAfter: true,
    });
  });

  it("blocks a diagnostics dry-run without an agent codename", async () => {
    const user = userEvent.setup();
    const onRunLocalAction = vi.fn();
    vi.spyOn(apiClient, "supportsNativeActions").mockReturnValue(true);
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(setupStatus("/tmp/alfred-home"));

    render(renderSettings("http://127.0.0.1:7010", { onRunLocalAction }));

    await user.click(screen.getByRole("tab", { name: "Diagnostics" }));
    const input = await screen.findByLabelText("Dry-run agent");
    await user.clear(input);
    await user.type(input, "   ");

    const button = screen.getByRole("button", { name: "Run dry-run" });
    expect(button).toBeDisabled();
    await user.click(button);
    expect(onRunLocalAction).not.toHaveBeenCalled();
  });

  it("clears displayed setup inventory while a new server URL is loading", async () => {
    const newRequest = deferred<SetupStatus>();
    vi.spyOn(apiClient, "supportsNativeActions").mockReturnValue(true);
    vi.spyOn(apiSetup, "loadSetupStatus")
      .mockResolvedValueOnce(setupStatus("/tmp/old-alfred-home"))
      .mockReturnValueOnce(newRequest.promise);

    const view = render(renderSettings("http://127.0.0.1:7010"));
    expect((await screen.findAllByText("/tmp/old-alfred-home")).length).toBeGreaterThan(0);

    view.rerender(renderSettings("http://127.0.0.1:7011"));

    await waitFor(() => {
      expect(screen.queryByText("/tmp/old-alfred-home")).not.toBeInTheDocument();
    });

    newRequest.resolve(setupStatus("/tmp/new-alfred-home"));
    expect((await screen.findAllByText("/tmp/new-alfred-home")).length).toBeGreaterThan(0);
  });

  it("ignores stale setup inventory reads after the server URL changes", async () => {
    const oldRequest = deferred<SetupStatus>();
    const newRequest = deferred<SetupStatus>();
    vi.spyOn(apiClient, "supportsNativeActions").mockReturnValue(true);
    vi.spyOn(apiSetup, "loadSetupStatus")
      .mockReturnValueOnce(oldRequest.promise)
      .mockReturnValueOnce(newRequest.promise);

    const view = render(renderSettings("http://127.0.0.1:7010"));
    view.rerender(renderSettings("http://127.0.0.1:7011"));

    newRequest.resolve(setupStatus("/tmp/new-alfred-home"));
    expect((await screen.findAllByText("/tmp/new-alfred-home")).length).toBeGreaterThan(0);

    oldRequest.resolve(setupStatus("/tmp/old-alfred-home"));
    await waitFor(() => {
      expect(screen.queryByText("/tmp/old-alfred-home")).not.toBeInTheDocument();
    });
  });

  it("ignores stale setup inventory after a same-url disconnect and reconnect", async () => {
    const staleRequest = deferred<SetupStatus>();
    const loadStatus = vi
      .spyOn(apiSetup, "loadSetupStatus")
      .mockReturnValueOnce(staleRequest.promise)
      .mockResolvedValue(setupStatus("/tmp/reconnected-alfred-home"));
    vi.spyOn(apiClient, "supportsNativeActions").mockReturnValue(true);

    const view = render(renderSettings("http://127.0.0.1:7010"));
    await waitFor(() => expect(loadStatus).toHaveBeenCalledTimes(1));

    view.rerender(renderSettings("http://127.0.0.1:7010", { connected: false }));
    view.rerender(renderSettings("http://127.0.0.1:7010", { connected: true }));

    expect((await screen.findAllByText("/tmp/reconnected-alfred-home")).length).toBeGreaterThan(
      0,
    );
    staleRequest.resolve(setupStatus("/tmp/stale-alfred-home"));

    await waitFor(() => {
      expect(screen.queryByText("/tmp/stale-alfred-home")).not.toBeInTheDocument();
    });
  });

  it("surfaces first-run readiness blockers on the Runtime tab", async () => {
    const user = userEvent.setup();
    const onRunLocalAction = vi.fn();
    vi.spyOn(apiClient, "supportsNativeActions").mockReturnValue(true);
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      setupStatus("/tmp/alfred-home", {
        first_run: {
          version: 1,
          ready: false,
          status: "needs_action",
          headline: "1 required setup item needs action.",
          summary: {
            required_ready: 6,
            required_total: 7,
            recommended_ready: 0,
            recommended_total: 3,
            optional_ready: 0,
            optional_total: 2,
            blockers: ["repo_local_paths"],
          },
          checks: [
            {
              key: "repo_local_paths",
              title: "Local repo paths",
              category: "repos",
              tier: "required",
              required: true,
              ready: false,
              state: "actionable",
              detail: "1 selected repo needs local path mapping.",
              action:
                "Clone the missing repo locally or set ALFRED_REPO_LOCAL_MAP with repo=path entries.",
              path: null,
            },
            {
              key: "code_graph",
              title: "Code graph memory",
              category: "memory",
              tier: "recommended",
              required: false,
              ready: false,
              state: "actionable",
              detail: "Code-memory binary is present; run an index before relying on graph queries.",
              action: "Run `alfred code-memory doctor`, then `alfred code-memory index`.",
              path: "/tmp/alfred-home/state/code-memory",
              detected: { capability_state: "needs_index", enabled: true },
            },
            {
              key: "engineering_skills",
              title: "Engineering skills",
              category: "skills",
              tier: "recommended",
              required: false,
              ready: false,
              state: "actionable",
              detail: "Starter engineering skills are not installed yet.",
              action: "Run `alfred skills install --starter`.",
              path: "/tmp/alfred-home/skills",
            },
          ],
        },
      }),
    );

    render(renderSettings("http://127.0.0.1:7010", { onRunLocalAction }));

    expect(await screen.findByText("Ready for first real run")).toBeInTheDocument();
    expect(screen.getByText("1 blocking")).toBeInTheDocument();
    expect(screen.getByText("Local repo paths")).toBeInTheDocument();
    expect(screen.getByText(/ALFRED_REPO_LOCAL_MAP/)).toBeInTheDocument();
    expect(screen.getByText("Code graph memory")).toBeInTheDocument();
    expect(screen.getByText("Engineering skills")).toBeInTheDocument();
    expect(screen.getByText(/0 of 3 recommended ready/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Index code memory" }));
    expect(onRunLocalAction).toHaveBeenCalledWith({
      action: "code_memory_index",
      refreshAfter: true,
    });

    await user.click(screen.getByRole("button", { name: "Install starter skills" }));
    expect(onRunLocalAction).toHaveBeenCalledWith({
      action: "skills_install_starter",
      refreshAfter: true,
    });
  });

  it("shows first-run repair progress while a native readiness action is busy", async () => {
    vi.spyOn(apiClient, "supportsNativeActions").mockReturnValue(true);
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      setupStatus("/tmp/alfred-home", {
        first_run: {
          version: 1,
          ready: false,
          status: "needs_action",
          headline: "Recommended setup can be improved.",
          summary: {
            required_ready: 7,
            required_total: 7,
            recommended_ready: 0,
            recommended_total: 3,
            optional_ready: 0,
            optional_total: 2,
            blockers: [],
          },
          checks: [
            {
              key: "engineering_skills",
              title: "Engineering skills",
              category: "skills",
              tier: "recommended",
              required: false,
              ready: false,
              state: "actionable",
              detail: "Starter engineering skills are not installed yet.",
              action: "Run `alfred skills install --starter`.",
              path: "/tmp/alfred-home/skills",
            },
          ],
        },
      }),
    );

    render(
      renderSettings("http://127.0.0.1:7010", {
        nativeBusy: "skills_install_starter:fleet",
      }),
    );

    const busyButton = await screen.findByRole("button", { name: "Installing skills" });
    expect(busyButton).toBeDisabled();
  });

  it("does not offer code-memory indexing when code memory is disabled", async () => {
    vi.spyOn(apiClient, "supportsNativeActions").mockReturnValue(true);
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      setupStatus("/tmp/alfred-home", {
        first_run: {
          version: 1,
          ready: false,
          status: "needs_action",
          headline: "Recommended setup can be improved.",
          summary: {
            required_ready: 7,
            required_total: 7,
            recommended_ready: 0,
            recommended_total: 3,
            optional_ready: 0,
            optional_total: 2,
            blockers: [],
          },
          checks: [
            {
              key: "code_graph",
              title: "Code graph memory",
              category: "memory",
              tier: "recommended",
              required: false,
              ready: false,
              state: "actionable",
              detail: "Code memory is disabled with ALFRED_CODE_MEMORY_MCP.",
              action: "Enable code memory before indexing.",
              path: "/tmp/alfred-home/state/code-memory",
              detected: { capability_state: "disabled", enabled: false },
            },
          ],
        },
      }),
    );

    render(renderSettings("http://127.0.0.1:7010"));

    expect(await screen.findByText("Code graph memory")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Index code memory" })).not.toBeInTheDocument();
  });

  it("runs the code-memory install repair before indexing on fresh machines", async () => {
    vi.spyOn(apiClient, "supportsNativeActions").mockReturnValue(true);
    const user = userEvent.setup();
    const onRunLocalAction = vi.fn().mockResolvedValue(undefined);
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      setupStatus("/tmp/alfred-home", {
        first_run: {
          version: 1,
          ready: false,
          status: "needs_action",
          headline: "Recommended setup can be improved.",
          summary: {
            required_ready: 7,
            required_total: 7,
            recommended_ready: 0,
            recommended_total: 3,
            optional_ready: 0,
            optional_total: 2,
            blockers: [],
          },
          checks: [
            {
              key: "code_graph",
              title: "Code graph memory",
              category: "memory",
              tier: "recommended",
              required: false,
              ready: false,
              state: "actionable",
              detail:
                "Code-memory binary is not installed yet; Alfred can fetch the pinned release on first explicit use.",
              action: "Run `alfred code-memory doctor`, then `alfred code-memory index`.",
              path: "/tmp/alfred-home/state/code-memory",
              detected: { capability_state: "installable", enabled: true },
            },
          ],
        },
      }),
    );

    render(renderSettings("http://127.0.0.1:7010", { onRunLocalAction }));

    await user.click(await screen.findByRole("button", { name: "Install code memory" }));
    expect(onRunLocalAction).toHaveBeenCalledWith({
      action: "code_memory_status",
      refreshAfter: true,
    });
    expect(screen.queryByRole("button", { name: "Index code memory" })).not.toBeInTheDocument();
  });

  it("installs a selected Graphify battery from readiness", async () => {
    vi.spyOn(apiClient, "supportsNativeActions").mockReturnValue(true);
    const user = userEvent.setup();
    const onRunLocalAction = vi.fn().mockResolvedValue(undefined);
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      setupStatus("/tmp/alfred-home", {
        first_run: {
          version: 1,
          ready: false,
          status: "needs_action",
          headline: "Recommended setup can be improved.",
          summary: {
            required_ready: 7,
            required_total: 7,
            recommended_ready: 0,
            recommended_total: 3,
            optional_ready: 0,
            optional_total: 2,
            blockers: [],
          },
          checks: [
            {
              key: "code_graph",
              title: "Code graph memory",
              category: "memory",
              tier: "recommended",
              required: false,
              ready: false,
              state: "actionable",
              detail: "Graphify is selected and needs its Python package.",
              action: "Install Graphify.",
              path: null,
              detected: {
                capability_state: "installable",
                enabled: true,
                engine: "graphify",
              },
            },
          ],
        },
      }),
    );

    render(renderSettings("http://127.0.0.1:7010", { onRunLocalAction }));

    await user.click(await screen.findByRole("button", { name: "Install Graphify" }));
    expect(onRunLocalAction).toHaveBeenCalledWith({
      action: "battery_enable",
      target: "graphify",
      refreshAfter: true,
    });
  });
});
