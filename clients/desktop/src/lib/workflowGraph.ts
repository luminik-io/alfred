// The canonical engineering workflow the fleet runs, as a left-to-right
// pipeline of lanes with the handoffs between agents. The nodes are live
// (status + runs come from the roster); the lanes and edges are the fixed
// delivery flow. This is intentionally declarative so a future editable
// canvas (drag handoffs, add agents) can read and write the same shape.
//
// Lanes are the canonical ROLES (see agentRoster.ts), and every agent the fleet
// reports is placed into the lane matching its role, with a fallback lane so no
// agent is ever dropped. The graph is no longer gated by a hardcoded list of
// codenames.

import dagre from "@dagrejs/dagre";
import type { Edge, Node } from "@xyflow/react";

import type { AlfredTone } from "../components/ui/alfred";
import {
  APPROVAL_GATE_ROLE_EDGE,
  ROLE_EDGE_LABEL,
  ROLE_EDGES,
  ROLE_LANE_LABEL,
  roleOrder,
  WORKFLOW_ROLES,
  type WorkflowRole,
} from "./agentRoster";

// Node + lane footprint used both for the dagre layout and the CSS sizing of
// the rendered card. Keep these in sync with the .wf-node / .wf-lane rules.
const NODE_WIDTH = 232;
const NODE_HEIGHT = 98;
// Dagre spacing. ranksep controls the horizontal gap between lanes (we lay the
// graph out left-to-right), nodesep the vertical gap within a rank.
const RANK_SEP = 96;
const NODE_SEP = 28;
const EDGE_SEP = 18;
// Vertical offset that lifts each lane label clear of the agent cards beneath
// it. Lane labels are derived from the laid-out agent positions, not laid out
// by dagre, so we place them by hand above the band.
const LANE_LABEL_LIFT = 78;

/**
 * The display fields a node needs, derived by the caller from the live row. The
 * `role` is the canonical lane the agent belongs to (derived from metadata, not
 * a name list); `roleLabel` is the human-readable role shown on the card.
 */
export type WorkflowNodeInput = {
  codename: string;
  role: WorkflowRole;
  label: string;
  // The plain role label (e.g. "Reviewer"), shown independent of the name.
  roleLabel: string;
  accent: string;
  tone: AlfredTone;
  statusLabel: string;
  runsToday: number;
  // Optional richer fields (the card degrades gracefully without them).
  lastRunLabel?: string;
  failStreak?: number;
};

export type AgentNodeData = WorkflowNodeInput & {
  laneId: WorkflowRole;
  selected: boolean;
  [key: string]: unknown;
};

export type LaneNodeData = { label: string; [key: string]: unknown };

// Canvas zoom bounds and fit padding, shared by the React Flow canvas and the
// pure zoom-state helpers below so the component and its tests agree on one set
// of numbers. Kept generous enough that the whole pipeline fits at the low end
// and a single card is readable at the high end.
export const WORKFLOW_ZOOM = {
  min: 0.35,
  max: 1.75,
  // Multiplicative step for a single zoom-in / zoom-out control press, matching
  // React Flow's own zoomIn/zoomOut factor so the buttons and the wheel agree.
  step: 1.2,
  // Padding fraction left around the graph when fitting all nodes in view.
  fitPadding: 0.14,
} as const;

/** Clamp a zoom level to the canvas bounds. Pure. */
export function clampWorkflowZoom(zoom: number): number {
  if (!Number.isFinite(zoom)) return WORKFLOW_ZOOM.min;
  return Math.min(WORKFLOW_ZOOM.max, Math.max(WORKFLOW_ZOOM.min, zoom));
}

/** The next zoom level after a zoom-in press, clamped to the max. Pure. */
export function zoomInLevel(zoom: number): number {
  return clampWorkflowZoom(zoom * WORKFLOW_ZOOM.step);
}

/** The next zoom level after a zoom-out press, clamped to the min. Pure. */
export function zoomOutLevel(zoom: number): number {
  return clampWorkflowZoom(zoom / WORKFLOW_ZOOM.step);
}

