import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  agentEvidence,
  buildSelfProof,
  isAgentShipped,
  labelNames,
  updateReadmeSelfProof,
} from "./lib/self-proof.mjs";

const REPO = "luminik-io/alfred";
const DAYS = Number.parseInt(process.env.ALFRED_IMPACT_DAYS || "30", 10);
const OUT = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../src/data/impact-proof.json",
);
// The repo README carries a live self-proof line between SELF_PROOF markers.
// This build rewrites it from the same data it writes to the JSON, so the
// documented `npm run proof:update` command actually refreshes the README.
const README = resolve(dirname(fileURLToPath(import.meta.url)), "../../README.md");
const AGENT_BRANCH_PREFIXES = csvEnv(
  "ALFRED_IMPACT_AGENT_BRANCH_PREFIXES",
  [
    "alfred/",
    "alfred-nightly/",
    "automerge/",
    "bane/",
    "batman/",
    "damian/",
    "lucius/",
    "nightwing/",
    "rasalghul/",
    "robin/",
  ],
  { lowercase: false },
);
const AGENT_SHIPPED_LABELS = csvEnv("ALFRED_IMPACT_AGENT_LABELS", [
  "agent:authored",
  "agent:done",
  "agent:shipped",
  "alfred:shipped",
  "shipped-by-alfred",
]);
const EXCLUDED_AUTHORS = new Set(
  csvEnv("ALFRED_IMPACT_EXCLUDED_AUTHORS", [
    "app/dependabot",
    "dependabot",
    "dependabot[bot]",
  ]),
);

const token = process.env.GITHUB_TOKEN || process.env.GH_TOKEN || readGhToken();

if (!token) {
  throw new Error(
    "Missing GITHUB_TOKEN or GH_TOKEN. Run `gh auth login`, or set a token with public repo read access.",
  );
}

const now = new Date();
const from = new Date(now.getTime() - DAYS * 24 * 60 * 60 * 1000);
const dateOnly = (date) => date.toISOString().slice(0, 10);
const toDate = dateOnly(new Date(now.getTime() + 24 * 60 * 60 * 1000));
const windowRange = `${dateOnly(from)}..${toDate}`;

const prQuery = `repo:${REPO} is:pr is:merged merged:${windowRange}`;
const openedIssueQuery = `repo:${REPO} is:issue created:${windowRange}`;
const closedIssueQuery = `repo:${REPO} is:issue closed:${windowRange}`;

const prs = await searchGitHub(prQuery);
const issuesOpened = await searchGitHub(openedIssueQuery);
const issuesClosed = await searchGitHub(closedIssueQuery);

const sortedPrs = prs
  .filter(
    (item) =>
      item.__typename === "PullRequest" &&
      item.mergedAt &&
      isWithinWindow(item.mergedAt),
  )
  .sort((a, b) => new Date(b.mergedAt) - new Date(a.mergedAt));

const sortedIssuesOpened = issuesOpened
  .filter(
    (item) =>
      item.__typename === "Issue" &&
      item.createdAt &&
      isWithinWindow(item.createdAt),
  )
  .sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt));

const sortedIssuesClosed = issuesClosed
  .filter(
    (item) =>
      item.__typename === "Issue" &&
      item.closedAt &&
      isWithinWindow(item.closedAt),
  )
  .sort((a, b) => new Date(b.closedAt) - new Date(a.closedAt));

const agentPrs = sortedPrs.filter(isAgentMarked);
const agentIssuesOpened = sortedIssuesOpened.filter(isAgentIssue);
const agentIssuesClosed = sortedIssuesClosed.filter(isAgentIssue);

