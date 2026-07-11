import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RecentThreads } from "./RecentThreads";
import type { RecentThread } from "./useAskThread";

const THREADS: RecentThread[] = [
  {
    id: "current",
    title: "Current chat",
    updatedAt: Date.now(),
    active: true,
    messageCount: 2,
  },
  {
    id: "older",
    title: "Older chat",
    updatedAt: Date.now() - 60_000,
    active: false,
    messageCount: 4,
  },
];

describe("RecentThreads", () => {
  it("closes through the Sheet lifecycle when a deletion leaves one chat", async () => {
    const user = userEvent.setup();
    const fallback = document.createElement("button");
    document.body.append(fallback);
    const onRetireFocus = vi.fn(() => fallback.focus());
    const view = render(
      <RecentThreads
        threads={THREADS}
        onResume={vi.fn()}
        onDelete={vi.fn()}
        onRetireFocus={onRetireFocus}
      />,
    );
    await user.click(screen.getByRole("button", { name: /recent/i }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    view.rerender(
      <RecentThreads
        threads={[THREADS[0]]}
        onResume={vi.fn()}
        onDelete={vi.fn()}
        onRetireFocus={onRetireFocus}
      />,
    );

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(onRetireFocus).toHaveBeenCalledOnce();
    expect(fallback).toHaveFocus();
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /recent/i })).not.toBeInTheDocument(),
    );
  });

  it("keeps the trigger when the sole surviving thread is not the active chat", async () => {
    // Deleting the ACTIVE chat resets the surface to a fresh unsaved
    // conversation, so one stored thread survives with active=false. It must
    // stay switchable: a length-based guard would hide the trigger and strand
    // that conversation until a new chat is saved.
    const user = userEvent.setup();
    const onResume = vi.fn();
    const view = render(
      <RecentThreads threads={THREADS} onResume={onResume} onDelete={vi.fn()} />,
    );

    view.rerender(
      <RecentThreads threads={[THREADS[1]]} onResume={onResume} onDelete={vi.fn()} />,
    );

    const trigger = screen.getByRole("button", { name: /recent/i });
    expect(trigger).toBeInTheDocument();
    await user.click(trigger);
    await user.click(screen.getByRole("button", { name: /^older chat/i }));
    expect(onResume).toHaveBeenCalledWith("older");
  });

  it("renders nothing when only the active chat exists", () => {
    render(<RecentThreads threads={[THREADS[0]]} onResume={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /recent/i })).not.toBeInTheDocument();
  });
});
