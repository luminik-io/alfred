import { defineConfig, devices } from "playwright/test";

import baseConfig from "./playwright.config";

export default defineConfig({
  ...baseConfig,
  fullyParallel: false,
  workers: 1,
  testMatch: "public-gallery.spec.ts",
  outputDir: "test-results/public-gallery-artifacts",
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 862 },
      },
    },
  ],
  use: {
    ...baseConfig.use,
    trace: "off",
    viewport: { width: 1440, height: 862 },
  },
});
