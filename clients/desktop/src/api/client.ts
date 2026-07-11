import { invoke } from "@tauri-apps/api/core";

export const DEFAULT_BASE_URL = "http://127.0.0.1:7010";
const BASE_URL_KEY = "alfred-desktop.base-url";
const DEV_BASE_URL = import.meta.env.VITE_ALFRED_BASE_URL?.trim();

declare global {
  interface Window {
    __TAURI_INTERNALS__?: unknown;
  }
}

// An error that carries a plain-language `message` for the UI plus the raw
// `detail` string (status line, stderr, stack) so panels can hide the technical
// text behind a "Details" disclosure instead of leading with it.
export class ApiError extends Error {
  readonly detail: string | null;
  constructor(message: string, detail: string | null = null) {
    super(message);
    this.name = "ApiError";
    this.detail = detail;
  }
}

// Pull the raw technical text out of any thrown value for the Details panel.
export function errorDetail(err: unknown): string | null {
  if (err instanceof ApiError) {
    return err.detail;
  }
  if (err instanceof Error) {
    return err.message;
  }
  return null;
}

// Map an HTTP error status to plain-language guidance. Returns the operator-
// facing message; the raw status line + body stay available on ApiError.detail.
export function humanizeFetchError(status: number, serverMessage?: string | null): string {
  const genericAuthBody =
    status === 401 || status === 403
      ? ["forbidden", "unauthorized", "auth required"].includes(
          (serverMessage || "").trim().toLowerCase(),
        )
      : false;
  if (serverMessage && status >= 400 && status < 500 && !genericAuthBody) {
    return serverMessage;
  }
  if (status === 401 || status === 403) {
    if (!isTauri() && !isHostedBrowser()) {
      return "This action needs the Alfred desktop app so it can attach the launch token. Open the desktop app, then retry.";
    }
    if (isHostedBrowser()) {
      return "Alfred serve rejected this action (auth token mismatch). Reload this page so it can pick up a fresh launch token, or restart alfred serve.";
    }
    return "Alfred serve is running but rejected this client (auth token mismatch). Restart the runtime or check your token.";
  }
  if (status === 404) {
    return "Alfred serve answered, but this endpoint is missing. Restart the runtime and check the local server logs.";
  }
  if (status === 502 || status === 503 || status === 504) {
    return "Alfred serve is reachable but not ready yet. Give the runtime a moment, then refresh.";
  }
  if (status >= 500) {
    return "Alfred serve hit an internal error handling this request. Check the runtime logs.";
  }
  return `Alfred serve returned an unexpected ${status} response. See details below.`;
}

export function serverErrorMessage(text: string): string | null {
  if (!text.trim()) {
    return null;
  }
  try {
    const payload = JSON.parse(text) as unknown;
    if (payload && typeof payload === "object") {
      const record = payload as Record<string, unknown>;
      for (const key of ["error", "message", "detail"]) {
        const value = record[key];
        if (typeof value === "string" && value.trim()) {
          return value.trim();
        }
      }
    }
  } catch {
    return null;
  }
  return null;
}

// Map a transport-level failure (no HTTP response at all) to plain language.
export function humanizeTransportError(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err);
  const lower = raw.toLowerCase();
  if (
    lower.includes("connection refused") ||
    lower.includes("econnrefused") ||
    lower.includes("failed to fetch") ||
    lower.includes("load failed") ||
    lower.includes("networkerror")
  ) {
    return "Could not reach Alfred serve. Start the runtime, or point this client at the URL where alfred serve is listening.";
  }
  if (lower.includes("timeout") || lower.includes("timed out")) {
    return "Alfred serve did not respond in time. The runtime may be busy or stuck; check it, then refresh.";
  }
  return raw;
}

export function clientBaseUrl(value?: string | null): string {
  const trimmed = value?.trim() || DEFAULT_BASE_URL;
  if (shouldNormalizeDevPreviewBaseUrl(trimmed)) {
    return DEFAULT_BASE_URL;
  }
  return trimmed;
}

