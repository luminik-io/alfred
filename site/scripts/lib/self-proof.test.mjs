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
  "batman/",
  "lucius/",
  "robin/",
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
  // This is the defect the fix closes: a human PR on a codename-looking or
  // stale automerge branch, with no provenance label, must not count.
  for (const branch of ["lucius/fix", "batman/rollout", "automerge/dep-bump"]) {
    assert.equal(
      isAgentShipped(pr({ branch }), opts),
      false,
      `branch ${branch} must not qualify without a label`,
    );
  }
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
  const node = pr({ labels: ["agent:authored"], branch: "lucius/x" });
  const evidence = agentEvidence(node, {
    agentLabels: AGENT_LABELS,
    agentBranchPrefixes: BRANCH_PREFIXES,
  });
  assert.ok(evidence.includes("label:agent:authored"));
  assert.ok(evidence.includes("branch:lucius/x"));

  // Branch-only PR: evidence records the branch, but isAgentShipped is false.
  const branchOnly = pr({ branch: "automerge/dep" });
  const branchEvidence = agentEvidence(branchOnly, {
    agentLabels: AGENT_LABELS,
    agentBranchPrefixes: BRANCH_PREFIXES,
  });
  assert.deepEqual(branchEvidence, ["branch:automerge/dep"]);
  assert.equal(isAgentShipped(branchOnly, opts), false);
});

test("buildSelfProof computes the share and honest headline", () => {
  const proof = buildSelfProof(9, 12, 7);
  assert.equal(proof.agent_shipped, 9);
  assert.equal(proof.merged_total, 12);
  assert.equal(proof.share_pct, 75);
  assert.equal(proof.repos_counted, 1);
  assert.match(proof.headline, /9 of 12 merged PRs \(75%\)/);
  assert.match(proof.sentence, /75% of merged PRs/);
});

test("buildSelfProof keeps fractional shares precise", () => {
  const proof = buildSelfProof(2, 3, 7);
  assert.equal(proof.share_pct, 66.7);
});

test("empty window yields null share, not a real 0%", () => {
  const proof = buildSelfProof(0, 0, 30);
  assert.equal(proof.share_pct, null);
  assert.equal(proof.merged_total, 0);
  assert.equal(proof.repos_counted, 0);
  assert.match(proof.headline, /No merged PRs/);
  assert.match(proof.sentence, /No merged PRs/);
});

test("noDataSelfProof is a null-share seed, publishable without a refresh", () => {
  const seed = noDataSelfProof(30);
  assert.equal(seed.share_pct, null);
  assert.equal(seed.agent_shipped, 0);
  assert.equal(seed.merged_total, 0);
  assert.equal(seed.repos_counted, 0);
});

test("the JS numerator matches the Python rule on a mixed population", () => {
  // 1 labelled agent PR + 1 branch-only human PR + 1 plain human PR.
  // Only the labelled one is agent-shipped; all three are merged.
  const population = [
    pr({ labels: ["agent:authored"] }),
    pr({ branch: "lucius/human-lookalike" }),
    pr({ branch: "feature/plain" }),
  ];
  const agent = population.filter((p) => isAgentShipped(p, opts)).length;
  assert.equal(agent, 1);
  const proof = buildSelfProof(agent, population.length, 7);
  assert.equal(proof.share_pct, 33.3);
});
