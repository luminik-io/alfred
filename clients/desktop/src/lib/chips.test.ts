import { describe, expect, it } from "vitest";

import { agentForShipped } from "./chips";
import type { ShippedCard } from "../types";

function card(overrides: Partial<ShippedCard> = {}): ShippedCard {
  return {
    repo: "your-org/api",
    number: 12,
    title: "Ready issue",
    url: "https://example.com/issues/12",
    author: "senior-dev",
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
// theme the same way the Roster page does.
describe("agentForShipped", () => {
  it("returns the codename slug detected from the author, not a display name", () => {
    expect(agentForShipped(card({ author: "senior-dev" }))).toBe("senior-dev");
    expect(agentForShipped(card({ author: "architect" }))).toBe("architect");
  });

  it("detects each known fleet codename from labels or evidence", () => {
    expect(
      agentForShipped(card({ author: "", labels: ["agent:large-feature"] })),
    ).toBe("architect");
    expect(
      agentForShipped(card({ author: "", labels: ["agent:implement"] })),
    ).toBe("senior-dev");
    expect(agentForShipped(card({ author: "fixer-bot" }))).toBe("fixer");
    expect(agentForShipped(card({ author: "spec-planner" }))).toBe("spec-planner");
    expect(agentForShipped(card({ author: "test-engineer" }))).toBe("test-engineer");
    expect(agentForShipped(card({ author: "e2e-runner" }))).toBe("e2e-runner");
    expect(
      agentForShipped(card({ author: "", agent_evidence: ["reviewer"] })),
    ).toBe("reviewer");
  });

  it("attributes slugged evidence (branch prefixes, labels) to the role slug", () => {
    // After the rename, PR evidence / branch prefixes are slugged, e.g.
    // `senior-dev-pr-open` or `agent/senior-dev/123`. The .includes match on the
    // slug substring covers both branch-prefix forms.
    expect(
      agentForShipped(card({ author: "", agent_evidence: ["senior-dev-pr-open"] })),
    ).toBe("senior-dev");
    expect(
      agentForShipped(card({ author: "", labels: ["agent/senior-dev/123"] })),
    ).toBe("senior-dev");
    expect(agentForShipped(card({ author: "architect" }))).toBe("architect");
    expect(
      agentForShipped(card({ author: "", agent_evidence: ["reviewer-approved"] })),
    ).toBe("reviewer");
    expect(agentForShipped(card({ author: "fixer" }))).toBe("fixer");
    expect(agentForShipped(card({ author: "spec-planner" }))).toBe("spec-planner");
    expect(agentForShipped(card({ author: "test-engineer" }))).toBe("test-engineer");
    expect(agentForShipped(card({ author: "e2e-runner" }))).toBe("e2e-runner");
  });

  it("attributes pre-cutover theme evidence without restoring runtime aliases", () => {
    expect(
      agentForShipped(card({ author: "", agent_evidence: ["branch:batman/42"] })),
    ).toBe("architect");
    expect(
      agentForShipped(card({ author: "", agent_evidence: ["branch:lucius/42"] })),
    ).toBe("senior-dev");
    expect(
      agentForShipped(card({ author: "", agent_evidence: ["branch:nightwing/42"] })),
    ).toBe("fixer");
    expect(
      agentForShipped(card({ author: "", agent_evidence: ["branch:damian/42"] })),
    ).toBe("spec-planner");
    expect(
      agentForShipped(card({ author: "", agent_evidence: ["branch:bane/42"] })),
    ).toBe("test-engineer");
    expect(
      agentForShipped(card({ author: "", agent_evidence: ["branch:huntress/42"] })),
    ).toBe("e2e-runner");
    expect(
      agentForShipped(card({ author: "", agent_evidence: ["branch:rasalghul/42"] })),
    ).toBe("reviewer");
  });

  it("returns null when no known codename is present", () => {
    expect(agentForShipped(card({ author: "someone-else", labels: [] }))).toBeNull();
  });
});
