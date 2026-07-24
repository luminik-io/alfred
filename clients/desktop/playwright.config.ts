import { defineConfig, devices } from "playwright/test";

const contractPort = Number(process.env.ALFRED_CONTRACT_PORT ?? 41_173);
const contractOrigin = `http://127.0.0.1:${contractPort}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "line",
  use: {
    baseURL: contractOrigin,
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: `npm run preview:contract -- --port ${contractPort}`,
    url: contractOrigin,
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
