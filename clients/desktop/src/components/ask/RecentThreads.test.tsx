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
    expect(screen.queryByRole("button", { name: /recent/i })).not.toBeInTheDocument();
  });
});
