import {
  Bot,
  Bell,
  GitPullRequest,
  type LucideIcon,
  MessageSquare,
  Settings2,
} from "lucide-react";

import type { OperatorKey, TabKey } from "./uiTypes";

export type PrimaryTab = { key: TabKey; label: string; icon: LucideIcon };

// Canonical job-shaped IA. Labels match the surfaces people actually use:
// Inbox: decisions, activity, and shipped PRs.
// Ask: conversational request intake.
// Work: plans, issue handoff, PR lifecycle, shipped evidence.
// Agents: roles, schedules, activity, lessons, and local controls.
// Settings: runtime, collaborators, diagnostics, and appearance.
export const PRIMARY_TABS: PrimaryTab[] = [
  { key: "home", label: "Inbox", icon: Bell },
  { key: "compose", label: "Ask", icon: MessageSquare },
  { key: "pipeline", label: "Work", icon: GitPullRequest },
  { key: "fleet", label: "Agents", icon: Bot },
  { key: "settings", label: "Settings", icon: Settings2 },
];

// Agents groups the live roster, activity tail, and learning queue.
export const FLEET_SUBTABS: Array<{ key: OperatorKey; label: string }> = [
  { key: "fleet", label: "Roster" },
  { key: "logs", label: "Activity" },
  { key: "lessons", label: "Learnings" },
];
