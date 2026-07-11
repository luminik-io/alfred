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
  SetupSelectReposResponse,
  SetupStatus,
} from "../types";
import { readAlfredJson, withTimeout, writeAlfredJson } from "./client";

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
): Promise<SetupSelectReposResponse> {
  return writeAlfredJson(baseUrl, "/api/setup/repos", { repos, queue_repos: repos });
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
