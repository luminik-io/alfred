/**
 * Shared self-proof attribution + share computation for the site emitters.
 *
 * This is the JS twin of lib/self_proof.py and MUST stay in lockstep with it:
 * the number this produces is the PUBLICLY DISPLAYED "% of merged PRs shipped
 * by Alfred agents". Attribution is LABEL-AUTHORITATIVE and exact-match. A
 * merged PR counts as agent-shipped ONLY when it carries one of Alfred's
 * provenance labels (agent:authored and friends), matched exactly. A canonical
 * role branch prefix (senior-dev/, architect/, automerge/, ...) is display-only
 * corroboration and NEVER qualifies a PR on its own, so a human PR pushed to a
 * role-looking or stale automerge branch cannot inflate the headline share.
 * Near-miss labels (not-agent:authored, agent:authored-needed) do not match.
 *
 * Keeping the exact-match, label-only rule here means the site and the Python
 * CLI compute the SAME numerator; there is one attribution definition, not two.
 */

/**
 * Lowercased label names on a PR/issue from the GraphQL `labels.nodes` shape.
 * @param {{labels?: {nodes?: Array<{name?: string}>}}} item
 * @returns {string[]}
 */
export function labelNames(item) {
  return (item.labels?.nodes || []).map((label) =>
    String(label.name || "").toLowerCase(),
  );
}

/**
 * Lowercased trimmed PR author login.
 * @param {{author?: {login?: string}}} item
 * @returns {string}
 */
export function authorLogin(item) {
  return String(item.author?.login || "")
    .trim()
    .toLowerCase();
}

/**
 * True only when a merged PR carries an exact Alfred provenance label and its
 * author is not excluded. Label-only and exact-match by design (see module
 * docstring); branch prefixes never qualify here.
 *
 * @param {object} item PR node
 * @param {object} opts
 * @param {string[]} opts.agentLabels lowercased provenance labels
 * @param {Set<string>} opts.excludedAuthors lowercased excluded logins
 * @returns {boolean}
 */
export function isAgentShipped(item, { agentLabels, excludedAuthors }) {
  if (excludedAuthors.has(authorLogin(item))) {
    return false;
  }
  const wanted = new Set(agentLabels);
  return labelNames(item).some((label) => wanted.has(label));
}

/**
 * Display-only agent evidence: exact labels qualify, branch prefixes corroborate.
 * Mirrors pr_agent_evidence in the Python module. Never used to decide the share.
 *
 * @param {object} item PR node
 * @param {object} opts
 * @param {string[]} opts.agentLabels lowercased provenance labels
 * @param {string[]} opts.agentBranchPrefixes branch prefixes (case preserved)
 * @returns {string[]}
 */
export function agentEvidence(item, { agentLabels, agentBranchPrefixes }) {
  const evidence = [];
  const wanted = new Set(agentLabels);
  for (const label of labelNames(item)) {
    if (wanted.has(label)) {
      evidence.push(`label:${label}`);
    }
  }
  const branch = String(item.headRefName || "").trim();
  const lowered = branch.toLowerCase();
  if (
    lowered &&
    agentBranchPrefixes.some((prefix) => lowered.startsWith(prefix.toLowerCase()))
  ) {
    evidence.push(`branch:${lowered}`);
  }
  return evidence;
}

/**
 * Format a share so an integer reads as "75" and a fraction stays precise.
 * @param {number} value
 * @returns {string}
 */
export function formatShare(value) {
  return Number.isInteger(value) ? String(value) : String(value);
}

/**
 * The rolling-window copy, kept as a SECONDARY stat under the cumulative
 * headline. Honest on empty data: never quotes a fabricated 0% share.
 *
 * @param {number} agentMerged agent-shipped merged PRs in the window
 * @param {number} totalMerged all merged PRs in the window
 * @param {number|null} sharePct window share, null when there is no data
 * @param {number} days window size
 * @returns {{headline: string, sentence: string}}
 */
function windowCopy(agentMerged, totalMerged, sharePct, days) {
  if (sharePct === null) {
    return {
      headline: `No merged PRs in the last ${days} days.`,
      sentence: `No merged PRs to measure in the last ${days} days.`,
    };
  }
  if (agentMerged <= 0) {
    const text = `No agent-attributed PRs among ${totalMerged} merged PRs in the last ${days} days.`;
    return { headline: text, sentence: text };
  }
  return {
    headline: `${agentMerged} of ${totalMerged} merged PRs (${formatShare(
      sharePct,
    )}%) in the last ${days} days.`,
    sentence: `${formatShare(sharePct)}% in the last ${days} days.`,
  };
}

/**
 * Build the self_proof block. The HEADLINE metric is CUMULATIVE: the all-time
 * count of merged PRs attributed to Alfred agents in the repo, so the proof
 * reflects total impact and does not read as 0 when the fleet is paused or the
 * rolling window happens to be empty. The rolling window survives as a
 * secondary stat (window_headline / window_sentence).
 *
 * Honesty is preserved: agentTotal is a real count (0 stays 0, never faked
 * upward), the window share_pct is null (never 0) when there are no merged PRs
 * in the window, and an all-zero repo renders a plain "no agent-attributed PRs
 * yet" line rather than a fabricated number. Use noDataSelfProof() for a
 * committed seed so a skipped pre-deploy refresh cannot publish stale traction.
 *
 * @param {object} args
 * @param {number} args.agentTotal cumulative all-time agent-attributed merged PRs
 * @param {boolean} [args.agentTotalIncomplete] true when the count is a floor
 *   (a GitHub search cap hid older PRs), so it renders as "N+"
 * @param {number} args.agentWindow agent-attributed merged PRs in the window
 * @param {number} args.mergedWindow all merged PRs in the window
 * @param {number} args.windowDays window size in days
 * @param {string|null} [args.firstAgentMergedAt] ISO date of the first agent PR
 * @returns {object}
 */
