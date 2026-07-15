import type { CodeIntelligenceResponse } from "../types";
import { readAlfredJson, withTimeout } from "./client";

export async function loadCodeIntelligence(
  baseUrl: string,
  options: { repo?: string; path?: string; limit?: number } = {},
): Promise<CodeIntelligenceResponse> {
  const params = new URLSearchParams();
  if (options.repo?.trim()) params.set("repo", options.repo.trim());
  if (options.path?.trim()) params.set("path", options.path.trim());
  if (options.limit) params.set("limit", String(options.limit));
  const query = params.toString();
  return withTimeout(
    readAlfredJson<CodeIntelligenceResponse>(
      baseUrl,
      `/api/code-intelligence${query ? `?${query}` : ""}`,
    ),
    12_000,
    "/api/code-intelligence",
  );
}
