import type {
  ActionsResponse,
  FiringRecord,
  FiringsResponse,
  MemoryCandidatesResponse,
  MemoryLessonsResponse,
  PlansResponse,
  ScheduleResponse,
  ShippedBoard,
  Snapshot,
  StatusResponse,
  TrustedSlackUsersResponse,
  UsageResponse,
} from "../types";
import { readAlfredJson, settledError, streamingUrl, withTimeout } from "./client";

// The dashboard reads several independent endpoints. A failure on any one of them
// should not blank the whole view, so we settle each request and render what
// resolved, marking the missing sections as degraded. /api/status is the spine
// (it carries fleet liveness and the reliability rollup): if it fails the whole
// snapshot is genuinely unusable, so that one rejection still surfaces as the
// connection error the banner shows.
export async function loadSnapshot(baseUrl: string): Promise<Snapshot> {
  const [status, actions, memoryCandidates, memoryLessons, firings, plans, trustedSlack, schedule] =
    await Promise.allSettled([
      readAlfredJson<StatusResponse>(baseUrl, "/api/status"),
      readAlfredJson<ActionsResponse>(baseUrl, "/api/actions"),
      readAlfredJson<MemoryCandidatesResponse>(baseUrl, "/api/memory/candidates?limit=20"),
      readAlfredJson<MemoryLessonsResponse>(baseUrl, "/api/memory/lessons?limit=30"),
      readAlfredJson<FiringsResponse>(baseUrl, "/api/firings?limit=14"),
      readAlfredJson<PlansResponse>(baseUrl, "/api/plans?limit=14"),
      readAlfredJson<TrustedSlackUsersResponse>(baseUrl, "/api/slack/trusted-users"),
      readAlfredJson<ScheduleResponse>(baseUrl, "/api/schedule"),
    ]);

  if (status.status === "rejected") {
    throw status.reason instanceof Error ? status.reason : new Error(String(status.reason));
  }

  const degraded: NonNullable<Snapshot["degraded"]> = {};
  if (actions.status === "rejected") degraded.actions = settledError(actions.reason);
  if (memoryCandidates.status === "rejected") {
    degraded.memoryCandidates = settledError(memoryCandidates.reason);
  }
  if (firings.status === "rejected") degraded.firings = settledError(firings.reason);
  if (plans.status === "rejected") degraded.plans = settledError(plans.reason);
  if (trustedSlack.status === "rejected") degraded.trustedSlack = settledError(trustedSlack.reason);
  if (schedule.status === "rejected") degraded.schedule = settledError(schedule.reason);

  return {
    loadedAt: new Date(),
    status: status.value,
    actions:
      actions.status === "fulfilled"
        ? actions.value
        : {
            status: "degraded",
            actions: [],
            failure_patterns: [],
            stale_workers: [],
            promotion_suggestions: [],
          },
    memoryCandidates:
      memoryCandidates.status === "fulfilled"
        ? { rows: memoryCandidates.value.rows || [], error: memoryCandidates.value.error }
        : { rows: [] },
    memoryLessons:
      memoryLessons.status === "fulfilled"
        ? { rows: memoryLessons.value.rows || [], error: memoryLessons.value.error }
        : { rows: [] },
    firings: firings.status === "fulfilled" ? firings.value.rows || [] : [],
    plans: plans.status === "fulfilled" ? plans.value.rows || [] : [],
    trustedSlack: trustedSlack.status === "fulfilled" ? trustedSlack.value : null,
    // Upcoming scheduled runs (agents.conf). A rejection degrades to an empty
    // lane, never a blanked view.
    schedule: schedule.status === "fulfilled" ? schedule.value.runs || [] : [],
    // The Kanban board is fetched separately (loadShipped) so its slower
    // multi-repo gh scan never gates the core snapshot.
    shipped: null,
    degraded: Object.keys(degraded).length ? degraded : undefined,
  };
}

// The Kanban board scans many repos via gh and is the page's centerpiece, so it
// is fetched on its own (decoupled from loadSnapshot) with a generous timeout
// and its own loading/error state. The rest of the dashboard never waits on it.
export async function loadShipped(
  baseUrl: string,
  days = 14,
  options: { demo?: boolean } = {},
): Promise<ShippedBoard> {
  const params = new URLSearchParams({ days: String(days) });
  if (options.demo) params.set("demo", "1");
  const board = await withTimeout(
    readAlfredJson<ShippedBoard>(baseUrl, `/api/shipped?${params.toString()}`),
    20000,
    "/api/shipped",
  );
  return normalizeShippedBoard(board);
}