export function initialBaseUrl(): string {
  // When the app is served in a browser BY `alfred serve` (not the desktop
  // shell, not the Vite dev server), the API lives at the same origin the page
  // was loaded from. Prefer that so relative `/api/*` calls just work and no
  // localStorage/default guessing is needed.
  if (isHostedBrowser()) {
    return clientBaseUrl(window.location.origin);
  }
  return clientBaseUrl(DEV_BASE_URL || window.localStorage.getItem(BASE_URL_KEY));
}

export function rememberBaseUrl(value: string): void {
  window.localStorage.setItem(BASE_URL_KEY, clientBaseUrl(value));
}

// True once Alfred has successfully connected at least once on this machine.
// `rememberBaseUrl` persists the connected URL, so a stored value is a durable
// proxy for "this is a returning user" that survives app restarts. Used to keep
// first-run onboarding from re-firing on every cold start for established users.
export function hasStoredBaseUrl(): boolean {
  return Boolean(window.localStorage.getItem(BASE_URL_KEY));
}

export function settledError(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

// Bound a fetch so a slow optional endpoint cannot stall the snapshot batch.
// The underlying request is not aborted (it rides a Tauri invoke), but the
// client stops waiting and the caller treats the timeout as a rejection.
export function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new ApiError(`${label} timed out after ${ms}ms`, "timeout")),
      ms,
    );
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (err) => {
        clearTimeout(timer);
        reject(err);
      },
    );
  });
}

// True when a thrown error is the server's "no live session" degrade signal, so
// Compose can quietly fall back to the one-shot rubric form instead of showing
// a scary error. Matches either the structured error code carried on the raw
// detail or the 503 status line.
export function isLiveSessionUnavailable(err: unknown): boolean {
  const detail = errorDetail(err);
  if (detail && detail.includes("live_session_unavailable")) {
    return true;
  }
  return Boolean(detail && /\b503\b/.test(detail));
}

// --------------------------------------------------------------------------- //
// Real-time streaming (#41 live log tail, #36 compose token stream)
//
// These ride the webview's own fetch / EventSource against the localhost server
// directly, NOT the buffered Tauri JSON bridge (the Rust `reqwest` path calls
// `.text()` and so cannot stream a body incrementally). The buffered helpers
// above stay the canonical path for every request/response endpoint; streaming
// is a progressive enhancement that always has a non-streaming fallback.
// --------------------------------------------------------------------------- //

// Resolve the URL for a streaming request. In dev/browser we go through the
// Vite `/alfred-api` proxy so the request is same-origin (and the proxy injects
// the Origin header the server's same-origin check wants); native and prod hit
// the localhost server directly.
export function streamingUrl(baseUrl: string, path: string): string {
  const url = new URL(path, normalizedBaseUrl(baseUrl));
  if (!isLocalAlfredUrl(url)) {
    throw new ApiError(
      "Streaming is only available against a local Alfred runtime.",
      "streaming target must be http localhost, 127.0.0.1, or ::1",
    );
  }
  return shouldUseDevProxy(url) ? `/alfred-api${path}` : url.toString();
}

// One frame parsed out of a text/event-stream body.
export type SseFrame = { event: string; data: unknown };

// A minimal SSE parser over a fetch ReadableStream. Yields one frame per
// `event:`/`data:` block. Used for the converse POST stream (EventSource is
// GET-only and cannot send the token header); the log tail uses EventSource
// directly since it is an open GET.
export async function* readSseStream(
  response: Response,
  signal?: AbortSignal,
): AsyncGenerator<SseFrame> {
  const body = response.body;
  if (!body) {
    return;
  }
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      if (signal?.aborted) {
        return;
      }
      const { value, done } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      let sep = buffer.indexOf("\n\n");
      while (sep !== -1) {
        const block = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const frame = parseSseBlock(block);
        if (frame) {
          yield frame;
        }
        sep = buffer.indexOf("\n\n");
      }
    }
  } finally {
    reader.releaseLock();
  }
}

function parseSseBlock(block: string): SseFrame | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trim());
    }
  }
  if (!dataLines.length) {
    return null;
  }
  try {
    return { event, data: JSON.parse(dataLines.join("\n")) };
  } catch {
    return { event, data: dataLines.join("\n") };
  }
}