export function buildSelfProof({
  agentTotal,
  agentTotalIncomplete = false,
  agentWindow,
  mergedWindow,
  windowDays,
  firstAgentMergedAt = null,
}) {
  const days = windowDays;
  const sharePct =
    mergedWindow > 0 ? Math.round((1000 * agentWindow) / mergedWindow) / 10 : null;
  const window = windowCopy(agentWindow, mergedWindow, sharePct, days);

  let headline;
  let sentence;
  if (agentTotal > 0) {
    // A capped enumeration is a lower bound, rendered "N+", never a silent
    // undercount presented as exact.
    const noun = agentTotal === 1 && !agentTotalIncomplete ? "PR" : "PRs";
    const count = agentTotalIncomplete ? `${agentTotal}+` : String(agentTotal);
    sentence = `${count} agent-attributed ${noun} merged so far.`;
    headline = `Alfred agents have merged ${count} agent-attributed ${noun} so far.`;
  } else if (agentTotalIncomplete) {
    sentence = "Agent-attributed PR count is temporarily unavailable.";
    headline = "Agent-attributed PR count is temporarily unavailable.";
  } else {
    sentence = "No agent-attributed PRs merged yet.";
    headline = "No agent-attributed PRs merged yet.";
  }

  return {
    // Cumulative headline metric.
    agent_shipped_total: agentTotal,
    agent_shipped_total_incomplete: agentTotalIncomplete,
    first_agent_merged_at: firstAgentMergedAt,
    headline,
    sentence,
    // Rolling window, kept as a secondary stat.
    window_days: days,
    agent_shipped: agentWindow,
    merged_total: mergedWindow,
    share_pct: sharePct,
    repos_counted: mergedWindow > 0 ? 1 : 0,
    repo_word: "repo",
    window_headline: window.headline,
    window_sentence: window.sentence,
  };
}

/**
 * A no-data self_proof block for the committed seed. The cumulative count is
 * zero and the window share_pct is null, so the Impact page shows "no
 * agent-attributed PRs yet" rather than a real number if a pre-deploy refresh
 * is ever skipped.
 *
 * @param {number} days window size the live build will use
 * @returns {object}
 */
export function noDataSelfProof(days) {
  return buildSelfProof({
    agentTotal: 0,
    agentWindow: 0,
    mergedWindow: 0,
    windowDays: days,
  });
}

// The README carries a live self-proof line between these markers. The proof
// build rewrites the text between them from real data so the documented
// `npm run proof:update` command actually updates it (not a hand-typed
// placeholder). The markers themselves are preserved so the next refresh finds
// them again.
export const SELF_PROOF_MARKER_OPEN = "<!-- SELF_PROOF -->";
export const SELF_PROOF_MARKER_CLOSE = "<!-- /SELF_PROOF -->";

/**
 * The README sentence for a self_proof block, honest on empty data.
 *
 * Leads with the CUMULATIVE count ("Alfred agents have merged N agent-attributed
 * PRs in this repo so far") and appends the rolling window as a secondary clause
 * when it carries agent work. An all-zero repo says so plainly, so a
 * refreshed-but-idle repo never advertises a fabricated number.
 *
 * @param {object} selfProof a block from buildSelfProof / noDataSelfProof
 * @returns {string}
 */
export function readmeSelfProofText(selfProof) {
  const total = selfProof.agent_shipped_total ?? 0;
  const incomplete = selfProof.agent_shipped_total_incomplete ?? false;
  const days = selfProof.window_days;
  if (total <= 0) {
    if (incomplete) {
      return "Agent-attributed PR count for Alfred's own repo is temporarily unavailable";
    }
    return "No agent-attributed PRs in Alfred's own repo yet";
  }
  const noun = total === 1 && !incomplete ? "PR" : "PRs";
  const count = incomplete ? `${total}+` : String(total);
  let text = `Alfred agents have merged ${count} agent-attributed ${noun} in this repo so far`;
  if ((selfProof.agent_shipped ?? 0) > 0) {
    text += `, ${selfProof.agent_shipped} in the last ${days} days`;
  }
  return text;
}

/**
 * Rewrite the text between the SELF_PROOF markers in `readme` from real data.
 *
 * Returns the updated README string and whether it changed. The markers are
 * required; if they are absent the README is returned unchanged with
 * updated=false (the caller decides whether that is an error). Idempotent:
 * running twice with the same data yields the same text.
 *
 * @param {string} readme full README contents
 * @param {object} selfProof a block from buildSelfProof / noDataSelfProof
 * @returns {{content: string, updated: boolean, found: boolean}}
 */
export function updateReadmeSelfProof(readme, selfProof) {
  const open = SELF_PROOF_MARKER_OPEN;
  const close = SELF_PROOF_MARKER_CLOSE;
  const openAt = readme.indexOf(open);
  const closeAt = readme.indexOf(close);
  if (openAt === -1 || closeAt === -1 || closeAt < openAt) {
    return { content: readme, updated: false, found: false };
  }
  const before = readme.slice(0, openAt + open.length);
  const after = readme.slice(closeAt);
  const next = `${before}${readmeSelfProofText(selfProof)}${after}`;
  return { content: next, updated: next !== readme, found: true };
}
