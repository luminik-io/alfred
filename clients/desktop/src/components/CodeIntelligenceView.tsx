import {
  AlertTriangle,
  Braces,
  FileCode2,
  GitFork,
  RefreshCw,
  Search,
  ShieldCheck,
  TestTube2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";

import { loadCodeIntelligence } from "../api/code";
import { errorDetail } from "../api/client";
import { exactTime, friendlyTime, shortId, titleCase } from "../format";
import type {
  CodeGraphRepoSummary,
  CodeImpactBrief,
  CodeIntelligenceResponse,
} from "../types";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "./ui/select";

export function CodeIntelligenceView({ baseUrl }: { baseUrl: string }) {
  const [data, setData] = useState<CodeIntelligenceResponse | null>(null);
  const [selectedRepo, setSelectedRepo] = useState("");
  const [queryPath, setQueryPath] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [errorRaw, setErrorRaw] = useState<string | null>(null);
  const requestIdRef = useRef(0);

  const load = useCallback(
    async (options: { repo?: string; path?: string } = {}) => {
      const requestId = ++requestIdRef.current;
      setLoading(true);
      setError(null);
      setErrorRaw(null);
      try {
        const response = await loadCodeIntelligence(baseUrl, options);
        if (requestId !== requestIdRef.current) return;
        setData(response);
        setSelectedRepo((current) =>
          response.repos.some((repo) => repo.name === current)
            ? current
            : response.selected_repo || response.repos[0]?.name || "",
        );
      } catch (err) {
        if (requestId !== requestIdRef.current) return;
        setError(
          err instanceof Error
            ? err.message
            : "Could not read code intelligence.",
        );
        setErrorRaw(errorDetail(err));
      } finally {
        if (requestId === requestIdRef.current) setLoading(false);
      }
    },
    [baseUrl],
  );

  useEffect(() => {
    void load();
    return () => {
      requestIdRef.current += 1;
    };
  }, [load]);

  const selectedSummary = useMemo(
    () =>
      data?.repos.find((repo) => repo.name === selectedRepo) ||
      data?.repos[0] ||
      null,
    [data, selectedRepo],
  );

  const analyze = useCallback(
    (path = queryPath) => {
      const clean = path.trim();
      if (!selectedRepo || !clean) return;
      setQueryPath(clean);
      void load({ repo: selectedRepo, path: clean });
    },
    [load, queryPath, selectedRepo],
  );

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    analyze();
  };

  return (
    <section className="space-y-4 motion-fade" aria-label="Code intelligence">
      <header className="alfred-page-hero px-4 py-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0 space-y-1">
            <h1 className="font-heading text-2xl font-medium tracking-normal text-foreground">
              Code intelligence
            </h1>
            <p className="max-w-3xl text-sm text-muted-foreground">
              Check what a file can affect before Alfred changes it.
            </p>
          </div>
          <div className="flex items-center gap-2">
            {data?.generated_at ? (
              <span
                className="text-xs text-muted-foreground"
                title={exactTime(data.generated_at)}
              >
                indexed {friendlyTime(data.generated_at)}
              </span>
            ) : null}
            <Button
              variant="ghost"
              size="icon-sm"
              type="button"
              aria-label="Refresh code intelligence"
              disabled={loading}
              onClick={() => void load()}
            >
              <RefreshCw
                className={loading ? "animate-spin" : undefined}
                aria-hidden="true"
              />
            </Button>
          </div>
        </div>
      </header>

      {error ? (
        <div className="inline-notice inline-notice--error" role="alert">
          <AlertTriangle size={18} aria-hidden="true" />
          <div className="min-w-0">
            <strong>Code intelligence is unavailable.</strong>
            <p>{error}</p>
            {errorRaw && errorRaw !== error ? (
              <details className="notice-details">
                <summary>Details</summary>
                <pre>{errorRaw}</pre>
              </details>
            ) : null}
          </div>
        </div>
      ) : null}

      {!loading && !error && data?.repos.length === 0 ? <EmptyCodeMap /> : null}

      {!error && data?.repos.length ? (
        <>
          <form
            onSubmit={onSubmit}
            className="grid gap-3 rounded-lg border border-border/70 bg-card/45 p-3 shadow-sm backdrop-blur-xl lg:grid-cols-[minmax(11rem,0.32fr)_minmax(18rem,1fr)_auto] lg:items-end"
          >
            <label className="grid min-w-0 gap-1.5 text-xs font-medium text-muted-foreground">
              Repository
              <Select
                value={selectedRepo || selectedSummary?.name || ""}
                onValueChange={(value) => {
                  requestIdRef.current += 1;
                  setSelectedRepo(value);
                  setLoading(false);
                  setError(null);
                  setErrorRaw(null);
                  setData((current) =>
                    current ? { ...current, impact: null } : current,
                  );
                }}
              >
                <SelectTrigger className="w-full" aria-label="Repository">
                  <SelectValue placeholder="Choose a repository" />
                </SelectTrigger>
                <SelectContent align="start">
                  {data.repos.map((repo) => (
                    <SelectItem key={repo.name} value={repo.name}>
                      {repo.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>
            <label className="grid min-w-0 gap-1.5 text-xs font-medium text-muted-foreground">
              File path
              <Input
                value={queryPath}
                onChange={(event) => setQueryPath(event.target.value)}
                placeholder="src/server/routes.ts"
                autoComplete="off"
                spellCheck={false}
                aria-label="File path"
              />
            </label>
            <Button
              type="submit"
              disabled={loading || !selectedRepo || !queryPath.trim()}
            >
              <Search aria-hidden="true" />
              {loading && data?.impact ? "Analyzing" : "Analyze impact"}
            </Button>
          </form>

          {data.impact ? (
            <ImpactWorkspace
              impact={data.impact}
              onAnalyzeCandidate={analyze}
            />
          ) : selectedSummary ? (
            <RepositoryOverview
              repo={selectedSummary}
              driftCount={selectedSummary.contract_drift_count}
            />
          ) : null}
        </>
      ) : null}
    </section>
  );
}

function EmptyCodeMap() {
  return (
    <section className="rounded-lg border border-dashed border-border bg-card/35 p-6 text-center">
      <GitFork
        className="mx-auto mb-3 text-muted-foreground"
        aria-hidden="true"
      />
      <h2 className="font-heading text-lg font-medium text-foreground">
        No repositories indexed yet
      </h2>
      <p className="mx-auto mt-1 max-w-xl text-sm text-muted-foreground">
        Build the local code map once. Alfred will keep it refreshed after
        setup.
      </p>
      <code className="mt-4 inline-block rounded-md border border-border bg-muted/60 px-3 py-2 text-xs text-foreground">
        alfred code-map build .
      </code>
    </section>
  );
}

function RepositoryOverview({
  repo,
  driftCount,
}: {
  repo: CodeGraphRepoSummary;
  driftCount: number;
}) {
  const languages = Object.entries(repo.summary.languages)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6);
  return (
    <section className="space-y-4" aria-label={`${repo.name} index summary`}>
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border bg-border md:grid-cols-4">
        <Metric label="Files" value={repo.summary.files} />
        <Metric label="Symbols" value={repo.summary.symbols} />
        <Metric label="Imports" value={repo.summary.imports} />
        <Metric
          label="Contract drift"
          value={driftCount}
          tone={driftCount ? "warn" : "normal"}
        />
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(16rem,0.42fr)]">
        <section className="rounded-lg border border-border bg-card/45 p-4 backdrop-blur-xl">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-xs font-medium uppercase text-muted-foreground">
                Repository
              </p>
              <h2 className="mt-1 truncate font-heading text-lg font-medium text-foreground">
                {repo.name}
              </h2>
            </div>
            {repo.head_sha ? (
              <code className="rounded-md bg-muted px-2 py-1 text-xs text-muted-foreground">
                {shortId(repo.head_sha)}
              </code>
            ) : null}
          </div>
          <div className="mt-4 grid gap-2 sm:grid-cols-3">
            <Evidence label="Routes" value={repo.route_count} icon={GitFork} />
            <Evidence
              label="Endpoints"
              value={repo.endpoint_count}
              icon={Braces}
            />
            <Evidence
              label="API calls"
              value={repo.api_call_count}
              icon={ShieldCheck}
            />
          </div>
          {repo.summary.truncated ? (
            <div className="mt-4 flex gap-2 rounded-md border border-primary/35 bg-primary/10 p-3 text-sm text-foreground">
              <AlertTriangle
                className="mt-0.5 size-4 text-primary"
                aria-hidden="true"
              />
              The index reached its configured file limit. Impact results may be
              incomplete.
            </div>
          ) : null}
        </section>
        <section className="rounded-lg border border-border bg-card/45 p-4 backdrop-blur-xl">
          <h2 className="text-sm font-medium text-foreground">Languages</h2>
          <div className="mt-3 space-y-2">
            {languages.length ? (
              languages.map(([language, count]) => (
                <div
                  key={language}
                  className="flex items-center justify-between gap-3 text-sm"
                >
                  <span className="truncate text-muted-foreground">
                    {titleCase(language)}
                  </span>
                  <span className="tabular-nums text-foreground">{count}</span>
                </div>
              ))
            ) : (
              <p className="text-sm text-muted-foreground">
                No language data in this index.
              </p>
            )}
          </div>
        </section>
      </div>
    </section>
  );
}

function ImpactWorkspace({
  impact,
  onAnalyzeCandidate,
}: {
  impact: CodeImpactBrief;
  onAnalyzeCandidate: (path: string) => void;
}) {
  if (impact.match_status === "ambiguous") {
    return (
      <section className="rounded-lg border border-primary/40 bg-primary/10 p-4">
        <h2 className="font-heading text-lg font-medium text-foreground">
          Choose the exact file
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          More than one indexed path matches. Alfred will not guess.
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          {impact.candidate_matches.map((candidate) => (
            <Button
              key={candidate}
              variant="outline"
              size="sm"
              onClick={() => onAnalyzeCandidate(candidate)}
            >
              <FileCode2 aria-hidden="true" />
              {candidate}
            </Button>
          ))}
        </div>
      </section>
    );
  }
  if (impact.match_status === "not_found") {
    return (
      <section className="rounded-lg border border-border bg-card/45 p-5">
        <h2 className="font-heading text-lg font-medium text-foreground">
          File not found in the index
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Check the repository and path, or refresh the code map before trying
          again.
        </p>
      </section>
    );
  }

  const tone =
    impact.level === "high"
      ? "error"
      : impact.level === "medium"
        ? "warn"
        : "ok";
  return (
    <section className="space-y-4" aria-label="Impact analysis">
      <div className="rounded-lg border border-border bg-card/50 p-4 shadow-sm backdrop-blur-xl">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className={`status-pill status-pill--${tone}`}>
                <span aria-hidden="true" />
                {titleCase(impact.level)} impact
              </span>
              {impact.language ? (
                <span className="text-xs text-muted-foreground">
                  {titleCase(impact.language)}
                </span>
              ) : null}
            </div>
            <h2 className="mt-2 break-all font-mono text-sm font-medium text-foreground">
              {impact.matched_file || impact.path}
            </h2>
            <p className="mt-2 max-w-4xl text-sm text-muted-foreground">
              {plainImpactSummary(impact)}
            </p>
          </div>
          {impact.head_sha ? (
            <code className="shrink-0 rounded-md bg-muted px-2 py-1 text-xs text-muted-foreground">
              {shortId(impact.head_sha)}
            </code>
          ) : null}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border bg-border md:grid-cols-5">
        <Metric label="Dependents" value={impact.counts.direct_dependents} />
        <Metric
          label="Dependencies"
          value={impact.counts.direct_dependencies}
        />
        <Metric label="Symbols" value={impact.counts.symbols} />
        <Metric label="Contracts" value={impact.counts.contract_surfaces} />
        <Metric
          label="Drift"
          value={impact.counts.contract_drift}
          tone={impact.counts.contract_drift ? "warn" : "normal"}
          wideOnNarrow
        />
      </div>

      <section className="grid overflow-hidden rounded-lg border border-border bg-card/40 lg:grid-cols-[minmax(0,1fr)_minmax(12rem,0.65fr)_minmax(0,1fr)]">
        <RelationshipList
          title="Used by"
          rows={impact.direct_dependents}
          empty="No direct dependents found."
        />
        <div className="flex min-h-32 flex-col items-center justify-center border-y border-border bg-muted/35 p-4 text-center lg:border-x lg:border-y-0">
          <FileCode2 className="mb-2 text-primary" aria-hidden="true" />
          <strong className="break-all font-mono text-xs text-foreground">
            {impact.matched_file}
          </strong>
          <span className="mt-1 text-xs text-muted-foreground">
            selected file
          </span>
        </div>
        <RelationshipList
          title="Uses"
          rows={impact.direct_dependencies}
          empty="No direct dependencies found."
        />
      </section>

      <div className="grid gap-4 xl:grid-cols-2">
        <EvidenceList
          title="Symbols"
          icon={Braces}
          empty="No named symbols are attached to this file."
          items={impact.symbols.map((symbol) =>
            [
              symbol.name || "Unnamed symbol",
              typeof symbol.line === "number" ? `line ${symbol.line}` : null,
            ]
              .filter(Boolean)
              .join(" at "),
          )}
        />
        <EvidenceList
          title="Contract surfaces"
          icon={ShieldCheck}
          empty="No routes, endpoints, or API calls are attached to this file."
          items={impact.contract_surfaces.map((row) =>
            [row.kind, row.method, row.path].filter(Boolean).join(" "),
          )}
        />
        <EvidenceList
          title="Contract drift"
          icon={AlertTriangle}
          empty="No contract drift is recorded for this file."
          tone={impact.contract_drift.length ? "warn" : "normal"}
          items={impact.contract_drift.map((row) =>
            [
              row.method,
              row.path,
              row.normalized ? `normalized as ${row.normalized}` : null,
            ]
              .filter(Boolean)
              .join(" "),
          )}
        />
        <EvidenceList
          title="Nearby files and tests"
          icon={TestTube2}
          empty="No nearby indexed files were found."
          items={impact.nearby_files}
        />
        <EvidenceList
          title="Checks before changing it"
          icon={ShieldCheck}
          empty="No extra checks were generated."
          items={impact.next_checks}
        />
      </div>
    </section>
  );
}

function Metric({
  label,
  value,
  tone = "normal",
  wideOnNarrow = false,
}: {
  label: string;
  value: number;
  tone?: "normal" | "warn";
  wideOnNarrow?: boolean;
}) {
  return (
    <div
      className={`min-w-0 bg-card px-3 py-3 ${wideOnNarrow ? "col-span-2 md:col-span-1" : ""}`}
    >
      <strong
        className={tone === "warn" ? "text-destructive" : "text-foreground"}
      >
        {value}
      </strong>
      <p className="mt-0.5 truncate text-xs text-muted-foreground">{label}</p>
    </div>
  );
}

function plainImpactSummary(impact: CodeImpactBrief): string {
  const facts = [
    countPhrase(impact.counts.direct_dependents, "direct dependent"),
    countPhrase(
      impact.counts.direct_dependencies,
      "direct dependency",
      "direct dependencies",
    ),
    countPhrase(impact.counts.contract_surfaces, "contract surface"),
    countPhrase(impact.counts.contract_drift, "contract drift finding"),
  ].filter((value): value is string => Boolean(value));
  if (!facts.length) {
    return "No direct dependents, dependencies, or contract surfaces were found in the local index.";
  }
  if (facts.length === 1) return `The local index found ${facts[0]}.`;
  return `The local index found ${facts.slice(0, -1).join(", ")}, and ${facts[facts.length - 1]}.`;
}

function countPhrase(
  count: number,
  singular: string,
  pluralName = `${singular}s`,
): string | null {
  if (!count) return null;
  return `${count} ${count === 1 ? singular : pluralName}`;
}

function Evidence({
  label,
  value,
  icon: Icon,
}: {
  label: string;
  value: number;
  icon: typeof GitFork;
}) {
  return (
    <div className="flex items-center gap-2 rounded-md border border-border/70 bg-background/35 p-2.5">
      <Icon className="size-4 text-muted-foreground" aria-hidden="true" />
      <div>
        <strong className="text-sm text-foreground">{value}</strong>
        <p className="text-xs text-muted-foreground">{label}</p>
      </div>
    </div>
  );
}

function RelationshipList({
  title,
  rows,
  empty,
}: {
  title: string;
  rows: Array<{ path: string; via: string; kind: string }>;
  empty: string;
}) {
  return (
    <div className="min-w-0 p-4">
      <h3 className="text-xs font-medium uppercase text-muted-foreground">
        {title}
      </h3>
      <div className="mt-3 space-y-2">
        {rows.length ? (
          rows.map((row, index) => (
            <div
              key={`${row.path}-${index}`}
              className="min-w-0 rounded-md border border-border/70 bg-background/35 p-2.5"
            >
              <p className="break-all font-mono text-xs text-foreground">
                {row.path}
              </p>
              <p
                className="mt-1 truncate text-xs text-muted-foreground"
                title={row.via}
              >
                {titleCase(row.kind)} via {row.via}
              </p>
            </div>
          ))
        ) : (
          <p className="text-sm text-muted-foreground">{empty}</p>
        )}
      </div>
    </div>
  );
}

function EvidenceList({
  title,
  icon: Icon,
  items,
  empty,
  tone = "normal",
}: {
  title: string;
  icon: typeof ShieldCheck;
  items: string[];
  empty: string;
  tone?: "normal" | "warn";
}) {
  return (
    <section
      className={`rounded-lg border p-4 ${tone === "warn" ? "border-destructive/40 bg-destructive/10" : "border-border bg-card/45"}`}
    >
      <div className="flex items-center gap-2">
        <Icon
          className={`size-4 ${tone === "warn" ? "text-destructive" : "text-muted-foreground"}`}
          aria-hidden="true"
        />
        <h3 className="text-sm font-medium text-foreground">{title}</h3>
      </div>
      {items.length ? (
        <ul className="mt-3 space-y-2">
          {items.map((item, index) => (
            <li
              key={`${item}-${index}`}
              className="break-words text-sm text-muted-foreground"
            >
              {item}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 text-sm text-muted-foreground">{empty}</p>
      )}
    </section>
  );
}
