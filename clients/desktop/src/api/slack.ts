import type { TrustedSlackUsersResponse } from "../types";
import { readAlfredJson, writeAlfredJson } from "./client";

// Read the current trusted-Slack-approver list on its own (the snapshot batch
// also fetches it, but the onboarding Slack step needs a standalone read so it
// can show who is already trusted without pulling the whole dashboard).
export async function loadTrustedSlackUsers(
  baseUrl: string,
): Promise<TrustedSlackUsersResponse> {
  return readAlfredJson<TrustedSlackUsersResponse>(baseUrl, "/api/slack/trusted-users");
}

export async function addTrustedSlackUser(
  baseUrl: string,
  userId: string,
): Promise<TrustedSlackUsersResponse> {
  return writeAlfredJson(baseUrl, "/api/slack/trusted-users", { user_id: userId });
}

export async function removeTrustedSlackUser(
  baseUrl: string,
  userId: string,
): Promise<TrustedSlackUsersResponse> {
  return writeAlfredJson(
    baseUrl,
    `/api/slack/trusted-users/${encodeURIComponent(userId)}/remove`,
  );
}
