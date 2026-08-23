import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MiniMap,
  type NodeProps,
  Position,
  ReactFlow,
  type ReactFlowProps,
  useReactFlow,
  useNodesInitialized,
  useStore,
} from "@xyflow/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Maximize2, Minimize2 } from "lucide-react";

import {
  type AgentNodeData,
  buildWorkflowGraph,
  declaredWorkflowBounds,
  initialWorkflowViewport,
  type LaneNodeData,
  WORKFLOW_ZOOM,
  type WorkflowNodeInput,
} from "../lib/workflowGraph";
import { useMediaQuery } from "../hooks/use-mobile";
import { AlfredStatusDot } from "./ui/alfred";

import "@xyflow/react/dist/style.css";

/** A lane heading sitting above its column of agents. */
function LaneNode({ data }: NodeProps) {
  const { label } = data as LaneNodeData;
  return <div className="wf-lane">{label}</div>;
}

/**
 * One agent in the pipeline: monogram, name, role, live status, today's run
 * count, last-run recency, and a health line when a fail streak is building.
 * Status drives the accent rail color so the canvas reads at a glance.
 */
function AgentNode({ data }: NodeProps) {
  const node = data as AgentNodeData;
  const monogram = (node.label || node.codename).trim().charAt(0).toUpperCase();
  const failStreak = node.failStreak ?? 0;
  return (
    <div
      className="wf-node"
      data-tone={node.tone}
      data-selected={node.selected ? "true" : "false"}
      style={{ "--agent-accent": node.accent } as React.CSSProperties}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="wf-node__handle"
      />
      <span className="wf-node__rail" aria-hidden="true" />
      <span className="wf-node__mark" aria-hidden="true">
        {monogram}
      </span>
      <span className="wf-node__body">
        <span className="wf-node__name">{node.label || node.codename}</span>
        <span className="wf-node__role">{node.roleLabel || node.codename}</span>
      </span>
      <span className="wf-node__status" data-tone={node.tone}>
        <AlfredStatusDot tone={node.tone} aria-hidden="true" />
        {node.statusLabel}
      </span>
      <span className="wf-node__meta">
        <span className="wf-node__metaitem">
          <span className="wf-node__metalabel">Runs</span>
          <span className="wf-node__metavalue">{node.runsToday} today</span>
        </span>
        {node.lastRunLabel ? (
          <span className="wf-node__metaitem">
            <span className="wf-node__metalabel">Last run</span>
            <span className="wf-node__metavalue">{node.lastRunLabel}</span>
          </span>
        ) : null}
        {failStreak >= 1 ? (
          <span className="wf-node__metaitem wf-node__metaitem--warn">
            <span className="wf-node__metalabel">Fails</span>
            <span className="wf-node__metavalue">{failStreak} in a row</span>
          </span>
        ) : null}
      </span>
      <Handle
        type="source"
        position={Position.Right}
        className="wf-node__handle"
      />
    </div>
  );
}

const NODE_TYPES: ReactFlowProps["nodeTypes"] = {
  agent: AgentNode,
  lane: LaneNode,
};

/** Explains pipeline stages and status colors above the workflow canvas. */
function WorkflowLegend() {
  return (
    <aside className="wf-legend" aria-label="Workflow legend">
      <p className="wf-legend__title">Delivery pipeline</p>
      <p className="wf-legend__flow">
        Plan <span aria-hidden="true">&rarr;</span> approve{" "}
        <span aria-hidden="true">&rarr;</span> build{" "}
        <span aria-hidden="true">&rarr;</span> review{" "}
        <span aria-hidden="true">&rarr;</span> merge
      </p>
      <ul className="wf-legend__items">
        <li className="wf-legend__item">
          <span
            className="wf-legend__swatch"
            data-tone="ok"
            aria-hidden="true"
          />
          Running
        </li>
        <li className="wf-legend__item">
          <span
            className="wf-legend__swatch"
            data-tone="warn"
            aria-hidden="true"
          />
          Needs attention
        </li>
        <li className="wf-legend__item">
          <span
            className="wf-legend__swatch"
            data-tone="error"
            aria-hidden="true"
          />
          Failing
        </li>
        <li className="wf-legend__item">
          <span
            className="wf-legend__swatch"
            data-tone="idle"
            aria-hidden="true"
          />
          Idle
        </li>
        <li className="wf-legend__item wf-legend__item--edge">
          <span className="wf-legend__edge" aria-hidden="true" />
          Handoff
        </li>
        <li className="wf-legend__item wf-legend__item--edge">
          <span
            className="wf-legend__edge wf-legend__edge--gate"
            aria-hidden="true"
          />
          Your approval
        </li>
      </ul>
    </aside>
  );
}

