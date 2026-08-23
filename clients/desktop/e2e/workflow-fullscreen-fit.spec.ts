import { expect, test } from "playwright/test";

import { assertAlfredApiComplete, installAlfredApi } from "./alfred-api.fixture";

test("full-screen workflow frames every agent on desktop", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await installAlfredApi(page, "workflow");
  await page.goto("/");

  await page.getByRole("button", { name: "Agents" }).click();
  await expect(
    page.getByRole("button", { name: "Workflow view" }),
  ).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Maximize workflow" }).click();

  const dialog = page.getByRole("dialog", { name: "Agent workflow graph" });
  const canvas = dialog.locator(".workflow-graph__canvas");
  const nodes = dialog.locator(".wf-node");
  await expect(nodes).toHaveCount(6);
  await expect
    .poll(async () => {
      const canvasBox = await canvas.boundingBox();
      const nodeBoxes = await nodes.evaluateAll((elements) =>
        elements.map((element) => {
          const box = element.getBoundingClientRect();
          return {
            bottom: box.bottom,
            left: box.left,
            right: box.right,
            top: box.top,
          };
        }),
      );
      if (!canvasBox) return false;
      return nodeBoxes.every(
        (box) =>
          box.left >= canvasBox.x &&
          box.right <= canvasBox.x + canvasBox.width &&
          box.top >= canvasBox.y &&
          box.bottom <= canvasBox.y + canvasBox.height,
      );
    })
    .toBe(true);

  assertAlfredApiComplete(page);
});

test("full-screen workflow keeps the first agent readable on mobile", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installAlfredApi(page, "workflow");
  await page.goto("/");

  await page.getByRole("button", { name: "Toggle Sidebar" }).click();
  await page.getByRole("button", { name: "Agents" }).click();
  await page.getByRole("button", { name: "Maximize workflow" }).click();

  const dialog = page.getByRole("dialog", { name: "Agent workflow graph" });
  const canvas = dialog.locator(".workflow-graph__canvas");
  const triage = dialog.locator(".wf-node").filter({ hasText: "Triage" }).first();
  await expect
    .poll(async () => {
      const canvasBox = await canvas.boundingBox();
      const triageBox = await triage.boundingBox();
      if (!canvasBox || !triageBox) return false;
      return (
        triageBox.width >= 150 &&
        triageBox.x >= canvasBox.x &&
        triageBox.x + triageBox.width <= canvasBox.x + canvasBox.width
      );
    })
    .toBe(true);

  assertAlfredApiComplete(page);
});
