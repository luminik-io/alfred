import { repoShortName } from "../../lib/chips";
import { isSafeExternalUrl } from "../../lib/links";
import type { AssignmentTargetAgent, PlanDraft, QueueAction, ShippedCard } from "../../types";
import type { RepoChip } from "../LifecycleCard";

export type QueueActionHandler = (
  repo: string,
  issueNumber: number,
  action: QueueAction,
  targetAgent?: AssignmentTargetAgent,
) => void | Promise<boolean>;

export function repoChips(repos: string[]): RepoChip[] {
  return repos.map((repo) => ({ short: repoShortName(repo), full: repo }));
}

export function cardKey(card: ShippedCard): string {
  return `${card.repo}#${card.number ?? card.title}`;
}

// A plain outcome sentence for a board card: strip the conventional-commit
// prefix and present the title as a sentence. A richer summary is a Phase 2
// backend change (flagged in the spec).
export function cardOutcome(card: ShippedCard): string {
  // Prefer the server-derived plain-language outcome when present; fall back to
  // a cleaned title when no outcome is present.
  const serverOutcome = (card.outcome || "").trim();
  if (serverOutcome) return serverOutcome;
  const title = (card.title || "").trim();
  if (!title) return "Shipped a change to this repo.";
  const cleaned = title.replace(
    /^\s*(feat|fix|chore|docs|refactor|test)(\([^)]*\))?:\s*/i,
    "",
  );
  const sentence = cleaned.charAt(0).toUpperCase() + cleaned.slice(1);
  return /[.!?]$/.test(sentence) ? sentence : `${sentence}.`;
}

// A working draft (compose / planning) with no parent issue can be discarded.
// Genuine architect go/no-go plans and Slack follow-ups are decisions, not junk
// drafts, so they never get the quiet discard.
export function planCanDiscard(plan: PlanDraft): boolean {
  const hasParent = Boolean(plan.parent && isSafeExternalUrl(plan.parent));
  return !hasParent && (plan.source === "compose" || plan.source === "planning");
}

// Full repo slugs (for the inspector dd) from the affected-repos string.
export function splitReposFull(value: string | null | undefined): string[] {
  if (!value) return [];
  return value
    .split(/[,\s]+/)
    .map((entry) => entry.trim())
    .filter(Boolean);
}
