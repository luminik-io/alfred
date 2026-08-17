import {
  Moon,
  RefreshCw,
  Sun,
} from "lucide-react";
import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  ConnectionBanner,
  NativeResultPanel,
} from "./components/atoms";
import { CommandPalette, type Command } from "./components/CommandPalette";
import { AppShell } from "./components/layout/AppShell";
import { OnboardingView } from "./components/OnboardingView";
import { RequestThread } from "./components/RequestThread";
import { ReviewView } from "./components/ReviewView";
import { CustomThemeEditor } from "./components/CustomThemeEditor";
import { RosterThemePicker } from "./components/RosterThemePicker";
import { ThemeBuilderDialog } from "./components/ThemeBuilderDialog";
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
import { listenAppMenuEvents } from "./lib/appMenuEvents";
import { FLEET_SUBTABS, PRIMARY_TABS } from "./lib/primaryTabs";
import {
  desktopScreenImporters,
  preloadAgentTab,
  preloadDesktopTab,
} from "./lib/desktopScreenLoaders";
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

const CodeIntelligenceView = lazy(async () => ({
  default: (await desktopScreenImporters.code()).CodeIntelligenceView,
}));
const ComposeView = lazy(async () => ({
  default: (await desktopScreenImporters.ask()).ComposeView,
}));
const CustomAgentsPanel = lazy(async () => ({
  default: (await desktopScreenImporters.customAgents()).CustomAgentsPanel,
}));
const FleetControlView = lazy(async () => ({
  default: (await desktopScreenImporters.agentRoster()).FleetControlView,
}));
const LogsView = lazy(async () => ({
  default: (await desktopScreenImporters.activity()).LogsView,
}));
const MemoryView = lazy(async () => ({
  default: (await desktopScreenImporters.learnings()).MemoryView,
}));
const PipelineView = lazy(async () => ({
  default: (await desktopScreenImporters.work()).PipelineView,
}));
const SettingsView = lazy(async () => ({
  default: (await desktopScreenImporters.settings()).SettingsView,
}));

