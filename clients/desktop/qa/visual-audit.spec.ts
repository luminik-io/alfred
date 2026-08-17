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
  short: { width: 1280, height: 720 },
  tablet: { width: 768, height: 900 },
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

async function seedAskHistory(page: Page): Promise<void> {
  await page.evaluate(() => {
    const now = Date.now();
    localStorage.setItem(
      "alfred.ask.history.v2",
      JSON.stringify({
        version: 2,
        conversations: [
          {
            id: "visual-current",
            title: "Review the release checklist",
            updatedAt: now,
            turns: [
              {
                kind: "message",
                role: "user",
                content: "Review the release checklist.",
              },
              {
                kind: "message",
                role: "assistant",
                content: "I found two checks that still need evidence.",
              },
            ],
          },
          {
            id: "visual-saved",
            title: "Check the Desktop layout",
            updatedAt: now - 60_000,
            turns: [
              {
                kind: "message",
                role: "user",
                content: "Check the Desktop layout.",
              },
              {
                kind: "message",
                role: "assistant",
                content: "The compact layout is ready for review.",
              },
            ],
          },
        ],
      }),
    );
  });
}

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

async function captureTakeover(page: Page, name: string): Promise<void> {
  expect(
    await page.evaluate(() => window.scrollY),
    `${name} moved the onboarding viewport`,
  ).toBe(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
    `${name} overflowed the onboarding viewport`,
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
  if (viewport !== "mobile") {
    const sidebar = page.locator('[data-slot="sidebar"]').first();
    const width = await sidebar.evaluate(
      (element) => element.getBoundingClientRect().width,
    );
    expect(
      width,
      "desktop sidebar must use the approved compact rail",
    ).toBeLessThanOrEqual(208);
    const labels = await sidebar
      .locator('[data-sidebar="menu-button"] > span')
      .allTextContents();
    expect(
      labels.slice(0, 3),
      "the rail must lead from decisions to autonomous work, then new requests",
    ).toEqual(["Inbox", "Work", "Ask"]);
  }
}

async function assertWorkflowContract(
  page: Page,
  viewport: ViewportName,
): Promise<void> {
  if (viewport !== "mobile") return;
  if (
    (await page
      .getByRole("button", { name: "Workflow view" })
      .getAttribute("aria-pressed")) !== "true"
  ) {
    return;
  }

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

        await seedAskHistory(page);
        await page.reload();
        await openPrimaryView(page, viewportName, "Ask");
        await expect(
          page.getByText("I found two checks that still need evidence."),
        ).toBeVisible();
        await page.getByRole("button", { name: "Recent" }).click();
        const recentChats = page.getByRole("dialog", {
          name: "Recent chats",
        });
        await expect(recentChats).toBeVisible();
        await recentChats
          .getByRole("button", {
            name: "Delete chat: Check the Desktop layout",
          })
          .click();
        const deleteChat = page.getByRole("button", { name: "Delete chat" });
        await expect(page.getByRole("alertdialog")).toBeVisible();
        await expect(deleteChat).toBeFocused();
        await capture(page, viewportName, "ask-delete-chat");
        await page.keyboard.press("Escape");
        await expect(page.getByRole("alertdialog")).toBeHidden();
        await page.keyboard.press("Escape");
        await expect(recentChats).toBeHidden();

        await openPrimaryView(page, viewportName, "Work");
        await expect(
          page.getByRole("heading", { name: "Work", exact: true }),
        ).toBeVisible();
        if (appearance.theme !== "category-standard") {
          const cardTitleSize = await page
            .locator(".alfred-card__outcome")
            .first()
            .evaluate((element) =>
              Number.parseFloat(getComputedStyle(element).fontSize),
            );
          expect(
            cardTitleSize,
            `${appearance.theme} must keep the more spacious approved card typography`,
          ).toBeGreaterThanOrEqual(14);
        }
        await capture(page, viewportName, "work");

        await page
          .getByRole("button", { name: /Replace legacy appearance presets/ })
          .click();
        await expect(
          viewportName === "desktop"
            ? page.getByRole("complementary", { name: "Work item inspector" })
            : page.getByRole("dialog", { name: "Work item" }),
        ).toBeVisible();
        if (viewportName === "desktop") {
          const inspector = await page
            .getByRole("complementary", { name: "Work item inspector" })
            .boundingBox();
          expect(
            inspector?.width,
            "the desktop evidence inspector must remain readable beside the board",
          ).toBeGreaterThanOrEqual(360);

          const lanes = await page.locator(".alfred-pipeline__column").all();
          for (const lane of lanes) {
            const box = await lane.boundingBox();
            expect(
              box?.width,
              "each lifecycle lane must preserve a readable card width with the inspector open",
            ).toBeGreaterThanOrEqual(190);
          }
        }
        await capture(page, viewportName, "work-inspector");

        if (viewportName !== "desktop") {
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
        await expect(
          page.getByRole("button", { name: "List view" }),
          "a one-agent roster must use the compact list instead of an empty workflow canvas",
        ).toHaveAttribute("aria-pressed", "true");
        await assertWorkflowContract(page, viewportName);
        await capture(page, viewportName, "agents-roster");

        await page.getByRole("tab", { name: "Activity" }).click();
        await expect(
          page.getByText("Completed the Desktop visual contract"),
          "the fixture-backed visual audit must exercise a real activity row",
        ).toBeVisible();
        await capture(page, viewportName, "agents-activity");

        await page.getByRole("tab", { name: "Learnings" }).click();
        await expect(
          page.getByText("Keep fixture data separate from operator data."),
          "the fixture-backed visual audit must exercise a real memory card",
        ).toBeVisible();
        await capture(page, viewportName, "agents-learnings");

        await openPrimaryView(page, viewportName, "Settings");
        await expect(
          page.getByRole("heading", { name: "Settings" }),
        ).toBeVisible();
        await capture(page, viewportName, "settings-runtime");

        await page.getByRole("tab", { name: "Appearance" }).click();
        await capture(page, viewportName, "settings-appearance");

        await page.getByRole("tab", { name: "Collaborators" }).click();
        await capture(page, viewportName, "settings-collaborators");
        await page.getByRole("button", { name: "Remove UTEAM12345" }).click();
        const removeCollaborator = page.getByRole("button", {
          name: "Remove collaborator",
        });
        await expect(page.getByRole("alertdialog")).toBeVisible();
        await expect(removeCollaborator).toBeFocused();
        await capture(page, viewportName, "settings-collaborator-remove");
        await page.keyboard.press("Escape");
        await expect(page.getByRole("alertdialog")).toBeHidden();

        await page.getByRole("tab", { name: "Diagnostics" }).click();
        await capture(page, viewportName, "settings-diagnostics");
      });
    });
  }
}

