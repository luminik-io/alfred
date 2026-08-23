import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const desktopDir = resolve(__dirname, "../..");
const repoDir = resolve(desktopDir, "../..");

describe("Work inspector breakpoint contract", () => {
  it("uses the same 1600-pixel dock threshold in code, styles, and design guidance", () => {
    const component = readFileSync(
      resolve(desktopDir, "src/components/PipelineView.tsx"),
      "utf8",
    );
    const styles = readFileSync(
      resolve(desktopDir, "src/styles/polish.css"),
      "utf8",
    );
    const design = readFileSync(resolve(repoDir, "DESIGN.md"), "utf8");

    expect(component).toContain('useMediaQuery("(min-width: 1600px)")');
    expect(styles).toMatch(
      /@media \(min-width: 1600px\) \{\s*\.alfred-pipeline__workspace\.has-inspector/,
    );
    expect(design).toContain("at widths of 1600px and above");
  });
});
