import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const srcDir = resolve(__dirname, "..");
const component = readFileSync(
  resolve(srcDir, "components/WorkflowGraph.tsx"),
  "utf8",
);
const styles = readFileSync(resolve(srcDir, "styles/workflow.css"), "utf8");

describe("Workflow surface contract", () => {
  it("uses the shared status tokens for graph state and approval", () => {
    expect(component).toContain('readToken("--ok"');
    expect(component).toContain('readToken("--warn"');
    expect(component).toContain('readToken("--error"');
    expect(styles).toContain('--wf-tone: var(--ok)');
    expect(styles).toContain('--wf-tone: var(--warn)');
    expect(styles).toContain('--wf-tone: var(--error)');
    expect(styles).toContain('stroke: var(--warn)');
    expect(styles).not.toContain("oklch(0.78 0.16 80)");
  });

  it("gives full-screen and canvas controls 44-pixel mobile targets", () => {
    const coarsePointerBlock = styles.match(
      /@media \(max-width: 480px\), \(pointer: coarse\) \{([\s\S]*?)\n\}/,
    )?.[1];
    expect(coarsePointerBlock).toContain(".wf-maximize");
    expect(coarsePointerBlock).toContain(".react-flow__controls-button");
    expect(coarsePointerBlock).toContain("width: 44px");
    expect(coarsePointerBlock).toContain("height: 44px");
  });

  it("keeps data nodes flat and removes the minimap from phone layouts", () => {
    const nodeBlock = styles.match(/\.wf-node \{([\s\S]*?)\n\}/)?.[1];
    expect(nodeBlock).toContain("background: var(--card)");
    expect(nodeBlock).not.toContain("backdrop-filter");

    const phoneBlock = styles.match(
      /@media \(max-width: 640px\) \{([\s\S]*?)\n\}/,
    )?.[1];
    expect(phoneBlock).toContain(".workflow-graph .wf-minimap");
    expect(phoneBlock).toContain("display: none");
  });
});
