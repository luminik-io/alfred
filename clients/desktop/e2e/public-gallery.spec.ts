import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";

import { expect, test, type Page } from "playwright/test";

import {
  assertAlfredApiComplete,
  installAlfredApi,
} from "./alfred-api.fixture";

const outputDir = resolve(process.cwd(), "test-results/public-gallery-source");

async function prepare(page: Page, theme: string): Promise<void> {
  await page.setViewportSize({ width: 1440, height: 862 });
  await page.addInitScript((themeName) => {
    localStorage.setItem("alfred-theme-name", themeName);
    localStorage.setItem("alfred-theme", "light");
  }, theme);
  await installAlfredApi(page);
  await page.goto("/");
  await page.evaluate(() => document.fonts.ready);
  await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
  await expect(page.locator("html")).toHaveClass(/\blight\b/);
  await expect
    .poll(() => page.evaluate(() => [window.innerWidth, window.innerHeight]))
    .toEqual([1440, 862]);
  await expect(page.getByRole("note")).toHaveText(
    "Demo data. No real repositories or agent activity.",
  );
}

async function capture(page: Page, name: string): Promise<void> {
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: resolve(outputDir, name),
    animations: "disabled",
  });
}

test.beforeAll(async () => {
  await mkdir(outputDir, { recursive: true });
});

test.afterEach(async ({ page }) => {
  assertAlfredApiComplete(page);
});

test("captures autonomous work and evidence", async ({ page }) => {
  await prepare(page, "signal-edge");
  await page.getByRole("button", { name: "Work", exact: true }).click();
  await page
    .getByRole("button", { name: /Replace legacy appearance presets/ })
    .click();
  await expect(page.getByRole("dialog", { name: "Work item" })).toBeVisible();
  await capture(page, "alfred-gallery-work.png");
});

test("captures the agent roster and recent work", async ({ page }) => {
  await prepare(page, "category-standard");
  await page.getByRole("button", { name: "Agents", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Agents" })).toBeVisible();
  await expect(
    page.getByText("Completed the Desktop visual contract"),
  ).toHaveCount(0);
  await page.getByRole("tab", { name: "Activity" }).click();
  await expect(
    page.getByText("Completed the Desktop visual contract"),
  ).toBeVisible();
  await page
    .getByRole("button", { name: /Open senior-dev's latest run/ })
    .click();
  await expect(page.getByRole("tab", { name: "Latest run" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(
    page.getByText("Ran layout and responsive checks"),
  ).toBeVisible();
  await expect(page.getByRole("note")).toBeVisible();
  await capture(page, "alfred-gallery-agents.png");
});

test("captures the operator approval gate", async ({ page }) => {
  await prepare(page, "linked-fold");
  await expect(
    page.getByRole("heading", { name: "Alfred needs one decision" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve" })).toBeVisible();
  await page.setViewportSize({ width: 1270, height: 760 });
  const roleFits = await page
    .locator(".command-center__agent-route article")
    .first()
    .evaluate((card) => {
      const container = card.closest(".command-center__route");
      if (!container) return false;
      const cardBox = card.getBoundingClientRect();
      const containerBox = container.getBoundingClientRect();
      return (
        cardBox.top >= containerBox.top &&
        cardBox.bottom <= containerBox.bottom
      );
    });
  expect(roleFits, "the agent role card must not be clipped").toBe(true);
  const decisionHasFold = await page
    .locator(".command-center__decision")
    .evaluate((card) => getComputedStyle(card).clipPath !== "none");
  expect(
    decisionHasFold,
    "the Ledger decision card must carry the approved fold geometry",
  ).toBe(true);
  await page.setViewportSize({ width: 1440, height: 862 });
  await capture(page, "alfred-gallery-approval.png");
});
