import { invoke } from "@tauri-apps/api/core";

import type {
  AgentModelProvider,
  AgentModelsResponse,
  CustomAgentsResponse,
  CustomAgentWrite,
  DeleteCustomAgentResponse,
  NativeAction,
  NativeCommandResult,
  SaveCustomAgentResponse,
  SaveAgentModelResponse,
} from "../types";
import { deleteAlfredJson, isTauri, readAlfredJson, writeAlfredJson } from "./client";

export async function loadCustomAgents(
  baseUrl: string,
  options: { includePrompt?: boolean } = {},
): Promise<CustomAgentsResponse> {
  const query = options.includePrompt ? "?include_prompt=1" : "";
  return readAlfredJson<CustomAgentsResponse>(baseUrl, `/api/custom-agents${query}`, {
    token: Boolean(options.includePrompt),
  });
}

export async function saveCustomAgent(
  baseUrl: string,
  body: CustomAgentWrite,
): Promise<SaveCustomAgentResponse> {
  return writeAlfredJson<SaveCustomAgentResponse>(baseUrl, "/api/custom-agents", body);
}

export async function deleteCustomAgent(
  baseUrl: string,
  codename: string,
): Promise<DeleteCustomAgentResponse> {
  return deleteAlfredJson<DeleteCustomAgentResponse>(
    baseUrl,
    `/api/custom-agents/${encodeURIComponent(codename)}`,
  );
}

export async function loadAgentModels(baseUrl: string): Promise<AgentModelsResponse> {
  return readAlfredJson<AgentModelsResponse>(baseUrl, "/api/agent-models");
}

export async function saveAgentModel(
  baseUrl: string,
  agent: string,
  provider: AgentModelProvider,
  model: string | null,
): Promise<SaveAgentModelResponse> {
  return writeAlfredJson<SaveAgentModelResponse>(baseUrl, "/api/agent-models", {
    agent,
    provider,
    model,
  });
}

export async function runNativeAction(
  action: NativeAction,
  target?: string,
  cadence?: string,
): Promise<NativeCommandResult> {
  if (!isTauri()) {
    throw new Error("Native Alfred actions are available in the desktop app.");
  }
  return invoke<NativeCommandResult>("run_alfred_action", { action, target, cadence });
}

export async function startLocalRuntime(port = 7010): Promise<NativeCommandResult> {
  if (!isTauri()) {
    throw new Error("The desktop app is needed to start Alfred locally.");
  }
  return invoke<NativeCommandResult>("start_alfred_runtime", { port });
}

export async function installAlfredCore(runtimePort: number): Promise<NativeCommandResult> {
  if (!isTauri()) {
    throw new Error("The desktop app is needed to install Alfred locally.");
  }
  return invoke<NativeCommandResult>("install_alfred_core", { runtimePort });
}

export async function setTrayStatus(
  level: "ok" | "warn" | "error" | "unknown",
  summary?: string,
): Promise<void> {
  if (!isTauri()) {
    return;
  }
  try {
    await invoke("set_tray_status", { level, summary });
  } catch {
    // The tray is best-effort; never let a tray hiccup break the UI.
  }
}