// The explicit fit-to-view button (React Flow's <Controls>) may reach all the
// way down to WORKFLOW_ZOOM.min so a single click reveals the WHOLE pipeline,
// even below the readable floor the default view respects. The default framing
// (initial load + resize) is driven by initialWorkflowViewport instead, which
// floors zoom at WORKFLOW_ZOOM.readable and starts wide graphs at their leftmost
// lanes. Keeping the two separate is deliberate: the default stays legible while
// "show me everything" stays one click away.
const FIT_OPTIONS = {
  padding: WORKFLOW_ZOOM.fitPadding,
  minZoom: WORKFLOW_ZOOM.min,
  maxZoom: WORKFLOW_ZOOM.max,
} as const;

const MAX_INITIAL_FRAME_ATTEMPTS = 8;

// MiniMap node color tracks the agent's live status, so the overview reads the
// same as the canvas. The minimap paints into a raw SVG, so resolve theme
// tokens to concrete values up front (CSS variables do not apply on <rect>).
function readToken(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  return value || fallback;
}

function miniMapNodeColor(node: { type?: string; data?: unknown }): string {
  if (node.type !== "agent") {
    return "rgba(148, 163, 184, 0.35)";
  }
  const tone = (node.data as AgentNodeData | undefined)?.tone;
  if (tone === "error") return readToken("--error", "oklch(0.6 0.2 25)");
  if (tone === "warn") return readToken("--warn", "oklch(0.7 0.14 80)");
  if (tone === "ok") return readToken("--ok", "oklch(0.62 0.13 158)");
  return "rgba(148, 163, 184, 0.6)";
}

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/**
 * Frame the pipeline on load and keep it framed as the canvas resizes. React
 * Flow tracks the container size but does not re-fit on its own, so the graph
 * would open unframed and then clip when the window resizes, the sidebar
 * toggles, or the layout stacks below on narrow screens. We watch the store's
 * width/height and (debounced) set the viewport with initialWorkflowViewport, so
 * the default view respects the readable zoom floor and starts a too-wide fleet
 * at its leftmost lanes at every breakpoint, instead of shrinking node text to
 * fit. The explicit fit-to-view button (Controls) still reaches the full graph.
 */
function FitToContainer({
  signature,
  fallbackBounds,
}: {
  signature: string;
  fallbackBounds: { x: number; y: number; width: number; height: number };
}) {
  const { getNodes, getNodesBounds, setViewport } = useReactFlow();
  const nodesInitialized = useNodesInitialized();
  const width = useStore((state) => state.width);
  const height = useStore((state) => state.height);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const {
    x: fallbackX,
    y: fallbackY,
    width: fallbackWidth,
    height: fallbackHeight,
  } = fallbackBounds;

  useEffect(() => {
    if (!width || !height || !nodesInitialized) {
      return;
    }
    let cancelled = false;
    let attempts = 0;

    const frame = () => {
      clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        if (cancelled) return;
        const nodes = getNodes();
        const bounds = nodes.length ? getNodesBounds(nodes) : null;
        if (!bounds || bounds.width <= 0 || bounds.height <= 0) {
          attempts += 1;
          if (attempts < MAX_INITIAL_FRAME_ATTEMPTS) {
            frame();
          } else if (fallbackWidth > 0 && fallbackHeight > 0) {
            const target = initialWorkflowViewport(
              {
                x: fallbackX,
                y: fallbackY,
                width: fallbackWidth,
                height: fallbackHeight,
              },
              { width, height },
            );
            void setViewport(target, {
              duration: prefersReducedMotion() ? 0 : 240,
            });
          }
          return;
        }
        const target = initialWorkflowViewport(bounds, { width, height });
        void setViewport(target, {
          duration: prefersReducedMotion() ? 0 : 240,
        });
      }, 80);
    };

    frame();
    return () => {
      cancelled = true;
      clearTimeout(timer.current);
    };
  }, [
    width,
    height,
    nodesInitialized,
    signature,
    getNodes,
    getNodesBounds,
    setViewport,
    fallbackX,
    fallbackY,
    fallbackWidth,
    fallbackHeight,
  ]);

  return null;
}

