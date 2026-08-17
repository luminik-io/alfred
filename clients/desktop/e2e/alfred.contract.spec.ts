import { expect, test } from "playwright/test";

import {
  assertAlfredApiComplete,
  CONTRACT_TOKEN,
  installAlfredApi,
} from "./alfred-api.fixture";

test.afterEach(async ({ page }) => {
  assertAlfredApiComplete(page);
});

test("saved dark appearances apply before the client bundle starts", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("alfred-theme-name", "linked-fold");
    localStorage.setItem("alfred-theme", "dark");
  });
  await page.route("**/assets/index-*.js", (route) => route.abort());

  await page.goto("/");

  await expect(page.locator("html")).toHaveAttribute("data-theme", "linked-fold");
  await expect(page.locator("html")).toHaveClass(/\bdark\b/);
  await expect(page.getByText("Starting Alfred")).toBeVisible();
});

test("fresh onboarding owns the window before application navigation", async ({ page }) => {
  await installAlfredApi(page, "onboarding");

  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Let's get you set up." })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Set up Alfred" })).toHaveClass(/sr-only/);
  await expect(page.locator('[data-slot="sidebar"]')).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Inbox" })).toHaveCount(0);
});

test("built Ask paints a live token before the authenticated stream settles", async ({ page }) => {
  const api = await installAlfredApi(page);

  await page.goto("/");
  await page.getByRole("button", { name: "Ask" }).click();
  await page.getByPlaceholder("Ask a question, or describe a change you want made.").fill(
    "Add browser coverage for the desktop protocol",
  );
  await page.getByRole("button", { name: "Send message" }).click();

  await expect(page.getByText("I found the relevant desktop protocol.", { exact: false })).toBeVisible();
  await expect(page.getByText("What outcome should the test prove?", { exact: false })).toHaveCount(0);
  await api.releaseStream();
  await expect(page.getByText("What outcome should the test prove?", { exact: false })).toBeVisible();
  const request = api.find("POST", "/api/compose/converse/stream");
  expect(request?.headers["x-alfred-token"]).toBe(CONTRACT_TOKEN);
  expect(request?.body).toMatchObject({
    context_repos: ["example/workspace"],
  });
});

test("approving a plan sends an authenticated mutation and refreshes the queue", async ({ page }) => {
  const api = await installAlfredApi(page);

  await page.goto("/");
  await expect(
    page.getByText("Make the memory benchmark use the shipped provider chain"),
  ).toBeVisible();
  await page.getByRole("button", { name: "Approve" }).click();

  await expect(
    page.getByText("Make the memory benchmark use the shipped provider chain"),
  ).toHaveCount(0);
  const request = api.find("POST", "/api/plans/42-plan/decision");
  expect(request?.headers["x-alfred-token"]).toBe(CONTRACT_TOKEN);
  expect(request?.body).toEqual({ decision: "approve" });
});

test("compact Work windows open the inspector as a sheet", async ({ page }) => {
  await installAlfredApi(page);
  await page.setViewportSize({ width: 900, height: 800 });

  await page.goto("/");
  await page.getByRole("button", { name: "Work", exact: true }).click();
  await page
    .getByRole("button", { name: /Record engine settings for every run/ })
    .click();

  await expect(page.getByRole("dialog", { name: "Work item" })).toBeVisible();
  await expect(page.getByRole("complementary", { name: "Work item inspector" })).toHaveCount(0);
});

test("standard desktop Work windows keep the inspector in a sheet", async ({ page }) => {
  await installAlfredApi(page);
  await page.setViewportSize({ width: 1440, height: 900 });

  await page.goto("/");
  await page.getByRole("button", { name: "Work", exact: true }).click();
  await page
    .getByRole("button", { name: /Record engine settings for every run/ })
    .click();

  await expect(page.getByRole("dialog", { name: "Work item" })).toBeVisible();
  await expect(
    page.getByRole("complementary", { name: "Work item inspector" }),
  ).toHaveCount(0);
});

