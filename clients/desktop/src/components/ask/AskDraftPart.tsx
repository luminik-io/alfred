import { CheckCircle2, ExternalLink } from "lucide-react";
import type { ReactNode } from "react";
import type { ToolCallMessagePartProps } from "@assistant-ui/react";

import { repoShortName } from "../../lib/chips";
import { openExternal } from "../../lib/links";
import { LifecycleCard, type RepoChip } from "../LifecycleCard";
import { useAskSurface } from "./AskContext";
import { cleanRepos, type DraftCardModel } from "./askModel";
import type { DraftToolArgs } from "./useAskThread";

function repoChipsFor(repos: string[]): RepoChip[] {
  return cleanRepos(repos).map((repo) => ({ short: repoShortName(repo), full: repo }));
}

// A single labelled section on the plan card. Rendered only when it has content
// (the caller filters empties), so a header never appears above nothing.
function PlanSection({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="ask-draft__section">
      <span className="ask-draft__section-label">{label}</span>
      <div className="ask-draft__section-body">{children}</div>
    </div>
  );
}

// The scope line: the repo(s) the plan touches, in plain words, so the person
// sees WHERE the change lands before filing. Repo-less plans omit this.
function scopeText(repos: string[]): string | null {
  const clean = cleanRepos(repos);
  if (!clean.length) return null;
  const names = clean.map((repo) => repoShortName(repo));
  if (names.length === 1) return `Changes ${names[0]}.`;
  const last = names[names.length - 1];
  return `Changes ${names.slice(0, -1).join(", ")} and ${last}.`;
}

// The plain-words consequence line for the primary action, shown ONLY when the
// plan names a concrete target repo. Filing a repo-less draft is refused by the
// file-issue path (the server enforces repo allowlisting), so promising "this
// files a real issue" there would overpromise. Returns null when there is no
// repo; the card shows a neutral "add a repo" hint instead (see repoHintText).
function consequenceText(repos: string[]): string | null {
  const clean = cleanRepos(repos);
  if (!clean.length) return null;
  const target =
    clean.length === 1
      ? ` on ${repoShortName(clean[0])}`
      : ` on ${clean.map((repo) => repoShortName(repo)).join(", ")}`;
  return `This files a real issue${target}. An engineer-agent picks it up and opens a pull request you review.`;
}

// The neutral hint shown in place of the consequence line when the plan has no
// repo yet, so the person knows what is still missing before filing can succeed
// rather than being promised a filing that would fail.
const REPO_HINT = "Name the repo this should change to file it.";

// The inline lifecycle card rendered when a turn produces a saved draft, wired
// as an assistant-ui "alfred-draft" tool-call part. One primary action: File
// issue (one-gate). The card is an OFFER attached to a build turn, not a form:
// the chat reply already carries Alfred's questions, so the card stays quiet
// (neutral "Draft plan") until the plan is ready to file ("Ready to file"), and
// File issue is always available because the server is the real readiness gate.
export function AskDraftPart({ args }: ToolCallMessagePartProps<DraftToolArgs>) {
  const surface = useAskSurface();
  const draft: DraftCardModel | undefined = args?.draft;
  if (!draft) return null;

  // Each card shows only its OWN file result, keyed by this draft's id, so a
  // filed confirmation stays on the card whose plan was filed (and survives
  // conversational turns that land after) rather than drifting onto the last one.
  const notice = surface.fileNotices[draft.draftId] ?? null;
  const filed = notice?.tone === "ok";
  const busy = surface.fileBusyId === draft.draftId;

  const scope = scopeText(draft.repos);
  const acceptance = (draft.acceptanceCriteria || []).filter((item) => item.trim());
  const problem = (draft.problem || "").trim();
  const desired = (draft.desiredBehavior || "").trim();
  const testPlan = (draft.testPlan || "").trim();
  // Whether the plan carries any structured detail worth a section block. A thin
  // early draft (title + repo only) shows no sections and reads as a quiet offer.
  const hasDetail = Boolean(problem || desired || scope || acceptance.length || testPlan);
  // The consequence line is repo-gated: it only promises a real filing when a
  // concrete target repo exists (the file path refuses repo-less drafts). With no
  // repo, show the neutral "name the repo" hint instead of overpromising.
  const consequence = consequenceText(draft.repos);
  const hasRepo = cleanRepos(draft.repos).length > 0;

  return (
    <div className="ask-draft" aria-label="Plan Alfred is shaping">
      <LifecycleCard
        chip={
          filed
            ? { label: "Filed", tone: "ok" }
            : draft.ready
              ? { label: "Ready to file", tone: "ok" }
              : { label: "Draft plan", tone: "idle" }
        }
        repos={repoChipsFor(draft.repos)}
        outcome={draft.title}
        attribution={
          <span>{draft.ready ? "Ready when you are" : "Keep chatting to firm it up"}</span>
        }
        action={
          filed ? (
            notice?.url ? (
              <button
                className="secondary-button"
                type="button"
                onClick={() => void openExternal(notice.url as string)}
              >
                <ExternalLink size={15} aria-hidden="true" />
                <span>View issue</span>
              </button>
            ) : (
              <button className="secondary-button" type="button" onClick={surface.onOpenWork}>
                <span>Open Work</span>
              </button>
            )
          ) : (
            <button
              className={
                draft.ready ? "icon-button ask-draft__file" : "secondary-button ask-draft__file"
              }
              type="button"
              // The file path is single-flight: useAskThread.fileIssue has one
              // global guard, so a click on another card while one is filing is a
              // silent no-op. Disable every card's File button while ANY file is
              // in flight (surface.fileBusyId !== null), and let only the filing
              // card show the "Filing..." spinner (via `busy`), so no button
              // looks active but dead.
              disabled={surface.fileBusyId !== null}
              onClick={() => surface.onFile(draft.draftId)}
              title={
                draft.ready
                  ? "File this as a GitHub issue"
                  : "File it now, or keep chatting to add detail first"
              }
            >
              <CheckCircle2 size={15} aria-hidden="true" />
              <span>
                {busy ? "Filing..." : draft.ready ? "File issue" : "File as an issue"}
              </span>
            </button>
          )
        }
        ariaLabel={`Plan: ${draft.title}`}
      />
      {hasDetail ? (
        <div className="ask-draft__detail">
          {problem ? <PlanSection label="Intent">{problem}</PlanSection> : null}
          {scope || desired ? (
            <PlanSection label="Scope">
              {scope ? <p className="ask-draft__scope">{scope}</p> : null}
              {desired ? <p>{desired}</p> : null}
            </PlanSection>
          ) : null}
          {acceptance.length ? (
            <PlanSection label="Done when">
              <ul className="ask-draft__criteria">
                {acceptance.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </PlanSection>
          ) : null}
          {testPlan ? <PlanSection label="Verified by">{testPlan}</PlanSection> : null}
        </div>
      ) : null}
      {!filed ? (
        hasRepo && consequence ? (
          <p className="ask-draft__consequence" role="note">
            {consequence}
          </p>
        ) : (
          <p className="ask-draft__consequence ask-draft__consequence--hint" role="note">
            {REPO_HINT}
          </p>
        )
      ) : null}
      {notice ? (
        <p className={`ask-draft__notice ask-draft__notice--${notice.tone}`} role="status">
          {notice.message}
        </p>
      ) : null}
    </div>
  );
}
