import { ArrowRight, Download, KeyRound, MessageCircle, Server, Sparkles } from "lucide-react";

import type { SetupInstallInventory, SetupStatus } from "../../types";
import { InstallInventoryPanel } from "./InstallInventoryPanel";
import { Button } from "../ui";

/**
 * Step 0: Welcome. The hero screen of the setup takeover. It says the value
 * once, shows any detected local install, then leads with the trust
 * differentiator (no API keys, runs on the subscriptions you already pay for),
 * and offers one primary door for the guided path plus a quiet shortcut for a
 * developer who already has a server running.
 *
 * It deliberately does not carry a StepFrame title above it: the journey
 * framing is said once in the shell, and the value is said once here.
 */
export function WelcomeStep({
  install,
  queue,
  connected,
  canRun,
  nativeBusy,
  onInstallCore,
  onGetStarted,
  onChatSetup,
  onDevShortcut,
}: {
  install?: SetupInstallInventory | null;
  queue?: SetupStatus["queue"] | null;
  connected: boolean;
  canRun: boolean;
  nativeBusy: string | null;
  onInstallCore: () => void;
  onGetStarted: () => void;
  // Open the conversational setup: Alfred walks the person through setup in chat.
  onChatSetup: () => void;
  onDevShortcut: () => void;
}) {
  const needsNativeInstall = canRun && !connected;
  const installBusy = nativeBusy === "core:install" || nativeBusy === "runtime:start";
  const primaryLabel =
    nativeBusy === "core:install"
      ? "Installing"
      : nativeBusy === "runtime:start"
        ? "Starting"
        : needsNativeInstall
          ? "Install Alfred"
          : "Get started";

  return (
    <div className="alfred-onboarding-welcome grid gap-6">
      <div className="grid max-w-xl gap-3">
        <span
          className="status-live-glow flex size-12 items-center justify-center rounded-none border border-primary/40 bg-primary/15 text-primary"
          aria-hidden="true"
        >
          <Sparkles size={22} />
        </span>
        <h2 className="font-heading text-2xl font-medium tracking-tight text-foreground">
          Let's get you set up.
        </h2>
        <p className="text-base text-muted-foreground">
          A few short steps. Alfred checks this Mac, connects to GitHub, and ends
          on a real result you can see.
        </p>
      </div>

      <div className="alfred-onboarding-welcome__trust">
        <KeyRound size={15} aria-hidden="true" />
        <span>
          No API keys. Alfred runs on the Claude Max and Codex Pro subscriptions
          you already pay for.
        </span>
      </div>

      <InstallInventoryPanel inventory={install} queue={queue} />

      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          size="lg"
          className="btn-primary-glow"
          disabled={installBusy}
          onClick={needsNativeInstall ? onInstallCore : onGetStarted}
        >
          {needsNativeInstall ? (
            <Download size={16} aria-hidden="true" />
          ) : (
            <ArrowRight size={16} aria-hidden="true" />
          )}
          <span>{primaryLabel}</span>
        </Button>
        {needsNativeInstall ? (
          <Button type="button" variant="outline" size="lg" onClick={onGetStarted}>
            <ArrowRight size={16} aria-hidden="true" />
            <span>Continue setup</span>
          </Button>
        ) : (
          <Button type="button" variant="outline" size="lg" onClick={onChatSetup}>
            <MessageCircle size={16} aria-hidden="true" />
            <span>Set it up by chatting</span>
          </Button>
        )}
        <Button type="button" variant="ghost" size="lg" onClick={onDevShortcut}>
          <Server size={16} aria-hidden="true" />
          <span>I have a server running</span>
        </Button>
      </div>
    </div>
  );
}