/**
 * The zoom the "reset / fit to view" control lands on for a given content and
 * viewport size: the largest zoom (within bounds) at which the whole graph
 * bounding box, plus its padding, still fits the viewport. Pure so the fit math
 * is testable without a live canvas; the live canvas uses React Flow's fitView,
 * which computes the same quantity.
 */
export function fitToViewZoom(
  content: { width: number; height: number },
  viewport: { width: number; height: number },
): number {
  if (content.width <= 0 || content.height <= 0) return WORKFLOW_ZOOM.min;
  if (viewport.width <= 0 || viewport.height <= 0) return WORKFLOW_ZOOM.min;
  const pad = 1 + WORKFLOW_ZOOM.fitPadding * 2;
  const scaleX = viewport.width / (content.width * pad);
  const scaleY = viewport.height / (content.height * pad);
  return clampWorkflowZoom(Math.min(scaleX, scaleY));
}

/**
 * Lay the pipeline out with dagre as a left-to-right DAG. We seed each agent and
 * lane label as a sized node, add the surviving handoffs as edges, and pin the
 * lane rank so the canonical order (triage -> architect -> ... -> ops) is never
 * reordered by the solver. Returns top-left positions React Flow can consume.
 */
function layoutGraph(
  agents: { codename: string; laneId: WorkflowRole }[],
  lanes: { id: WorkflowRole }[],
  edges: [string, string][],
): Map<string, { x: number; y: number }> {
  // No compound nodes here (setParent is never called), so leave the graph in
  // its default non-compound mode to keep dagre's rank assignment unaltered.
  const g = new dagre.graphlib.Graph();
  g.setGraph({
    rankdir: "LR",
    ranksep: RANK_SEP,
    nodesep: NODE_SEP,
    edgesep: EDGE_SEP,
    marginx: 24,
    marginy: 48,
  });
  g.setDefaultEdgeLabel(() => ({}));

  // Only real agent nodes go through dagre. Lane labels are not part of the
  // flow (nothing connects to them), so we derive their positions from the
  // laid-out agents below instead of registering throwaway nodes.
  for (const agent of agents) {
    g.setNode(agent.codename, { width: NODE_WIDTH, height: NODE_HEIGHT });
  }
  for (const [source, target] of edges) {
    g.setEdge(source, target);
  }

  dagre.layout(g);

  // Force lane labels to share the x rank of the first agent in their lane and
  // sit above the band, so they read as column headings rather than drifting
  // into the flow. Dagre gives center coords; React Flow wants top-left.
  const positions = new Map<string, { x: number; y: number }>();
  let minAgentY = Infinity;
  for (const agent of agents) {
    const n = g.node(agent.codename);
    if (!n) continue;
    minAgentY = Math.min(minAgentY, n.y - NODE_HEIGHT / 2);
    positions.set(agent.codename, {
      x: n.x - NODE_WIDTH / 2,
      y: n.y - NODE_HEIGHT / 2,
    });
  }

  for (const lane of lanes) {
    const laneAgents = agents.filter((a) => a.laneId === lane.id);
    if (!laneAgents.length) continue;
    const first = positions.get(laneAgents[0].codename);
    if (!first) continue;
    const labelY = (Number.isFinite(minAgentY) ? minAgentY : first.y) - LANE_LABEL_LIFT;
    positions.set(`lane:${lane.id}`, { x: first.x, y: labelY });
  }

  return positions;
}

/**
 * Build React Flow nodes + edges for the workflow graph from live node inputs.
 * EVERY input is placed: each agent goes into the lane named by its `role`, so
 * the whole reported roster renders (not a hardcoded subset). Edges connect the
 * agents occupying each pair of handoff roles. Pure and deterministic.
 */
