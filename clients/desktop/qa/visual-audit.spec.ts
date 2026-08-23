import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";

import { expect, test, type Page } from "playwright/test";

import {
  assertAlfredApiComplete,
  installAlfredApi,
} from "../e2e/alfred-api.fixture";

const outputDir = resolve(process.cwd(), "test-results/visual-audit");
const viewports = {
  wide: { width: 1600, height: 1000 },
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
    const scrollRegions = await page.locator("[data-alfred-scroll-region]").all();
    for (const region of scrollRegions) {
      if (await region.isVisible()) {
        expect(
          await region.evaluate((element) => element.scrollTop),
          `${name} did not start at the top`,
        ).toBe(0);
      }
    }
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

async function assertLearningsCompositionContract(page: Page): Promise<void> {
  const lessonList = page.locator(".active-lesson-list").first();
  const advancedPanel = page.locator(".advanced-panel");
  await expect(lessonList).toBeVisible();
  await expect(advancedPanel).toBeVisible();

  const [lessonListBox, advancedPanelBox] = await Promise.all([
    lessonList.boundingBox(),
    advancedPanel.boundingBox(),
  ]);
  expect(lessonListBox).not.toBeNull();
  expect(advancedPanelBox).not.toBeNull();

  expect(
    Math.abs(
      (lessonListBox?.x ?? 0) + (lessonListBox?.width ?? 0) -
        ((advancedPanelBox?.x ?? 0) + (advancedPanelBox?.width ?? 0)),
    ),
    "the lesson list and technical disclosure must share one content edge",
  ).toBeLessThanOrEqual(1);
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

async function assertCodeIntelligenceContract(
  page: Page,
  viewport: ViewportName,
  theme: (typeof appearances)[number]["theme"],
): Promise<void> {
  const root = page.getByLabel("Code intelligence", { exact: true });
  const query = root.locator(".code-intelligence__query");
  const action = root.getByRole("button", { name: "Analyze impact" });
  const [rootBox, queryBox, actionBox] = await Promise.all([
    root.boundingBox(),
    query.boundingBox(),
    action.boundingBox(),
  ]);
  expect(rootBox, "Code Intelligence must be visible").not.toBeNull();
  expect(queryBox, "the file query must be visible").not.toBeNull();
  expect(actionBox, "the impact action must be visible").not.toBeNull();
  if (!rootBox || !queryBox || !actionBox) return;

  expect(queryBox.x).toBeGreaterThanOrEqual(rootBox.x);
  expect(queryBox.x + queryBox.width).toBeLessThanOrEqual(
    rootBox.x + rootBox.width + 0.5,
  );
  expect(actionBox.height, "the primary action must be easy to target").toBeGreaterThanOrEqual(
    36,
  );
  if (viewport === "mobile") {
    expect(
      actionBox.width,
      "the mobile primary action must fill the query surface",
    ).toBeGreaterThanOrEqual(queryBox.width - 34);
  }

  const radius = await query.evaluate((element) =>
    Number.parseFloat(getComputedStyle(element).borderRadius),
  );
  if (theme === "category-standard") expect(radius).toBe(0);
  if (theme === "linked-fold") expect(radius).toBeLessThanOrEqual(4);
  if (theme === "signal-edge") expect(radius).toBeGreaterThanOrEqual(8);
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
        test.setTimeout(60_000);
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
        await recentChats.getByRole("button", { name: "Close" }).click();
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
          .getByRole("button", {
            name: /Record engine settings for every run/,
          })
          .click();
        const dockWorkInspector = viewport.width >= 1600;
        if (dockWorkInspector) {
          const inspector = page.getByRole("complementary", {
            name: "Work item inspector",
          });
          await expect(inspector).toBeVisible();
          await expect(
            page.getByRole("dialog", { name: "Work item" }),
          ).toHaveCount(0);
          const [inspectorBox, signatureBox] = await Promise.all([
            inspector.boundingBox(),
            inspector.getByText("Signature", { exact: true }).boundingBox(),
          ]);
          expect(inspectorBox).not.toBeNull();
          expect(signatureBox).not.toBeNull();
          if (inspectorBox && signatureBox) {
            expect(
              signatureBox.y + signatureBox.height,
              "the docked action area must not cover the evidence summary",
            ).toBeLessThanOrEqual(inspectorBox.y + inspectorBox.height);
          }
          await inspector.evaluate((element) => {
            element.scrollTop = element.scrollHeight;
          });
          const actionBox = await inspector
            .getByRole("button", { name: "Open on GitHub" })
            .boundingBox();
          expect(actionBox).not.toBeNull();
          if (inspectorBox && actionBox) {
            expect(actionBox.y).toBeGreaterThanOrEqual(inspectorBox.y);
            expect(actionBox.y + actionBox.height).toBeLessThanOrEqual(
              inspectorBox.y + inspectorBox.height,
            );
          }
          await inspector.evaluate((element) => {
            element.scrollTop = 0;
          });
        } else {
          await expect(
            page.getByRole("dialog", { name: "Work item" }),
          ).toBeVisible();
        }
        await capture(page, viewportName, "work-inspector");
        await page
          .getByRole("button", {
            name: dockWorkInspector ? "Close inspector" : "Close",
          })
          .click();

        await openPrimaryView(page, viewportName, "Code");
        await expect(
          page.getByRole("heading", { name: "Code intelligence" }),
        ).toBeVisible();
        await assertCodeIntelligenceContract(
          page,
          viewportName,
          appearance.theme,
        );
        await capture(page, viewportName, "code");
        await page.getByLabel("File path").fill("src/server/routes.ts");
        await page.getByRole("button", { name: "Analyze impact" }).click();
        await expect(
          page.getByRole("region", { name: "Impact analysis" }),
        ).toBeVisible();
        await capture(page, viewportName, "code-impact");

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

        await page.getByRole("tab", { name: "Latest run" }).click();
        await expect(
          page.getByRole("region", { name: "Run evidence" }),
          "the fixture-backed visual audit must exercise the saved run evidence record",
        ).toBeVisible();
        await expect(page.getByText("Imported session ready")).toBeVisible();
        await capture(page, viewportName, "agents-latest-run");

        await page.getByRole("tab", { name: "Learnings" }).click();
        await expect(
          page.getByText("Keep fixture data separate from operator data."),
          "the fixture-backed visual audit must exercise a real memory card",
        ).toBeVisible();
        await assertLearningsCompositionContract(page);
        await capture(page, viewportName, "agents-learnings");

        await openPrimaryView(page, viewportName, "Settings");
        await expect(
          page.getByRole("heading", { name: "Settings" }),
        ).toBeVisible();
        await capture(page, viewportName, "settings-runtime");

        await page.getByRole("tab", { name: "Tools" }).click();
        await expect(page.getByRole("region", { name: "Included" })).toBeVisible();
        await capture(page, viewportName, "settings-tools");

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

const workflowViewports = {
  desktop: viewports.desktop,
  mobile: viewports.mobile,
} as const;

for (const appearance of appearances) {
  for (const [viewportName, viewport] of Object.entries(workflowViewports) as Array<
    [keyof typeof workflowViewports, (typeof workflowViewports)[keyof typeof workflowViewports]]
  >) {
    test.describe(`${appearance.theme}-${appearance.mode}-${viewportName}-workflow`, () => {
      test.beforeEach(async ({ page }) => {
        await mkdir(outputDir, { recursive: true });
        await page.setViewportSize(viewport);
        await page.addInitScript(({ theme, mode }) => {
          localStorage.setItem("alfred-theme-name", theme);
          localStorage.setItem("alfred-theme", mode);
          localStorage.removeItem("alfred.roster.view.v1");
        }, appearance);
        await installAlfredApi(page, "workflow");
        await page.goto("/");
      });

      test.afterEach(async ({ page }) => {
        assertAlfredApiComplete(page);
      });

      test("workflow canvas and full-screen state", async ({ page }) => {
        await openPrimaryView(page, viewportName, "Agents");
        await expect(
          page.getByRole("button", { name: "Workflow view" }),
        ).toHaveAttribute("aria-pressed", "true");
        await expect(
          page.locator(".wf-node__name", { hasText: "Senior developer" }),
        ).toBeVisible();
        await assertWorkflowContract(page, viewportName);
        await capture(page, viewportName, "agents-workflow");

        const maximize = page.getByRole("button", { name: "Maximize workflow" });
        const maximizeBox = await maximize.boundingBox();
        expect(maximizeBox, "the workflow full-screen action must be visible").not.toBeNull();
        if (viewportName === "mobile" && maximizeBox) {
          expect(maximizeBox.width).toBeGreaterThanOrEqual(44);
          expect(maximizeBox.height).toBeGreaterThanOrEqual(44);
        }
        await maximize.click();

        const dialog = page.getByRole("dialog", { name: "Agent workflow graph" });
        await expect(dialog).toBeVisible();
        await expect(dialog).toHaveAttribute("aria-modal", "true");
        expect(await page.evaluate(() => document.body.style.overflow)).toBe("hidden");
        const dialogBox = await dialog.boundingBox();
        expect(dialogBox?.width).toBe(viewport.width);
        expect(dialogBox?.height).toBe(viewport.height);
        await capture(page, viewportName, "agents-workflow-fullscreen");

        await page.keyboard.press("Escape");
        await expect(dialog).toBeHidden();
        await expect(maximize).toBeFocused();
      });
    });
  }
}

const stateViewports = {
  desktop: viewports.desktop,
  mobile: viewports.mobile,
} as const;

for (const appearance of appearances) {
  for (const [viewportName, viewport] of Object.entries(stateViewports) as Array<
    [keyof typeof stateViewports, (typeof stateViewports)[keyof typeof stateViewports]]
  >) {
    test.describe(`${appearance.theme}-${appearance.mode}-${viewportName}-empty`, () => {
      test.beforeEach(async ({ page }) => {
        await mkdir(outputDir, { recursive: true });
        await page.setViewportSize(viewport);
        await page.addInitScript(({ theme, mode }) => {
          localStorage.setItem("alfred-theme-name", theme);
          localStorage.setItem("alfred-theme", mode);
        }, appearance);
        await installAlfredApi(page, "empty");
        await page.goto("/");
      });

      test.afterEach(async ({ page }) => {
        assertAlfredApiComplete(page);
      });

      test("empty primary screens", async ({ page }) => {
        await expect(
          page.getByRole("heading", { name: "Alfred is clear" }),
        ).toBeVisible();
        await capture(page, viewportName, "inbox-empty");

        await openPrimaryView(page, viewportName, "Work");
        await expect(page.getByText("Nothing in the pipeline yet.")).toBeVisible();
        await capture(page, viewportName, "work-empty");

        await openPrimaryView(page, viewportName, "Code");
        await expect(
          page.getByRole("heading", { name: "No repositories indexed yet" }),
        ).toBeVisible();
        await capture(page, viewportName, "code-empty");

        await openPrimaryView(page, viewportName, "Agents");
        await expect(page.getByText("No agent roles yet.")).toBeVisible();
        await capture(page, viewportName, "agents-empty");

        await page.getByRole("tab", { name: "Activity" }).click();
        await expect(page.getByText("No activity yet.")).toBeVisible();
        await capture(page, viewportName, "activity-empty");

        await page.getByRole("tab", { name: "Learnings" }).click();
        await expect(
          page.getByText("Alfred has not remembered anything yet."),
        ).toBeVisible();
        await capture(page, viewportName, "learnings-empty");

        await openPrimaryView(page, viewportName, "Settings");
        await page.getByRole("tab", { name: "Collaborators" }).click();
        await expect(page.getByText("No collaborators yet.")).toBeVisible();
        await capture(page, viewportName, "collaborators-empty");
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
