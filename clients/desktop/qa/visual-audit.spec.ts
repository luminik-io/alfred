import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";

import { expect, test, type Page } from "playwright/test";

import {
  assertAlfredApiComplete,
  installAlfredApi,
} from "../e2e/alfred-api.fixture";

const outputDir = resolve(process.cwd(), "test-results/visual-audit");
const viewports = {
  desktop: { width: 1440, height: 900 },
  mobile: { width: 390, height: 844 },
} as const;
const appearances = [
  { theme: "signal-edge", mode: "light" },
  { theme: "signal-edge", mode: "dark" },
  { theme: "category-standard", mode: "light" },
  { theme: "category-standard", mode: "dark" },
  { theme: "linked-fold", mode: "light" },
  { theme: "linked-fold", mode: "dark" },
] as const;

type ViewportName = keyof typeof viewports;

async function openPrimaryView(
  page: Page,
  viewport: ViewportName,
  label: "Inbox" | "Ask" | "Work" | "Code" | "Agents" | "Settings",
): Promise<void> {
  if (viewport === "mobile") {
    await page.getByRole("button", { name: "Toggle Sidebar" }).click();
  }
  await page.getByRole("button", { name: label, exact: true }).click();
}

async function capture(
  page: Page,
  viewport: ViewportName,
  name: string,
): Promise<void> {
  expect(
    await page.evaluate(() => window.scrollY),
    `${name} moved the app viewport`,
  ).toBe(0);
  if (name !== "work-inspector") {
    expect(
      await page
        .locator("[data-alfred-scroll-region]")
        .evaluate((element) => element.scrollTop),
      `${name} did not start at the top`,
    ).toBe(0);
  }
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: resolve(outputDir, `${test.info().titlePath[1]}-${name}.png`),
    animations: "disabled",
  });
}

async function assertShellContract(
  page: Page,
  viewport: ViewportName,
): Promise<void> {
  if (viewport === "desktop") {
    const sidebar = page.locator('[data-slot="sidebar"]').first();
    const width = await sidebar.evaluate(
      (element) => element.getBoundingClientRect().width,
    );
    expect(
      width,
      "desktop sidebar must use the approved compact rail",
    ).toBeLessThanOrEqual(208);
  }
}

async function assertWorkflowContract(
  page: Page,
  viewport: ViewportName,
): Promise<void> {
  if (viewport !== "mobile") return;

  const legend = await page.getByLabel("Workflow legend").boundingBox();
  const canvas = await page.locator(".workflow-graph__canvas").boundingBox();
  const nodes = await page.locator(".react-flow__node").all();
  expect(legend, "workflow legend must be visible").not.toBeNull();
  expect(canvas, "workflow canvas must be visible").not.toBeNull();

  for (const node of nodes) {
    const box = await node.boundingBox();
    if (!legend || !canvas || !box) continue;
    const visibleBox = {
      x: Math.max(box.x, canvas.x),
      y: Math.max(box.y, canvas.y),
      width: Math.max(
        0,
        Math.min(box.x + box.width, canvas.x + canvas.width) -
          Math.max(box.x, canvas.x),
      ),
      height: Math.max(
        0,
        Math.min(box.y + box.height, canvas.y + canvas.height) -
          Math.max(box.y, canvas.y),
      ),
    };
    const overlaps = !(
      visibleBox.width === 0 ||
      visibleBox.height === 0 ||
      legend.x + legend.width <= visibleBox.x ||
      visibleBox.x + visibleBox.width <= legend.x ||
      legend.y + legend.height <= visibleBox.y ||
      visibleBox.y + visibleBox.height <= legend.y
    );
    expect(
      overlaps,
      `mobile workflow legend ${JSON.stringify(legend)} must not cover visible agent node ${JSON.stringify(visibleBox)}`,
    ).toBe(false);
  }
}

for (const appearance of appearances) {
  for (const [viewportName, viewport] of Object.entries(viewports) as Array<
    [ViewportName, (typeof viewports)[ViewportName]]
  >) {
    test.describe(`${appearance.theme}-${appearance.mode}-${viewportName}`, () => {
      test.beforeEach(async ({ page }) => {
        await mkdir(outputDir, { recursive: true });
        await page.setViewportSize(viewport);
        await page.addInitScript(({ theme, mode }) => {
          localStorage.setItem("alfred-theme-name", theme);
          localStorage.setItem("alfred-theme", mode);
        }, appearance);
        await installAlfredApi(page);
        await page.goto("/");
        await expect(page.locator("html")).toHaveAttribute(
          "data-theme",
          appearance.theme,
        );
        await expect(page.locator("html")).toHaveClass(
          new RegExp(`\\b${appearance.mode}\\b`),
        );
        await assertShellContract(page, viewportName);
      });

      test.afterEach(async ({ page }) => {
        assertAlfredApiComplete(page);
      });

      test("primary screens", async ({ page }) => {
        await expect(
          page.getByRole("heading", { name: /Alfred needs/ }),
        ).toBeVisible();
        await capture(page, viewportName, "inbox");

        await openPrimaryView(page, viewportName, "Ask");
        await expect(
          page.getByPlaceholder(
            "Ask a question, or describe a change you want made.",
          ),
        ).toBeVisible();
        await capture(page, viewportName, "ask");

        await openPrimaryView(page, viewportName, "Work");
        await expect(
          page.getByRole("heading", { name: "Work", exact: true }),
        ).toBeVisible();
        await capture(page, viewportName, "work");

        await page
          .getByRole("button", { name: /Replace legacy appearance presets/ })
          .click();
        await expect(
          viewportName === "desktop"
            ? page.getByRole("complementary", { name: "Work item inspector" })
            : page.getByRole("dialog", { name: "Work item" }),
        ).toBeVisible();
        await capture(page, viewportName, "work-inspector");

        if (viewportName === "mobile") {
          await page.getByRole("button", { name: "Close" }).click();
        } else {
          await page.getByRole("button", { name: "Close inspector" }).click();
        }

        await openPrimaryView(page, viewportName, "Code");
        await expect(
          page.getByRole("heading", { name: "Code intelligence" }),
        ).toBeVisible();
        await capture(page, viewportName, "code");

        await openPrimaryView(page, viewportName, "Agents");
        await expect(
          page.getByRole("heading", { name: "Agents" }),
        ).toBeVisible();
        await assertWorkflowContract(page, viewportName);
        await capture(page, viewportName, "agents-roster");

        await page.getByRole("tab", { name: "Activity" }).click();
        await capture(page, viewportName, "agents-activity");

        await page.getByRole("tab", { name: "Learnings" }).click();
        await capture(page, viewportName, "agents-learnings");

        await openPrimaryView(page, viewportName, "Settings");
        await expect(
          page.getByRole("heading", { name: "Settings" }),
        ).toBeVisible();
        await capture(page, viewportName, "settings-runtime");

        for (const tab of [
          "Appearance",
          "Collaborators",
          "Diagnostics",
        ] as const) {
          await page.getByRole("tab", { name: tab }).click();
          await capture(page, viewportName, `settings-${tab.toLowerCase()}`);
        }
      });
    });
  }
}