const summary = {
  prs_merged: agentPrs.length,
  issues_opened: agentIssuesOpened.length,
  issues_closed: agentIssuesClosed.length,
  issues_triaged: agentIssuesOpened.filter(isTriagedIssue).length,
  lines_added: sum(agentPrs, "additions"),
  lines_removed: sum(agentPrs, "deletions"),
  files_changed: sum(agentPrs, "changedFiles"),
  repo_activity: {
    prs_merged: sortedPrs.length,
    issues_opened: sortedIssuesOpened.length,
    issues_closed: sortedIssuesClosed.length,
    lines_added: sum(sortedPrs, "additions"),
    lines_removed: sum(sortedPrs, "deletions"),
    files_changed: sum(sortedPrs, "changedFiles"),
  },
};

// Self-proof stat: the share of merged PRs shipped by Alfred agents. This is
// summary.prs_merged / summary.repo_activity.prs_merged over the same window,
// surfaced as a first-class, re-quotable field so the Impact page can render
// "X% of Alfred's own merged PRs were shipped by Alfred agents" without
// re-deriving it. Honest on an empty window: share_pct is null (not 0), so the
// page shows "no data yet" rather than a fabricated 0%.
const selfProof = buildSelfProof(agentPrs.length, sortedPrs.length, DAYS);

const proof = {
  generated_at: now.toISOString(),
  source: {
    repo: REPO,
    url: `https://github.com/${REPO}`,
    note: "Public Alfred activity from GitHub. Dependabot is excluded. The committed JSON is a seed; main-branch site builds refresh it before deploy.",
  },
  window: {
    days: DAYS,
    from: from.toISOString(),
    to: now.toISOString(),
  },
  summary,
  self_proof: selfProof,
  trend: buildTrend(agentPrs),
  prs: agentPrs.slice(0, 10).map((pr) => ({
    number: pr.number,
    title: pr.title,
    url: pr.url,
    merged_at: pr.mergedAt,
    lines_added: pr.additions || 0,
    lines_removed: pr.deletions || 0,
    files_changed: pr.changedFiles || 0,
    agent_authored: isAgentMarked(pr),
    agent_evidence: agentEvidence(pr, {
      agentLabels: AGENT_SHIPPED_LABELS,
      agentBranchPrefixes: AGENT_BRANCH_PREFIXES,
    }),
  })),
  issues: agentIssuesOpened.slice(0, 8).map((issue) => ({
    number: issue.number,
    title: issue.title,
    url: issue.url,
    state: issue.state,
    created_at: issue.createdAt,
    closed_at: issue.closedAt || null,
  })),
};

mkdirSync(dirname(OUT), { recursive: true });
writeFileSync(OUT, `${JSON.stringify(proof, null, 2)}\n`);
console.log(
  `Wrote ${OUT}: ${summary.prs_merged} agent PRs, ${summary.issues_opened} agent issues, ${summary.repo_activity.prs_merged} total public PRs.`,
);

// Refresh the README self-proof line from the same data. This is what makes
// the documented refresh command honest: the marker text is generated, not
// hand-typed. A missing README or missing markers is a non-fatal warning so a
// docs-only edit cannot break the JSON/site refresh.
refreshReadmeSelfProof();

function refreshReadmeSelfProof() {
  let readme;
  try {
    readme = readFileSync(README, "utf8");
  } catch (error) {
    console.warn(`Skipped README self-proof refresh: ${error.message}`);
    return;
  }
  const { content, updated, found } = updateReadmeSelfProof(readme, selfProof);
  if (!found) {
    console.warn(
      `Skipped README self-proof refresh: SELF_PROOF markers not found in ${README}`,
    );
    return;
  }
  if (updated) {
    writeFileSync(README, content);
    console.log(`Updated README self-proof line in ${README}.`);
  } else {
    console.log("README self-proof line already current.");
  }
}

