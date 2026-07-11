import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  RefreshCw,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { supportsMutations } from "../api/client";
import { exactTime, friendlyTime } from "../format";
import { type BoardColumn } from "../lib/chips";
import {
  type CustomRosterNames,
  DEFAULT_ROSTER_THEME,
  EMPTY_CUSTOM_NAMES,
  type RosterThemeId,
} from "../lib/agentThemes";
import {
  dedupePlans,
  isLowSignalPlan,
} from "../lib/derive";
import type { ActionNotice, FollowupAction } from "../lib/uiTypes";
import type {
  PlanDecision,
  PlanDraft,
  ShippedBoard,
} from "../types";
import { EmptyState } from "./atoms";
import { Button } from "./ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "./ui/sheet";
import { BoardLifecycleCard, PlanLifecycleCard } from "./pipeline/LifecycleCards";
import { CardInspector, PlanInspector } from "./pipeline/Inspectors";
import { PipelineColumn } from "./pipeline/PipelineColumn";
import { QueueComposer } from "./pipeline/QueueComposer";
import { cardKey, type QueueActionHandler } from "./pipeline/types";

// A unified selection key so the detail panel can address either a plan or a
// board card without a shared id space.
type Selection =
  | { kind: "plan"; id: string }
  | { kind: "card"; key: string };

const BOARD_COLUMNS: Array<{
  key: BoardColumn;
  label: string;
  lane: "queued" | "working" | "shipped";
}> = [
  { key: "queued", label: "Queued", lane: "queued" },
  { key: "in_progress", label: "Working now", lane: "working" },
  { key: "shipped", label: "Shipped", lane: "shipped" },
];

