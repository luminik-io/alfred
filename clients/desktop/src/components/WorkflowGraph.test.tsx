import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { WorkflowGraph } from "./WorkflowGraph";
import type { WorkflowNodeInput } from "../lib/workflowGraph";
import type { WorkflowRole } from "../lib/agentRoster";

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
  input("robin", "triage"),
  input("batman", "architect"),
  input("lucius", "implement"),
  input("rasalghul", "review"),
  input("automerge", "ship"),
  input("gordon", "ops"),
];

describe("WorkflowGraph", () => {
  it("renders zoom-in, zoom-out, and fit-to-view controls so a cramped graph can be zoomed and reset", () => {
    render(
      <WorkflowGraph agents={ROSTER} selectedCodename={null} onSelect={vi.fn()} />,
    );
    // React Flow's <Controls> exposes accessible buttons for the three canvas
    // actions the operator needs: zoom in, zoom out, and fit (reset) to view.
    expect(screen.getByRole("button", { name: /zoom in/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /zoom out/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /fit view/i })).toBeInTheDocument();
  });

  it("keeps the delivery-pipeline legend anchored in the canvas", () => {
    render(
      <WorkflowGraph agents={ROSTER} selectedCodename={null} onSelect={vi.fn()} />,
    );
    expect(screen.getByLabelText("Workflow legend")).toBeInTheDocument();
    expect(screen.getByText("Delivery pipeline")).toBeInTheDocument();
  });
});
