import { describe, expect, it } from "vitest";

import { agentForShipped } from "./chips";
import type { ShippedCard } from "../types";

function card(overrides: Partial<ShippedCard> = {}): ShippedCard {
  return {
    repo: "your-org/api",
    number: 12,
    title: "Ready issue",
    url: "https://example.com/issues/12",
    author: "lucius",
    kind: "issue",
    timestamp: "2026-06-02T11:00:00Z",
    age_days: 0,
    is_draft: false,
    labels: [],
    ...overrides,
  };
}

// agentForShipped must return the canonical CODENAME SLUG (not a themed display
// name), so the Work board resolves the visible name through the active roster
// theme the same way the Roster page does. The Batman-cast names still appear in
// author/evidence tokens, so they are matched but mapped to the canonical slug.
describe("agentForShipped", () => {
  it("returns the codename slug detected from the author, not a display name", () => {
    expect(agentForShipped(card({ author: "lucius" }))).toBe("senior-dev");
    expect(agentForShipped(card({ author: "batman" }))).toBe("architect");
  });

  it("detects each known fleet codename from labels or evidence", () => {
    expect(
      agentForShipped(card({ author: "", labels: ["agent:large-feature"] })),
    ).toBe("architect");
    expect(
      agentForShipped(card({ author: "", labels: ["agent:implement"] })),
    ).toBe("senior-dev");
    expect(agentForShipped(card({ author: "nightwing-bot" }))).toBe("fixer");
    expect(agentForShipped(card({ author: "damian" }))).toBe("spec-planner");
    expect(agentForShipped(card({ author: "bane" }))).toBe("test-engineer");
    expect(
      agentForShipped(card({ author: "", agent_evidence: ["rasalghul"] })),
    ).toBe("reviewer");
  });

  it("returns null when no known codename is present", () => {
    expect(agentForShipped(card({ author: "someone-else", labels: [] }))).toBeNull();
  });
});
