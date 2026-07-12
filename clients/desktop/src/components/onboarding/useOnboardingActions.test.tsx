import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useOnboardingActions } from "./useOnboardingActions";

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
});
