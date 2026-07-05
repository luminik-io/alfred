import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ThemeBuilderDialog } from "./ThemeBuilderDialog";
import { themeBuilderConverse } from "../api";
import type { ThemeBuilderResponse } from "../types";

vi.mock("../api", () => ({
  themeBuilderConverse: vi.fn(),
  isLiveSessionUnavailable: (err: unknown) =>
    err instanceof Error
      ? err.message.includes("live_session_unavailable") || err.message.includes("503")
      : false,
}));

const converseMock = vi.mocked(themeBuilderConverse);
const BASE_URL = "http://127.0.0.1:7010";

function renderDialog(overrides: Partial<Parameters<typeof ThemeBuilderDialog>[0]> = {}) {
  const props = {
    open: true,
    baseUrl: BASE_URL,
    onOpenChange: vi.fn(),
    onPropose: vi.fn(),
    onManualEdit: vi.fn(),
    ...overrides,
  };
  render(<ThemeBuilderDialog {...props} />);
  return props;
}

describe("ThemeBuilderDialog", () => {
  beforeEach(() => {
    converseMock.mockReset();
  });

  it("opens with a vibe-asking greeting", () => {
    renderDialog();
    expect(screen.getByText(/what vibe do you want/i)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /name your team/i })).toBeInTheDocument();
  });

  it("sends a message and shows Alfred's reply", async () => {
    const user = userEvent.setup();
    const reply: ThemeBuilderResponse = {
      reply: "A sci-fi crew, aye. Give me a moment.",
      action: null,
    };
    converseMock.mockResolvedValue(reply);
    renderDialog();

    await user.type(
      screen.getByLabelText(/describe a vibe/i),
      "make them a sci-fi crew",
    );
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() =>
      expect(screen.getByText(/a sci-fi crew, aye/i)).toBeInTheDocument(),
    );
    // The person's message is echoed in the log.
    expect(screen.getByText(/make them a sci-fi crew/i)).toBeInTheDocument();
    expect(converseMock).toHaveBeenCalledWith(
      BASE_URL,
      { messages: expect.arrayContaining([{ role: "user", content: "make them a sci-fi crew" }]) },
      expect.anything(),
    );
  });

  it("pre-fills the editor when a turn proposes a team", async () => {
    const user = userEvent.setup();
    const proposal: ThemeBuilderResponse = {
      reply: "Middle-earth it is.",
      action: {
        tool: "propose_theme",
        args: {
          custom_names: { architect: "Gandalf", reviewer: "Galadriel" },
          custom_roles: {},
        },
      },
    };
    converseMock.mockResolvedValue(proposal);
    const props = renderDialog();

    await user.type(screen.getByLabelText(/describe a vibe/i), "lord of the rings");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() =>
      expect(props.onPropose).toHaveBeenCalledWith({
        names: { architect: "Gandalf", reviewer: "Galadriel" },
        roles: {},
      }),
    );
    // The proposal closes the chat so the editor takes over.
    expect(props.onOpenChange).toHaveBeenCalledWith(false);
  });

  it("falls back to the manual editor when the engine is unavailable (503)", async () => {
    const user = userEvent.setup();
    converseMock.mockRejectedValue(new Error("live_session_unavailable (503)"));
    const props = renderDialog();

    await user.type(screen.getByLabelText(/describe a vibe/i), "a band");
    await user.click(screen.getByRole("button", { name: /send/i }));

    // The composer is replaced with a manual-editor offer; the standalone editor
    // path still works.
    const manualButton = await screen.findByRole("button", { name: /edit names by hand/i });
    await user.click(manualButton);
    expect(props.onManualEdit).toHaveBeenCalled();
    expect(props.onOpenChange).toHaveBeenLastCalledWith(false);
  });

  it("shows a plain error on a non-engine failure and keeps chatting", async () => {
    const user = userEvent.setup();
    converseMock.mockRejectedValue(new Error("network blip"));
    renderDialog();

    await user.type(screen.getByLabelText(/describe a vibe/i), "greek gods");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/network blip/i));
    // The composer stays available for a retry (not the engine-down fallback).
    expect(screen.getByLabelText(/describe a vibe/i)).toBeInTheDocument();
  });
});
