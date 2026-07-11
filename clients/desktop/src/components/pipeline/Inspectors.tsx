import { Ban, Check, ExternalLink, FilePlus2, GitPullRequest, MessageSquare, X } from "lucide-react";

import { exactTime, friendlyTime } from "../../format";
import type { BoardColumn } from "../../lib/chips";
import { planNeedsAttention } from "../../lib/derive";
import { firstLink, isSafeExternalUrl, openExternal } from "../../lib/links";
import type { FollowupAction } from "../../lib/uiTypes";
import type { PlanDecision, PlanDraft, ShippedCard } from "../../types";
import { Markdown } from "../Markdown";
import { cardOutcome, type QueueActionHandler } from "./types";

// The plan detail sheet body: metadata, the go/no-go decision block, file /
// discard / follow-up actions, and the rendered plan markdown.
export function PlanInspector({
  plan,
  busyPlanAction,
  onDecision,
  onDiscardPlan,
  onFileIssue,
  onFollowupAction,
}: {
  plan: PlanDraft;
  busyPlanAction: string | null;
  onDecision: (plan: PlanDraft, decision: PlanDecision) => void;
  onDiscardPlan: (plan: PlanDraft) => void;
  onFileIssue: (plan: PlanDraft) => void;
  onFollowupAction: (plan: PlanDraft, action: FollowupAction) => void;
}) {
  const parentLink = plan.parent && isSafeExternalUrl(plan.parent) ? plan.parent : null;
  const slackLink = firstLink(plan.content, /slack\.com/i);
  const canDecide = planNeedsAttention(plan);
  const canFileIssue =
    !parentLink &&
    plan.readiness_ok === true &&
    (plan.source === "compose" || plan.source === "planning");
  const canDiscardDraft =
    !parentLink &&
    (plan.source === "compose" || plan.source === "planning");
  const isFollowup = plan.source === "followup";
  const actionBusy = busyPlanAction?.startsWith(`${plan.plan_id}:`) || false;
  const discardLabel = plan.revision_count > 1 ? "Discard drafts" : "Discard draft";
  return (
    <div className="detail-panel detail-panel--sheet" aria-label="Selected plan details">
      <div className="detail-panel__head">
        <span>{plan.status}</span>
        <h3>{plan.title}</h3>
      </div>
      <dl className="compact-meta">
        {plan.affected_repos ? (
          <div>
            <dt>Repos</dt>
            <dd>{plan.affected_repos}</dd>
          </div>
        ) : null}
        {plan.updated_at ? (
          <div>
            <dt>Updated</dt>
            <dd title={exactTime(plan.updated_at)}>{friendlyTime(plan.updated_at)}</dd>
          </div>
        ) : null}
        {/* Dev-only: the raw readiness number survives in the panel, never on the card face. */}
        {plan.readiness_score !== null ? (
          <div>
            <dt>Readiness</dt>
            <dd>{plan.readiness_score}/100</dd>
          </div>
        ) : null}
        {/* Dev-only: the source is an internal routing detail, shown as origin here only. */}
        <div>
          <dt>Origin</dt>
          <dd>{plan.source}</dd>
        </div>
      </dl>
      {canDecide ? (
        <div className="plan-decision">
          <p className="plan-decision__note" role="note">
            Approving lets architect file this exact scope on its next run. Declining
            stops it. No code or worktrees move until you decide.
          </p>
          <div className="card-actions card-actions--start">
            <button
              className="approve-button"
              type="button"
              disabled={actionBusy}
              onClick={() => onDecision(plan, "approve")}
            >
              <Check size={16} aria-hidden="true" />
              <span>Approve plan</span>
            </button>
            <button
              className="decline-button"
              type="button"
              disabled={actionBusy}
              onClick={() => onDecision(plan, "decline")}
            >
              <X size={16} aria-hidden="true" />
              <span>Decline</span>
            </button>
          </div>
        </div>
      ) : null}
      <div className="card-actions card-actions--start">
        {canFileIssue ? (
          <button
            className="approve-button"
            type="button"
            disabled={actionBusy}
            onClick={() => onFileIssue(plan)}
          >
            <FilePlus2 size={16} aria-hidden="true" />
            <span>File GitHub issue</span>
          </button>
        ) : null}
        {canDiscardDraft ? (
          <button
            className="decline-button"
            type="button"
            disabled={actionBusy}
            onClick={() => onDiscardPlan(plan)}
          >
            <X size={16} aria-hidden="true" />
            <span>{discardLabel}</span>
          </button>
        ) : null}
        {isFollowup ? (
          <>
            <button
              className="approve-button"
              type="button"
              disabled={actionBusy}
              onClick={() => onFollowupAction(plan, "convert")}
            >
              <FilePlus2 size={16} aria-hidden="true" />
              <span>Plan next pass</span>
            </button>
            <button
              className="secondary-button"
              type="button"
              disabled={actionBusy}
              onClick={() => onFollowupAction(plan, "handled")}
            >
              <Check size={16} aria-hidden="true" />
              <span>Mark handled</span>
            </button>
          </>
        ) : null}
        {parentLink ? (
          <button className="secondary-button" type="button" onClick={() => void openExternal(parentLink)}>
            <GitPullRequest size={16} aria-hidden="true" />
            <span>Open issue</span>
          </button>
        ) : null}
        {slackLink ? (
          <button className="secondary-button" type="button" onClick={() => void openExternal(slackLink)}>
            <MessageSquare size={16} aria-hidden="true" />
            <span>Open in Slack</span>
          </button>
        ) : null}
      </div>
      <Markdown className="detail-md">
        {plan.content || plan.preview || "No plan body saved yet."}
      </Markdown>
    </div>
  );
}

