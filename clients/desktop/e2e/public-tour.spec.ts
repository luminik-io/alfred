import { expect, test } from "playwright/test";

import {
  assertAlfredApiComplete,
  installAlfredApi,
} from "./alfred-api.fixture";

const pauseMs = Number(process.env.ALFRED_TOUR_PAUSE_MS ?? 0);

async function pause(): Promise<void> {
  if (pauseMs > 0) await new Promise((resolve) => setTimeout(resolve, pauseMs));
}

test.afterEach(async ({ page }) => {
  assertAlfredApiComplete(page);
});

test("records the public tour from sample data only", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.addInitScript(() => {
    localStorage.setItem("alfred-theme-name", "signal-edge");
    localStorage.setItem("alfred-theme", "light");
  });
  const api = await installAlfredApi(page);

  await page.goto("/");
  await expect
    .poll(() => page.evaluate(() => [window.innerWidth, window.innerHeight]))
    .toEqual([1440, 900]);
  await expect(page.getByLabel("Inbox", { exact: true })).toBeVisible();
  await expect(page.getByText("Usage unavailable.")).toHaveCount(0);
  await pause();

  await page.getByRole("button", { name: "Ask" }).click();
  await page
    .getByPlaceholder("Ask a question, or describe a change you want made.")
    .fill("Add regression tests for slug formatting.");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(
    page.getByText("I found the relevant desktop protocol.", { exact: false }),
  ).toBeVisible();
  await api.releaseStream();
  await expect(
    page.getByText("What outcome should the test prove?", { exact: false }),
  ).toBeVisible();
  await pause();

  await page.getByRole("button", { name: "Work", exact: true }).click();
  await expect(page.getByRole("note")).toHaveText(
    "Demo data. No real repositories or agent activity.",
  );
  await page
    .getByRole("button", { name: /Record engine settings for every run/ })
    .click();
  await expect(page.getByRole("dialog", { name: "Work item" })).toBeVisible();
  await pause();

  await page.getByRole("button", { name: "Close" }).click();

  await page.getByRole("button", { name: "Code", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Code intelligence" }),
  ).toBeVisible();
  await pause();

  await page.getByRole("button", { name: "Agents", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Agents" })).toBeVisible();
  await pause();

  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
  await page.getByRole("tab", { name: "Appearance" }).click();
  await expect(page.getByRole("region", { name: "Appearance" })).toBeVisible();
  await expect(page.getByText("Prism", { exact: true })).toBeVisible();
  await pause();
});
