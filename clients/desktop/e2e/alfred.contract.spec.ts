import { expect, test } from "playwright/test";

import {
  assertAlfredApiComplete,
  CONTRACT_TOKEN,
  installAlfredApi,
} from "./alfred-api.fixture";

test.afterEach(async ({ page }) => {
  assertAlfredApiComplete(page);
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
  await expect(page.getByText("Add browser protocol coverage")).toBeVisible();
  await page.getByRole("button", { name: "Approve" }).click();

  await expect(page.getByText("Add browser protocol coverage")).toHaveCount(0);
  const request = api.find("POST", "/api/plans/42-plan/decision");
  expect(request?.headers["x-alfred-token"]).toBe(CONTRACT_TOKEN);
  expect(request?.body).toEqual({ decision: "approve" });
});

test("primary navigation loads code, models, settings, and returns to Inbox", async ({ page }) => {
  const api = await installAlfredApi(page);

  await page.goto("/");

  await page.getByRole("button", { name: "Code" }).click();
  await expect(page.getByRole("heading", { name: "Code intelligence" })).toBeVisible();
  const codeSummary = page.getByRole("region", { name: "example/workspace index summary" });
  await expect(codeSummary.getByText("128", { exact: true })).toBeVisible();

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
  expect(api.find("GET", "/api/agent-models")).toBeDefined();
});
