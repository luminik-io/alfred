import {
  Ban,
  Check,
  CheckCircle2,
  CircleDotDashed,
  CircleX,
  ExternalLink,
  FileCode2,
  FilePlus2,
  GitCommitHorizontal,
  GitPullRequest,
  MessageSquare,
  X,
} from "lucide-react";

import { exactTime, friendlyTime } from "../../format";
import type { BoardColumn } from "../../lib/chips";
import { planNeedsAttention } from "../../lib/derive";
import { firstLink, isSafeExternalUrl, openExternal } from "../../lib/links";
import type { FollowupAction } from "../../lib/uiTypes";
import type { PlanDecision, PlanDraft, ShippedCard } from "../../types";
import { Markdown } from "../Markdown";
import { cardOutcome, type QueueActionHandler } from "./types";

const FAILED_CHECK_STATES = new Set([
  "ACTION_REQUIRED",
  "CANCELLED",
  "ERROR",
  "FAILURE",
  "STALE",
  "STARTUP_FAILURE",
  "TIMED_OUT",
]);
const PASSED_CHECK_STATES = new Set(["NEUTRAL", "SKIPPED", "SUCCESS"]);

function checkPriority(status: string): number {
  const normalized = status.toUpperCase();
  if (FAILED_CHECK_STATES.has(normalized)) return 0;
  if (PASSED_CHECK_STATES.has(normalized)) return 2;
  return 1;
}

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
  const parentLink =
    plan.parent && isSafeExternalUrl(plan.parent) ? plan.parent : null;
  const slackLink = firstLink(plan.content, /slack\.com/i);
  const canDecide = planNeedsAttention(plan);
  const canFileIssue =
    !parentLink &&
    plan.readiness_ok === true &&
    (plan.source === "compose" || plan.source === "planning");
  const canDiscardDraft =
    !parentLink && (plan.source === "compose" || plan.source === "planning");
  const isFollowup = plan.source === "followup";
  const actionBusy = busyPlanAction?.startsWith(`${plan.plan_id}:`) || false;
  const discardLabel =
    plan.revision_count > 1 ? "Discard drafts" : "Discard draft";
  return (
    <div
      className="detail-panel detail-panel--sheet"
      aria-label="Selected plan details"
    >
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
            <dd title={exactTime(plan.updated_at)}>
              {friendlyTime(plan.updated_at)}
            </dd>
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
            Approving lets architect file this exact scope on its next run.
            Declining stops it. No code or worktrees move until you decide.
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
          <button
            className="secondary-button"
            type="button"
            onClick={() => void openExternal(parentLink)}
          >
            <GitPullRequest size={16} aria-hidden="true" />
            <span>Open issue</span>
          </button>
        ) : null}
        {slackLink ? (
          <button
            className="secondary-button"
            type="button"
            onClick={() => void openExternal(slackLink)}
          >
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
    canQueue &&
    column === "queued" &&
    card.kind === "issue" &&
    !card.demo &&
    !!card.number;
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
  const reviewState =
    card.kind === "pr"
      ? column === "shipped"
        ? "Shipped"
        : card.is_draft
          ? "In review"
          : "Open PR"
      : column === "awaiting_approval"
        ? "Awaiting approval"
        : column === "queued"
          ? "Queued"
          : "Issue";
  const evidence = (card.agent_evidence || []).map((entry) => {
    const separator = entry.indexOf(":");
    if (separator < 0) return entry;
    const kind = entry.slice(0, separator);
    const value = entry.slice(separator + 1);
    return `${kind === "label" ? "Label" : kind === "branch" ? "Branch" : kind} ${value}`;
  });
  const github = card.github_evidence;
  const harnessLabel = card.labels.find((label) =>
    label.toLowerCase().startsWith("harness:"),
  );
  const harness = harnessLabel
    ? harnessLabel
        .slice("harness:".length)
        .replace(
          /(^|[-_])([a-z])/g,
          (_, space, letter: string) =>
            `${space ? " " : ""}${letter.toUpperCase()}`,
        )
    : null;
  const humanState = (value: string | null) =>
    value
      ? value
          .toLowerCase()
          .replace(/_/g, " ")
          .replace(/^./, (letter: string) => letter.toUpperCase())
      : "Not reported";
  const visibleChecks = github
    ? [...github.checks]
        .sort(
          (left, right) =>
            checkPriority(left.status) - checkPriority(right.status),
        )
        .slice(0, 6)
    : [];
  const visibleFiles = github?.changed_files.slice(0, 6) || [];
  const countLabel = (count: number, incomplete: boolean) =>
    `${count}${incomplete ? "+" : ""}`;
  return (
    <div
      className="detail-panel detail-panel--sheet"
      aria-label="Selected pipeline item"
    >
      <div className="detail-panel__head">
        <span>{card.repo}</span>
        <h3>{cardOutcome(card)}</h3>
      </div>
      <dl className="compact-meta">
        <div>
          <dt>State</dt>
          <dd>{reviewState}</dd>
        </div>
        {card.timestamp ? (
          <div>
            <dt>Updated</dt>
            <dd title={exactTime(card.timestamp)}>
              {friendlyTime(card.timestamp)}
            </dd>
          </div>
        ) : null}
        {card.author ? (
          <div>
            <dt>Author</dt>
            <dd>{card.author}</dd>
          </div>
        ) : null}
      </dl>
      {evidence.length ? (
        <section className="inspector-evidence" aria-label="Agent evidence">
          <h4>Agent evidence</h4>
          <ul>
            {evidence.map((entry) => (
              <li key={entry}>{entry}</li>
            ))}
          </ul>
        </section>
      ) : null}
      {github ? (
        <section
          className="inspector-evidence inspector-evidence--github"
          aria-label="GitHub evidence"
        >
          <h4>GitHub evidence</h4>
          <dl className="evidence-summary">
            {harness ? (
              <div>
                <dt>Harness</dt>
                <dd>{harness}</dd>
              </div>
            ) : null}
            {github.head_sha ? (
              <div>
                <dt>Head commit</dt>
                <dd className="evidence-mono" title={github.head_sha}>
                  {github.head_sha.slice(0, 12)}
                </dd>
              </div>
            ) : null}
            <div>
              <dt>Review</dt>
              <dd>{humanState(github.review_state)}</dd>
            </div>
            <div>
              <dt>Commits</dt>
              <dd>
                {countLabel(
                  github.commit_count,
                  github.commit_count_incomplete,
                )}
              </dd>
            </div>
            <div>
              <dt>Signature</dt>
              <dd>Not included</dd>
            </div>
          </dl>
          {visibleChecks.length ? (
            <div className="evidence-block" aria-label="Check results">
              <h5>Checks</h5>
              <ul>
                {visibleChecks.map((check) => {
                  const status = check.status.toUpperCase();
                  const passed = PASSED_CHECK_STATES.has(status);
                  const failed = FAILED_CHECK_STATES.has(status);
                  const state = passed
                    ? "passed"
                    : failed
                      ? "failed"
                      : "pending";
                  return (
                    <li
                      key={`${check.name}:${check.status}`}
                      data-state={state}
                    >
                      {passed ? (
                        <CheckCircle2 aria-hidden="true" />
                      ) : failed ? (
                        <CircleX aria-hidden="true" />
                      ) : (
                        <CircleDotDashed aria-hidden="true" />
                      )}
                      <span>{check.name}</span>
                      <small>{humanState(check.status)}</small>
                    </li>
                  );
                })}
              </ul>
              {github.check_count_incomplete ? (
                <p>
                  At least {github.checks.length - visibleChecks.length} more
                  checks on GitHub; this list is incomplete.
                </p>
              ) : github.checks.length > visibleChecks.length ? (
                <p>
                  {github.checks.length - visibleChecks.length} more checks on
                  GitHub
                </p>
              ) : null}
            </div>
          ) : null}
          {github.latest_reviews.length ? (
            <div className="evidence-block" aria-label="Latest reviews">
              <h5>Latest reviews</h5>
              <ul>
                {github.latest_reviews.slice(0, 4).map((review) => (
                  <li key={`${review.author}:${review.state}`}>
                    <GitCommitHorizontal aria-hidden="true" />
                    <span>{review.author}</span>
                    <small>{humanState(review.state)}</small>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </section>
      ) : null}
      {github && visibleFiles.length ? (
        <section
          className="inspector-evidence inspector-evidence--files"
          aria-label="Changed files"
        >
          <h4>
            Changed files{" "}
            <span>
              {countLabel(
                github.changed_file_count,
                github.changed_file_count_incomplete,
              )}
            </span>
          </h4>
          <ul>
            {visibleFiles.map((file) => (
              <li key={file}>
                <FileCode2 aria-hidden="true" />
                {file}
              </li>
            ))}
          </ul>
          {github.changed_file_count > visibleFiles.length ? (
            <p>
              {github.changed_file_count_incomplete ? "At least " : ""}
              {github.changed_file_count - visibleFiles.length} more files on
              GitHub
            </p>
          ) : null}
        </section>
      ) : null}
      <div className="card-actions card-actions--start">
        {canGiveGoAhead && card.number ? (
          <button
            className="approve-button"
            type="button"
            disabled={approving}
            onClick={() =>
              onQueueAction?.(card.repo, card.number as number, "queue")
            }
          >
            <Check size={16} aria-hidden="true" />
            <span>{approving ? "Approving" : "Give go-ahead"}</span>
          </button>
        ) : null}
        {card.url ? (
          <button
            className="secondary-button"
            type="button"
            onClick={() => void openExternal(card.url as string)}
          >
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
              onClick={() =>
                onQueueAction?.(card.repo, card.number as number, "hold")
              }
            >
              <Ban size={16} aria-hidden="true" />
              <span>{holding ? "Holding" : "Hold"}</span>
            </button>
            <button
              className="secondary-button"
              type="button"
              disabled={closing}
              onClick={() =>
                onQueueAction?.(card.repo, card.number as number, "done")
              }
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
