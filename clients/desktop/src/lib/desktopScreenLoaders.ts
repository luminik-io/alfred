import type { OperatorKey, TabKey } from "./uiTypes";

// Keep every import path literal so Vite can create one predictable chunk per
// primary surface. Inbox and onboarding stay eager because either can be the
// first useful screen after the setup probe.
export const desktopScreenImporters = {
  ask: () => import("../components/ComposeView"),
  code: () => import("../components/CodeIntelligenceView"),
  settings: () => import("../components/SettingsView"),
  work: () => import("../components/PipelineView"),
  agentRoster: () => import("../components/FleetControlView"),
  customAgents: () => import("../components/CustomAgentsPanel"),
  activity: () => import("../components/LogsView"),
  learnings: () => import("../components/MemoryView"),
} as const;

export async function preloadDesktopTab(tab: TabKey): Promise<void> {
  switch (tab) {
    case "pipeline":
      await desktopScreenImporters.work();
      return;
    case "compose":
      await desktopScreenImporters.ask();
      return;
    case "code":
      await desktopScreenImporters.code();
      return;
    case "settings":
      await desktopScreenImporters.settings();
      return;
    case "fleet":
      await preloadAgentTab("fleet");
      return;
    case "logs":
      await preloadAgentTab("logs");
      return;
    case "lessons":
      await preloadAgentTab("lessons");
      return;
    case "home":
      return;
  }
}

export async function preloadAgentTab(tab: OperatorKey): Promise<void> {
  switch (tab) {
    case "fleet":
      await Promise.all([
        desktopScreenImporters.customAgents(),
        desktopScreenImporters.agentRoster(),
      ]);
      return;
    case "logs":
      await desktopScreenImporters.activity();
      return;
    case "lessons":
      await desktopScreenImporters.learnings();
  }
}