export function PipelineView({
  board,
  state,
  error,
  plans,
  busyPlanAction,
  busyQueue,
  notice,
  onRefresh,
  onQueueAction,
  onDecision,
  onDiscardPlan,
  onFileIssue,
  onFollowupAction,
  rosterTheme = DEFAULT_ROSTER_THEME,
  customNames = EMPTY_CUSTOM_NAMES,
}: {
  board: ShippedBoard | null;
  state: "idle" | "loading" | "error";
  error?: string | null;
  plans: PlanDraft[];
  busyPlanAction: string | null;
  busyQueue?: string | null;
  notice?: ActionNotice;
  onRefresh?: () => void;
  onQueueAction?: QueueActionHandler;
  onDecision: (plan: PlanDraft, decision: PlanDecision) => void;
  onDiscardPlan: (plan: PlanDraft) => void;
  onFileIssue: (plan: PlanDraft) => void;
  onFollowupAction: (plan: PlanDraft, action: FollowupAction) => void;
  rosterTheme?: RosterThemeId;
  customNames?: CustomRosterNames;
}) {
  const [selection, setSelection] = useState<Selection | null>(null);
  const [showLowSignal, setShowLowSignal] = useState(false);

  const loading = state === "loading";
  const columns = board?.columns;
  const hardError = board?.error;
  const loadError = state === "error" ? error || "Work refresh failed." : null;

  // Column 1: plans awaiting you. Dedupe identical drafts (issue 314) and tuck
  // low-signal drafts behind a disclosure so junk never crowds the column.
  const deduped = useMemo(() => dedupePlans(plans), [plans]);
  const visiblePlans = useMemo(
    () => deduped.filter((entry) => !isLowSignalPlan(entry.plan)),
    [deduped],
  );
  const lowSignal = useMemo(
    () => deduped.filter((entry) => isLowSignalPlan(entry.plan)),
    [deduped],
  );
  // GitHub issues gated on the operator's go-ahead (agent:plan-pending-approval)
  // are decisions too, not queued work. The server surfaces them in the
  // `awaiting_approval` lane; render them alongside the local plans so the
  // "Needs your go-ahead" count is honest instead of hiding a real backlog.
  const awaitingApproval = useMemo(
    () => columns?.awaiting_approval ?? [],
    [columns],
  );
  // The honest decision count: local plans waiting plus gated GitHub issues.
  const goAheadCount = visiblePlans.length + awaitingApproval.length;

  const selectedPlan =
    selection?.kind === "plan"
      ? plans.find((plan) => plan.plan_id === selection.id) || null
      : null;
  const selectedCard =
    selection?.kind === "card"
      ? [
          ...(columns?.queued || []),
          ...(columns?.in_progress || []),
          ...(columns?.shipped || []),
          ...awaitingApproval,
        ].find((card) => cardKey(card) === selection.key) || null
      : null;

  // Drop a stale selection when the underlying object disappears on refresh.
  useEffect(() => {
    if (selection?.kind === "plan" && !selectedPlan) setSelection(null);
    if (selection?.kind === "card" && !selectedCard) setSelection(null);
  }, [selection, selectedPlan, selectedCard]);

  const hasAnything =
    visiblePlans.length ||
    lowSignal.length ||
    awaitingApproval.length ||
    (columns &&
      (columns.queued.length || columns.in_progress.length || columns.shipped.length));

  // Queue actions (assign / give-go-ahead / hold / done) are token-gated HTTP
  // writes to /api/queue, which succeed from the Tauri shell AND the browser
  // shell served by `alfred serve`. Gate on `supportsMutations()`, not
  // `supportsNativeActions()`, so the hosted browser build is not left with a
  // read-only board it can never act on.
  const canQueue = Boolean(onQueueAction) && supportsMutations();
  const generatedAt = board?.generated_at;
  const status = loadError
    ? "Work refresh failed."
    : hardError
      ? "Couldn't reach GitHub. Check gh auth."
      : generatedAt
        ? `updated ${friendlyTime(generatedAt)}`
        : null;

  return (
    <section className="alfred-pipeline" aria-label="Work">
      <section className="alfred-page-hero px-4 py-4" aria-label="Work summary">
        <div className="relative flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
          <div className="min-w-0 space-y-1">
            <h1 className="font-heading text-2xl font-medium tracking-normal text-foreground">
              Work
            </h1>
            <p className="max-w-3xl text-sm text-muted-foreground">
              One lifecycle: plans you approve become queued work, then runs in
              flight, then shipped outcomes.
            </p>
          </div>
          <div className="flex items-center gap-2">
            {status ? (
              <span className="text-xs text-muted-foreground" title={generatedAt ? exactTime(generatedAt) : undefined}>
                {status}
              </span>
            ) : null}
            {onRefresh ? (
              <Button
                variant="ghost"
                size="icon-sm"
                type="button"
                onClick={onRefresh}
                disabled={loading}
                aria-label="Refresh pipeline"
              >
                <RefreshCw
                  size={15}
                  aria-hidden="true"
                  className={loading ? "animate-spin" : undefined}
                />
              </Button>
            ) : null}
          </div>
        </div>
      </section>

      {notice ? (
        <div className={`inline-notice inline-notice--${notice.tone}`}>
          {notice.tone === "ok" ? (
            <CheckCircle2 size={18} aria-hidden="true" />
          ) : (
            <AlertTriangle size={18} aria-hidden="true" />
          )}
          <span>{notice.message}</span>
        </div>
      ) : null}

      {canQueue && onQueueAction ? (
        <QueueComposer onQueueAction={onQueueAction} busy={Boolean(busyQueue)} />
      ) : null}

      {hardError && !hasAnything ? (
        <div className="inline-notice inline-notice--error">
          <AlertTriangle size={18} aria-hidden="true" />
          <span>
            Alfred reached the runtime but the pipeline failed to build ({hardError}).
            Check <code>gh auth status</code> and the watched-repo config.
          </span>
        </div>
      ) : null}

      {!loading && !hasAnything && !hardError ? (
        <EmptyState
          title="Nothing in the pipeline yet."
          body="When you ask Alfred for something, it appears here first as a plan for you to approve, then as work in progress, then as shipped."
        />
      ) : (
        <div className="alfred-pipeline__columns motion-rise">
          <PipelineColumn label="Needs your go-ahead" count={goAheadCount} lane="needs">
            {visiblePlans.map((entry) => (
              <PlanLifecycleCard
                key={entry.plan.plan_id}
                plan={entry.plan}
                revisions={entry.revisions}
                busyPlanAction={busyPlanAction}
                selected={selection?.kind === "plan" && selection.id === entry.plan.plan_id}
                onSelect={() => setSelection({ kind: "plan", id: entry.plan.plan_id })}
                onDecision={onDecision}
                onDiscardPlan={onDiscardPlan}
              />
            ))}
            {awaitingApproval.map((card) => (
              <BoardLifecycleCard
                key={cardKey(card)}
                card={card}
                column="awaiting_approval"
                selected={selection?.kind === "card" && selection.key === cardKey(card)}
                onSelect={() => setSelection({ kind: "card", key: cardKey(card) })}
                canQueue={canQueue}
                busyQueue={busyQueue}
                onQueueAction={onQueueAction}
                rosterTheme={rosterTheme}
                customNames={customNames}
              />
            ))}
            {lowSignal.length ? (
              <div className="alfred-pipeline__lowsignal">
                <button
                  type="button"
                  className="alfred-pipeline__lowsignal-toggle"
                  aria-expanded={showLowSignal}
                  onClick={() => setShowLowSignal((open) => !open)}
                >
                  <ChevronDown
                    size={14}
                    aria-hidden="true"
                    className={showLowSignal ? "rotate-180 transition-transform" : "transition-transform"}
                  />
                  {showLowSignal ? "Hide low signal" : `${lowSignal.length} low signal`}
                </button>
                {showLowSignal
                  ? lowSignal.map((entry) => (
                      <PlanLifecycleCard
                        key={entry.plan.plan_id}
                        plan={entry.plan}
                        revisions={entry.revisions}
                        busyPlanAction={busyPlanAction}
                        selected={selection?.kind === "plan" && selection.id === entry.plan.plan_id}
                        onSelect={() => setSelection({ kind: "plan", id: entry.plan.plan_id })}
                        onDecision={onDecision}
                        onDiscardPlan={onDiscardPlan}
                      />
                    ))
                  : null}
              </div>
            ) : null}
            {!visiblePlans.length && !lowSignal.length && !awaitingApproval.length ? (
              <p className="alfred-pipeline__empty">No plans waiting on you.</p>
            ) : null}
          </PipelineColumn>

          {BOARD_COLUMNS.map((col) => {
            const cards = columns?.[col.key] || [];
            return (
              <PipelineColumn key={col.key} label={col.label} count={cards.length} lane={col.lane}>
                {cards.length ? (
                  cards.map((card) => (
                    <BoardLifecycleCard
                      key={cardKey(card)}
                      card={card}
                      column={col.key}
                      selected={selection?.kind === "card" && selection.key === cardKey(card)}
                      onSelect={() => setSelection({ kind: "card", key: cardKey(card) })}
                      canQueue={canQueue}
                      busyQueue={busyQueue}
                      onQueueAction={onQueueAction}
                      rosterTheme={rosterTheme}
                      customNames={customNames}
                    />
                  ))
                ) : (
                  <p className="alfred-pipeline__empty">Nothing here yet.</p>
                )}
              </PipelineColumn>
            );
          })}
        </div>
      )}

      <Sheet
        open={Boolean(selectedPlan || selectedCard)}
        onOpenChange={(open) => {
          if (!open) setSelection(null);
        }}
      >
        <SheetContent className="plan-detail-sheet">
          <SheetHeader>
            <SheetTitle>
              {selectedPlan ? "Review plan" : "Work item"}
            </SheetTitle>
            <SheetDescription>
              {selectedPlan
                ? "Approve, file, or open the GitHub evidence."
                : "Open the GitHub record or change the queue state."}
            </SheetDescription>
          </SheetHeader>
          {selectedPlan ? (
            <PlanInspector
              plan={selectedPlan}
              busyPlanAction={busyPlanAction}
              onDecision={onDecision}
              onDiscardPlan={onDiscardPlan}
              onFileIssue={onFileIssue}
              onFollowupAction={onFollowupAction}
            />
          ) : null}
          {selectedCard ? (
            <CardInspector
              card={selectedCard}
              column={
                (columns?.shipped || []).some((c) => cardKey(c) === cardKey(selectedCard))
                  ? "shipped"
                  : (columns?.in_progress || []).some((c) => cardKey(c) === cardKey(selectedCard))
                    ? "in_progress"
                    : awaitingApproval.some((c) => cardKey(c) === cardKey(selectedCard))
                      ? "awaiting_approval"
                      : "queued"
              }
              busyQueue={busyQueue}
              canQueue={canQueue}
              onQueueAction={onQueueAction}
            />
          ) : null}
        </SheetContent>
      </Sheet>
    </section>
  );
}