export function supportsNativeActions(): boolean {
  return isTauri();
}

// Whether this client can hold a live, server-backed conversation (the desktop
// Ask). The conversational engine runs SERVER-SIDE in `alfred serve`
// (`/api/compose/converse` and its streaming variant execute the model
// regardless of client), so both the native Tauri window AND the hosted browser
// shell can converse: the native window reaches the server through its bridge,
// and the hosted browser calls the same-origin endpoint with the injected
// per-launch token. This is deliberately BROADER than `supportsNativeActions`,
// which stays Tauri-only for genuine native actions (starting the runtime,
// installing core). Gating Ask on `supportsNativeActions` was the bug that left
// the browser Ask stuck on the offline draft fallback with no real conversation.
export function supportsConversation(): boolean {
  return isTauri() || isHostedBrowser();
}

// True when this client can send state-mutating HTTP requests to `alfred serve`
// with a valid launch token. That is either the Tauri desktop shell (the Rust
// bridge injects the token) OR the browser shell served by `alfred serve` (the
// server injects the per-launch token into the page as `<meta name="alfred-
// token">`, and `browserFetch` attaches it on writes). The Vite dev preview is
// deliberately excluded: it has no token, so its writes would 403. Panels that
// gate an HTTP mutation (queue actions, custom-agent save/delete) must use this,
// NOT `supportsNativeActions()`, or they wrongly hide working controls in the
// hosted browser build.
export function supportsMutations(): boolean {
  return isTauri() || isHostedBrowser();
}

export async function readAlfredJson<T>(
  baseUrl: string,
  path: string,
  options: { token?: boolean } = {},
): Promise<T> {
  const resolvedBaseUrl = clientBaseUrl(baseUrl);
  const command = options.token ? "fetch_alfred_json_with_token" : "fetch_alfred_json";
  const text = isTauri()
    ? await invokeAlfredJson(command, { baseUrl: resolvedBaseUrl, path })
    : await browserFetch(resolvedBaseUrl, path, "GET", undefined, undefined, {
        token: options.token,
      });
  return JSON.parse(text) as T;
}

export async function writeAlfredJson<T>(
  baseUrl: string,
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  // An already-aborted run short-circuits before doing any work. The native
  // Tauri invoke path cannot be cancelled mid-flight (the buffered Rust bridge
  // resolves whole), so callers also guard the resolved value; the browser/dev
  // path threads the signal into fetch so it can abort in-flight.
  if (signal?.aborted) {
    throw new DOMException("Aborted", "AbortError");
  }
  const resolvedBaseUrl = clientBaseUrl(baseUrl);
  const payload = body === undefined ? undefined : JSON.stringify(body);
  const text = isTauri()
    ? await invokeAlfredJson("post_alfred_json", { baseUrl: resolvedBaseUrl, path, body: payload })
    : await browserFetch(resolvedBaseUrl, path, "POST", payload, signal);
  return JSON.parse(text) as T;
}

export async function deleteAlfredJson<T>(baseUrl: string, path: string): Promise<T> {
  const resolvedBaseUrl = clientBaseUrl(baseUrl);
  const text = isTauri()
    ? await invokeAlfredJson("delete_alfred_json", { baseUrl: resolvedBaseUrl, path })
    : await browserFetch(resolvedBaseUrl, path, "DELETE");
  return JSON.parse(text) as T;
}

// The native fetch command surfaces the same auth/transport failures the browser
// path does, just as a Tauri invoke rejection. Humanize those too so the desktop
// build does not leak a raw Rust error string into the connection banner.
async function invokeAlfredJson(
  command:
    | "fetch_alfred_json"
    | "fetch_alfred_json_with_token"
    | "post_alfred_json"
    | "delete_alfred_json",
  args: Record<string, unknown>,
): Promise<string> {
  try {
    return await invoke<string>(command, args);
  } catch (err) {
    const raw = err instanceof Error ? err.message : String(err);
    const statusMatch = raw.match(/\b(40[13]|404|5\d\d)\b/);
    if (statusMatch) {
      throw new ApiError(humanizeFetchError(Number(statusMatch[1])), raw);
    }
    throw new ApiError(humanizeTransportError(err), raw);
  }
}

