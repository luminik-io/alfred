import { describe, expect, it } from "vitest";

import type { WorkflowRole } from "./agentRoster";
import {
  buildWorkflowGraph,
  clampWorkflowZoom,
  fitToViewZoom,
  initialWorkflowViewport,
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
  input("triage", "triage"),
  input("architect", "architect"),
  input("senior-dev", "senior-dev"),
  input("build-partner", "senior-dev"),
  input("reviewer", "reviewer"),
  input("automerge", "ship"),
  input("ops-watch", "ops"),
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
      [input("architect", "architect"), input("mystery-bot", "ops")],
      null,
    );
    const agentIds = nodes.filter((n) => n.type === "agent").map((n) => n.id);
    expect(agentIds.sort()).toEqual(["architect", "mystery-bot"]);
  });

  it("wires handoff edges between present lanes and drops edges to absent lanes", () => {
    // Only architect + senior-dev present: the architect->senior-dev edge
    // survives; edges to missing lanes (e.g. senior-dev->reviewer) do not.
    const { nodes, edges } = buildWorkflowGraph(
      [input("architect", "architect"), input("senior-dev", "senior-dev")],
      null,
    );
    const agentIds = nodes.filter((n) => n.type === "agent").map((n) => n.id);
    expect(agentIds.sort()).toEqual(["architect", "senior-dev"]);
    expect(edges.map((e) => e.id)).toContain("architect->senior-dev");
    expect(
      edges.every((e) => agentIds.includes(e.source) && agentIds.includes(e.target)),
    ).toBe(true);
  });

  it("wires every agent in a multi-agent lane into the pipeline, not just the first", () => {
    // senior-dev has three agents; each must hand off into the reviewer lane so
    // no secondary agent is left without a pipeline edge.
    const { nodes, edges } = buildWorkflowGraph(
      [
        input("senior-dev", "senior-dev"),
        input("build-partner", "senior-dev"),
        input("feature-helper", "senior-dev"),
        input("reviewer", "reviewer"),
      ],
      null,
    );
    const agentIds = nodes.filter((n) => n.type === "agent").map((n) => n.id);
    expect(agentIds.sort()).toEqual(["build-partner", "feature-helper", "reviewer", "senior-dev"]);
    // Every implement agent connects to the review lane representative.
    const edgeIds = edges.map((e) => e.id);
    expect(edgeIds).toContain("senior-dev->reviewer");
    expect(edgeIds).toContain("build-partner->reviewer");
    expect(edgeIds).toContain("feature-helper->reviewer");
    // No agent in the lane is orphaned: each has at least one incident edge.
    for (const codename of ["senior-dev", "build-partner", "feature-helper"]) {
      expect(
        edges.some((e) => e.source === codename || e.target === codename),
      ).toBe(true);
    }
  });

  it("marks the selected node and animates its incident edges", () => {
    const { nodes, edges } = buildWorkflowGraph(
      [input("senior-dev", "senior-dev"), input("reviewer", "reviewer")],
      "reviewer",
    );
    const selected = nodes.find((n) => n.id === "reviewer");
    expect((selected?.data as { selected: boolean }).selected).toBe(true);
    expect(edges.find((e) => e.id === "senior-dev->reviewer")?.animated).toBe(true);
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

  it("fits a graph that still fits at or above the readable floor to its exact scale", () => {
    // A pipeline (1200x600) modestly larger than the viewport (1400x700) fits at
    // a zoom that is between the readable floor and the max, so the default view
    // uses that exact fit (no floor kicks in) and every node stays visible.
    const content = { width: 1200, height: 600 };
    const viewport = { width: 1400, height: 700 };
    const zoom = fitToViewZoom(content, viewport);
    expect(zoom).toBeGreaterThanOrEqual(WORKFLOW_ZOOM.readable);
    expect(zoom).toBeLessThanOrEqual(WORKFLOW_ZOOM.max);
    // The fit leaves padding: the scaled content plus padding fits the viewport.
    const pad = 1 + WORKFLOW_ZOOM.fitPadding * 2;
    expect(content.width * zoom * pad).toBeLessThanOrEqual(viewport.width + 0.001);
  });

  it("floors the default zoom at the readable level for a graph too wide to fit there", () => {
    // A wide pipeline (1600x600) in a smaller viewport (1000x600) would need a
    // sub-readable zoom to fit; the DEFAULT view refuses to crush the text and
    // holds at the readable floor (the graph overflows and is panned instead).
    const content = { width: 1600, height: 600 };
    const viewport = { width: 1000, height: 600 };
    const zoom = fitToViewZoom(content, viewport);
    expect(zoom).toBe(WORKFLOW_ZOOM.readable);
    // The floored view intentionally does NOT fit the whole graph: it overflows,
    // which initialWorkflowViewport handles by starting at the leftmost lanes.
    const pad = 1 + WORKFLOW_ZOOM.fitPadding * 2;
    expect(content.width * zoom * pad).toBeGreaterThan(viewport.width);
  });

  it("still floors at the readable level for an extremely wide graph, never below", () => {
    // Even a graph far too wide to fit stays at the readable floor rather than
    // shrinking to an unreadable overview (the fit button reaches min instead).
    const zoom = fitToViewZoom({ width: 4000, height: 600 }, { width: 600, height: 600 });
    expect(zoom).toBe(WORKFLOW_ZOOM.readable);
    expect(zoom).toBeGreaterThan(WORKFLOW_ZOOM.min);
  });

  it("does not overshoot the max zoom for a tiny graph", () => {
    // A small graph in a big viewport would fit at a huge scale; the default must
    // cap at the max so nodes are not blown up past readable size.
    const zoom = fitToViewZoom({ width: 100, height: 60 }, { width: 1600, height: 1200 });
    expect(zoom).toBe(WORKFLOW_ZOOM.max);
  });
});

