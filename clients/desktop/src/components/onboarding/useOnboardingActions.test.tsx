import { renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../../api/setup";
import type { SetupStatus } from "../../types";
import { useOnboardingActions } from "./useOnboardingActions";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useOnboardingActions", () => {
  it("does not install batteries while the runtime is disconnected", async () => {
    const onRunLocalAction = vi.fn(async () => null);
    const { result } = renderHook(() =>
      useOnboardingActions({
        baseUrl: "http://127.0.0.1:7010",
        canMutate: true,
        canRun: true,
        connected: false,
        githubConnected: false,
        refreshStatus: vi.fn(async () => null),
        startGithubAuthLogin: vi.fn(async () => false),
        onRunLocalAction,
        onReposSaved: vi.fn(async () => false),
        onSaveCustomNames: vi.fn(async () => undefined),
        onBatteriesDecision: vi.fn(),
        onSlackDecision: vi.fn(),
        onOpenSlackSetup: vi.fn(),
      }),
    );

    const outcome = await result.current({
      tool: "set_batteries",
      args: { batteries: ["code-memory-mcp"] },
    });

    expect(outcome).toEqual({
      ok: false,
      note: "Connect to the Alfred runtime before installing batteries.",
    });
    expect(onRunLocalAction).not.toHaveBeenCalled();
  });

  it("does not enable conversational battery config before the API write succeeds", async () => {
    vi.spyOn(api, "saveSetupBattery").mockRejectedValue(new Error("runtime unavailable"));
    const onRunLocalAction = vi.fn(async () => ({
      command: ["alfred", "batteries", "install", "code-memory-mcp", "--yes"],
      stdout: "",
      stderr: "",
      status: 0,
      success: true,
      pid: 1,
      message: "installed without enabling",
    }));
    const onBatteriesDecision = vi.fn();
    const { result } = renderHook(() =>
      useOnboardingActions({
        baseUrl: "http://127.0.0.1:7010",
        canMutate: true,
        canRun: true,
        connected: true,
        githubConnected: false,
        refreshStatus: vi.fn(async () => null),
        startGithubAuthLogin: vi.fn(async () => false),
        onRunLocalAction,
        onReposSaved: vi.fn(async () => false),
        onSaveCustomNames: vi.fn(async () => undefined),
        onBatteriesDecision,
        onSlackDecision: vi.fn(),
        onOpenSlackSetup: vi.fn(),
      }),
    );

    const outcome = await result.current({
      tool: "set_batteries",
      args: { batteries: ["code-memory-mcp"] },
    });

    expect(onRunLocalAction).toHaveBeenCalledWith({
      action: "battery_install",
      target: "code-memory-mcp",
      refreshAfter: false,
    });
    expect(api.saveSetupBattery).toHaveBeenCalledWith(
      "http://127.0.0.1:7010",
      "code-memory-mcp",
      true,
    );
    expect(outcome.ok).toBe(false);
    expect(onBatteriesDecision).not.toHaveBeenCalled();
  });

  it("does not finish conversational setup while required setup is missing", async () => {
    const refreshStatus = vi.fn(async () =>
      ({
        engine_ready: false,
        github: { ok: false },
        repos: { count: 0 },
        first_run: {
          ready: false,
          headline: "3 required setup items need action.",
        },
      }) as SetupStatus,
    );
    const { result } = renderHook(() =>
      useOnboardingActions({
        baseUrl: "http://127.0.0.1:7010",
        canMutate: true,
        canRun: true,
        connected: true,
        githubConnected: false,
        refreshStatus,
        startGithubAuthLogin: vi.fn(async () => false),
        onRunLocalAction: vi.fn(async () => null),
        onReposSaved: vi.fn(async () => false),
        onSaveCustomNames: vi.fn(async () => undefined),
        onBatteriesDecision: vi.fn(),
        onSlackDecision: vi.fn(),
        onOpenSlackSetup: vi.fn(),
      }),
    );

    await expect(result.current({ tool: "finish_setup", args: {} })).resolves.toEqual({
      ok: false,
      note: "3 required setup items need action.",
    });
  });

  it("finishes conversational setup only when required setup is ready", async () => {
    const refreshStatus = vi.fn(async () =>
      ({
        engine_ready: true,
        github: { ok: true },
        repos: { count: 1 },
        first_run: { ready: true },
      }) as SetupStatus,
    );
    const { result } = renderHook(() =>
      useOnboardingActions({
        baseUrl: "http://127.0.0.1:7010",
        canMutate: true,
        canRun: true,
        connected: true,
        githubConnected: true,
        refreshStatus,
        startGithubAuthLogin: vi.fn(async () => true),
        onRunLocalAction: vi.fn(async () => null),
        onReposSaved: vi.fn(async () => true),
        onSaveCustomNames: vi.fn(async () => undefined),
        onBatteriesDecision: vi.fn(),
        onSlackDecision: vi.fn(),
        onOpenSlackSetup: vi.fn(),
      }),
    );

    await expect(result.current({ tool: "finish_setup", args: {} })).resolves.toEqual({
      ok: true,
      note: "Setup is ready. Choose Alfred's first job next.",
    });
  });
});
