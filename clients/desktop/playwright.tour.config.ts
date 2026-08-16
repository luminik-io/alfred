import { defineConfig, devices } from "playwright/test";

import baseConfig from "./playwright.config";

export default defineConfig({
  ...baseConfig,
  fullyParallel: false,
  workers: 1,
  testMatch: "public-tour.spec.ts",
  outputDir: "test-results/public-tour",
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 900 },
      },
    },
  ],
  use: {
    ...baseConfig.use,
    trace: "off",
    video: {
      mode: "on",
      size: { width: 1440, height: 900 },
    },
    viewport: { width: 1440, height: 900 },
  },
});
