import type { AssignmentTargetAgent, QueueAction, QueueActionResponse } from "../types";
import { writeAlfredJson } from "./client";

// Assign an issue to architect or senior-dev, arm it directly for senior-dev pickup
// (`queue` -> agent:implement), hold it (`hold` -> do-not-pickup), or close it
// (`done` -> GitHub's native closed state). Mutating, so it rides the
// token-bearing POST path.
export async function setQueuePickup(
  baseUrl: string,
  repo: string,
  number: number,
  action: QueueAction,
  targetAgent: AssignmentTargetAgent = "auto",
): Promise<QueueActionResponse> {
  const target_agent = action === "assign" && targetAgent !== "auto" ? targetAgent : undefined;
  return writeAlfredJson(baseUrl, "/api/queue", { repo, number, action, target_agent });
}
