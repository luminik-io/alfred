import {
  Moon,
  RefreshCw,
  Sun,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AppearancePicker } from "./components/AppearancePicker";
import {
  ConnectionBanner,
  NativeResultPanel,
} from "./components/atoms";
import { CommandPalette, type Command } from "./components/CommandPalette";
import { ComposeView } from "./components/ComposeView";
import { CustomAgentsPanel } from "./components/CustomAgentsPanel";
import { FleetControlView } from "./components/FleetControlView";
import { AppShell } from "./components/layout/AppShell";
import { LogsView } from "./components/LogsView";
import { MemoryView } from "./components/MemoryView";
import { OnboardingView } from "./components/OnboardingView";
import { PipelineView } from "./components/PipelineView";
import { RequestThread } from "./components/RequestThread";
import { ReviewView } from "./components/ReviewView";
import { CustomThemeEditor } from "./components/CustomThemeEditor";
import { RosterThemePicker } from "./components/RosterThemePicker";
import { ThemeBuilderDialog } from "./components/ThemeBuilderDialog";
import { SetupView } from "./components/SetupView";
import { Tabs, type TabItem } from "./components/Tabs";
import {
  Dialog,
  DialogContent,
  DialogTitle,
} from "./components/ui";
import { useAlfred } from "./hooks/useAlfred";
import { useDesktopRoute } from "./hooks/useDesktopRoute";
import { supportsNativeActions } from "./api/client";
import { loadSetupStatus } from "./api/setup";
import { isSetupComplete } from "./lib/setupCompletion";
import { FLEET_SUBTABS, PRIMARY_TABS } from "./lib/primaryTabs";
import {
  type CustomRosterNames,
  editableAgents,
  type EditableAgentSource,
  normalizeCodename,
} from "./lib/agentThemes";
import { scheduleRoleLabelForEditor } from "./lib/agentRoster";
import type { OperatorKey, RequestThreadModel, TabKey } from "./lib/uiTypes";
import { useRosterTheme } from "./lib/useRosterTheme";
import { useTheme } from "./lib/useTheme";