async function browserFetch(
  baseUrl: string,
  path: string,
  method: "GET" | "POST" | "DELETE",
  body?: string,
  signal?: AbortSignal,
  options: { token?: boolean } = {},
): Promise<string> {
  const url = new URL(path, normalizedBaseUrl(baseUrl));
  const devProxyPath = shouldUseDevProxy(url) ? `/alfred-api${path}` : url.toString();
  const headers: Record<string, string> = {};
  if (body !== undefined) {
    headers["content-type"] = "application/json";
  }
  // Hosted browser (served by `alfred serve`): attach the injected per-launch
  // token so state-mutating requests pass the server's `_authorized_mutation`
  // gate. Most reads (GET) need no token, but a privileged read (e.g. the
  // custom-agents inventory WITH `include_prompt=1`) is gated the same way, so
  // an explicit `options.token` forces the token onto a GET too. The dev-proxy
  // path injects the token server-side, so we only add it for the direct
  // same-origin hosted case.
  const needsToken = method !== "GET" || options.token === true;
  if (needsToken && !shouldUseDevProxy(url) && isHostedBrowser()) {
    const token = hostedBrowserToken();
    if (token) {
      headers["X-Alfred-Token"] = token;
    }
  }
  let response: Response;
  try {
    response = await fetch(devProxyPath, {
      method,
      headers: Object.keys(headers).length ? headers : undefined,
      body,
      signal,
    });
  } catch (err) {
    // No HTTP response at all: connection refused, DNS, timeout, CORS, etc.
    throw new ApiError(humanizeTransportError(err), err instanceof Error ? err.message : String(err));
  }
  const text = await response.text();
  if (!response.ok) {
    const raw = `alfred serve returned ${response.status}${text ? `: ${text}` : ""}`;
    throw new ApiError(humanizeFetchError(response.status, serverErrorMessage(text)), raw);
  }
  return text;
}

export function normalizedBaseUrl(baseUrl: string): string {
  const url = new URL(baseUrl);
  url.pathname = "/";
  url.search = "";
  url.hash = "";
  return url.toString();
}

export function isTauri(): boolean {
  return Boolean(window.__TAURI_INTERNALS__);
}

// True when the built app is being served in a plain browser by `alfred serve`
// (the production browser shell): not the Tauri native window, and not the Vite
// dev server. In this mode the API is same-origin and the server injects the
// per-launch token into the page, so the client attaches it directly rather
// than relying on the native bridge (Tauri) or the dev proxy (Vite dev).
export function isHostedBrowser(): boolean {
  return !isTauri() && !import.meta.env.DEV && typeof window !== "undefined";
}

// The per-launch mutation token the server injects into the served index.html
// as `<meta name="alfred-token">`. A same-origin page can read its own document
// to recover it; a cross-origin drive-by page cannot (the same-origin policy),
// and the token file stays 0600 on disk. Mirrors the Tauri bridge's token and
// the Vite dev proxy's header. Returns null when absent (e.g. the "not built"
// page, or an older server), in which case a mutation surfaces the server's 403.
export function hostedBrowserToken(): string | null {
  if (typeof document === "undefined") {
    return null;
  }
  const meta = document.querySelector('meta[name="alfred-token"]');
  const token = meta?.getAttribute("content")?.trim();
  return token || null;
}

function shouldUseDevProxy(url: URL): boolean {
  return (
    import.meta.env.DEV &&
    url.protocol === "http:" &&
    ["127.0.0.1", "localhost"].includes(url.hostname)
  );
}

function shouldNormalizeDevPreviewBaseUrl(value: string): boolean {
  if (!import.meta.env.DEV || isTauri() || DEV_BASE_URL) {
    return false;
  }
  try {
    const url = new URL(value);
    return isLocalAlfredUrl(url);
  } catch {
    return false;
  }
}

function isLocalAlfredUrl(url: URL): boolean {
  return (
    url.protocol === "http:" &&
    ["127.0.0.1", "localhost", "::1", "[::1]"].includes(url.hostname)
  );
}
