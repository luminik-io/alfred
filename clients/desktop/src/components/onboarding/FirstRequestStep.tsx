import { ArrowRight, GaugeCircle, PlayCircle, Sparkles, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { errorDetail } from "../../api/client";
import {
  clearSetupDemo,
  composeSetupPlaybook,
  loadSetupPlaybooks,
  seedSetupDemo,
} from "../../api/setup";
import type { SetupPlaybook } from "../../types";
import { Button, Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "../ui";
import type { OnboardingNotice } from "./types";

/**
 * Step 5: First request (the payoff). The journey always ends on a populated
 * Inbox, never an empty one.
 *
 *  - Guided path: starter specs as plain cards (GET /api/setup/playbooks).
 *    Selecting one drafts a real first Request (POST /api/setup/playbook) and
 *    opens Ask so the request can be refined in plain words.
 *  - Demo path: "Show me a sample first" seeds a labelled demo lifecycle
 *    (POST /api/setup/demo) so Home / Pipeline render populated and clearly
 *    "Sample". The step then keeps a "Clear sample data" control
 *    (POST /api/setup/demo/clear) next to "Open Home" so the sample is never a
 *    one-way door.
 *  - Operator shortcut: skip straight to writing a brief in Ask.
 */
export function FirstRequestStep({
  baseUrl,
  canMutate,
  finishing,
  setupReady,
  demoPresent,
  setNotice,
  onOpenCompose,
  onOpenInbox,
  onComplete,
  onSeedDemo,
  onClearDemo,
}: {
  baseUrl: string;
  canMutate: boolean;
  finishing: boolean;
  setupReady: boolean;
  // Server truth from SetupStatus.demo.present, so the "Clear sample data"
  // exit survives a remount (open Inbox, reload, navigate back) instead of
  // depending only on the in-component seed flag, which resets to false.
  demoPresent: boolean;
  setNotice: (notice: OnboardingNotice) => void;
  onOpenCompose: () => void | Promise<boolean>;
  onOpenInbox: () => void | Promise<boolean>;
  // Called after a real request or demo lands so the orchestrator can mark the
  // journey complete and refresh the board.
  onComplete: (kind: "request" | "demo") => void;
  // Seed the demo lifecycle and refresh the board, owned by the orchestrator so
  // it can also flip the board into demo mode.
  onSeedDemo: () => Promise<void>;
  // Flip the board back out of demo mode after the sample is cleared, owned by
  // the orchestrator so it can also refresh the board with demo: false.
  onClearDemo: () => Promise<void>;
}) {
  const [playbooks, setPlaybooks] = useState<SetupPlaybook[]>([]);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [requestDrafted, setRequestDrafted] = useState(false);
  const [demoBusy, setDemoBusy] = useState(false);
  // Optimistic local override so the seed/clear toggle flips instantly, before
  // the parent's status refresh resolves. null means "defer to server truth"
  // (demoPresent); true/false is an in-flight optimistic value. This keeps the
  // "Clear sample data" exit visible across a remount, because demoPresent is
  // sourced from SetupStatus.demo.present rather than a flag that resets.
  const [demoSeededOverride, setDemoSeededOverride] = useState<boolean | null>(null);
  const demoSeeded = demoSeededOverride ?? demoPresent;
  const [clearBusy, setClearBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Once the first job fires (a real request drafted or a sample seeded), show a
  // plain "what this costs" reassurance so a solo builder sees spend is legible
  // and under control before anything runs for real. It is an honest range on
  // the subscriptions they already pay for, not a per-token invoice.
  const [firstJobFired, setFirstJobFired] = useState(false);

  // Once the server confirms a state that matches the optimistic override,
  // drop the override so the component tracks server truth again.
  useEffect(() => {
    if (demoSeededOverride !== null && demoSeededOverride === demoPresent) {
      setDemoSeededOverride(null);
    }
  }, [demoSeededOverride, demoPresent]);

  useEffect(() => {
    let cancelled = false;
    loadSetupPlaybooks(baseUrl)
      .then((result) => {
        if (!cancelled) setPlaybooks(result.playbooks);
      })
      .catch((err) => {
        if (!cancelled) setError(errorDetail(err) || "Could not load starter specs.");
      });
    return () => {
      cancelled = true;
    };
  }, [baseUrl]);

  const pick = async (key: string) => {
    setBusyKey(key);
    try {
      const result = await composeSetupPlaybook(baseUrl, key);
      // This path navigates to Compose immediately, which unmounts this step, so
      // the inline cost chip below would never be seen. Fold the spend
      // reassurance into the success notice instead, so it surfaces on the screen
      // the user actually lands on.
      setNotice({
        tone: "ok",
        message: `Drafted your first request: "${result.title}". Refine it in Ask, then save the plan. It runs on the Claude and Codex subscriptions you already pay for, with no per-request bill, and you can watch usage in the sidebar.`,
      });
      setRequestDrafted(true);
      onComplete("request");
      await onOpenCompose();
    } catch (err) {
      setNotice({ tone: "error", message: errorDetail(err) || "Could not draft from that spec." });
    } finally {
      setBusyKey(null);
    }
  };

  const seedDemo = async () => {
    setDemoBusy(true);
    try {
      await seedSetupDemo(baseUrl);
      setDemoSeededOverride(true);
      setFirstJobFired(true);
      await onSeedDemo();
      setNotice({
        tone: "ok",
        message:
          "Seeded a sample lifecycle, clearly labelled. Inbox and Work are populated. Clear it whenever you like.",
      });
      onComplete("demo");
    } catch (err) {
      setNotice({ tone: "error", message: errorDetail(err) || "Could not seed the sample." });
    } finally {
      setDemoBusy(false);
    }
  };

  const clearDemo = async () => {
    setClearBusy(true);
    try {
      await clearSetupDemo(baseUrl);
      setDemoSeededOverride(false);
      await onClearDemo();
      setNotice({
        tone: "ok",
        message: "Cleared the sample data. Inbox and Work are back to your real work.",
      });
    } catch (err) {
      setNotice({ tone: "error", message: errorDetail(err) || "Could not clear the sample." });
    } finally {
      setClearBusy(false);
    }
  };

  return (
    <div className="grid gap-4">
      {error ? (
        <Card size="sm" className="rounded-lg border-border/70 bg-muted/35 shadow-none">
          <CardContent className="px-3 text-sm text-muted-foreground">{error}</CardContent>
        </Card>
      ) : null}

      {firstJobFired || demoSeeded ? (
        <div className="alfred-onboarding-cost-chip" role="status">
          <GaugeCircle size={16} aria-hidden="true" />
          <span>
            Here's what this costs: it runs on the Claude and Codex subscriptions
            you already pay for, so there's no per-request bill. Watch live usage
            and limits any time in the sidebar.
          </span>
        </div>
      ) : null}

      <div className="grid gap-2">
        <p className="text-sm font-medium text-foreground">Pick something for Alfred to do first</p>
        {!setupReady ? (
          <p className="text-sm text-muted-foreground">
            Finish Tools, GitHub, and Repositories before starting the first job.
          </p>
        ) : null}
        {playbooks.map((playbook) => (
          <Card
            size="sm"
            className="rounded-lg border-border/70 bg-background/55 shadow-none"
            key={playbook.key}
          >
            <CardHeader className="gap-2 md:grid-cols-[1fr_auto]">
              <div className="min-w-0">
                <CardTitle className="text-sm">{playbook.title}</CardTitle>
                <CardDescription>{playbook.summary}</CardDescription>
              </div>
              <CardAction>
                <Button
                  variant="outline"
                  type="button"
                  onClick={() => void pick(playbook.key)}
                  disabled={
                    !canMutate || !setupReady || busyKey !== null || requestDrafted || finishing
                  }
                >
                  <Sparkles size={14} aria-hidden="true" />
                  <span>{busyKey === playbook.key ? "Drafting" : "Use this"}</span>
                </Button>
              </CardAction>
            </CardHeader>
          </Card>
        ))}
        {playbooks.length === 0 && !error ? (
          <p className="text-sm text-muted-foreground">Loading starter specs.</p>
        ) : null}
      </div>

      <Card size="sm" className="rounded-lg border-border/70 bg-muted/25 shadow-none">
        <CardContent className="grid gap-2 px-3">
          <p className="text-sm font-medium text-foreground">Just want to look first?</p>
          {demoSeeded ? (
            <>
              <p className="text-sm text-muted-foreground">
                Sample data is active. Inbox, Work, and shipped outcomes are populated and clearly labelled
                "Sample". Clear it whenever you want your real board back.
              </p>
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  type="button"
                  onClick={() => void clearDemo()}
                  disabled={!canMutate || clearBusy}
                >
                  <Trash2 size={15} aria-hidden="true" />
                  <span>{clearBusy ? "Clearing" : "Clear sample data"}</span>
                </Button>
                <Button
                  variant="ghost"
                  type="button"
                  onClick={() => void onOpenInbox()}
                  disabled={!setupReady || finishing}
                >
                  <span>Open Inbox</span>
                  <ArrowRight size={15} aria-hidden="true" />
                </Button>
              </div>
            </>
          ) : (
            <>
              <p className="text-sm text-muted-foreground">
                Seed a sample lifecycle so Inbox, Work, and shipped outcomes render populated and clearly
                labelled "Sample". Clear it any time.
              </p>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  onClick={() => void seedDemo()}
                  disabled={!canMutate || !setupReady || demoBusy}
                >
                  <PlayCircle size={15} aria-hidden="true" />
                  <span>{demoBusy ? "Seeding" : "Show me a sample first"}</span>
                </Button>
                <Button
                  variant="ghost"
                  type="button"
                  onClick={() => void onOpenCompose()}
                  disabled={!setupReady || finishing}
                >
                  <span>Write a brief in Ask</span>
                  <ArrowRight size={15} aria-hidden="true" />
                </Button>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      {!canMutate ? (
        <p className="rounded-lg border border-border/70 bg-muted/35 px-3 py-2 text-sm text-muted-foreground">
          The desktop app drafts a first request and seeds the sample. The browser preview can read
          the starter specs but cannot draft or seed.
        </p>
      ) : null}
    </div>
  );
}