describe("initialWorkflowViewport", () => {
  it("centers a graph that fits at the fitted zoom", () => {
    // Content that fits the viewport at its readable-or-larger fit is centered on
    // both axes, so a small fleet opens balanced rather than pinned to a corner.
    const content = { x: 0, y: 0, width: 1200, height: 600 };
    const viewport = { width: 1400, height: 700 };
    const { x, y, zoom } = initialWorkflowViewport(content, viewport);
    expect(zoom).toBe(fitToViewZoom(content, viewport));
    // Centered: equal gutter either side (scaledWidth <= viewport.width).
    const scaledWidth = content.width * zoom;
    expect(x).toBeCloseTo((viewport.width - scaledWidth) / 2, 5);
    const scaledHeight = content.height * zoom;
    expect(y).toBeCloseTo((viewport.height - scaledHeight) / 2, 5);
  });

  it("pins a too-wide graph to its leftmost lanes at the readable floor", () => {
    // A pipeline too wide to fit at the readable floor opens at the readable zoom,
    // showing the pipeline START (leftmost lanes) with a small left gutter, and
    // is panned right rather than opening centered with both ends clipped.
    const content = { x: 0, y: 0, width: 1600, height: 600 };
    const viewport = { width: 1000, height: 600 };
    const { x, zoom } = initialWorkflowViewport(content, viewport);
    expect(zoom).toBe(WORKFLOW_ZOOM.readable);
    // Left-pinned with a positive gutter: the leftmost node's left edge is
    // on-screen (x > 0), not centered (which would be negative) or off-screen.
    expect(x).toBeCloseTo(viewport.width * WORKFLOW_ZOOM.fitPadding, 5);
    expect(x).toBeGreaterThan(0);
  });

  it("respects a content origin that does not start at (0, 0)", () => {
    // Bounds from the live canvas start at the dagre margin, not the origin. The
    // left-pin math must map that origin in so the leftmost lane still lands at
    // the gutter, not off-screen.
    const content = { x: 40, y: 30, width: 1600, height: 600 };
    const viewport = { width: 1000, height: 600 };
    const { x, zoom } = initialWorkflowViewport(content, viewport);
    // Screen position of the content's left edge: content.x * zoom + x.
    const leftEdgeOnScreen = content.x * zoom + x;
    expect(leftEdgeOnScreen).toBeCloseTo(viewport.width * WORKFLOW_ZOOM.fitPadding, 5);
  });
});
