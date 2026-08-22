import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const srcDir = resolve(__dirname, "..");
const askStyles = readFileSync(resolve(srcDir, "styles/ask.css"), "utf8");
const pipelineStyles = readFileSync(
  resolve(srcDir, "styles/pipeline.css"),
  "utf8",
);

function phoneBlock(styles: string): string | undefined {
  return styles.match(
    /@media \(max-width: 480px\) \{([\s\S]*?)\n\}/,
  )?.[1];
}

describe("Phone action target contract", () => {
  it("gives Ask copy actions a 36-pixel phone target", () => {
    const rule = phoneBlock(askStyles);

    expect(rule).toContain(".ask-bubble__copy");
    expect(rule).toContain(".ask-bubble__action");
    expect(rule).toContain(".ask-code__copy");
    expect(rule).toContain("width: 36px");
    expect(rule).toContain("min-height: 36px");
  });

  it("gives Work card quick actions a 36-pixel phone target", () => {
    const rule = phoneBlock(pipelineStyles);

    expect(rule).toContain(".card-hover-action");
    expect(rule).toContain("width: 36px");
    expect(rule).toContain("height: 36px");
  });
});