async function searchGitHub(query) {
  const out = [];
  let cursor = null;
  do {
    const data = await graphQL(
      `query ImpactProof($query: String!, $cursor: String) {
        search(type: ISSUE, query: $query, first: 100, after: $cursor) {
          pageInfo { hasNextPage endCursor }
          nodes {
            __typename
            ... on PullRequest {
              number
              title
              url
              mergedAt
              additions
              deletions
              changedFiles
              headRefName
              author { login }
              labels(first: 30) { nodes { name } }
            }
            ... on Issue {
              number
              title
              url
              createdAt
              closedAt
              state
              labels(first: 30) { nodes { name } }
            }
          }
        }
      }`,
      { query, cursor },
    );
    const search = data.search;
    out.push(...search.nodes.filter(Boolean));
    cursor = search.pageInfo.hasNextPage ? search.pageInfo.endCursor : null;
  } while (cursor);
  return out;
}

async function graphQL(query, variables) {
  const maxAttempts = 3;
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    let response;
    let payload;
    try {
      response = await fetch("https://api.github.com/graphql", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          "User-Agent": "alfred-os-impact-proof",
        },
        body: JSON.stringify({ query, variables }),
      });
      payload = await response.json();
    } catch (error) {
      if (attempt === maxAttempts) {
        throw error;
      }
      await sleep(500 * attempt);
      continue;
    }
    if (response.ok && !payload.errors) {
      return payload.data;
    }
    const message = JSON.stringify(payload.errors || payload, null, 2);
    if (payload.errors || response.status < 500 || attempt === maxAttempts) {
      throw new Error(message);
    }
    await sleep(500 * attempt);
  }
  throw new Error("GitHub GraphQL request failed");
}

function sleep(ms) {
  return new Promise((resolveSleep) => {
    setTimeout(resolveSleep, ms);
  });
}

function readGhToken() {
  try {
    return execFileSync("gh", ["auth", "token"], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
  } catch {
    return "";
  }
}

function sum(items, field) {
  return items.reduce((total, item) => total + Number(item[field] || 0), 0);
}

function isWithinWindow(value) {
  const timestamp = new Date(value).getTime();
  return Number.isFinite(timestamp) && timestamp >= from.getTime() && timestamp <= now.getTime();
}

function csvEnv(name, fallback, { lowercase = true } = {}) {
  const normalize = (value) => (lowercase ? value.toLowerCase() : value);
  const raw = (process.env[name] || "").trim();
  if (!raw) return fallback.map((item) => normalize(item));
  return raw
    .split(",")
    .map((item) => normalize(item.trim()))
    .filter(Boolean);
}

// Attribution is LABEL-ONLY and exact-match, delegated to the shared module so
// the site and the Python CLI agree on the numerator. A branch prefix never
// qualifies a PR (see lib/self-proof.mjs); it is recorded as display-only
// evidence via agentEvidence, so a human PR on a lucius/ or automerge/ branch
// cannot inflate the publicly displayed share.
function isAgentMarked(item) {
  return isAgentShipped(item, {
    agentLabels: AGENT_SHIPPED_LABELS,
    excludedAuthors: EXCLUDED_AUTHORS,
  });
}

function isAgentIssue(issue) {
  return labelNames(issue).some((label) => label.startsWith("agent:"));
}

function isTriagedIssue(issue) {
  return labelNames(issue).some(
    (label) =>
      label.startsWith("agent:") ||
      ["bug", "enhancement", "documentation", "question"].includes(label),
  );
}

function buildTrend(items) {
  const weeks = new Map();
  for (const item of items) {
    const week = isoWeek(item.mergedAt);
    weeks.set(week, (weeks.get(week) || 0) + 1);
  }
  return [...weeks.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([week, prs_merged]) => ({ week, prs_merged }));
}

function isoWeek(value) {
  const input = new Date(value);
  const date = new Date(Date.UTC(input.getUTCFullYear(), input.getUTCMonth(), input.getUTCDate()));
  const day = date.getUTCDay() || 7;
  date.setUTCDate(date.getUTCDate() + 4 - day);
  const yearStart = new Date(Date.UTC(date.getUTCFullYear(), 0, 1));
  const week = Math.ceil(((date - yearStart) / 86400000 + 1) / 7);
  return `${date.getUTCFullYear()}-W${String(week).padStart(2, "0")}`;
}
