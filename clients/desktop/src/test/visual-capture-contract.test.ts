import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const desktopDir = resolve(__dirname, "../..");

describe("Desktop visual capture", () => {
  it("uses the fixture-backed Playwright audit as the supported capture path", () => {
    const packageJson = JSON.parse(
      readFileSync(resolve(desktopDir, "package.json"), "utf8"),
    ) as { scripts: Record<string, string> };
    const visualAudit = readFileSync(
      resolve(desktopDir, "qa/visual-audit.spec.ts"),
      "utf8",
    );

    expect(packageJson.scripts["capture:visual"]).toBe(
      "node scripts/run-contract-tests.mjs --config=playwright.visual.config.ts",
    );
    expect(visualAudit).toContain("installAlfredApi(page)");
    expect(existsSync(resolve(desktopDir, "scripts/design-shots.mjs"))).toBe(
      false,
    );
  });
});
