import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as apiClient from "../api/client";
import * as apiSetup from "../api/setup";
import type { SetupBattery, SetupBatteryManifest, SetupStatus } from "../types";
import { SettingsView } from "./SettingsView";

function setupStatus(home: string, overrides: Partial<SetupStatus> = {}): SetupStatus {
  const base: SetupStatus = {
    github: { ok: true, account: "octocat", detail: "Signed in to GitHub as octocat." },
    engines: [
      {
        name: "claude",
        display_name: "Claude Code",
        installed: true,
        protocol_compatible: true,
        ready: true,
        dispatchable: true,
        state: "ready",
        detail: "Claude Code is compatible and signed in.",
        path: "/opt/homebrew/bin/claude",
        version: "Claude Code 2.1.41",
        capabilities: ["text", "worktree-write"],
        failures: [],
      },
    ],
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

function batteryManifest(batteries: SetupBattery[]): SetupBatteryManifest {
  return {
    version: 1,
    summary: { total: batteries.length },
    batteries,
  };
}

function headroomBattery(overrides: Partial<SetupBattery> = {}): SetupBattery {
  return {
    id: "headroom-compression",
    name: "Headroom compression",
    category: "compression",
    what: "An optional compressor for tool output.",
    how_it_helps: "Tests whether shorter output keeps the required evidence.",
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
    install_hint: "Run alfred batteries install headroom-compression --yes.",
    pip_extra: "headroom",
    env_keys: ["ALFRED_HEADROOM_ENABLED"],
    docs: "docs/COMPRESSION.md",
    version: "headroom-ai==0.29.0",
    license: "Apache-2.0",
    source_url: "https://pypi.org/project/headroom-ai/0.29.0/",
    integrity: "Python package index artifact hashes",
    install_command: "alfred batteries install headroom-compression --yes",
    check_command: "alfred batteries list --json",
    disable_command: "alfred batteries disable headroom-compression --yes",
    remove_command: "alfred batteries remove headroom-compression --yes",
    ...overrides,
  };
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
      themeName="signal-edge"
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
  it("keeps battery management available after onboarding", async () => {
    vi.spyOn(apiClient, "supportsNativeActions").mockReturnValue(true);
    vi.spyOn(apiClient, "supportsMutations").mockReturnValue(true);
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      setupStatus("/tmp/alfred-home"),
    );
    vi.spyOn(apiSetup, "loadSetupBatteries").mockResolvedValue(
      batteryManifest([headroomBattery()]),
    );
    vi.spyOn(apiSetup, "saveSetupBattery").mockResolvedValue({
      ok: true,
      battery: "headroom-compression",
      configured: true,
      enabled: true,
      env_path: "/tmp/alfred-home/.env",
      keys: ["ALFRED_HEADROOM_ENABLED"],
      manifest: batteryManifest([
        headroomBattery({ configured: true, enabled: true, status: "enabled" }),
      ]),
    });
    const onRunLocalAction = vi.fn(async () => ({
      command: ["alfred", "batteries", "install", "headroom-compression", "--yes"],
      stdout: "",
      stderr: "",
      status: 0,
      success: true,
      pid: 1,
      message: "installed",
    }));
    const user = userEvent.setup();

    render(renderSettings("http://127.0.0.1:7010", { onRunLocalAction }));

    await user.click(screen.getByRole("tab", { name: "Tools" }));
    await user.click(
      await screen.findByRole("switch", { name: "Enable Headroom compression" }),
    );

    await waitFor(() =>
      expect(apiSetup.saveSetupBattery).toHaveBeenCalledWith(
        "http://127.0.0.1:7010",
        "headroom-compression",
        true,
      ),
    );
    expect(onRunLocalAction).toHaveBeenCalledWith({
      action: "battery_install",
      target: "headroom-compression",
      refreshAfter: false,
    });
    expect(screen.getByText("Turned Headroom compression on.")).toBeInTheDocument();
  });

  it("keeps tool switches read-only without mutation support", async () => {
    vi.spyOn(apiClient, "supportsNativeActions").mockReturnValue(false);
    vi.spyOn(apiClient, "supportsMutations").mockReturnValue(false);
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      setupStatus("/tmp/alfred-home"),
    );
    vi.spyOn(apiSetup, "loadSetupBatteries").mockResolvedValue(
      batteryManifest([headroomBattery()]),
    );
    const user = userEvent.setup();

    render(renderSettings("http://127.0.0.1:7010"));

    await user.click(screen.getByRole("tab", { name: "Tools" }));
    expect(
      await screen.findByRole("switch", { name: "Enable Headroom compression" }),
    ).toBeDisabled();
    expect(screen.getByText(/read-only preview cannot change batteries/i)).toBeInTheDocument();
  });

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

  it("explains browser-only diagnostics before the disabled controls", async () => {
    const user = userEvent.setup();
    vi.spyOn(apiClient, "supportsNativeActions").mockReturnValue(false);
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      setupStatus("/tmp/alfred-home"),
    );

    render(renderSettings("http://127.0.0.1:7010"));

    await user.click(screen.getByRole("tab", { name: "Diagnostics" }));
    const note = await screen.findByText(
      "Open Alfred Desktop to run these checks. Browser preview is read-only.",
    );
    const firstControl = screen.getByRole("button", { name: "Agent status" });

    expect(note.compareDocumentPosition(firstControl)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    expect(firstControl).toBeDisabled();
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

  it("confirms before removing a trusted collaborator", async () => {
    vi.spyOn(apiClient, "supportsNativeActions").mockReturnValue(true);
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      setupStatus("/tmp/alfred-home"),
    );
    const user = userEvent.setup();
    const onRemoveTrustedUser = vi.fn();

    render(
      renderSettings("http://127.0.0.1:7010", {
        trustedSlack: {
          operator_user_id: "UOPERATOR",
          users: [
            {
              user_id: "U0123ABCDEF",
              sources: ["state"],
              added_at: null,
              added_by: null,
              can_remove: true,
            },
          ],
          state_path: "/tmp/alfred-home/state/slack-trusted-users.json",
        },
        onRemoveTrustedUser,
      }),
    );

    await user.click(screen.getByRole("tab", { name: /^Collaborators/ }));
    await user.click(screen.getByRole("button", { name: "Remove U0123ABCDEF" }));

    expect(onRemoveTrustedUser).not.toHaveBeenCalled();
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Remove collaborator" }),
    ).toHaveFocus();

    await user.click(screen.getByRole("button", { name: "Remove collaborator" }));
    expect(onRemoveTrustedUser).toHaveBeenCalledWith("U0123ABCDEF");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("cancels collaborator removal with Escape", async () => {
    vi.spyOn(apiClient, "supportsNativeActions").mockReturnValue(true);
    vi.spyOn(apiSetup, "loadSetupStatus").mockResolvedValue(
      setupStatus("/tmp/alfred-home"),
    );
    const user = userEvent.setup();
    const onRemoveTrustedUser = vi.fn();

    render(
      renderSettings("http://127.0.0.1:7010", {
        trustedSlack: {
          operator_user_id: "UOPERATOR",
          users: [
            {
              user_id: "U0123ABCDEF",
              sources: ["state"],
              added_at: null,
              added_by: null,
              can_remove: true,
            },
          ],
          state_path: "/tmp/alfred-home/state/slack-trusted-users.json",
        },
        onRemoveTrustedUser,
      }),
    );

    await user.click(screen.getByRole("tab", { name: /^Collaborators/ }));
    await user.click(screen.getByRole("button", { name: "Remove U0123ABCDEF" }));
    await user.keyboard("{Escape}");

    expect(onRemoveTrustedUser).not.toHaveBeenCalled();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });
});
