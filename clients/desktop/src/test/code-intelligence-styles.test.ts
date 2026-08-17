import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const srcDir = resolve(__dirname, "..");
const component = readFileSync(
  resolve(srcDir, "components/CodeIntelligenceView.tsx"),
  "utf8",
);
const manifest = readFileSync(resolve(srcDir, "index.css"), "utf8");
const styles = readFileSync(
  resolve(srcDir, "styles/code-intelligence.css"),
  "utf8",
);

describe("Code Intelligence visual contract", () => {
  it("loads one named surface stylesheet", () => {
    expect(manifest).toContain('@import "./styles/code-intelligence.css";');
    expect(component).toContain('className="code-intelligence motion-fade"');
  });

  it("does not rebuild the surface system from anonymous utility cards", () => {
    for (const utility of [
      "bg-card",
      "bg-background",
      "backdrop-blur",
      "rounded-lg",
      "rounded-md",
      "shadow-sm",
    ]) {
      expect(component, utility).not.toContain(utility);
    }
  });

  it("gives each approved theme a deliberate Code surface treatment", () => {
    for (const theme of ["signal-edge", "category-standard", "linked-fold"]) {
      expect(styles).toContain(`:root[data-theme="${theme}"] .code-intelligence`);
    }
  });

  it("protects the narrow-screen query and relationship layouts", () => {
    expect(styles).toMatch(/@media \(max-width: 767px\)/);
    expect(styles).toContain(".code-intelligence__query-action");
    expect(styles).toContain(".code-intelligence__relationship-map");
  });
});
