import { defineConfig, devices } from "playwright/test";

import baseConfig from "./playwright.config";

export default defineConfig({
  ...baseConfig,
  fullyParallel: false,
  workers: 1,
  testDir: "./qa",
  testMatch: "visual-audit.spec.ts",
  outputDir: "test-results/visual-audit-artifacts",
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  use: {
    ...baseConfig.use,
    trace: "retain-on-failure",
  },
});
