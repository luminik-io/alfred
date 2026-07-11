import { Ban, Check, ExternalLink, X } from "lucide-react";

import {
  agentForShipped,
  boardCardChip,
  planChip,
  type BoardColumn,
} from "../../lib/chips";
import {
  type CustomRosterNames,
  resolveThemedIdentity,
  type RosterThemeId,
} from "../../lib/agentThemes";
import { planNeedsAttention } from "../../lib/derive";
import { openExternal } from "../../lib/links";
import type { PlanDecision, PlanDraft, ShippedCard } from "../../types";
import { LifecycleCard } from "../LifecycleCard";
import {
  cardOutcome,
  planCanDiscard,
  repoChips,
  splitReposFull,
  type QueueActionHandler,
} from "./types";

// A local planning / compose draft awaiting the operator, rendered in the
// "Needs your go-ahead" column. Offers an inline Approve and a quiet hover
// Discard for junk drafts.
export function PlanLifecycleCard({
  plan,
  revisions,
  busyPlanAction,
  selected,
  onSelect,
  onDecision,
  onDiscardPlan,
}: {
  plan: PlanDraft;
  revisions: number;
  busyPlanAction: string | null;
  selected: boolean;
  onSelect: () => void;
  onDecision: (plan: PlanDraft, decision: PlanDecision) => void;
  onDiscardPlan?: (plan: PlanDraft) => void;
}) {
  const canDecide = planNeedsAttention(plan);
  const actionBusy = busyPlanAction?.startsWith(`${plan.plan_id}:`) || false;
  const repos = repoChips(splitReposFull(plan.affected_repos));
  const outcome = revisions > 1 ? `${plan.title} (${revisions} revisions)` : plan.title;
  // A quiet Discard on the card face for junk working drafts, revealed on hover
  // or keyboard focus (like the queued Hold action), so a dead-end draft can be
  // cleared without opening the detail sheet.
  const discardable = Boolean(onDiscardPlan) && planCanDiscard(plan);
  const hoverActions =
    discardable && onDiscardPlan ? (
      <button
        className="card-hover-action"
        type="button"
        disabled={actionBusy}
        title="Discard this draft"
        aria-label="Discard this draft"
        onClick={() => onDiscardPlan(plan)}
      >
        <X size={14} aria-hidden="true" />
      </button>
    ) : null;
  return (
    <LifecycleCard
      chip={planChip(plan)}
      repos={repos}
      age={plan.updated_at}
      outcome={outcome}
      selected={selected}
      onSelect={onSelect}
      hoverActions={hoverActions}
      action={
        canDecide ? (
          <button
            className="approve-button"
            type="button"
            disabled={actionBusy}
            onClick={() => onDecision(plan, "approve")}
          >
            <Check size={15} aria-hidden="true" />
            <span>Approve</span>
          </button>
        ) : null
      }
    />
  );
}

export function BoardLifecycleCard({
  card,
  column,
  selected,
  onSelect,
  canQueue,
  busyQueue,
  onQueueAction,
  rosterTheme,
  customNames,
}: {
  card: ShippedCard;
  column: BoardColumn;
  selected: boolean;
  onSelect: () => void;
  canQueue?: boolean;
  busyQueue?: string | null;
  onQueueAction?: QueueActionHandler;
  rosterTheme: RosterThemeId;
  customNames: CustomRosterNames;
}) {
  // Resolve the detected codename through the ACTIVE roster theme so the shipped
  // attribution reads the same themed name as the Roster page, not a hardcoded
  // architect-cast name.
  const codename = agentForShipped(card);
  const agent = codename
    ? resolveThemedIdentity({ codename }, rosterTheme, customNames).name
    : null;
  // A working PR still in draft is in review/verification, not merged. That is
  // representable straight from existing card data (kind + is_draft on the
  // working lane), so surface a small "In review" indicator; anything not
  // representable is skipped rather than fabricated.
  const inReview =
    column === "in_progress" && card.kind === "pr" && card.is_draft === true;
  // A gated plan (agent:plan-pending-approval) is a decision waiting on the
  // operator. The `queue` action strips the gate label (see lib/issue_queue.py),
  // so it IS the in-app "give the go-ahead" path: a card that says "Needs your
  // go-ahead" must let you actually give it, not just link to GitHub.
  const canGiveGoAhead =
    Boolean(canQueue) &&
    column === "awaiting_approval" &&
    card.kind === "issue" &&
    !card.demo &&
    !!card.number;
  const approving = busyQueue === `queue:${card.repo}#${card.number}`;
  const action =
    canGiveGoAhead && card.number && onQueueAction ? (
      <button
        className="approve-button"
        type="button"
        disabled={approving}
        onClick={() => onQueueAction(card.repo, card.number as number, "queue")}
      >
        <Check size={15} aria-hidden="true" />
        <span>{approving ? "Approving" : "Give go-ahead"}</span>
      </button>
    ) : column === "shipped" && card.url ? (
      <button
        className="secondary-button"
        type="button"
        onClick={() => void openExternal(card.url as string)}
      >
        <ExternalLink size={15} aria-hidden="true" />
        <span>Open PR</span>
      </button>
    ) : null;
  // A queued issue can be held without opening the sheet. Revealed on hover or
  // keyboard focus by LifecycleCard.
  const actionable =
    Boolean(canQueue) && column === "queued" && card.kind === "issue" && !card.demo && !!card.number;
  const holding = busyQueue === `hold:${card.repo}#${card.number}`;
  const hoverActions =
    actionable && card.number && onQueueAction ? (
      <button
        className="card-hover-action"
        type="button"
        disabled={holding}
        title={holding ? "Holding" : "Hold"}
        aria-label={holding ? "Holding this issue" : "Hold this issue"}
        onClick={() => onQueueAction(card.repo, card.number as number, "hold")}
      >
        <Ban size={14} aria-hidden="true" />
      </button>
    ) : null;
  const attribution =
    agent || inReview ? (
      <span className="board-attribution">
        {agent ? <AgentAvatar name={agent} /> : null}
        {agent ? <span className="board-attribution__name">{agent}</span> : null}
        {inReview ? (
          <span className="board-attribution__state" title="Pull request is in review">
            In review
          </span>
        ) : null}
      </span>
    ) : null;
  return (
    <LifecycleCard
      chip={boardCardChip(card, column)}
      repos={repoChips([card.repo])}
      age={card.timestamp}
      outcome={cardOutcome(card)}
      attribution={attribution}
      action={action}
      hoverActions={hoverActions}
      selected={selected}
      onSelect={onSelect}
    />
  );
}

// The per-card agent avatar chip: a monogram from the themed agent name, using
// the SAME resolved-identity name and the same monogram styling as the Roster
// page (charAt(0), --agent-accent). Purely decorative, so it is aria-hidden and
// the visible name beside it carries the attribution for assistive tech.
function AgentAvatar({ name }: { name: string }) {
  const monogram = name.trim().charAt(0).toUpperCase() || "A";
  return (
    <span className="board-avatar" aria-hidden="true">
      {monogram}
    </span>
  );
}
