import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { MemoryView } from "./MemoryView";
import type { MemoryCandidate, MemoryLesson, Snapshot } from "../types";

vi.mock("../api", () => ({
  supportsNativeActions: () => true,
}));

function candidate(overrides: Partial<MemoryCandidate> = {}): MemoryCandidate {
  return {
    id: "mem:1",
    codename: "lucius",
    repo: "your-org/api",
    body: "Use request fixtures for attendee imports.",
    tags: ["tests"],
    severity: "info",
    source: "slack",
    source_firing_id: null,
    evidence: JSON.stringify({ thread_ts: "1716480000.000000" }),
    confidence: 0.82,
    status: "candidate",
    created_at: "2026-05-30T12:00:00Z",
    ...overrides,
  };
}

function lesson(overrides: Partial<MemoryLesson> = {}): MemoryLesson {
  return {
    id: "lesson:memory_candidate:1",
    codename: "lucius",
    repo: "your-org/api",
    body: "GraphQL schema lives in src/schema.graphql.",
    tags: ["graphql"],
    severity: "info",
    created_at: "2026-05-30T12:00:00Z",
    firing_id: null,
    ...overrides,
  };
}

function snapshot(overrides: Partial<Snapshot> = {}): Snapshot {
  return {
    loadedAt: new Date("2026-05-30T12:00:00Z"),
    shipped: null,
    schedule: [],
    status: { agents: [], total_today: 0, reliability: { status: "ok" } },
    actions: {
      status: "ok",
      actions: [],
      failure_patterns: [],
      stale_workers: [],
      promotion_suggestions: [],
    },
    memoryCandidates: { rows: [] },
    memoryLessons: { rows: [lesson()] },
    firings: [],
    plans: [],
    trustedSlack: null,
    ...overrides,
  };
}

