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

async function expectFramedHeader(header: Locator): Promise<void> {
  await expect(header).toBeVisible();
  await expect(header).toHaveClass(/\balfred-page-hero\b/);
  const style = await header.evaluate((element) => {
    const computed = getComputedStyle(element);
    return {
      borderTopWidth: Number.parseFloat(computed.borderTopWidth),
      backgroundColor: computed.backgroundColor,
    };
  });
  expect(style.borderTopWidth).toBeGreaterThanOrEqual(1);
  expect(style.backgroundColor).not.toBe("rgba(0, 0, 0, 0)");
}

test.afterEach(async ({ page }) => {
  assertAlfredApiComplete(page);
});

test("primary screens share the approved framed page hierarchy", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.addInitScript(() => {
    localStorage.setItem("alfred-theme-name", "signal-edge");
    localStorage.setItem("alfred-theme", "light");
  });
  await installAlfredApi(page);
  await page.goto("/");

  await expectFramedHeader(page.locator('[aria-label="Inbox summary"]'));

  await openPrimaryView(page, "Work");
  await expectFramedHeader(page.locator('[aria-label="Work summary"]'));

  await openPrimaryView(page, "Code");
  await expectFramedHeader(
    page.locator('[aria-label="Code intelligence summary"]'),
  );

  await openPrimaryView(page, "Agents");
  await expectFramedHeader(page.locator('[aria-label="Agents summary"]'));

  await openPrimaryView(page, "Settings");
  await expectFramedHeader(
    page.locator('[aria-label="Settings summary"]'),
  );
});
