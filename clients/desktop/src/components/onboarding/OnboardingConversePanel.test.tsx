import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

async function sendMessage(text: string) {
  const user = userEvent.setup();
  const input = screen.getByLabelText(/message alfred to set up/i);
  await user.type(input, text);
  await user.click(screen.getByRole("button", { name: /send/i }));
}

describe("OnboardingConversePanel", () => {
  // Regression for the Greptile P1: a SIDE-EFFECTFUL action the model proposes
  // must NOT execute until the person clicks Approve. One send cannot silently
  // chain setup writes.
  it("does not execute a side-effectful action without user approval", async () => {
    const onRunAction = vi.fn(async () => ({ ok: true, note: "done" }));
    vi.spyOn(api, "onboardingConverse").mockResolvedValue(
      turn({ reply: "Which repos?", action: { tool: "set_repos", args: { repos: ["acme/api"] } } }),
    );

    render(
      <OnboardingConversePanel
        baseUrl="http://127.0.0.1:7010"
        onRunAction={onRunAction}
        onDone={vi.fn()}
        onUseStepped={vi.fn()}
      />,
    );

    await sendMessage("use acme/api");

    // The model proposed a write; the panel parks it behind an Approve button and
    // runs nothing on its own.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /save repositories/i })).toBeInTheDocument(),
    );
    expect(onRunAction).not.toHaveBeenCalled();
    // Only after the explicit click does the shared handler run.
    await userEvent.setup().click(screen.getByRole("button", { name: /save repositories/i }));
    await waitFor(() => expect(onRunAction).toHaveBeenCalledTimes(1));
    expect(onRunAction).toHaveBeenCalledWith({ tool: "set_repos", args: { repos: ["acme/api"] } });
  });

  // A READ-ONLY action (check_engine) may auto-proceed so a status check flows
  // straight into the model's next prompt, no click needed.
  it("auto-proceeds a read-only action without an approval click", async () => {
    const onRunAction = vi.fn(async () => ({ ok: true, note: "Found claude." }));
    const converse = vi
      .spyOn(api, "onboardingConverse")
      .mockResolvedValueOnce(
        turn({ reply: "Checking tools.", action: { tool: "check_engine", args: {} } }),
      )
      .mockResolvedValueOnce(turn({ reply: "Great, claude is installed. Connect GitHub next?" }));

    render(
      <OnboardingConversePanel
        baseUrl="http://127.0.0.1:7010"
        onRunAction={onRunAction}
        onDone={vi.fn()}
        onUseStepped={vi.fn()}
      />,
    );

    await sendMessage("start");

    // The read-only check ran with no Approve button, and the chat flowed on to a
    // second model turn.
    await waitFor(() =>
      expect(onRunAction).toHaveBeenCalledWith({ tool: "check_engine", args: {} }),
    );
    await waitFor(() => expect(converse).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("button", { name: /check tools/i })).not.toBeInTheDocument();
  });

  // Regression for the Codex P3: the internal [setup] outcome note is threaded to
  // the model but must never render as a visible chat bubble.
  it("keeps internal [setup] notes out of the visible chat", async () => {
    const onRunAction = vi.fn(async () => ({ ok: true, note: "Found claude." }));
    let secondTurnMessages: { role: string; content: string }[] = [];
    vi.spyOn(api, "onboardingConverse")
      .mockResolvedValueOnce(
        turn({ reply: "Checking tools.", action: { tool: "check_engine", args: {} } }),
      )
      .mockImplementationOnce(async (_base, request) => {
        secondTurnMessages = request.messages;
        return turn({ reply: "All good." });
      });

    render(
      <OnboardingConversePanel
        baseUrl="http://127.0.0.1:7010"
        onRunAction={onRunAction}
        onDone={vi.fn()}
        onUseStepped={vi.fn()}
      />,
    );

    await sendMessage("start");

    await waitFor(() => expect(onRunAction).toHaveBeenCalledTimes(1));
    // The model DID receive the [setup] note in the transcript...
    await waitFor(() =>
      expect(
        secondTurnMessages.some((m) => m.content.includes("[setup] check_engine completed")),
      ).toBe(true),
    );
    // ...but it is NOT rendered anywhere in the visible chat.
    expect(screen.queryByText(/\[setup\]/)).not.toBeInTheDocument();
  });

  // Regression for the earlier Codex P1: the terminal finish_setup turn (which
  // carries done) completes the chat once the person approves it.
  it("completes the chat when an approved finish_setup turn returns done", async () => {
    const onDone = vi.fn();
    const onRunAction = vi.fn(async () => ({ ok: true, note: "Setup is done." }));
    const converse = vi.spyOn(api, "onboardingConverse").mockResolvedValue(
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

    await sendMessage("finish");

    // finish_setup is side-effectful/terminal: it waits for approval, then routes
    // out via onDone without recursing past done.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /finish setup/i })).toBeInTheDocument(),
    );
    expect(onDone).not.toHaveBeenCalled();
    await userEvent.setup().click(screen.getByRole("button", { name: /finish setup/i }));
    await waitFor(() =>
      expect(onRunAction).toHaveBeenCalledWith({ tool: "finish_setup", args: {} }),
    );
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    // No extra converse turn past done.
    expect(converse).toHaveBeenCalledTimes(1);
  });
});
