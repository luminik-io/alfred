import { CheckCircle2, CircleAlert, FolderOpen, RefreshCw, Search } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { errorDetail } from "../../api/client";
import {
  loadSetupRepos,
  pickSetupRepoFolder,
  saveSetupRepos,
  SetupRepoCheckoutValidationError,
} from "../../api/setup";
import type { SetupRepo, SetupRepoCheckout } from "../../types";
import { Badge, Button, Card, CardContent, Input } from "../ui";
import type { OnboardingNotice } from "./types";

export type RepoSaveOutcome = {
  indexed: boolean;
  warning?: string;
};

function repoShortName(slug: string): string {
  const slash = slug.lastIndexOf("/");
  return slash === -1 ? slug : slug.slice(slash + 1);
}

function repoOwner(slug: string): string {
  return slug.split("/", 1)[0]?.toLowerCase() || "";
}

function repoSelectionOwner(slugs: Iterable<string>): string | null {
  for (const slug of slugs) {
    const owner = repoOwner(slug);
    if (owner) return owner;
  }
  return null;
}

function checkoutMessage(checkout: SetupRepoCheckout | undefined): string | null {
  if (!checkout) return null;
  if (checkout.ready) return "GitHub repository verified";
  switch (checkout.reason) {
    case "missing":
      return "Folder not found";
    case "not_git_repo":
      return "This folder is not a Git checkout";
    case "missing_github_remote":
      return "This checkout has no GitHub remote";
    case "remote_mismatch":
      return checkout.github_remote_repo
        ? `${checkout.github_remote_name || "Remote"} points to ${
            checkout.github_remote_repo
          }, not ${checkout.repo}`
        : "This checkout belongs to another repository";
    default:
      return null;
  }
}