const onboardingSteps = [
  { key: "engine", label: "Tools", heading: "Let's find your coding tools." },
  { key: "github", label: "GitHub", heading: "Connect GitHub." },
  { key: "repos", label: "Repositories", heading: "Where should Alfred work?" },
  { key: "batteries", label: "Tools included", heading: "Your tools are included." },
  { key: "team", label: "Team", heading: "Name your team." },
  { key: "slack", label: "Slack", heading: "Want approvals in Slack?" },
  { key: "request", label: "First request", heading: "Give Alfred its first job." },
] as const;

for (const appearance of appearances) {
  for (const [viewportName, viewport] of Object.entries(viewports) as Array<
    [ViewportName, (typeof viewports)[ViewportName]]
  >) {
    test.describe(`${appearance.theme}-${appearance.mode}-${viewportName}-onboarding`, () => {
      test.beforeEach(async ({ page }) => {
        await mkdir(outputDir, { recursive: true });
        await page.setViewportSize(viewport);
        await page.addInitScript(({ theme, mode }) => {
          localStorage.setItem("alfred-theme-name", theme);
          localStorage.setItem("alfred-theme", mode);
        }, appearance);
        await installAlfredApi(page, "onboarding");
        await page.goto("/");
      });

      test.afterEach(async ({ page }) => {
        assertAlfredApiComplete(page);
      });

      test("setup journey", async ({ page }) => {
        const stepper = page.getByRole("navigation", {
          name: "Onboarding progress",
        });
        await expect(
          page.getByRole("heading", { name: "Let's get you set up." }),
        ).toBeVisible();
        if (viewportName === "mobile") {
          await expect(
            stepper.locator(".alfred-stepper__mobile-label"),
          ).toHaveText("Welcome");
        }
        await captureTakeover(page, "welcome");

        await page.getByRole("button", { name: "Get started" }).click();

        for (const step of onboardingSteps) {
          if (step.key !== "engine") {
            await stepper.getByRole("button", { name: step.label }).click();
          }
          await expect(
            page.getByRole("heading", { name: step.heading }),
          ).toBeVisible();
          if (viewportName === "mobile") {
            await expect(
              stepper.locator(".alfred-stepper__mobile-label"),
            ).toHaveText(step.label);
          }
          await captureTakeover(page, step.key);
        }
      });
    });
  }
}
