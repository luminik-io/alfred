import { describe, expect, it } from "vitest";

import type { WorkflowRole } from "./agentRoster";
import {
  buildWorkflowGraph,
  clampWorkflowZoom,
  fitToViewZoom,
  WORKFLOW_ZOOM,
  type WorkflowNodeInput,
  zoomInLevel,
  zoomOutLevel,
} from "./workflowGraph";

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

// A small representative roster spanning every lane.
const ROSTER: WorkflowNodeInput[] = [
  input("robin", "triage"),
  input("batman", "architect"),
  input("lucius", "implement"),
  input("bane", "implement"),
  input("rasalghul", "review"),
  input("automerge", "ship"),
  input("gordon", "ops"),
];

describe("buildWorkflowGraph", () => {
  it("builds an agent node for every input plus a lane label per present lane", () => {
    const { nodes } = buildWorkflowGraph(ROSTER, null);

    const agentNodes = nodes.filter((n) => n.type === "agent");
    const laneNodes = nodes.filter((n) => n.type === "lane");
    // Every reported agent is placed (no hardcoded subset filter).
    expect(agentNodes).toHaveLength(ROSTER.length);
    expect(agentNodes.map((n) => n.id).sort()).toEqual(
      ROSTER.map((r) => r.codename).sort(),
    );
    // One lane label per distinct role present (six here).
    expect(laneNodes).toHaveLength(6);
  });

  it("places an unknown-role agent in its given lane and never drops it", () => {
    // An agent the runtime reports with the fallback role still appears.
    const { nodes } = buildWorkflowGraph(
      [input("batman", "architect"), input("mystery-bot", "ops")],
      null,
    );
    const agentIds = nodes.filter((n) => n.type === "agent").map((n) => n.id);
    expect(agentIds.sort()).toEqual(["batman", "mystery-bot"]);
  });

  it("wires handoff edges between present lanes and drops edges to absent lanes", () => {
    // Only architect + implement present: the architect->implement edge
    // survives; edges to missing lanes (e.g. implement->review) do not.
    const { nodes, edges } = buildWorkflowGraph(
      [input("batman", "architect"), input("lucius", "implement")],
      null,
    );
    const agentIds = nodes.filter((n) => n.type === "agent").map((n) => n.id);
    expect(agentIds.sort()).toEqual(["batman", "lucius"]);
    expect(edges.map((e) => e.id)).toContain("batman->lucius");
    expect(
      edges.every((e) => agentIds.includes(e.source) && agentIds.includes(e.target)),
    ).toBe(true);
  });

  it("wires every agent in a multi-agent lane into the pipeline, not just the first", () => {
    // implement has three agents; each must hand off into the review lane so no
    // secondary agent (bane, nightwing) is left without a pipeline edge.
    const { nodes, edges } = buildWorkflowGraph(
      [
        input("lucius", "implement"),
        input("bane", "implement"),
        input("nightwing", "implement"),
        input("rasalghul", "review"),
      ],
      null,
    );
    const agentIds = nodes.filter((n) => n.type === "agent").map((n) => n.id);
    expect(agentIds.sort()).toEqual(["bane", "lucius", "nightwing", "rasalghul"]);
    // Every implement agent connects to the review lane representative.
    const edgeIds = edges.map((e) => e.id);
    expect(edgeIds).toContain("lucius->rasalghul");
    expect(edgeIds).toContain("bane->rasalghul");
    expect(edgeIds).toContain("nightwing->rasalghul");
    // No agent in the lane is orphaned: each has at least one incident edge.
    for (const codename of ["lucius", "bane", "nightwing"]) {
      expect(
        edges.some((e) => e.source === codename || e.target === codename),
      ).toBe(true);
    }
  });

  it("marks the selected node and animates its incident edges", () => {
    const { nodes, edges } = buildWorkflowGraph(
      [input("lucius", "implement"), input("rasalghul", "review")],
      "rasalghul",
    );
    const selected = nodes.find((n) => n.id === "rasalghul");
    expect((selected?.data as { selected: boolean }).selected).toBe(true);
    expect(edges.find((e) => e.id === "lucius->rasalghul")?.animated).toBe(true);
  });
});

describe("workflow canvas zoom + pan state", () => {
  it("zoom in increases the scale, zoom out decreases it", () => {
    const base = 1;
    expect(zoomInLevel(base)).toBeGreaterThan(base);
    expect(zoomOutLevel(base)).toBeLessThan(base);
    // Zoom in then out returns to the starting level (the step is symmetric).
    expect(zoomOutLevel(zoomInLevel(base))).toBeCloseTo(base, 5);
  });

  it("clamps zoom to the canvas bounds so a node stays readable and the graph never vanishes", () => {
    expect(clampWorkflowZoom(99)).toBe(WORKFLOW_ZOOM.max);
    expect(clampWorkflowZoom(0.0001)).toBe(WORKFLOW_ZOOM.min);
    // Repeated zoom-in saturates at the max rather than growing unbounded.
    let zoom = 1;
    for (let i = 0; i < 20; i += 1) zoom = zoomInLevel(zoom);
    expect(zoom).toBe(WORKFLOW_ZOOM.max);
    // Repeated zoom-out saturates at the min.
    zoom = 1;
    for (let i = 0; i < 20; i += 1) zoom = zoomOutLevel(zoom);
    expect(zoom).toBe(WORKFLOW_ZOOM.min);
  });

  it("reset fits the whole graph in view: a graph wider than the viewport zooms out to fit", () => {
    // A wide pipeline (1600x600) in a smaller viewport (1000x600) must scale
    // DOWN so every node is visible, never clipped off the right edge, while
    // staying above the min bound (so the exact fit, not the clamp, is tested).
    const content = { width: 1600, height: 600 };
    const viewport = { width: 1000, height: 600 };
    const zoom = fitToViewZoom(content, viewport);
    expect(zoom).toBeLessThan(1);
    expect(zoom).toBeGreaterThan(WORKFLOW_ZOOM.min);
    expect(zoom).toBeLessThanOrEqual(WORKFLOW_ZOOM.max);
    // The fit leaves padding: the scaled content plus padding fits the viewport.
    const pad = 1 + WORKFLOW_ZOOM.fitPadding * 2;
    expect(content.width * zoom * pad).toBeLessThanOrEqual(viewport.width + 0.001);
  });

  it("clamps the fit zoom to the min bound for a graph too wide to fit even scaled", () => {
    // An extremely wide graph would need a sub-min zoom to fit; the reset caps
    // at the min so nodes stay minimally readable rather than shrinking to dust.
    const zoom = fitToViewZoom({ width: 4000, height: 600 }, { width: 600, height: 600 });
    expect(zoom).toBe(WORKFLOW_ZOOM.min);
  });

  it("reset does not overshoot the max zoom for a tiny graph", () => {
    // A small graph in a big viewport would fit at a huge scale; the reset must
    // cap at the max so nodes are not blown up past readable size.
    const zoom = fitToViewZoom({ width: 100, height: 60 }, { width: 1600, height: 1200 });
    expect(zoom).toBe(WORKFLOW_ZOOM.max);
  });
});