/** Pick repository scope and prove where every selected checkout lives locally. */
export function ReposStep({
  baseUrl,
  canMutate,
  canRun,
  githubConnected,
  selectedCount,
  onSaved,
  setNotice,
}: {
  baseUrl: string;
  canMutate: boolean;
  canRun: boolean;
  githubConnected: boolean;
  selectedCount: number;
  onSaved: (repos: string[], checkouts: SetupRepoCheckout[]) => Promise<RepoSaveOutcome>;
  setNotice: (notice: OnboardingNotice) => void;
}) {
  const [repos, setRepos] = useState<SetupRepo[]>([]);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [paths, setPaths] = useState<Map<string, string>>(new Map());
  const [checkouts, setCheckouts] = useState<Map<string, SetupRepoCheckout>>(new Map());
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [retryingIndex, setRetryingIndex] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [indexWarning, setIndexWarning] = useState<string | null>(null);
  const [savedRepos, setSavedRepos] = useState<string[] | null>(null);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await loadSetupRepos(baseUrl);
      setRepos(result.repos);
      setPicked(new Set(result.selected.map((repo) => repo.toLowerCase())));
      setCheckouts(
        new Map(result.repo_checkouts.map((row) => [row.repo.toLowerCase(), row] as const)),
      );
      setPaths(
        new Map(result.repo_checkouts.map((row) => [row.repo.toLowerCase(), row.path] as const)),
      );
      setError(result.error || null);
      setLoaded(true);
    } catch (err) {
      setError(errorDetail(err) || "Could not list your repositories.");
    } finally {
      setLoading(false);
    }
  }, [baseUrl]);

  useEffect(() => {
    if (githubConnected) void load();
  }, [githubConnected, load]);

  const visibleRepos = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return repos;
    return repos.filter((repo) =>
      `${repo.name_with_owner} ${repo.description || ""}`.toLowerCase().includes(needle),
    );
  }, [query, repos]);

  const toggle = (slug: string) => {
    setPicked((previous) => {
      const next = new Set(previous);
      const key = slug.toLowerCase();
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
    setSavedRepos(null);
    setIndexWarning(null);
  };

  const setCheckoutPath = (slug: string, path: string) => {
    const key = slug.toLowerCase();
    setPaths((previous) => new Map(previous).set(key, path));
    setCheckouts((previous) => {
      const next = new Map(previous);
      next.delete(key);
      return next;
    });
    setSavedRepos(null);
    setIndexWarning(null);
  };

  const chooseCheckout = async (slug: string) => {
    const key = slug.toLowerCase();
    try {
      const selected = await pickSetupRepoFolder(paths.get(key));
      if (selected) setCheckoutPath(slug, selected);
    } catch (err) {
      setNotice({
        tone: "error",
        message: errorDetail(err) || "Could not open the folder picker.",
      });
    }
  };

  const selectedRepos = useMemo(() => {
    const names = new Map(
      repos.map((repo) => [repo.name_with_owner.toLowerCase(), repo.name_with_owner] as const),
    );
    return Array.from(picked).map((slug) => names.get(slug) || slug);
  }, [picked, repos]);
  const selectionOwner = useMemo(() => repoSelectionOwner(picked), [picked]);
  const missingPaths = selectedRepos.filter((repo) => !paths.get(repo.toLowerCase())?.trim());
  const canSave =
    canMutate &&
    loaded &&
    !loading &&
    !error &&
    (selectedRepos.length > 0 || selectedCount > 0) &&
    missingPaths.length === 0 &&
    !saving;

  const save = async () => {
    if (!canSave) return;
    setSaving(true);
    setError(null);
    setIndexWarning(null);
    try {
      const repoCheckouts = selectedRepos.map((repo) => ({
        repo,
        path: paths.get(repo.toLowerCase())!.trim(),
      }));
      const result = await saveSetupRepos(baseUrl, selectedRepos, repoCheckouts);
      setSavedRepos(result.repos);
      setCheckouts(
        new Map(result.repo_checkouts.map((row) => [row.repo.toLowerCase(), row] as const)),
      );
      setPaths(
        new Map(result.repo_checkouts.map((row) => [row.repo.toLowerCase(), row.path] as const)),
      );
      const outcome = await onSaved(result.repos, result.repo_checkouts);
      setIndexWarning(outcome.warning || null);
      setNotice({
        tone: "ok",
        message: result.repos.length
          ? `Saved and verified ${result.repos.length} ${
              result.repos.length === 1 ? "repository" : "repositories"
            }${outcome.indexed ? ", then built the code graph" : ""}.`
          : "Cleared repository scope.",
      });
    } catch (err) {
      if (err instanceof SetupRepoCheckoutValidationError) {
        setCheckouts(
          new Map(err.rows.map((row) => [row.repo.toLowerCase(), row] as const)),
        );
        setPaths(new Map(err.rows.map((row) => [row.repo.toLowerCase(), row.path] as const)));
      }
      setNotice({
        tone: "error",
        message: errorDetail(err) || "Could not save your repository selection.",
      });
    } finally {
      setSaving(false);
    }
  };

  const retryIndex = async () => {
    if (!savedRepos?.length || retryingIndex) return;
    const rows = savedRepos
      .map((repo) => checkouts.get(repo.toLowerCase()))
      .filter((row): row is SetupRepoCheckout => Boolean(row?.ready));
    if (rows.length !== savedRepos.length) return;
    setRetryingIndex(true);
    try {
      const outcome = await onSaved(savedRepos, rows);
      setIndexWarning(outcome.warning || null);
      if (outcome.indexed) {
        setNotice({ tone: "ok", message: "The code graph now covers the selected repositories." });
      }
    } catch (err) {
      setIndexWarning(errorDetail(err) || "The code graph build failed. Retry indexing.");
    } finally {
      setRetryingIndex(false);
    }
  };

  if (!githubConnected) {
    return (
      <Card size="sm" className="rounded-lg border-border/70 bg-muted/35 shadow-none">
        <CardContent className="px-3 text-sm text-muted-foreground">
          Connect GitHub first. Your repositories will appear here.
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="grid gap-3">
      <div className="relative">
        <Search
          size={15}
          aria-hidden="true"
          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground"
        />
        <Input
          aria-label="Search repositories"
          className="pl-9"
          placeholder="Search repositories"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          disabled={!loaded || loading}
        />
      </div>

      {error ? (
        <Card size="sm" className="rounded-lg border-destructive/30 bg-destructive/10 shadow-none">
          <CardContent className="px-3 text-sm text-destructive">{error}</CardContent>
        </Card>
      ) : null}

      {loaded && !error ? (
        repos.length ? (
          <div
            className="grid max-h-[44vh] gap-2 overflow-y-auto pr-1"
            role="group"
            aria-label="Repositories Alfred may work in"
          >
            {visibleRepos.map((repo) => {
              const key = repo.name_with_owner.toLowerCase();
              const selected = picked.has(key);
              const sameOwner = !selectionOwner || repoOwner(key) === selectionOwner;
              const selectable = selected || (repo.selectable !== false && sameOwner);
              const checkout = checkouts.get(key);
              const checkoutStatus = checkoutMessage(checkout);
              return (
                <div
                  className="grid gap-2 rounded-lg border border-border/70 bg-background/55 px-3 py-2"
                  key={repo.name_with_owner}
                >
                  <label
                    className={`grid grid-cols-[auto_1fr_auto] gap-2 ${
                      selectable ? "cursor-pointer" : "cursor-not-allowed opacity-55"
                    }`}
                  >
                    <input
                      className="mt-1 size-4 accent-primary"
                      type="checkbox"
                      checked={selected}
                      onChange={() => toggle(repo.name_with_owner)}
                      disabled={!selectable}
                    />
                    <span className="grid min-w-0 gap-0.5">
                      <span className="truncate text-sm font-medium text-foreground">
                        {repoShortName(repo.name_with_owner)}
                      </span>
                      {repo.description ? (
                        <span className="line-clamp-2 text-xs text-muted-foreground">
                          {repo.description}
                        </span>
                      ) : null}
                      <span className="truncate font-mono text-[0.7rem] text-muted-foreground/80">
                        {repo.name_with_owner}
                      </span>
                    </span>
                    <span className="flex flex-wrap justify-end gap-1">
                      {repo.is_private ? <Badge variant="outline">private</Badge> : null}
                      {repo.listed === false ? <Badge variant="secondary">saved</Badge> : null}
                      {!selectable ? <Badge variant="secondary">different owner</Badge> : null}
                    </span>
                  </label>

                  {selected ? (
                    <div className="grid gap-1 border-t border-border/55 pt-2">
                      <div className="flex gap-2">
                        <Input
                          aria-label={`Local checkout for ${repo.name_with_owner}`}
                          className="min-w-0 font-mono text-xs"
                          placeholder="Choose the local checkout"
                          value={paths.get(key) || ""}
                          onChange={(event) => setCheckoutPath(repo.name_with_owner, event.target.value)}
                          disabled={!canMutate}
                        />
                        <Button
                          variant="outline"
                          size="icon"
                          type="button"
                          title="Choose checkout folder"
                          aria-label={`Choose checkout folder for ${repo.name_with_owner}`}
                          onClick={() => void chooseCheckout(repo.name_with_owner)}
                          disabled={!canRun}
                        >
                          <FolderOpen size={15} aria-hidden="true" />
                        </Button>
                      </div>
                      {checkoutStatus ? (
                        <p
                          className={
                            checkout?.ready
                              ? "flex items-center gap-1 text-xs text-primary"
                              : "flex items-center gap-1 text-xs text-amber-700 dark:text-amber-300"
                          }
                        >
                          {checkout?.ready ? (
                            <CheckCircle2 size={13} aria-hidden="true" />
                          ) : (
                            <CircleAlert size={13} aria-hidden="true" />
                          )}
                          <span>{checkoutStatus}</span>
                        </p>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              );
            })}
            {visibleRepos.length === 0 ? (
              <p className="px-1 py-5 text-center text-sm text-muted-foreground">
                No repositories match that search.
              </p>
            ) : null}
          </div>
        ) : (
          <Card size="sm" className="rounded-lg border-border/70 bg-muted/35 shadow-none">
            <CardContent className="px-3 text-sm text-muted-foreground">
              <strong className="block text-foreground">No repositories found.</strong>
              GitHub did not return any repositories for this account.
            </CardContent>
          </Card>
        )
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" onClick={() => void save()} disabled={!canSave}>
          <CheckCircle2 size={15} aria-hidden="true" />
          <span>
            {saving
              ? "Verifying"
              : selectedRepos.length
                ? `Save ${selectedRepos.length} selected`
                : "Clear repository scope"}
          </span>
        </Button>
        <Button variant="outline" type="button" onClick={() => void load()} disabled={loading}>
          <RefreshCw size={14} aria-hidden="true" className={loading ? "animate-spin" : undefined} />
          <span>{loading ? "Loading" : "Refresh"}</span>
        </Button>
        {missingPaths.length ? (
          <span className="text-xs text-muted-foreground">
            Choose {missingPaths.length} local {missingPaths.length === 1 ? "checkout" : "checkouts"}.
          </span>
        ) : null}
      </div>

      {indexWarning ? (
        <div className="flex items-center gap-2 rounded-lg border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-sm text-amber-800 dark:text-amber-200">
          <CircleAlert size={15} aria-hidden="true" className="shrink-0" />
          <span className="min-w-0 flex-1">{indexWarning}</span>
          {canRun && savedRepos?.length ? (
            <Button
              variant="outline"
              size="sm"
              type="button"
              onClick={() => void retryIndex()}
              disabled={retryingIndex}
            >
              <RefreshCw
                size={14}
                aria-hidden="true"
                className={retryingIndex ? "animate-spin" : undefined}
              />
              <span>{retryingIndex ? "Retrying" : "Retry code graph"}</span>
            </Button>
          ) : null}
        </div>
      ) : null}

      {savedRepos ? (
        <p className="text-sm text-muted-foreground">
          Alfred is scoped to {savedRepos.join(", ")}.
        </p>
      ) : selectedCount ? (
        <p className="text-sm text-muted-foreground">
          {selectedCount} {selectedCount === 1 ? "repository" : "repositories"} already selected.
        </p>
      ) : null}

      {!canMutate ? (
        <p className="rounded-lg border border-border/70 bg-muted/35 px-3 py-2 text-sm text-muted-foreground">
          Open the desktop app to choose local checkout folders and save this scope.
        </p>
      ) : null}
    </div>
  );
}
