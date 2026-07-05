import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../../api";
import { OnboardingConversePanel } from "./OnboardingConversePanel";
import type { OnboardingConverseResponse } from "../../types";

afterEach(() => {
  vi.restoreAllMocks();
});

function turn(overrides: Partial<OnboardingConverseResponse>): OnboardingConverseResponse {
  return { reply: "", action: null, done: false, ...overrides };
}

describe("OnboardingConversePanel", () => {
  // Regression for the Codex P1: the server only sets `done` on a finish_setup
  // action, so the terminal turn ALWAYS carries an action. The done check must
  // run even when an action is present, or onDone never fires and the chat cannot
  // route out of onboarding.
  it("completes the chat when a finish_setup turn returns done", async () => {
    const onDone = vi.fn();
    const onRunAction = vi.fn(async () => ({ ok: true, note: "Setup is done." }));
    vi.spyOn(api, "onboardingConverse").mockResolvedValue(
      turn({
        reply: "All set.",
        action: { tool: "finish_setup", args: {} },
        done: true,
      }),
    );

    render(
      <OnboardingConversePanel
        baseUrl="http://127.0.0.1:7010"
        onRunAction={onRunAction}
        onDone={onDone}
        onUseStepped={vi.fn()}
      />,
    );

    // Drive one turn by sending a message.
    const input = screen.getByLabelText(/message alfred to set up/i);
    const send = screen.getByRole("button", { name: /send/i });
    await import("@testing-library/user-event").then(async ({ default: userEvent }) => {
      const user = userEvent.setup();
      await user.type(input, "finish");
      await user.click(send);
    });

    await waitFor(() => expect(onRunAction).toHaveBeenCalledTimes(1));
    // The finish_setup action ran AND the done check routed out.
    expect(onRunAction).toHaveBeenCalledWith({ tool: "finish_setup", args: {} });
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    // The terminal turn must NOT recurse into another converse turn past done.
    expect(api.onboardingConverse).toHaveBeenCalledTimes(1);
  });
});