// The board card detail sheet body: metadata plus the queue-state actions
// (give go-ahead, open on GitHub, hold, mark done) available for the card.
export function CardInspector({
  card,
  column,
  busyQueue,
  canQueue,
  onQueueAction,
}: {
  card: ShippedCard;
  column: BoardColumn;
  busyQueue?: string | null;
  canQueue: boolean;
  onQueueAction?: QueueActionHandler;
}) {
  const actionable =
    canQueue && column === "queued" && card.kind === "issue" && !card.demo && !!card.number;
  // A gated plan can be released from its detail too: `queue` strips the
  // approval gate (lib/issue_queue.py), which is the in-app go-ahead.
  const canGiveGoAhead =
    canQueue &&
    column === "awaiting_approval" &&
    card.kind === "issue" &&
    !card.demo &&
    !!card.number;
  const holding = busyQueue === `hold:${card.repo}#${card.number}`;
  const closing = busyQueue === `done:${card.repo}#${card.number}`;
  const approving = busyQueue === `queue:${card.repo}#${card.number}`;
  return (
    <div className="detail-panel detail-panel--sheet" aria-label="Selected pipeline item">
      <div className="detail-panel__head">
        <span>{card.repo}</span>
        <h3>{cardOutcome(card)}</h3>
      </div>
      <dl className="compact-meta">
        {card.timestamp ? (
          <div>
            <dt>Updated</dt>
            <dd title={exactTime(card.timestamp)}>{friendlyTime(card.timestamp)}</dd>
          </div>
        ) : null}
        {card.author ? (
          <div>
            <dt>Author</dt>
            <dd>{card.author}</dd>
          </div>
        ) : null}
      </dl>
      <div className="card-actions card-actions--start">
        {canGiveGoAhead && card.number ? (
          <button
            className="approve-button"
            type="button"
            disabled={approving}
            onClick={() => onQueueAction?.(card.repo, card.number as number, "queue")}
          >
            <Check size={16} aria-hidden="true" />
            <span>{approving ? "Approving" : "Give go-ahead"}</span>
          </button>
        ) : null}
        {card.url ? (
          <button className="secondary-button" type="button" onClick={() => void openExternal(card.url as string)}>
            <ExternalLink size={16} aria-hidden="true" />
            <span>Open on GitHub</span>
          </button>
        ) : null}
        {actionable && card.number ? (
          <>
            <button
              className="secondary-button"
              type="button"
              disabled={holding}
              onClick={() => onQueueAction?.(card.repo, card.number as number, "hold")}
            >
              <Ban size={16} aria-hidden="true" />
              <span>{holding ? "Holding" : "Hold"}</span>
            </button>
            <button
              className="secondary-button"
              type="button"
              disabled={closing}
              onClick={() => onQueueAction?.(card.repo, card.number as number, "done")}
            >
              <Check size={16} aria-hidden="true" />
              <span>{closing ? "Closing" : "Mark done"}</span>
            </button>
          </>
        ) : null}
      </div>
    </div>
  );
}
