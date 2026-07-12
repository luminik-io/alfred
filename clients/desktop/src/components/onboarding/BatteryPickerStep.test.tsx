import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../../api/setup";
import { BatteryPickerStep } from "./BatteryPickerStep";
import type { SetupBattery, SetupBatteryManifest } from "../../types";

afterEach(() => {
  vi.restoreAllMocks();
});

function battery(overrides: Partial<SetupBattery>): SetupBattery {
  return {
    id: "dense-embeddings",
    name: "Dense embeddings",
    category: "memory",
    what: "A vector recall arm.",
    how_it_helps: "Finds relevant lessons even when you word things differently.",
    builtin: false,
    default_on: false,
    status: "available",
    configured: false,
    enabled: false,
    installed: true,
    requires_daemon: false,
    service: "Ollama",
    install_kind: "pip-extra",
    install_hint: 'pip install "alfred-os[vector]"',
    pip_extra: "vector",
    env_keys: ["ALFRED_MEMORY_SQLITE_DENSE"],
    docs: "docs/MEMORY_PROVIDERS.md",
    ...overrides,
  };
}

function manifest(rows: SetupBattery[]): SetupBatteryManifest {
  return {
    version: 1,
    summary: { total: rows.length },
    batteries: rows,
  };
}

const BUILTIN = battery({
  id: "sqlite-memory",
  name: "Built-in memory",
  builtin: true,
  status: "included",
  enabled: true,
  install_kind: "included",
  requires_daemon: false,
});

describe("BatteryPickerStep", () => {
  it("shows built-ins as included and opt-ins as toggles", async () => {
    vi.spyOn(api, "loadSetupBatteries").mockResolvedValue(manifest([BUILTIN, battery({})]));

    render(
      <BatteryPickerStep baseUrl="http://127.0.0.1:7010" canMutate setNotice={vi.fn()} />,
    );

    await waitFor(() => expect(screen.getByText("Built-in memory")).toBeInTheDocument());
    // Built-in reads as included and has no toggle.
    expect(screen.getByText(/included, no setup/i)).toBeInTheDocument();
    // Opt-in has an enable switch.
    expect(
      screen.getByRole("switch", { name: /enable dense embeddings/i }),
    ).toBeInTheDocument();
  });

  it("toggling an opt-in writes the env flag via saveSetupBattery", async () => {
    vi.spyOn(api, "loadSetupBatteries").mockResolvedValue(manifest([battery({})]));
    const save = vi
      .spyOn(api, "saveSetupBattery")
      .mockResolvedValue({
        ok: true,
        battery: "dense-embeddings",
        configured: true,
        enabled: true,
        env_path: "/home/.alfred/.env",
        keys: ["ALFRED_MEMORY_SQLITE_DENSE"],
        manifest: manifest([battery({ enabled: true, status: "enabled" })]),
      });
    const setNotice = vi.fn();

    render(
      <BatteryPickerStep
        baseUrl="http://127.0.0.1:7010"
        canMutate
        setNotice={setNotice}
      />,
    );

    const user = userEvent.setup();
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: /enable dense embeddings/i })).toBeInTheDocument(),
    );
    await user.click(screen.getByRole("switch", { name: /enable dense embeddings/i }));

    await waitFor(() =>
      expect(save).toHaveBeenCalledWith("http://127.0.0.1:7010", "dense-embeddings", true),
    );
    expect(setNotice).toHaveBeenCalledWith(
      expect.objectContaining({ tone: "ok" }),
    );
  });

  it("installs a local battery before mirroring its enabled state", async () => {
    vi.spyOn(api, "loadSetupBatteries").mockResolvedValue(manifest([battery({})]));
    const save = vi.spyOn(api, "saveSetupBattery").mockResolvedValue({
      ok: true,
      battery: "dense-embeddings",
      configured: true,
      enabled: true,
      env_path: "/home/.alfred/.env",
      keys: ["ALFRED_MEMORY_SQLITE_DENSE"],
      manifest: manifest([
        battery({ configured: true, enabled: true, installed: true, status: "enabled" }),
      ]),
    });
    const onRunLocalAction = vi.fn(async () => ({
      command: ["alfred", "batteries", "enable", "dense-embeddings", "--yes"],
      stdout: "",
      stderr: "",
      status: 0,
      success: true,
      pid: 1,
      message: "installed",
    }));

    render(
      <BatteryPickerStep
        baseUrl="http://127.0.0.1:7010"
        canMutate
        canRun
        onRunLocalAction={onRunLocalAction}
        setNotice={vi.fn()}
      />,
    );

    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("switch", { name: /enable dense embeddings/i }),
    );

    await waitFor(() =>
      expect(onRunLocalAction).toHaveBeenCalledWith({
        action: "battery_enable",
        target: "dense-embeddings",
        refreshAfter: true,
      }),
    );
    expect(save).toHaveBeenCalledWith(
      "http://127.0.0.1:7010",
      "dense-embeddings",
      true,
    );
  });

  it("disables toggles in a read-only preview", async () => {
    vi.spyOn(api, "loadSetupBatteries").mockResolvedValue(manifest([battery({})]));

    render(
      <BatteryPickerStep
        baseUrl="http://127.0.0.1:7010"
        canMutate={false}
        setNotice={vi.fn()}
      />,
    );

    await waitFor(() =>
      expect(screen.getByRole("switch", { name: /enable dense embeddings/i })).toBeDisabled(),
    );
  });

  it("disables native installs while the runtime is disconnected", async () => {
    vi.spyOn(api, "loadSetupBatteries").mockResolvedValue(manifest([battery({})]));

    render(
      <BatteryPickerStep
        baseUrl="http://127.0.0.1:7010"
        canMutate
        canRun
        connected={false}
        onRunLocalAction={vi.fn(async () => null)}
        setNotice={vi.fn()}
      />,
    );

    expect(
      await screen.findByRole("switch", { name: /enable dense embeddings/i }),
    ).toBeDisabled();
  });

  it("surfaces the requirement for a battery that still needs a daemon", async () => {
    vi.spyOn(api, "loadSetupBatteries").mockResolvedValue(
      manifest([
        battery({
          id: "redis-ams",
          name: "Redis Agent Memory Server",
          status: "not_installed",
          configured: true,
          enabled: false,
          installed: false,
          requires_daemon: true,
          service: "Redis",
          install_kind: "daemon",
          pip_extra: "",
        }),
      ]),
    );

    render(
      <BatteryPickerStep baseUrl="http://127.0.0.1:7010" canMutate setNotice={vi.fn()} />,
    );

    await waitFor(() => expect(screen.getByText(/needs Redis/i)).toBeInTheDocument());
    expect(screen.getByText(/needs install/i)).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: /disable redis agent memory server/i })).toBeChecked();
  });
});