function App() {
  const { fleetTab, setFleetTab, setSetupMode, setTab, setupMode, tab } =
    useDesktopRoute();
  // A request opened as a lifecycle thread (from Inbox shipped cards).
  const [openThread, setOpenThread] = useState<RequestThreadModel | null>(null);

  // An agent card can deep-link into the Activity live-tail for one agent.
  const [logsFocus, setLogsFocus] = useState<{ agent: string | null; nonce: number }>({
    agent: null,
    nonce: 0,
  });
  // Navigation router. Legacy callers may still say "logs" or "lessons"; the
  // route hook maps both into Agents subtabs.
  const goTo = useCallback((key: TabKey) => {
    // Agents opens on the role roster. Lessons and Activity remain subtabs.
    if (key === "fleet") {
      setFleetTab("fleet");
    }
    setTab(key);
  }, [setTab, setFleetTab]);

  const viewAgentLogs = (codename: string) => {
    setLogsFocus((prev) => ({ agent: codename, nonce: prev.nonce + 1 }));
    setFleetTab("logs");
  };

  const {
    baseUrl,
    snapshot,
    error,
    errorRaw,
    loading,
    busyPlanAction,
    busyMemoryAction,
    busyTrustedUser,
    busyQueue,
    noticeFor,
    nativeBusy,
    nativeResult,
    nativeError,
    nativeErrorRaw,
    clearNativeResult,
    needsYou,
    fleetService,
    feed,
    unseenCount,
    seenIds,
    markActivitySeen,
    shipped,
    shippedState,
    shippedError,
    refreshShipped,
    usage,
    usageState,
    refresh,
    runFollowupAction,
    runPlanDecision,
    runPlanDiscard,
    runPlanIssueFile,
    runQueueAction,
    runMemoryCandidateAction,
    addTrustedUser,
    removeTrustedUser,
    runLocalAction,
    installCore,
    startRuntime,
  } = useAlfred();

  const { theme, toggle: toggleTheme, themeName, setThemeName, mode, setMode } =
    useTheme();
  const { rosterTheme, customNames, setRosterTheme, saveCustomNames, saveError: rosterSaveError } =
    useRosterTheme(baseUrl);
  const [customThemeEditorOpen, setCustomThemeEditorOpen] = useState(false);
  // The "Name your team" chat surface, and a proposed roster it hands off to the
  // editor. When `themeProposal` is set, the CustomThemeEditor opens pre-filled
  // with the proposal (an editable preview) instead of the persisted names.
  const [themeBuilderOpen, setThemeBuilderOpen] = useState(false);
  const [themeProposal, setThemeProposal] = useState<CustomRosterNames | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  // The Setup tab splits into Setup (get Alfred running) and Settings (appearance
  // and preferences), so theme selection no longer crowds the onboarding flow.
  const [settingsTab, setSettingsTab] = useState<"setup" | "settings">("setup");

  // ⌘K / Ctrl+K opens the command palette anywhere.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    window.scrollTo({ top: 0, left: 0, behavior: "auto" });
  }, [tab, fleetTab, setupMode]);

  // First-run routing. On boot, land the user in the ONBOARDING takeover unless
  // the local Alfred setup is actually complete. Two cases route to onboarding:
  //
  //   1. No `alfred serve` reachable (fresh machine, runtime not up): the initial
  //      load settles with a connection error and no snapshot. Route to the
  //      wizard so the user has an obvious next step instead of an empty Home
  //      behind an error banner.
  //   2. Runtime reachable but setup NOT complete (fresh/re-install: no engine
  //      detected, GitHub not connected, or no repository selected). We fetch the
  //      real server setup state (/api/setup/status) once and route to onboarding
  //      when isSetupComplete() is false. This is based on substantive
  //      completion, not the mere presence of a runtime directory, so a
  //      half-initialised install still lands in onboarding.
  //
  // A returning user with a completed setup boots straight to the Inbox. The
  // decision fires exactly once per launch: once we route (or confirm complete)
  // we never yank the user out of wherever they navigate next. It is NOT seeded
  // from the persisted base URL, so a re-install that left a stored URL but an
  // incomplete setup is still routed to onboarding.
  const routeToOnboarding = useCallback(() => {
    setSetupMode("guided");
    setTab("settings");
    setSettingsTab("setup");
  }, [setSetupMode, setTab, setSettingsTab]);
  // The decision is made exactly once per launch and tracked in a ref, NOT in
  // state: routing changes the tab, which re-renders and would re-run this
  // effect. A state guard plus an effect-cleanup cancel would cancel the
  // in-flight setup-status fetch the moment the guard flipped, so the async
  // branch could never route. The ref lets the effect no-op on re-run while the
  // original fetch resolves and routes freely.
  const firstRunDecided = useRef(false);
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);
  useEffect(() => {
    if (firstRunDecided.current) return;
    // Wait for the initial load to settle before deciding anything.
    if (loading) return;

    if (error && !snapshot) {
      // Fresh machine, runtime not up yet: take the user to the wizard.
      firstRunDecided.current = true;
      routeToOnboarding();
      return;
    }

    if (snapshot && !error) {
      // Runtime reachable: consult the real setup-completion state once. Only an
      // affirmatively-complete setup skips onboarding and lands on the Inbox.
      firstRunDecided.current = true;
      void loadSetupStatus(baseUrl)
        .then((status) => {
          if (!mountedRef.current) return;
          if (!isSetupComplete(status)) {
            routeToOnboarding();
          }
          // Complete setup: leave the default route (Inbox) untouched.
        })
        .catch(() => {
          // If we cannot read setup state, do not force onboarding on a user who
          // is otherwise connected; leave them on the default route.
        });
    }
  }, [loading, error, snapshot, baseUrl, routeToOnboarding]);

  const commands = useMemo<Command[]>(() => {
    const nav: Command[] = PRIMARY_TABS.map((item) => ({
      id: `go-${item.key}`,
      label: `Go to ${item.label}`,
      hint: "Navigate",
      icon: item.icon,
      run: () => goTo(item.key),
    }));
    return [
      ...nav,
      { id: "refresh", label: "Refresh agent state", hint: "Action", icon: RefreshCw, run: () => void refresh() },
      {
        id: "theme",
        label: `Switch to ${theme === "dark" ? "light" : "dark"} theme`,
        hint: "Appearance",
        icon: theme === "dark" ? Sun : Moon,
        run: toggleTheme,
      },
    ];
  }, [goTo, refresh, theme, toggleTheme]);

  const customThemeAgents = useMemo(() => {
    const byCodename = new Map<string, EditableAgentSource>();
    for (const run of snapshot?.schedule ?? []) {
      byCodename.set(normalizeCodename(run.codename), {
        codename: run.codename,
        displayName: run.display_name,
        roleLabel: scheduleRoleLabelForEditor({
          codename: run.codename,
          role: run.role,
          roleTitle: run.role_title,
        }),
        roleTitle: run.role_title || run.role,
        purpose: run.purpose,
      });
    }
    for (const agent of snapshot?.status.agents ?? []) {
      const key = normalizeCodename(agent.codename);
      const scheduled = byCodename.get(key);
      byCodename.set(key, {
        codename: agent.codename,
        displayName: agent.display_name ?? scheduled?.displayName,
        roleLabel: agent.role_title ?? scheduled?.roleLabel,
        roleTitle: agent.role_title ?? scheduled?.roleTitle,
        purpose: agent.purpose ?? scheduled?.purpose,
      });
    }
    return editableAgents(Array.from(byCodename.values()));
  }, [snapshot?.schedule, snapshot?.status.agents]);

  return (
    <AppShell
      baseUrl={baseUrl}
      error={error}
      loading={loading}
      navItems={PRIMARY_TABS}
      onCommand={() => setPaletteOpen(true)}
      onNavigate={goTo}
      onRefresh={() => void refresh()}
      onToggleTheme={toggleTheme}
      snapshot={snapshot}
      tab={tab}
      theme={theme}
      unseenCount={unseenCount}
    >

      {error ? (
        <ConnectionBanner
          error={error}
          errorRaw={errorRaw}
          nativeBusy={nativeBusy}
          onInstallCore={installCore}
          onStartRuntime={startRuntime}
        />
      ) : null}

      <NativeResultPanel
        error={nativeError}
        errorRaw={nativeErrorRaw}
        result={nativeResult}
        onDismiss={clearNativeResult}
      />

      {tab === "home" ? (
        <ReviewView
          snapshot={snapshot}
          needsYou={needsYou}
          shipped={shipped}
          usage={usage}
          usageState={usageState}
          onSwitch={goTo}
          onOpenThread={setOpenThread}
          onPlanDecision={runPlanDecision}
          busyPlanAction={busyPlanAction}
          rosterTheme={rosterTheme}
          customNames={customNames}
        />
      ) : null}
      {tab === "pipeline" ? (
        <section className="board-page">
          <PipelineView
            board={shipped}
            state={shippedState}
            error={shippedError}
            plans={snapshot?.plans || []}
            busyPlanAction={busyPlanAction}
            busyQueue={busyQueue}
            notice={noticeFor("board") || noticeFor("plans")}
            onRefresh={() => void refreshShipped()}
            onQueueAction={runQueueAction}
            onDecision={runPlanDecision}
            onDiscardPlan={runPlanDiscard}
            onFileIssue={runPlanIssueFile}
            onFollowupAction={runFollowupAction}
            rosterTheme={rosterTheme}
            customNames={customNames}
          />
        </section>
      ) : null}
      {tab === "compose" ? (
        <ComposeView
          baseUrl={baseUrl}
          selectedRepos={snapshot?.status.setup_repos?.selected || shipped?.repos || []}
          onSwitch={goTo}
        />
      ) : null}
      {tab === "settings" ? (
        <section className="settings-page space-y-4" aria-label="Setup and settings">
          <div className="space-y-1">
            <h1 className="font-heading text-2xl font-medium tracking-normal text-foreground">
              {settingsTab === "settings" ? "Settings" : "Setup"}
            </h1>
            <p className="max-w-2xl text-sm text-muted-foreground">
              {settingsTab === "settings"
                ? "Tune how Alfred looks and behaves on this Mac."
                : "Connect local tools, choose repos, and get Alfred running."}
            </p>
          </div>
          <Tabs
            tabs={
              [
                { key: "setup", label: "Setup" },
                { key: "settings", label: "Settings" },
              ] as TabItem<"setup" | "settings">[]
            }
            active={settingsTab}
            onChange={setSettingsTab}
            idBase="settings"
            ariaLabel="Setup and settings sections"
          />
          {settingsTab === "settings" ? (
            <section className="alfred-page-hero px-4 py-4" aria-label="Appearance">
              <div className="space-y-1">
                <p className="text-[0.68rem] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                  Appearance
                </p>
                <h2 className="font-heading text-lg font-medium text-foreground">
                  Theme and mode
                </h2>
                <p className="max-w-2xl text-sm text-muted-foreground">
                  Choose how Alfred looks on this Mac.
                </p>
              </div>
              <div className="mt-3">
                <AppearancePicker
                  themeName={themeName}
                  mode={mode}
                  onSelectTheme={setThemeName}
                  onSelectMode={setMode}
                />
              </div>
            </section>
          ) : setupMode === "advanced" ? (
            <section className="setup-mode-stack">
              <button
                className="secondary-button setup-mode-back"
                type="button"
                onClick={() => setSetupMode("guided")}
              >
                <span>Back to guided setup</span>
              </button>
              <SetupView
                baseUrl={baseUrl}
                loading={loading}
                connected={Boolean(snapshot) && !error}
                actionNotice={noticeFor("setup")}
                trustedSlack={snapshot?.trustedSlack || null}
                busyTrustedUser={busyTrustedUser}
                nativeBusy={nativeBusy}
                onAddTrustedUser={addTrustedUser}
                onRemoveTrustedUser={removeTrustedUser}
                onRunLocalAction={runLocalAction}
                onInstallCore={installCore}
                onStartRuntime={startRuntime}
                onConnectServer={(url) => void refresh(url)}
              />
            </section>
          ) : (
            <OnboardingView
              baseUrl={baseUrl}
              loading={loading}
              connected={Boolean(snapshot) && !error}
              canRun={supportsNativeActions()}
              nativeBusy={nativeBusy}
              nativeResult={nativeResult}
              rosterTheme={rosterTheme}
              customNames={customNames}
              rosterSaveError={rosterSaveError}
              onConnectServer={(url) => void refresh(url)}
              onInstallCore={installCore}
              onStartRuntime={startRuntime}
              onRunLocalAction={runLocalAction}
              onRosterThemeChange={setRosterTheme}
              onEditCustomTheme={() => setCustomThemeEditorOpen(true)}
              onSaveCustomNames={saveCustomNames}
              onOpenConnection={() => {
                setSetupMode("advanced");
              }}
              onSwitch={goTo}
              onRefreshBoard={(options) => refreshShipped(baseUrl, options)}
            />
          )}
        </section>
      ) : null}

      {tab === "fleet" ? (
        <section className="agents-page space-y-4" aria-label="Agents">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <div className="space-y-1">
              <h1 className="font-heading text-2xl font-medium tracking-normal text-foreground">
                Agents
              </h1>
              <p className="max-w-2xl text-sm text-muted-foreground">
                Understand roles, run agents manually, tune cadence, and inspect
                what the fleet learned.
              </p>
            </div>
            {fleetTab === "fleet" ? (
              <RosterThemePicker
                value={rosterTheme}
                onChange={setRosterTheme}
                onEditCustom={() => {
                  setThemeProposal(null);
                  setCustomThemeEditorOpen(true);
                }}
                onNameYourTeam={() => setThemeBuilderOpen(true)}
                saveError={rosterSaveError}
              />
            ) : null}
          </div>
          <Tabs
            tabs={FLEET_SUBTABS.map<TabItem<OperatorKey>>((s) => ({
              key: s.key,
              label: s.label,
              badge: s.key === "logs" && unseenCount > 0 ? unseenCount : null,
            }))}
            active={fleetTab}
            onChange={setFleetTab}
            idBase="fleet"
            ariaLabel="Agent sections"
          />
          {fleetTab === "fleet" ? (
            <div className="space-y-4 motion-fade" key="fleet-roster">
              <CustomAgentsPanel baseUrl={baseUrl} onChanged={() => void refresh()} />
              <FleetControlView
                agents={snapshot?.status.agents || []}
                schedule={snapshot?.schedule || []}
                service={fleetService}
                nativeBusy={nativeBusy}
                rosterTheme={rosterTheme}
                customNames={customNames}
                onRunLocalAction={runLocalAction}
                onViewLogs={viewAgentLogs}
              />
            </div>
          ) : null}
          {fleetTab === "logs" ? (
            <LogsView
              baseUrl={baseUrl}
              feed={feed}
              unseen={unseenCount}
              seen={seenIds}
              onMarkAllSeen={markActivitySeen}
              onOpenMemory={() => goTo("lessons")}
              firings={snapshot?.firings || []}
              focus={logsFocus}
            />
          ) : null}
          {fleetTab === "lessons" ? (
            <section className="space-y-4 motion-fade" aria-label="Lessons">
              <MemoryView
                snapshot={snapshot}
                actionNotice={noticeFor("memory")}
                busyMemoryAction={busyMemoryAction}
                nativeBusy={nativeBusy}
                onMemoryCandidateAction={runMemoryCandidateAction}
                onRunLocalAction={runLocalAction}
              />
            </section>
          ) : null}
        </section>
      ) : null}

      {/* A request opened as a lifecycle thread from a Home shipped card. */}
      {openThread ? (
        <ThreadModal thread={openThread} onClose={() => setOpenThread(null)} onOpenPlan={() => {
          setOpenThread(null);
          goTo("pipeline");
        }} />
      ) : null}

      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        commands={commands}
      />

      <ThemeBuilderDialog
        open={themeBuilderOpen}
        baseUrl={baseUrl}
        onOpenChange={setThemeBuilderOpen}
        onPropose={(names) => {
          // A proposed team pre-fills the editor as an editable preview; the
          // person tweaks + confirms there, which saves via saveCustomNames.
          setThemeProposal(names);
          setCustomThemeEditorOpen(true);
        }}
        onManualEdit={() => {
          setThemeProposal(null);
          setCustomThemeEditorOpen(true);
        }}
      />

      <CustomThemeEditor
        open={customThemeEditorOpen}
        // A live proposal from the chat wins as the editable preview; otherwise
        // the editor opens with the persisted custom names.
        value={themeProposal ?? customNames}
        agents={customThemeAgents}
        saveError={rosterSaveError}
        onOpenChange={(next) => {
          setCustomThemeEditorOpen(next);
          // Drop the one-shot proposal once the editor closes so the next manual
          // open starts from the persisted names, not a stale proposal.
          if (!next) setThemeProposal(null);
        }}
        onSave={saveCustomNames}
      />
    </AppShell>
  );
}

// A focused modal that shows a single request as a lifecycle thread, opened
// from an Inbox shipped card. Read-only: it deep-links to GitHub and to the plan
// sign-off, never embedding a diff or merge UI.
function ThreadModal({
  thread,
  onClose,
  onOpenPlan,
}: {
  thread: RequestThreadModel;
  onClose: () => void;
  onOpenPlan: () => void;
}) {
  return (
    <Dialog open onOpenChange={(next) => !next && onClose()}>
      <DialogContent
        className="thread-modal"
        aria-label="Request thread"
      >
        <DialogTitle className="sr-only">Request thread</DialogTitle>
        <RequestThread thread={thread} onOpenPlan={onOpenPlan} />
      </DialogContent>
    </Dialog>
  );
}

export default App;
