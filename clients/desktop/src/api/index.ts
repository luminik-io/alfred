// Barrel for the desktop API layer. The implementation is split by domain
// (client, snapshot, plans, converse, memory, roster, agents, setup, slack,
// queue); this file re-exports the full public surface so broad importers and
// tests can pull from `./api` while feature code imports the specific domain
// module it needs.

export {
  ApiError,
  DEFAULT_BASE_URL,
  clientBaseUrl,
  errorDetail,
  hasStoredBaseUrl,
  initialBaseUrl,
  isHostedBrowser,
  isLiveSessionUnavailable,
  rememberBaseUrl,
  supportsConversation,
  supportsMutations,
  supportsNativeActions,
} from "./client";

export {
  loadAgentFirings,
  loadShipped,
  loadSnapshot,
  loadUsage,
  streamFiringTail,
  type LogTailHandlers,
} from "./snapshot";

export {
  convertFollowupToDraft,
  decidePlan,
  discardPlan,
  filePlanIssue,
  markFollowupHandled,
} from "./plans";

export {
  composeConverse,
  composeDraft,
  conversationControl,
  streamComposeConverse,
} from "./converse";

export {
  isCandidateBackedLesson,
  lessonCandidateId,
  promoteMemoryCandidate,
  rejectMemoryCandidate,
  retireMemoryLesson,
} from "./memory";

export {
  loadRosterTheme,
  saveRosterTheme,
  themeBuilderConverse,
  type RosterThemeResponse,
  type RosterThemeWrite,
} from "./roster";

export {
  deleteCustomAgent,
  installAlfredCore,
  loadCustomAgents,
  runNativeAction,
  saveCustomAgent,
  setTrayStatus,
  startLocalRuntime,
} from "./agents";

export {
  clearSetupDemo,
  composeSetupPlaybook,
  loadSchedule,
  loadSetupBatteries,
  loadSetupPlaybooks,
  loadSetupRepos,
  loadSetupStatus,
  onboardingConverse,
  saveSetupBattery,
  saveSetupRepos,
  seedSetupDemo,
} from "./setup";

export {
  addTrustedSlackUser,
  loadTrustedSlackUsers,
  removeTrustedSlackUser,
} from "./slack";

export { setQueuePickup } from "./queue";

export { loadCodeIntelligence } from "./code";
