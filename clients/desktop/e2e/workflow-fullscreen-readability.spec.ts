import { expect, test } from "playwright/test";

import { assertAlfredApiComplete, installAlfredApi } from "./alfred-api.fixture";

test("desktop full-screen workflow keeps every agent at the readable scale", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await installAlfredApi(page, "workflow");
  await page.goto("/");

  await page.getByRole("button", { name: "Agents" }).click();
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
            width: box.width,
          };
        }),
      );
      if (!canvasBox) return false;
      return nodeBoxes.every(
        (box) =>
          box.width >= 162 &&
          box.left >= canvasBox.x &&
          box.right <= canvasBox.x + canvasBox.width &&
          box.top >= canvasBox.y &&
          box.bottom <= canvasBox.y + canvasBox.height,
      );
    })
    .toBe(true);

  assertAlfredApiComplete(page);
});
