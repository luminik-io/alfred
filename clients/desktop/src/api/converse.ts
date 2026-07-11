import { invoke } from "@tauri-apps/api/core";

import type {
  ComposeDraftRequest,
  ComposeDraftResponse,
  ConversationControlRequest,
  ConversationControlResponse,
  ConverseRequest,
  ConverseResponse,
} from "../types";
import {
  ApiError,
  hostedBrowserToken,
  humanizeFetchError,
  humanizeTransportError,
  isHostedBrowser,
  isTauri,
  readSseStream,
  serverErrorMessage,
  streamingUrl,
  writeAlfredJson,
} from "./client";

export async function composeDraft(
  baseUrl: string,
  request: ComposeDraftRequest,
): Promise<ComposeDraftResponse> {
  return writeAlfredJson(baseUrl, "/api/plans/draft", request);
}

export async function conversationControl(
  baseUrl: string,
  request: ConversationControlRequest,
  signal?: AbortSignal,
): Promise<ConversationControlResponse> {
  return writeAlfredJson(baseUrl, "/api/conversation/control", request, signal);
}

// One turn of the conversational, repo-grounded spec-builder. The server runs a
// single live interrogator turn and returns the reply + accumulating spec +
// readiness. When no live engine is configured the server returns a 503 with
// `error: "live_session_unavailable"`; the caller catches that (via
// isLiveSessionUnavailable) and degrades to the one-shot `composeDraft` form.
export async function composeConverse(
  baseUrl: string,
  request: ConverseRequest,
  signal?: AbortSignal,
): Promise<ConverseResponse> {
  return writeAlfredJson(baseUrl, "/api/compose/converse", request, signal);
}

// Token-stream one converse turn (#36). EventSource is GET-only and cannot send
// `X-Alfred-Token`, so this uses the webview's `fetch()` with a streamed
// ReadableStream body, which CAN carry the header. `onToken` fires with each
// assistant text fragment as it arrives; the returned promise resolves to the
// final reconciled ConverseResponse. On any streaming failure it REJECTS, so
// the caller can fall back to the non-streaming `composeConverse`. Native only
// (the browser preview stays on the one-shot form), but written transport-
// agnostically so it also works through the dev proxy.
export async function streamComposeConverse(
  baseUrl: string,
  request: ConverseRequest,
  onToken: (text: string) => void,
  signal?: AbortSignal,
): Promise<ConverseResponse> {
  const url = streamingUrl(baseUrl, "/api/compose/converse/stream");
  const headers: Record<string, string> = { "content-type": "application/json" };
  // Native build: attach the per-launch token the Rust side normally injects.
  // The dev proxy path is same-origin and the route still requires the token,
  // so attach it whenever we can read it; a missing token surfaces as the
  // server's 403, which the caller treats as a fallback trigger.
  if (isTauri()) {
    try {
      const token = await invoke<string>("alfred_server_token");
      if (token) {
        headers["X-Alfred-Token"] = token;
      }
    } catch {
      // No token: let the server reject so the caller falls back cleanly.
    }
  } else if (isHostedBrowser()) {
    // Browser served by `alfred serve`: the same-origin POST carries the
    // injected token. The stream route accepts same-origin + token, so this
    // works without the native bridge; a missing token surfaces as the 403 the
    // caller treats as a fallback trigger.
    const token = hostedBrowserToken();
    if (token) {
      headers["X-Alfred-Token"] = token;
    }
  }

  let response: Response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify(request),
      signal,
    });
  } catch (err) {
    throw new ApiError(humanizeTransportError(err), err instanceof Error ? err.message : String(err));
  }
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    const raw = `alfred serve returned ${response.status}${text ? `: ${text}` : ""}`;
    throw new ApiError(humanizeFetchError(response.status, serverErrorMessage(text)), raw);
  }

  let result: ConverseResponse | null = null;
  let streamError: string | null = null;
  for await (const frame of readSseStream(response, signal)) {
    if (frame.event === "token") {
      const text = (frame.data as { text?: string })?.text;
      if (typeof text === "string" && text) {
        onToken(text);
      }
    } else if (frame.event === "result") {
      result = frame.data as ConverseResponse;
    } else if (frame.event === "error") {
      streamError = (frame.data as { detail?: string })?.detail ?? "stream error";
    }
  }
  if (result) {
    return result;
  }
  // No result event: surface the server's degrade signal so the caller can fall
  // back to non-streaming converse (and then, if needed, the one-shot form).
  const detail = streamError ?? "live_session_unavailable";
  throw new ApiError("The conversational engine did not return a usable turn.", detail);
}
