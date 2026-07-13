import type {
  OnboardingConverseRequest,
  OnboardingConverseResponse,
  ScheduleResponse,
  SetupBatteryManifest,
  SetupBatterySaveResponse,
  SetupDemoResponse,
  SetupPlaybookComposeResponse,
  SetupPlaybooksResponse,
  SetupReposResponse,
  SetupRepoCheckout,
  SetupRepoCheckoutInput,
  SetupSelectReposResponse,
  SetupStatus,
} from "../types";
import { errorDetail, isTauri, readAlfredJson, withTimeout, writeAlfredJson } from "./client";

export class SetupRepoCheckoutValidationError extends Error {
  readonly rows: SetupRepoCheckout[];
  readonly detail: string | null;

  constructor(rows: SetupRepoCheckout[], detail: string | null = null) {
    super("One or more checkout folders could not be verified.");
    this.name = "SetupRepoCheckoutValidationError";
    this.rows = rows;
    this.detail = detail;
  }
}

function checkoutRowsFromError(err: unknown): SetupRepoCheckout[] | null {
  const detail = errorDetail(err);
  const start = detail?.indexOf("{") ?? -1;
  if (!detail || start < 0) return null;
  try {
    const payload = JSON.parse(detail.slice(start)) as { repo_checkouts?: unknown };
    if (!Array.isArray(payload.repo_checkouts)) return null;
    const rows = payload.repo_checkouts as SetupRepoCheckout[];
    return rows.every(
      (row) =>
        row &&
        typeof row === "object" &&
        typeof row.repo === "string" &&
        typeof row.path === "string" &&
        typeof row.ready === "boolean" &&
        (row.reason === null || typeof row.reason === "string"),
    )
      ? rows
      : null;
  } catch {
    return null;
  }
}

// The upcoming scheduled agent runs parsed from agents.conf. Read-only; the
// conversational onboarding's set_schedule uses it to learn which agents to
// re-cadence through the native `alfred schedule set` primitive.
export async function loadSchedule(baseUrl: string): Promise<ScheduleResponse> {
  return readAlfredJson<ScheduleResponse>(baseUrl, "/api/schedule");
}

export async function loadSetupStatus(baseUrl: string): Promise<SetupStatus> {
  return withTimeout(
    readAlfredJson<SetupStatus>(baseUrl, "/api/setup/status"),
    12000,
    "/api/setup/status",
  );
}

export async function loadSetupRepos(
  baseUrl: string,
  limit = 100,
): Promise<SetupReposResponse> {
  return withTimeout(
    readAlfredJson<SetupReposResponse>(baseUrl, `/api/setup/repos?limit=${limit}`),
    20000,
    "/api/setup/repos",
  );
}

export async function saveSetupRepos(
  baseUrl: string,
  repos: string[],
  repoCheckouts: SetupRepoCheckoutInput[],
): Promise<SetupSelectReposResponse> {
  try {
    return await writeAlfredJson(baseUrl, "/api/setup/repos", {
      repos,
      queue_repos: repos,
      repo_checkouts: repoCheckouts,
    });
  } catch (err) {
    const rows = checkoutRowsFromError(err);
    if (rows) throw new SetupRepoCheckoutValidationError(rows, errorDetail(err));
    throw err;
  }
}

export async function pickSetupRepoFolder(defaultPath?: string): Promise<string | null> {
  if (!isTauri()) return null;
  const { open } = await import("@tauri-apps/plugin-dialog");
  const selected = await open({
    directory: true,
    multiple: false,
    defaultPath: defaultPath?.trim() || undefined,
    title: "Choose the local repository checkout",
  });
  return typeof selected === "string" ? selected : null;
}

export async function loadSetupBatteries(baseUrl: string): Promise<SetupBatteryManifest> {
  return withTimeout(
    readAlfredJson<SetupBatteryManifest>(baseUrl, "/api/setup/batteries"),
    12000,
    "/api/setup/batteries",
  );
}

export async function saveSetupBattery(
  baseUrl: string,
  battery: string,
  enabled: boolean,
): Promise<SetupBatterySaveResponse> {
  return writeAlfredJson(baseUrl, "/api/setup/batteries", { battery, enabled });
}

export async function loadSetupPlaybooks(
  baseUrl: string,
): Promise<SetupPlaybooksResponse> {
  return readAlfredJson<SetupPlaybooksResponse>(baseUrl, "/api/setup/playbooks");
}

export async function composeSetupPlaybook(
  baseUrl: string,
  key: string,
  repos?: string[],
): Promise<SetupPlaybookComposeResponse> {
  const body = repos?.length ? { key, repos } : { key };
  return writeAlfredJson(baseUrl, "/api/setup/playbook", body);
}

export async function seedSetupDemo(baseUrl: string): Promise<SetupDemoResponse> {
  return writeAlfredJson(baseUrl, "/api/setup/demo", {});
}

export async function clearSetupDemo(baseUrl: string): Promise<SetupDemoResponse> {
  return writeAlfredJson(baseUrl, "/api/setup/demo/clear", {});
}

// One turn of the conversational Ask-driven onboarding guide. The server asks a
// short setup question, then requests one scoped action the client executes
// under the same token gate the stepped flow uses. When no live engine is
// configured the server returns a 503 with `error: "live_session_unavailable"`;
// the caller catches that (via isLiveSessionUnavailable) and falls back to the
// stepped onboarding flow. Nothing is executed server-side: the client runs the
// SAME setup handler both paths share, so they cannot drift.
export async function onboardingConverse(
  baseUrl: string,
  request: OnboardingConverseRequest,
  signal?: AbortSignal,
): Promise<OnboardingConverseResponse> {
  return writeAlfredJson(baseUrl, "/api/onboarding/converse", request, signal);
}
