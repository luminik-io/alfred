import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { Tabs, type TabItem } from "./Tabs";

// Mirrors how App.tsx wires the Agents view tabs: the Tabs component renders the
// tablist, and the page renders the panels as SIBLINGS outside the Radix root.
// This is the exact shape that left every trigger at tabindex="-1" and broke
// arrow-key navigation, so the harness reproduces the real integration.
function AgentsTabsHarness({ initial = "roster" as const }: { initial?: "roster" | "activity" | "learnings" }) {
  const tabs: TabItem<"roster" | "activity" | "learnings">[] = [
    { key: "roster", label: "Roster" },
    { key: "activity", label: "Activity" },
    { key: "learnings", label: "Learnings" },
  ];
  const [active, setActive] = useState<"roster" | "activity" | "learnings">(initial);
  return (
    <div>
      <Tabs tabs={tabs} active={active} onChange={setActive} idBase="agents" ariaLabel="Agent sections" />
      {active === "roster" ? <div>Roster panel</div> : null}
      {active === "activity" ? <div>Activity panel</div> : null}
      {active === "learnings" ? <div>Learnings panel</div> : null}
    </div>
  );
}

describe("Tabs (WAI-ARIA keyboard navigation)", () => {
  it("keeps the active tab in the tab order (roving tabindex)", () => {
    render(<AgentsTabsHarness />);
    expect(screen.getByRole("tab", { name: "Roster" })).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("tab", { name: "Activity" })).toHaveAttribute("tabindex", "-1");
    expect(screen.getByRole("tab", { name: "Learnings" })).toHaveAttribute("tabindex", "-1");
  });

  it("ArrowRight moves selection and focus to the next tab", async () => {
    const user = userEvent.setup();
    render(<AgentsTabsHarness />);
    screen.getByRole("tab", { name: "Roster" }).focus();
    await user.keyboard("{ArrowRight}");
    const activity = screen.getByRole("tab", { name: "Activity" });
    expect(activity).toHaveAttribute("aria-selected", "true");
    expect(activity).toHaveFocus();
    expect(activity).toHaveAttribute("tabindex", "0");
  });

  it("ArrowLeft from the first tab wraps to the last", async () => {
    const user = userEvent.setup();
    render(<AgentsTabsHarness />);
    screen.getByRole("tab", { name: "Roster" }).focus();
    await user.keyboard("{ArrowLeft}");
    const learnings = screen.getByRole("tab", { name: "Learnings" });
    expect(learnings).toHaveAttribute("aria-selected", "true");
    expect(learnings).toHaveFocus();
  });

  it("Home and End jump to the first and last tab", async () => {
    const user = userEvent.setup();
    render(<AgentsTabsHarness initial="activity" />);
    screen.getByRole("tab", { name: "Activity" }).focus();
    await user.keyboard("{End}");
    expect(screen.getByRole("tab", { name: "Learnings" })).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{Home}");
    expect(screen.getByRole("tab", { name: "Roster" })).toHaveAttribute("aria-selected", "true");
  });
});