export function WorkflowGraph({
  agents,
  selectedCodename,
  onSelect,
  onMaximize,
}: {
  agents: WorkflowNodeInput[];
  selectedCodename: string | null;
  onSelect: (codename: string) => void;
  onMaximize?: () => void;
}) {
  const { nodes, edges } = useMemo(
    () => buildWorkflowGraph(agents, selectedCodename),
    [agents, selectedCodename],
  );
  const fallbackBounds = declaredWorkflowBounds(nodes);

  // Re-fit whenever the set of agents changes (not on mere selection), so a
  // roster that loads or changes size still frames cleanly.
  const signature = useMemo(
    () => agents.map((agent) => agent.codename).join(","),
    [agents],
  );

  // Maximize expands the canvas to a full-viewport overlay. The pipeline is a
  // wide graph that is cramped when it shares the page with the stat cards and
  // roster header, so a mouse or trackpad user gets room to actually pan and
  // zoom. Escape exits; the signature change re-fits the graph to the new size.
  const [maximized, setMaximized] = useState(false);
  const graphRef = useRef<HTMLDivElement>(null);
  const maximizeButtonRef = useRef<HTMLButtonElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);
  const toggleMaximized = () => {
    if (!maximized) {
      restoreFocusRef.current =
        document.activeElement instanceof HTMLElement
          ? document.activeElement
          : maximizeButtonRef.current;
      setMaximized(true);
      onMaximize?.();
      return;
    }
    setMaximized(false);
  };
  const selectNode = (codename: string) => {
    if (maximized) setMaximized(false);
    onSelect(codename);
  };
  useEffect(() => {
    if (!maximized) return;
    const graph = graphRef.current;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    maximizeButtonRef.current?.focus();

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setMaximized(false);
        return;
      }
      if (event.key !== "Tab" || !graph) return;

      const focusable = Array.from(
        graph.querySelectorAll<HTMLElement>(
          "button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
        ),
      ).filter((element) => element.getAttribute("aria-hidden") !== "true");
      if (!focusable.length) {
        event.preventDefault();
        graph.focus();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
      if (restoreFocusRef.current?.isConnected) {
        restoreFocusRef.current.focus();
      } else {
        document
          .querySelector<HTMLButtonElement>(
            '.workflow-graph[data-maximized="false"] .wf-maximize',
          )
          ?.focus();
      }
    };
  }, [maximized]);

  const desktopLayout = useMediaQuery("(min-width: 1024px)");
  const fitMaximized = maximized && desktopLayout;

  const graph = (
    <div
      ref={graphRef}
      className="workflow-graph"
      data-maximized={maximized ? "true" : "false"}
      role={maximized ? "dialog" : "region"}
      aria-modal={maximized ? "true" : undefined}
      aria-label="Agent workflow graph"
      tabIndex={maximized ? -1 : undefined}
    >
      <div className="wf-toolbar">
        <button
          ref={maximizeButtonRef}
          type="button"
          className="wf-maximize"
          onClick={toggleMaximized}
          aria-pressed={maximized}
          aria-label={maximized ? "Exit full screen" : "Maximize workflow"}
          title={maximized ? "Exit full screen (Esc)" : "Maximize"}
        >
          {maximized ? (
            <Minimize2 aria-hidden="true" size={15} />
          ) : (
            <Maximize2 aria-hidden="true" size={15} />
          )}
        </button>
        <WorkflowLegend />
      </div>
      <div className="workflow-graph__canvas">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          // Initial + resize framing is driven by FitToContainer (readable floor,
          // leftmost-lane start). Full screen is the deliberate exception: it
          // opens with the complete pipeline in view, using the same bounds as
          // the explicit fit control instead of carrying over the cropped page
          // framing.
          fitView={fitMaximized}
          fitViewOptions={FIT_OPTIONS}
          minZoom={WORKFLOW_ZOOM.min}
          maxZoom={WORKFLOW_ZOOM.max}
          // Canvas controls: the mouse wheel and trackpad two-finger scroll zoom
          // the canvas (the primary way to zoom in on a cramped pipeline), pinch
          // zooms on touch, and click-drag pans. The +/- and fit-to-view controls
          // (bottom-left) and the minimap cover keyboard/mouse-only panning, so a
          // mouse user never has to drag to see a node clipped off an edge.
          zoomOnScroll
          panOnScroll={false}
          zoomOnPinch
          panOnDrag
          zoomOnDoubleClick={false}
          nodesConnectable={false}
          edgesFocusable={false}
          nodesDraggable={false}
          // We render our own selected state (data-selected) from the inspector,
          // so disable React Flow's native selection to avoid a second indicator.
          // onNodeClick still fires.
          elementsSelectable={false}
          proOptions={{ hideAttribution: false }}
          onNodeClick={(_event, node) => {
            if (node.type === "agent") {
              selectNode(node.id);
            }
          }}
        >
          <FitToContainer
            signature={signature}
            fallbackBounds={fallbackBounds}
          />
          <Background variant={BackgroundVariant.Dots} gap={24} size={1} />
          <Controls showInteractive={false} position="bottom-left" />
          <MiniMap
            pannable
            zoomable
            ariaLabel="Workflow minimap"
            position="bottom-right"
            className="wf-minimap"
            nodeColor={miniMapNodeColor}
            nodeStrokeWidth={0}
            maskColor="color-mix(in oklch, var(--background), transparent 35%)"
          />
        </ReactFlow>
      </div>
    </div>
  );
  return maximized && typeof document !== "undefined"
    ? createPortal(graph, document.body)
    : graph;
}
