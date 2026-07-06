import type { ShippedCard } from "../types";

type AttributionInput = Pick<ShippedCard, "author" | "labels" | "agent_evidence">;

type AgentHint = {
  codename: string;
  hints: string[];
};

const SHIPPED_AGENT_HINTS: AgentHint[] = [
  {
    codename: "architect",
    hints: ["architect", "agent:large-feature", "batman", "bruce"],
  },
  {
    codename: "senior-dev",
    hints: ["senior-dev", "agent:implement", "lucius", "lucius fox"],
  },
  {
    codename: "fixer",
    hints: ["fixer", "nightwing"],
  },
  {
    codename: "spec-planner",
    hints: ["spec-planner", "damian", "damian wayne"],
  },
  {
    codename: "test-engineer",
    hints: ["test-engineer", "bane"],
  },
  {
    codename: "e2e-runner",
    hints: ["e2e-runner", "huntress"],
  },
  {
    codename: "reviewer",
    hints: ["reviewer", "ras al ghul", "ras-al-ghul", "rasalghul", "ra's al ghul"],
  },
];

// Read-model attribution only: these hints keep historical proof cards badged
// after the role-slug cutover. They do not make display names runnable aliases.
export function detectShippedAgentCodename(card: AttributionInput): string | null {
  const tokens = [
    card.author || "",
    ...(card.labels || []),
    ...(card.agent_evidence || []),
  ].map((token) => token.toLowerCase());

  for (const agent of SHIPPED_AGENT_HINTS) {
    if (tokens.some((token) => agent.hints.some((hint) => token.includes(hint)))) {
      return agent.codename;
    }
  }

  return null;
}
