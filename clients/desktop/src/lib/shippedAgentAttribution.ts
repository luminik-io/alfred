import type { ShippedCard } from "../types";

type AttributionInput = Pick<ShippedCard, "author" | "labels" | "agent_evidence">;

type AgentHint = {
  codename: string;
  hints: string[];
};

const SHIPPED_AGENT_HINTS: AgentHint[] = [
  {
    codename: "architect",
    hints: ["architect", "agent:large-feature"],
  },
  {
    codename: "senior-dev",
    hints: ["senior-dev", "agent:implement"],
  },
  {
    codename: "fixer",
    hints: ["fixer"],
  },
  {
    codename: "spec-planner",
    hints: ["spec-planner"],
  },
  {
    codename: "test-engineer",
    hints: ["test-engineer"],
  },
  {
    codename: "e2e-runner",
    hints: ["e2e-runner"],
  },
  {
    codename: "reviewer",
    hints: ["reviewer"],
  },
];

// Read-model attribution only: these hints badge shipped cards by canonical
// role evidence. Theme display names are deliberately not accepted as aliases.
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