export function buildWorkflowGraph(
  inputs: WorkflowNodeInput[],
  selectedCodename: string | null,
): { nodes: Node[]; edges: Edge[] } {
  // Group the live agents by role, in canonical lane order. Within a lane keep
  // the caller's order (the roster already sorts by a stable agent order).
  const byRole = new Map<WorkflowRole, WorkflowNodeInput[]>();
  for (const input of inputs) {
    const bucket = byRole.get(input.role);
    if (bucket) {
      bucket.push(input);
    } else {
      byRole.set(input.role, [input]);
    }
  }

  const placedAgents: { codename: string; laneId: WorkflowRole }[] = [];
  const presentLanes: { id: WorkflowRole }[] = [];
  const presentRoles = new Set<WorkflowRole>();
  for (const role of [...WORKFLOW_ROLES].sort((a, b) => roleOrder(a) - roleOrder(b))) {
    const laneAgents = byRole.get(role);
    if (!laneAgents || !laneAgents.length) continue;
    presentLanes.push({ id: role });
    presentRoles.add(role);
    for (const agent of laneAgents) {
      placedAgents.push({ codename: agent.codename, laneId: role });
    }
  }

  // Role handoffs become agent->agent edges. Every agent in the source lane is
  // wired into the pipeline (not just the first), so a multi-agent lane never
  // leaves its secondary agents orphaned on the canvas. To avoid a dense
  // all-pairs mesh we connect each source agent to the target lane's single
  // representative (its first agent), which matches the old explicit edge list
  // (e.g. lucius/bane/nightwing all handed off to rasalghul). Only edges whose
  // both lanes are present survive.
  const firstInRole = (role: WorkflowRole): string | null =>
    byRole.get(role)?.[0]?.codename ?? null;

  const liveEdges: {
    source: string;
    target: string;
    label: string;
    gate: boolean;
  }[] = [];
  const seenEdges = new Set<string>();
  for (const [sourceRole, targetRole] of ROLE_EDGES) {
    if (!presentRoles.has(sourceRole) || !presentRoles.has(targetRole)) continue;
    const target = firstInRole(targetRole);
    if (!target) continue;
    const rolePair = `${sourceRole}->${targetRole}`;
    const label = ROLE_EDGE_LABEL[rolePair] ?? "";
    const gate = rolePair === APPROVAL_GATE_ROLE_EDGE;
    for (const sourceAgent of byRole.get(sourceRole) ?? []) {
      const source = sourceAgent.codename;
      if (source === target) continue;
      const key = `${source}->${target}`;
      if (seenEdges.has(key)) continue;
      seenEdges.add(key);
      liveEdges.push({ source, target, label, gate });
    }
  }

  const positions = layoutGraph(
    placedAgents,
    presentLanes,
    liveEdges.map((edge) => [edge.source, edge.target] as [string, string]),
  );

  const byCodename = new Map(inputs.map((input) => [input.codename, input]));
  const nodes: Node[] = [];

  for (const lane of presentLanes) {
    const pos = positions.get(`lane:${lane.id}`) ?? { x: 0, y: 0 };
    nodes.push({
      id: `lane:${lane.id}`,
      type: "lane",
      position: pos,
      data: { label: ROLE_LANE_LABEL[lane.id] } satisfies LaneNodeData,
      draggable: false,
      selectable: false,
      deletable: false,
    });
  }

  for (const placed of placedAgents) {
    const input = byCodename.get(placed.codename)!;
    const pos = positions.get(placed.codename) ?? { x: 0, y: 0 };
    nodes.push({
      id: placed.codename,
      type: "agent",
      position: pos,
      // Declare the card footprint so the minimap can paint the node before
      // React Flow measures the DOM (otherwise the overview renders empty).
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
      data: {
        ...input,
        laneId: placed.laneId,
        selected: placed.codename === selectedCodename,
      } satisfies AgentNodeData,
      draggable: false,
    });
  }

  const edges: Edge[] = liveEdges.map(({ source, target, label, gate }) => ({
    id: `${source}->${target}`,
    source,
    target,
    type: "smoothstep",
    label: label || undefined,
    // The approval-gate edge is marked so the renderer can flag the human
    // go-ahead step; a small data bag keeps the Edge shape declarative.
    data: { gate, label },
    className: gate ? "wf-edge wf-edge--gate" : "wf-edge",
    animated: source === selectedCodename || target === selectedCodename,
  }));

  return { nodes, edges };
}
