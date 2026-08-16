/**
 * Tests for scripts/lib/self-proof.mjs, the JS twin of lib/self_proof.py.
 *
 * The share this module produces is the PUBLICLY DISPLAYED headline number, so
 * these lock in the same guarantees the Python tests enforce: attribution is
 * label-authoritative and exact-match, a branch prefix never qualifies a PR on
 * its own, near-miss labels do not match, and an empty window yields a
 * null (no-data) share rather than a real 0%.
 *
 * Run with `node --test` (wired as `npm test` in site/package.json). No
 * network, no GitHub: everything operates on in-memory PR nodes.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  agentEvidence,
  buildSelfProof,
  isAgentShipped,
  noDataSelfProof,
} from "./self-proof.mjs";

const AGENT_LABELS = [
  "agent:authored",
  "agent:done",
  "agent:shipped",
  "alfred:shipped",
  "shipped-by-alfred",
];
const BRANCH_PREFIXES = [
  "alfred/",
  "automerge/",
  "architect/",
  "senior-dev/",
  "triage/",
];
const EXCLUDED = new Set(["app/dependabot", "dependabot", "dependabot[bot]"]);

function pr({ labels = [], branch = "feature/x", author = "alice" } = {}) {
  return {
    number: 1,
    headRefName: branch,
    author: { login: author },
    labels: { nodes: labels.map((name) => ({ name })) },
  };
}

const opts = { agentLabels: AGENT_LABELS, excludedAuthors: EXCLUDED };

test("provenance label qualifies a PR", () => {
  assert.equal(isAgentShipped(pr({ labels: ["agent:authored"] }), opts), true);
  assert.equal(isAgentShipped(pr({ labels: ["agent:shipped"] }), opts), true);
});

test("branch prefix alone does NOT qualify (the gameable case)", () => {
  // This is the defect the fix closes: a human PR on a role-looking or
  // stale automerge branch, with no provenance label, must not count.
  for (const branch of ["senior-dev/fix", "architect/rollout", "automerge/dep-bump"]) {
    assert.equal(
      isAgentShipped(pr({ branch }), opts),
      false,
      `branch ${branch} must not qualify without a label`,
    );
  }
});

test("theme display branch prefix is not evidence", () => {
  const evidence = agentEvidence(pr({ branch: "lucius/fix" }), {
    agentLabels: AGENT_LABELS,
    agentBranchPrefixes: BRANCH_PREFIXES,
  });
  assert.deepEqual(evidence, []);
});

test("near-miss labels do not match (exact-match only)", () => {
  for (const label of [
    "not-agent:authored",
    "agent:authored-needed",
    "agent:authoredx",
  ]) {
    assert.equal(isAgentShipped(pr({ labels: [label] }), opts), false);
  }
});

test("excluded author never counts even with a provenance label", () => {
  assert.equal(
    isAgentShipped(
      pr({ labels: ["agent:authored"], author: "dependabot[bot]" }),
      opts,
    ),
    false,
  );
});

test("branch is display-only evidence, never a qualifier", () => {
  const node = pr({ labels: ["agent:authored"], branch: "senior-dev/x" });
  const evidence = agentEvidence(node, {
    agentLabels: AGENT_LABELS,
    agentBranchPrefixes: BRANCH_PREFIXES,
  });
  assert.ok(evidence.includes("label:agent:authored"));
  assert.ok(evidence.includes("branch:senior-dev/x"));

  // Branch-only PR: evidence records the branch, but isAgentShipped is false.
  const branchOnly = pr({ branch: "automerge/dep" });
  const branchEvidence = agentEvidence(branchOnly, {
    agentLabels: AGENT_LABELS,
    agentBranchPrefixes: BRANCH_PREFIXES,
  });
  assert.deepEqual(branchEvidence, ["branch:automerge/dep"]);
  assert.equal(isAgentShipped(branchOnly, opts), false);
});

test("buildSelfProof leads with the cumulative all-time count", () => {
  const proof = buildSelfProof({
    agentTotal: 128,
    agentWindow: 9,
    mergedWindow: 12,
    windowDays: 7,
    firstAgentMergedAt: "2026-01-02T00:00:00Z",
  });
  // Cumulative is the headline.
  assert.equal(proof.agent_shipped_total, 128);
  assert.equal(proof.first_agent_merged_at, "2026-01-02T00:00:00Z");
  assert.match(proof.headline, /128 PRs carrying Alfred provenance labels have been merged so far/);
  assert.match(proof.sentence, /128 PRs carrying Alfred provenance labels have been merged so far/);
  // Rolling window survives as a secondary stat.
  assert.equal(proof.agent_shipped, 9);
  assert.equal(proof.merged_total, 12);
  assert.equal(proof.share_pct, 75);
  assert.equal(proof.repos_counted, 1);
  assert.match(proof.window_headline, /9 of 12 merged PRs \(75%\)/);
  assert.match(proof.window_sentence, /75% in the last 7 days/);
});

test("cumulative headline is singular for exactly one PR", () => {
  const proof = buildSelfProof({
    agentTotal: 1,
    agentWindow: 1,
    mergedWindow: 4,
    windowDays: 30,
  });
  assert.match(proof.headline, /1 PR carrying Alfred provenance labels has been merged so far/);
  assert.doesNotMatch(proof.headline, /PRs have/);
});

test("a capped cumulative count renders as a floor, never a silent undercount", () => {
  const proof = buildSelfProof({
    agentTotal: 1000,
    agentTotalIncomplete: true,
    agentWindow: 4,
    mergedWindow: 20,
    windowDays: 30,
  });
  assert.equal(proof.agent_shipped_total, 1000);
  assert.equal(proof.agent_shipped_total_incomplete, true);
  assert.match(proof.headline, /1000\+ PRs carrying Alfred provenance labels have been merged so far/);
});

test("a fully unavailable cumulative count never claims none", () => {
  const proof = buildSelfProof({
    agentTotal: 0,
    agentTotalIncomplete: true,
    agentWindow: 0,
    mergedWindow: 0,
    windowDays: 30,
  });
  assert.match(proof.headline, /temporarily unavailable/);
  assert.doesNotMatch(proof.headline, /No PRs carrying Alfred provenance labels have been merged yet/);
});

test("window keeps fractional shares precise", () => {
  const proof = buildSelfProof({
    agentTotal: 5,
    agentWindow: 2,
    mergedWindow: 3,
    windowDays: 7,
  });
  assert.equal(proof.share_pct, 66.7);
});

test("empty everything yields a real 0 cumulative and null window share", () => {
  const proof = buildSelfProof({
    agentTotal: 0,
    agentWindow: 0,
    mergedWindow: 0,
    windowDays: 30,
  });
  assert.equal(proof.agent_shipped_total, 0);
  assert.equal(proof.share_pct, null);
  assert.equal(proof.merged_total, 0);
  assert.equal(proof.repos_counted, 0);
  assert.match(proof.headline, /No PRs carrying Alfred provenance labels have been merged yet/);
  assert.match(proof.window_headline, /No merged PRs/);
});

test("cumulative traction shows even when the window is empty (the whole point)", () => {
  // Fleet paused this month: window is empty, but the all-time count stands.
  const proof = buildSelfProof({
    agentTotal: 42,
    agentWindow: 0,
    mergedWindow: 0,
    windowDays: 30,
  });
  assert.match(proof.headline, /42 PRs carrying Alfred provenance labels have been merged so far/);
  assert.doesNotMatch(proof.headline, /\b0\b/);
  assert.equal(proof.share_pct, null);
});

test("nonempty window with no attributed agent PRs avoids window 0% copy", () => {
  const proof = buildSelfProof({
    agentTotal: 7,
    agentWindow: 0,
    mergedWindow: 12,
    windowDays: 30,
  });
  assert.equal(proof.share_pct, 0);
  assert.equal(proof.merged_total, 12);
  assert.match(proof.window_headline, /No PRs carrying Alfred provenance labels among 12 merged PRs/);
  assert.doesNotMatch(proof.window_headline, /0%/);
});

test("noDataSelfProof is a zero-cumulative, null-share seed", () => {
  const seed = noDataSelfProof(30);
  assert.equal(seed.agent_shipped_total, 0);
  assert.equal(seed.share_pct, null);
  assert.equal(seed.agent_shipped, 0);
  assert.equal(seed.merged_total, 0);
  assert.equal(seed.repos_counted, 0);
  assert.match(seed.headline, /No PRs carrying Alfred provenance labels have been merged yet/);
});

test("the JS numerator matches the Python rule on a mixed population", () => {
  // 1 labelled agent PR + 1 branch-only human PR + 1 plain human PR.
  // Only the labelled one is agent-shipped; all three are merged.
  const population = [
    pr({ labels: ["agent:authored"] }),
    pr({ branch: "senior-dev/human-lookalike" }),
    pr({ branch: "feature/plain" }),
  ];
  const agent = population.filter((p) => isAgentShipped(p, opts)).length;
  assert.equal(agent, 1);
  const proof = buildSelfProof({
    agentTotal: agent,
    agentWindow: agent,
    mergedWindow: population.length,
    windowDays: 7,
  });
  assert.equal(proof.share_pct, 33.3);
});
