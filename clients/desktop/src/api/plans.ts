import type {
  DiscardPlanResponse,
  FilePlanIssueResponse,
  FollowupActionResponse,
  PlanDecision,
  PlanDecisionResponse,
} from "../types";
import { writeAlfredJson } from "./client";

export async function convertFollowupToDraft(
  baseUrl: string,
  planId: string,
): Promise<FollowupActionResponse> {
  return writeAlfredJson(baseUrl, `/api/plans/${planPathSegment(planId)}/convert-followup`);
}

export async function markFollowupHandled(
  baseUrl: string,
  planId: string,
): Promise<FollowupActionResponse> {
  return writeAlfredJson(baseUrl, `/api/plans/${planPathSegment(planId)}/mark-handled`);
}

// Record a real go/no-go on a genuine architect plan. The server writes the same
// `{issue_num}.approved` / `.rejected` marker the architect's file-poll fallback
// watches, so an approve here starts that exact scope and a decline stops it,
// with no Slack round-trip. Token-gated server-side via _authorized_mutation.
export async function decidePlan(
  baseUrl: string,
  planId: string,
  decision: PlanDecision,
  reason?: string,
): Promise<PlanDecisionResponse> {
  const body: { decision: PlanDecision; reason?: string } = { decision };
  if (reason && reason.trim()) body.reason = reason.trim();
  return writeAlfredJson(
    baseUrl,
    `/api/plans/${planPathSegment(planId)}/decision`,
    body,
  );
}

export async function filePlanIssue(
  baseUrl: string,
  planId: string,
): Promise<FilePlanIssueResponse> {
  return writeAlfredJson(baseUrl, `/api/plans/${planPathSegment(planId)}/file-issue`);
}

// Discard a local planning draft (issue 314). The server archives the draft
// JSON rather than hard-deleting it, and is idempotent, so a double click is
// safe. Token-gated server-side via _authorized_mutation.
export async function discardPlan(
  baseUrl: string,
  planId: string,
): Promise<DiscardPlanResponse> {
  return writeAlfredJson(baseUrl, `/api/plans/${planPathSegment(planId)}/discard`);
}

function planPathSegment(planId: string): string {
  const clean = planId.trim();
  if (!/^[A-Za-z0-9_.-]+$/.test(clean)) {
    throw new Error("Plan id is not safe to send to Alfred serve.");
  }
  return clean;
}