// Real subscription-usage headroom from GET /api/usage. The server reads local
// Claude/Codex logs with a bounded native reader, so it is fetched separately
// and never gates the core snapshot.
export async function loadUsage(baseUrl: string): Promise<UsageResponse> {
  return withTimeout(
    readAlfredJson<UsageResponse>(baseUrl, "/api/usage"),
    12000,
    "/api/usage",
  );
}

// Fetch one agent's own firing history. The Logs live tail uses this when a
// quieter agent has been pushed out of the limited global /api/firings feed, so
// "View logs" still surfaces real runs instead of an empty state.
export async function loadAgentFirings(
  baseUrl: string,
  codename: string,
  limit = 20,
): Promise<FiringRecord[]> {
  const params = new URLSearchParams({ codename, limit: String(limit) });
  const response = await readAlfredJson<FiringsResponse>(
    baseUrl,
    `/api/firings?${params.toString()}`,
  );
  return response.rows || [];
}

function normalizeShippedBoard(board: ShippedBoard): ShippedBoard {
  if (board.error) return board;
  const errors = board.errors || [];
  if (!errors.length) return board;
  const totalCards = board.counts.queued + board.counts.in_progress + board.counts.shipped;
  if (totalCards > 0) return board;
  const watchedRepos = new Set(board.repos.filter(Boolean));
  const erroredRepos = new Set(errors);
  if (
    !watchedRepos.size ||
    !Array.from(watchedRepos).every((repo) => erroredRepos.has(repo))
  ) {
    return board;
  }
  const shown = errors.slice(0, 3).join(", ");
  const more = errors.length > 3 ? `, +${errors.length - 3} more` : "";
  const repoLabel = errors.length === 1 ? "repo" : "repos";
  return {
    ...board,
    error: `GitHub data unavailable for ${errors.length} watched ${repoLabel}: ${shown}${more}`,
  };
}

// Handlers for a live log tail. `onLines` fires with each new batch of whole
// transcript lines (raw JSONL strings); `onDone` fires once the firing ends or
// the stream closes; `onError` fires on a transport error so the caller can
// fall back to its existing poll. Returns a disposer that closes the stream.
export type LogTailHandlers = {
  onLines: (lines: string[]) => void;
  onDone?: (reason: string) => void;
  onError?: (err: unknown) => void;
};

// Live-tail a running firing's transcript over Server-Sent-Events (#41). This
// is an OPEN GET, so it rides EventSource directly with no token. The caller
// keeps its 60s firing poll as the fallback: if EventSource is unavailable or
// errors, `onError` fires and the caller simply leans on the poll. Returns a
// disposer; call it on unmount or when switching firings.
export function streamFiringTail(
  baseUrl: string,
  firingId: string,
  handlers: LogTailHandlers,
): () => void {
  if (typeof EventSource === "undefined") {
    handlers.onError?.(new Error("EventSource unavailable"));
    return () => {};
  }
  const url = streamingUrl(baseUrl, `/api/firings/${encodeURIComponent(firingId)}/tail`);
  let source: EventSource;
  try {
    source = new EventSource(url);
  } catch (err) {
    handlers.onError?.(err);
    return () => {};
  }
  let closed = false;
  const close = () => {
    if (!closed) {
      closed = true;
      source.close();
    }
  };
  source.addEventListener("append", (event) => {
    try {
      const payload = JSON.parse((event as MessageEvent).data) as { lines?: string[] };
      if (Array.isArray(payload.lines) && payload.lines.length) {
        handlers.onLines(payload.lines);
      }
    } catch {
      // A torn frame is harmless; the next append carries the lines whole.
    }
  });
  source.addEventListener("done", (event) => {
    let reason = "complete";
    try {
      reason = (JSON.parse((event as MessageEvent).data) as { reason?: string }).reason ?? reason;
    } catch {
      // keep default reason
    }
    close();
    handlers.onDone?.(reason);
  });
  source.onerror = () => {
    // EventSource auto-reconnects, but for a localhost runtime a hard error
    // usually means the route is missing or the runtime is down. Close and let
    // the caller fall back to its poll rather than spin.
    close();
    handlers.onError?.(new Error("log tail stream error"));
  };
  return close;
}
