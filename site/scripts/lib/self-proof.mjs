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
 * Build the self_proof block: the share of merged PRs shipped by Alfred agents.
 *
 * share_pct is null (never 0) when there are no merged PRs, so the page renders
 * a "no data yet" state instead of a fabricated 0% share. Use noDataSelfProof()
 * for a committed seed so a skipped pre-deploy refresh cannot publish a real 0%.
 *
 * @param {number} agentMerged agent-shipped merged PRs (label-attributed)
 * @param {number} totalMerged all merged PRs in the window
 * @param {number} days window size
 * @returns {object}
 */
export function buildSelfProof(agentMerged, totalMerged, days) {
  const sharePct =
    totalMerged > 0 ? Math.round((1000 * agentMerged) / totalMerged) / 10 : null;
  let sentence;
  let headline;
  if (sharePct === null) {
    sentence = `No merged PRs to measure in the last ${days} days yet.`;
    headline = `No merged PRs in the last ${days} days yet.`;
  } else if (agentMerged <= 0) {
    sentence = `No public agent-attributed PRs among ${totalMerged} merged PRs in the last ${days} days yet.`;
    headline = `No public agent-attributed PRs among ${totalMerged} merged PRs in the last ${days} days yet.`;
  } else {
    sentence = `${formatShare(sharePct)}% of merged PRs in the last ${days} days were shipped by Alfred agents.`;
    headline = `Alfred agents shipped ${agentMerged} of ${totalMerged} merged PRs (${formatShare(
      sharePct,
    )}%) in the last ${days} days.`;
  }
  return {
    window_days: days,
    agent_shipped: agentMerged,
    merged_total: totalMerged,
    share_pct: sharePct,
    repos_counted: totalMerged > 0 ? 1 : 0,
    repo_word: "repo",
    headline,
    sentence,
  };
}

/**
 * A no-data self_proof block for the committed seed. share_pct is null and the
 * counts are zero, so the Impact page shows "no data yet" rather than a real
 * percentage if a pre-deploy refresh is ever skipped.
 *
 * @param {number} days window size the live build will use
 * @returns {object}
 */
export function noDataSelfProof(days) {
  return buildSelfProof(0, 0, days);
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
 * With merged PRs: "N% of Alfred's own merged PRs in the last D days were
 * shipped by Alfred agents". Empty window: a plain "no merged PRs yet" line, so
 * a refreshed-but-idle repo never advertises a fabricated 0%.
 *
 * @param {object} selfProof a block from buildSelfProof / noDataSelfProof
 * @returns {string}
 */
export function readmeSelfProofText(selfProof) {
  const days = selfProof.window_days;
  if (selfProof.share_pct === null || selfProof.merged_total <= 0) {
    return `No merged PRs in Alfred's own repo in the last ${days} days yet`;
  }
  if (selfProof.agent_shipped <= 0) {
    return `No public agent-attributed PRs in Alfred's own repo in the last ${days} days yet`;
  }
  return (
    `${formatShare(selfProof.share_pct)}% of Alfred's own merged PRs in the ` +
    `last ${days} days were shipped by Alfred agents`
  );
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