describe("MemoryView", () => {
  it("leads with what Alfred remembered on its own, no click required", () => {
    render(
      <MemoryView
        snapshot={snapshot()}
        actionNotice={null}
        busyMemoryAction={null}
        nativeBusy={null}
        onMemoryCandidateAction={vi.fn()}
        onRunLocalAction={vi.fn()}
      />,
    );

    // The intro reframes memory as automatic, not a review queue.
    expect(screen.getByText(/alfred remembers what it learns on its own/i)).toBeInTheDocument();
    // The auto-remembered lessons lead the surface.
    expect(
      screen.getByRole("heading", { name: /lessons alfred is using/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/graphql schema lives in/i)).toBeInTheDocument();
    // No pile of "keep this lesson" cards as the primary action.
    expect(
      screen.queryByRole("button", { name: /keep this lesson/i }),
    ).not.toBeInTheDocument();
  });

  it("gives every auto-remembered lesson an undo affordance that retires it", async () => {
    const onMemoryCandidateAction = vi.fn();
    const user = userEvent.setup();

    render(
      <MemoryView
        snapshot={snapshot()}
        actionNotice={null}
        busyMemoryAction={null}
        nativeBusy={null}
        onMemoryCandidateAction={onMemoryCandidateAction}
        onRunLocalAction={vi.fn()}
      />,
    );

    // Undo uses the lesson's recall id; the action layer strips it to the
    // candidate id the server retire route validates.
    await user.click(screen.getByRole("button", { name: /^undo$/i }));
    expect(onMemoryCandidateAction).toHaveBeenCalledWith("lesson:memory_candidate:1", "retire");
  });

  it("shows the undoing state for the lesson being retired", () => {
    render(
      <MemoryView
        snapshot={snapshot()}
        actionNotice={null}
        busyMemoryAction={"lesson:memory_candidate:1:retire"}
        nativeBusy={null}
        onMemoryCandidateAction={vi.fn()}
        onRunLocalAction={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: /undoing/i })).toBeDisabled();
  });

  it("demotes low-confidence candidates to a secondary confirmation section", async () => {
    const onMemoryCandidateAction = vi.fn();
    const user = userEvent.setup();

    render(
      <MemoryView
        snapshot={snapshot({ memoryCandidates: { rows: [candidate()] } })}
        actionNotice={null}
        busyMemoryAction={null}
        nativeBusy={null}
        onMemoryCandidateAction={onMemoryCandidateAction}
        onRunLocalAction={vi.fn()}
      />,
    );

    // The pending queue is a secondary section, not the lead.
    expect(
      screen.getByRole("heading", { name: /waiting for your confirmation/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /use request fixtures/i })).toBeInTheDocument();

    // Keep / dismiss still work for the candidates that were not sure enough.
    await user.click(screen.getByRole("button", { name: /keep this lesson/i }));
    expect(onMemoryCandidateAction).toHaveBeenCalledWith("mem:1", "promote");
    await user.click(screen.getByRole("button", { name: /dismiss/i }));
    expect(onMemoryCandidateAction).toHaveBeenCalledWith("mem:1", "reject");
  });

  it("shows the 'nothing remembered yet' empty state when there is nothing at all", () => {
    render(
      <MemoryView
        snapshot={snapshot({ memoryCandidates: { rows: [] }, memoryLessons: { rows: [] } })}
        actionNotice={null}
        busyMemoryAction={null}
        nativeBusy={null}
        onMemoryCandidateAction={vi.fn()}
        onRunLocalAction={vi.fn()}
      />,
    );

    expect(
      screen.getByText(/alfred has not remembered anything yet/i),
    ).toBeInTheDocument();
    // No confirmation section when there is nothing pending.
    expect(
      screen.queryByRole("heading", { name: /waiting for your confirmation/i }),
    ).not.toBeInTheDocument();
  });

  it("keeps the Redis / memory probes behind a closed Advanced disclosure", async () => {
    const onRunLocalAction = vi.fn();
    const user = userEvent.setup();

    render(
      <MemoryView
        snapshot={snapshot({ memoryLessons: { rows: [] } })}
        actionNotice={null}
        busyMemoryAction={null}
        nativeBusy={null}
        onMemoryCandidateAction={vi.fn()}
        onRunLocalAction={onRunLocalAction}
      />,
    );

    // The Advanced disclosure is present but closed by default, so the Redis
    // plumbing does not lead the surface.
    const advancedSummary = screen.getByText(/advanced \(technical detail\)/i);
    const advancedDetails = advancedSummary.closest("details");
    expect(advancedDetails).not.toBeNull();
    expect(advancedDetails).not.toHaveAttribute("open");

    // The probes still exist and still dispatch the real native actions once
    // the operator opens the disclosure.
    await user.click(advancedSummary);
    const advanced = within(advancedDetails as HTMLElement);
    await user.click(advanced.getByRole("button", { name: /run memory check/i }));
    await user.click(advanced.getByRole("button", { name: /preview redis sync/i }));
    await user.click(advanced.getByRole("button", { name: /queue failure lessons/i }));
    await user.click(advanced.getByRole("button", { name: /save judged lessons/i }));

    expect(onRunLocalAction).toHaveBeenCalledWith({ action: "brain_doctor" });
    expect(onRunLocalAction).toHaveBeenCalledWith({ action: "redis_sync_preview" });
    expect(onRunLocalAction).toHaveBeenCalledWith({
      action: "memory_harvest",
      refreshAfter: true,
    });
    expect(onRunLocalAction).toHaveBeenCalledWith({
      action: "memory_auto_promote",
      refreshAfter: true,
    });
  });

  it("hides the raw JSON evidence behind a closed disclosure on a pending candidate", () => {
    render(
      <MemoryView
        snapshot={snapshot({ memoryCandidates: { rows: [candidate()] }, memoryLessons: { rows: [] } })}
        actionNotice={null}
        busyMemoryAction={null}
        nativeBusy={null}
        onMemoryCandidateAction={vi.fn()}
        onRunLocalAction={vi.fn()}
      />,
    );

    const evidenceSummary = screen.getByText("Technical detail");
    const evidenceDetails = evidenceSummary.closest("details");
    expect(evidenceDetails).not.toBeNull();
    // Disclosure is closed by default: the raw JSON is not surfaced up front.
    expect(evidenceDetails).not.toHaveAttribute("open");
  });
});
