import { renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../../api/setup";
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
        onSaveCustomNames: vi.fn(async () => undefined),
        onBatteriesDecision: vi.fn(),
        onSlackDecision: vi.fn(),
        onOpenSlackSetup: vi.fn(),
        onFinishSetup: vi.fn(),
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
        onSaveCustomNames: vi.fn(async () => undefined),
        onBatteriesDecision,
        onSlackDecision: vi.fn(),
        onOpenSlackSetup: vi.fn(),
        onFinishSetup: vi.fn(),
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
});
