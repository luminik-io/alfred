import { expect, test, type Locator, type Page } from "playwright/test";

import {
  assertAlfredApiComplete,
  installAlfredApi,
} from "./alfred-api.fixture";

async function openPrimaryView(
  page: Page,
  label: "Inbox" | "Work" | "Code" | "Agents" | "Settings",
): Promise<void> {
  await page.getByRole("button", { name: label, exact: true }).click();
}

async function expectPlainHeader(header: Locator): Promise<void> {
  await expect(header).toBeVisible();
  await expect(header).toHaveClass(/\balfred-page-hero\b/);
  const style = await header.evaluate((element) => {
    const computed = getComputedStyle(element);
    return {
      borderTopWidth: Number.parseFloat(computed.borderTopWidth),
      backgroundColor: computed.backgroundColor,
      boxShadow: computed.boxShadow,
    };
  });
  expect(style.borderTopWidth).toBe(0);
  expect(style.backgroundColor).toBe("rgba(0, 0, 0, 0)");
  expect(style.boxShadow).toBe("none");
}

test.afterEach(async ({ page }) => {
  assertAlfredApiComplete(page);
});

test("primary screens share the approved plain page hierarchy", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.addInitScript(() => {
    localStorage.setItem("alfred-theme-name", "signal-edge");
    localStorage.setItem("alfred-theme", "light");
  });
  await installAlfredApi(page);
  await page.goto("/");

  await expectPlainHeader(page.locator('[aria-label="Inbox summary"]'));

  await openPrimaryView(page, "Work");
  await expectPlainHeader(page.locator('[aria-label="Work summary"]'));

  await openPrimaryView(page, "Code");
  await expectPlainHeader(
    page.locator('[aria-label="Code intelligence summary"]'),
  );

  await openPrimaryView(page, "Agents");
  await expectPlainHeader(page.locator('[aria-label="Agents summary"]'));

  const rosterRail = page.locator('.agents-deck__rail');
  const rosterRow = page.locator('.agents-deck__row').first();
  await expect(rosterRail).toBeVisible();
  await expect(rosterRow).toBeVisible();
  const rosterWidths = await Promise.all([
    rosterRail.evaluate((element) => element.getBoundingClientRect().width),
    rosterRow.evaluate((element) => element.getBoundingClientRect().width),
  ]);
  expect(rosterWidths[1]).toBeGreaterThan(rosterWidths[0] * 0.9);

  await openPrimaryView(page, "Settings");
  await expectPlainHeader(
    page.locator('[aria-label="Settings summary"]'),
  );
});