function App() {
  const { fleetTab, setFleetTab, setTab, tab } = useDesktopRoute();
  // A request opened as a lifecycle thread (from Inbox shipped cards).
  const [openThread, setOpenThread] = useState<RequestThreadModel | null>(null);

  // An agent card can deep-link into the Activity live-tail for one agent.
  const [logsFocus, setLogsFocus] = useState<{ agent: string | null; nonce: number }>({
    agent: null,
    nonce: 0,
  });
  // Navigation router. Activity and Lessons are first-class Agents subtabs.
  const goTo = useCallback((key: TabKey) => {
    void preloadDesktopTab(key);
    // Agents opens on the role roster. Lessons and Activity remain subtabs.
    if (key === "fleet") {
      setFleetTab("fleet");
    }
    setTab(key);
  }, [setTab, setFleetTab]);

  const selectFleetTab = useCallback((key: OperatorKey) => {
    void preloadAgentTab(key);
    setFleetTab(key);
  }, [setFleetTab]);

  const viewAgentLogs = (codename: string) => {
    setLogsFocus((prev) => ({ agent: codename, nonce: prev.nonce + 1 }));
    selectFleetTab("logs");
  };

  const {
    baseUrl,
    snapshot,
    snapshotRevision,
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

  const { toggle: toggleTheme, themeName, setThemeName, mode, setMode } = useTheme();
  const { rosterTheme, customNames, setRosterTheme, saveCustomNames, saveError: rosterSaveError } =
    useRosterTheme(baseUrl);
  const [customThemeEditorOpen, setCustomThemeEditorOpen] = useState(false);
  // The "Name your team" chat surface, and a proposed roster it hands off to the
  // editor. When `themeProposal` is set, the CustomThemeEditor opens pre-filled
  // with the proposal (an editable preview) instead of the persisted names.
  const [themeBuilderOpen, setThemeBuilderOpen] = useState(false);
  const [themeProposal, setThemeProposal] = useState<CustomRosterNames | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [appStage, setAppStage] = useState<"checking" | "onboarding" | "ready">(
    "checking",
  );
  const [onboardingFinishing, setOnboardingFinishing] = useState(false);
  const onboardingFinishingRef = useRef(false);
  const setupGateBaseUrlRef = useRef(baseUrl);
  const setupConfirmedIncompleteRef = useRef(false);
  const lastSetupProbeSnapshotRef = useRef<typeof snapshot>(null);

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
    let disposed = false;
    let unlisten = () => {};
    void listenAppMenuEvents({
      onNavigate: (destination) => {
        if (appStage === "ready") goTo(destination);
      },
      onCommandPalette: () => {
        if (appStage === "ready") setPaletteOpen(true);
      },
      onRefresh: () => {
        if (appStage === "ready") void refresh();
      },
    })
      .then((off) => {
        if (disposed) {
          off();
        } else {
          unlisten = off;
        }
      })
      .catch(() => {
        // Native menus are secondary navigation. The in-app controls remain usable.
      });
    return () => {
      disposed = true;
      unlisten();
    };
  }, [appStage, goTo, refresh]);

  useEffect(() => {
    window.scrollTo({ top: 0, left: 0, behavior: "auto" });
  }, [tab, fleetTab]);

  // Boot into one of two product states: first-run takeover or the normal app.
  // The AppShell is not mounted until canonical setup readiness is affirmative,
  // so a fresh install never sees navigation for surfaces it cannot use yet.
  useEffect(() => {
    if (appStage === "ready") return;
    if (loading) return;

    if (setupGateBaseUrlRef.current !== baseUrl) {
      setupGateBaseUrlRef.current = baseUrl;
      setupConfirmedIncompleteRef.current = false;
      lastSetupProbeSnapshotRef.current = null;
    }

    if (error && !snapshot) {
      setAppStage("onboarding");
      return;
    }

    if (snapshot && !error) {
      if (setupConfirmedIncompleteRef.current) return;
      if (lastSetupProbeSnapshotRef.current === snapshot) return;
      lastSetupProbeSnapshotRef.current = snapshot;

      let cancelled = false;
      void loadSetupStatus(baseUrl)
        .then((status) => {
          if (cancelled) return;
          const complete = isSetupComplete(status);
          setupConfirmedIncompleteRef.current = !complete;
          setAppStage(complete ? "ready" : "onboarding");
        })
        .catch(() => {
          if (!cancelled) setAppStage("onboarding");
        });
      return () => {
        cancelled = true;
      };
    }
  }, [appStage, loading, error, snapshot, baseUrl]);

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
        label: `Switch to ${mode === "dark" ? "light" : "dark"} theme`,
        hint: "Appearance",
        icon: mode === "dark" ? Sun : Moon,
        run: toggleTheme,
      },
    ];
  }, [goTo, mode, refresh, toggleTheme]);

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

  const finishOnboarding = useCallback(
    async (destination: "home" | "compose") => {
      if (onboardingFinishingRef.current) return false;
      onboardingFinishingRef.current = true;
      setOnboardingFinishing(true);
      try {
        if (!(await refresh(baseUrl))) return false;
        setTab(destination);
        setAppStage("ready");
        return true;
      } finally {
        onboardingFinishingRef.current = false;
        setOnboardingFinishing(false);
      }
    },
    [baseUrl, refresh, setTab],
  );

  const customThemeEditor = (
    <CustomThemeEditor
      open={customThemeEditorOpen}
      value={themeProposal ?? customNames}
      agents={customThemeAgents}
      saveError={rosterSaveError}
      onOpenChange={(next) => {
        setCustomThemeEditorOpen(next);
        if (!next) setThemeProposal(null);
      }}
      onSave={saveCustomNames}
    />
  );

  if (appStage === "checking") {
    return <StartupGate />;
  }

  if (appStage === "onboarding") {
    return (
      <main className="alfred-first-run">
        <div className="alfred-app-atmosphere" aria-hidden="true" />
        <div className="alfred-window-drag-region" data-tauri-drag-region aria-hidden="true" />
        <div className="alfred-first-run__result">
          <NativeResultPanel
            error={nativeError}
            errorRaw={nativeErrorRaw}
            result={nativeResult}
            onDismiss={clearNativeResult}
          />
        </div>
        <OnboardingView
          baseUrl={baseUrl}
          loading={loading}
          connected={Boolean(snapshot) && !error}
          canRun={supportsNativeActions()}
          nativeBusy={nativeBusy}
          rosterTheme={rosterTheme}
          customNames={customNames}
          rosterSaveError={rosterSaveError}
          finishing={onboardingFinishing}
          onConnectServer={(url) => void refresh(url)}
          onInstallCore={installCore}
          onStartRuntime={startRuntime}
          onRunLocalAction={runLocalAction}
          onRosterThemeChange={setRosterTheme}
          onEditCustomTheme={() => setCustomThemeEditorOpen(true)}
          onSaveCustomNames={saveCustomNames}
          onFinish={finishOnboarding}
          onRefreshBoard={(options) => refreshShipped(baseUrl, options)}
        />
        {customThemeEditor}
      </main>
    );
  }

  return (
    <AppShell
      baseUrl={baseUrl}
      error={error}
      loading={loading}
      navItems={PRIMARY_TABS}
      onCommand={() => setPaletteOpen(true)}
      onNavigate={goTo}
      onNavigateIntent={(key) => void preloadDesktopTab(key)}
      onRefresh={() => void refresh()}
      onToggleTheme={toggleTheme}
      snapshot={snapshot}
      tab={tab}
      theme={mode}
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
          <Suspense fallback={<ScreenChunkFallback label="Loading Work" />}>
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
          </Suspense>
        </section>
      ) : null}
      {tab === "compose" ? (
        <Suspense fallback={<ScreenChunkFallback label="Loading Ask" />}>
          <ComposeView
            baseUrl={baseUrl}
            selectedRepos={snapshot?.status.setup_repos?.selected || shipped?.repos || []}
            onSwitch={goTo}
          />
        </Suspense>
      ) : null}
      {tab === "code" ? (
        <Suspense fallback={<ScreenChunkFallback label="Loading Code" />}>
          <CodeIntelligenceView baseUrl={baseUrl} />
        </Suspense>
      ) : null}
      {tab === "settings" ? (
        <Suspense fallback={<ScreenChunkFallback label="Loading Settings" />}>
          <SettingsView
            baseUrl={baseUrl}
            loading={loading}
            connected={Boolean(snapshot) && !error}
            actionNotice={noticeFor("setup")}
            trustedSlack={snapshot?.trustedSlack || null}
            busyTrustedUser={busyTrustedUser}
            nativeBusy={nativeBusy}
            themeName={themeName}
            mode={mode}
            onSelectTheme={setThemeName}
            onSelectMode={setMode}
            onAddTrustedUser={addTrustedUser}
            onRemoveTrustedUser={removeTrustedUser}
            onRunLocalAction={runLocalAction}
            onInstallCore={installCore}
            onStartRuntime={startRuntime}
            onConnectServer={(url) => void refresh(url)}
          />
        </Suspense>
      ) : null}

      {tab === "fleet" ? (
        <section className="agents-page space-y-4" aria-label="Agents">
          <header
            className="alfred-page-hero flex flex-col gap-3 px-4 py-4 sm:flex-row sm:items-end sm:justify-between"
            aria-label="Agents summary"
          >
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
          </header>
          <Tabs
            tabs={FLEET_SUBTABS.map<TabItem<OperatorKey>>((s) => ({
              key: s.key,
              label: s.label,
              badge: s.key === "logs" && unseenCount > 0 ? unseenCount : null,
            }))}
            active={fleetTab}
            onChange={selectFleetTab}
            onIntent={(key) => void preloadAgentTab(key)}
            idBase="fleet"
            ariaLabel="Agent sections"
          />
          {fleetTab === "fleet" ? (
            <Suspense fallback={<ScreenChunkFallback label="Loading Roster" />}>
              <div className="space-y-4 motion-fade" key="fleet-roster">
                <CustomAgentsPanel baseUrl={baseUrl} onChanged={() => void refresh()} />
                <FleetControlView
                  baseUrl={baseUrl}
                  modelRefreshVersion={snapshotRevision}
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
            </Suspense>
          ) : null}
          {fleetTab === "logs" ? (
            <Suspense fallback={<ScreenChunkFallback label="Loading Activity" />}>
              <LogsView
                baseUrl={baseUrl}
                feed={feed}
                unseen={unseenCount}
                seen={seenIds}
                onMarkAllSeen={markActivitySeen}
                onOpenMemory={() => goTo("lessons")}
                firings={snapshot?.firings || []}
                focus={logsFocus}
                sample={Boolean(shipped?.sample)}
              />
            </Suspense>
          ) : null}
          {fleetTab === "lessons" ? (
            <Suspense fallback={<ScreenChunkFallback label="Loading Learnings" />}>
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
            </Suspense>
          ) : null}
        </section>
      ) : null}

      {/* A request opened as a lifecycle thread from an Inbox shipped card. */}
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

      {customThemeEditor}
    </AppShell>
  );
}

function StartupGate() {
  return (
    <main className="alfred-startup" aria-busy="true">
      <div className="alfred-app-atmosphere" aria-hidden="true" />
      <div className="alfred-window-drag-region" data-tauri-drag-region aria-hidden="true" />
      <div className="alfred-startup__content" role="status">
        <span className="alfred-brand-mark size-11" aria-hidden="true">
          <img
            src="/brand/alfred-logo-transparent.png"
            alt=""
            className="alfred-brand-logo size-11 object-contain"
          />
        </span>
        <div>
          <strong>Alfred</strong>
          <span>Checking this Mac</span>
        </div>
      </div>
    </main>
  );
}

function ScreenChunkFallback({ label }: { label: string }) {
  return (
    <div
      className="flex min-h-48 items-center justify-center rounded-xl border border-border/60 bg-card/35 text-sm text-muted-foreground"
      role="status"
    >
      {label}
    </div>
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
