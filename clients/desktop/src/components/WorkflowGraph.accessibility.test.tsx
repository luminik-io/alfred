import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { WorkflowRole } from "../lib/agentRoster";
import type { WorkflowNodeInput } from "../lib/workflowGraph";
import { WorkflowGraph } from "./WorkflowGraph";

function input(codename: string, role: WorkflowRole): WorkflowNodeInput {
  return {
    codename,
    role,
    label: codename,
    roleLabel: "role",
    accent: "#fff",
    tone: "ok",
    statusLabel: "Resting",
    runsToday: 0,
  };
}

const ROSTER: WorkflowNodeInput[] = [
  input("triage", "triage"),
  input("architect", "architect"),
  input("senior-dev", "senior-dev"),
  input("reviewer", "reviewer"),
  input("automerge", "ship"),
  input("ops-watch", "ops"),
];

const FOCUSABLE = [
  "button:not([disabled])",
  "a[href]",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

afterEach(() => {
  document.body.style.overflow = "";
});

describe("WorkflowGraph full-screen accessibility", () => {
  it("opens a modal dialog, locks page scroll, and restores focus on Escape", () => {
    document.body.style.overflow = "clip";
    render(
      <WorkflowGraph agents={ROSTER} selectedCodename={null} onSelect={vi.fn()} />,
    );

    const trigger = screen.getByRole("button", { name: /maximize workflow/i });
    trigger.focus();
    fireEvent.click(trigger);

    const dialog = screen.getByRole("dialog", { name: /agent workflow/i });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByRole("button", { name: /exit full screen/i })).toHaveFocus();
    expect(document.body.style.overflow).toBe("hidden");

    fireEvent.keyDown(window, { key: "Escape" });

    expect(screen.queryByRole("dialog", { name: /agent workflow/i })).toBeNull();
    expect(
      screen.getByRole("button", { name: /maximize workflow/i }),
    ).toHaveFocus();
    expect(document.body.style.overflow).toBe("clip");
  });

  it("keeps keyboard focus inside the full-screen workflow", () => {
    render(
      <WorkflowGraph agents={ROSTER} selectedCodename={null} onSelect={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /maximize workflow/i }));

    const dialog = screen.getByRole("dialog", { name: /agent workflow/i });
    const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE));
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    expect(first).toBeDefined();
    expect(last).toBeDefined();

    first.focus();
    fireEvent.keyDown(window, { key: "Tab", shiftKey: true });
    expect(last).toHaveFocus();

    last?.focus();
    fireEvent.keyDown(window, { key: "Tab" });
    expect(first).toHaveFocus();
  });

  it("moves the full-screen workflow outside the constrained application shell", () => {
    render(
      <WorkflowGraph agents={ROSTER} selectedCodename={null} onSelect={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /maximize workflow/i }));

    const dialog = screen.getByRole("dialog", { name: /agent workflow/i });
    expect(dialog.parentElement).toBe(document.body);
    expect(dialog.firstElementChild).toHaveClass("wf-toolbar");
    expect(dialog.lastElementChild).toHaveClass("workflow-graph__canvas");
  });
});