test("wide Work windows dock evidence without narrowing lifecycle lanes", async ({ page }) => {
  await installAlfredApi(page);
  await page.setViewportSize({ width: 1680, height: 900 });

  await page.goto("/");
  await page.getByRole("button", { name: "Work", exact: true }).click();
  await page
    .getByRole("button", { name: /Record engine settings for every run/ })
    .click();

  const inspector = page.getByRole("complementary", {
    name: "Work item inspector",
  });
  await expect(inspector).toBeVisible();
  await expect(page.getByRole("dialog", { name: "Work item" })).toHaveCount(0);

  const lanes = await page.locator(".alfred-pipeline__column").all();
  for (const lane of lanes) {
    const box = await lane.boundingBox();
    expect(box?.width).toBeGreaterThanOrEqual(230);
  }
});

test("narrow Settings keeps every section label readable", async ({ page }) => {
  await installAlfredApi(page);
  await page.setViewportSize({ width: 390, height: 844 });

  await page.goto("/?tab=settings");
  const tablist = page.getByRole("tablist", { name: "Settings sections" });
  await expect(tablist).toBeVisible();
  const tabs = tablist.getByRole("tab");
  await expect(tabs).toHaveCount(4);

  const metrics = await tabs.evaluateAll((items) =>
    items.map((item) => {
      const label = item.querySelector("span");
      return {
        label: label?.textContent?.trim(),
        labelWidth: label?.clientWidth ?? 0,
        labelScrollWidth: label?.scrollWidth ?? 0,
        tabHeight: item.getBoundingClientRect().height,
      };
    }),
  );

  expect(metrics.map(({ label }) => label)).toEqual([
    "Runtime",
    "Appearance",
    "Collaborators",
    "Diagnostics",
  ]);
  expect(metrics.every(({ labelWidth, labelScrollWidth }) => labelWidth >= labelScrollWidth)).toBe(
    true,
  );
  expect(metrics.every(({ tabHeight }) => tabHeight >= 38)).toBe(true);
});

test("primary navigation loads code, models, settings, and returns to Inbox", async ({ page }) => {
  const api = await installAlfredApi(page);

  await page.goto("/");

  await page.getByRole("button", { name: "Code" }).click();
  await expect(page.getByRole("heading", { name: "Code intelligence" })).toBeVisible();
  const codeSummary = page.getByRole("region", { name: "example/workspace index summary" });
  await expect(codeSummary.getByText("128", { exact: true })).toBeVisible();
  await page.getByLabel("File path").fill("src/server/routes.ts");
  await page.getByRole("button", { name: "Analyze impact" }).click();
  await expect(page.getByRole("region", { name: "Impact analysis" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "src/server/routes.ts" }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Agents" }).click();
  await expect(page.getByRole("heading", { name: "Agents" })).toBeVisible();
  await page.getByRole("button", { name: "List view" }).click();
  await page.getByRole("button", { name: "Select Batman, Architect" }).click();
  await expect(page.getByRole("heading", { name: "Models" })).toBeVisible();

  await page.getByRole("button", { name: "Settings" }).click();
  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();

  await page.getByRole("button", { name: "Inbox", exact: true }).click();
  await expect(page.getByLabel("Inbox", { exact: true })).toBeVisible();

  expect(api.find("GET", "/api/code-intelligence")).toBeDefined();
  expect(
    api.find(
      "GET",
      "/api/code-intelligence?repo=example%2Fworkspace&path=src%2Fserver%2Froutes.ts",
    ),
  ).toBeDefined();
  expect(api.find("GET", "/api/agent-models")).toBeDefined();
});

test("full-screen workflow contains focus and restores the page on exit", async ({ page }) => {
  await installAlfredApi(page, "workflow");
  await page.goto("/");

  await page.getByRole("button", { name: "Agents" }).click();
  await expect(page.getByRole("button", { name: "Workflow view" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );

  const maximize = page.getByRole("button", { name: "Maximize workflow" });
  await maximize.focus();
  await maximize.click();

  const dialog = page.getByRole("dialog", { name: "Agent workflow graph" });
  await expect(dialog).toBeVisible();
  await expect(dialog).toHaveAttribute("aria-modal", "true");
  await expect(page.getByRole("button", { name: "Exit full screen" })).toBeFocused();
  expect(await page.evaluate(() => document.body.style.overflow)).toBe("hidden");

  await page.keyboard.press("Shift+Tab");
  expect(
    await page.evaluate(() => document.activeElement?.getAttribute("aria-label")),
  ).toBe("React Flow attribution");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Exit full screen" })).toBeFocused();

  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(maximize).toBeFocused();
  expect(await page.evaluate(() => document.body.style.overflow)).not.toBe("hidden");
});
