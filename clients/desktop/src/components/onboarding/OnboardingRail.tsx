// The onboarding takeover's left rail: brand, the value promise, the three-step
// loop, and the trust + spend reassurance. Purely presentational; it takes only
// the completed-step tally so the footer can show honest progress. Collapses
// above the main column at narrow widths (styling owned by CSS).
export function OnboardingRail({
  completedCount,
  totalSteps,
}: {
  completedCount: number;
  totalSteps: number;
}) {
  return (
    <aside className="alfred-onboarding-rail alfred-glass" aria-hidden="true">
      <div className="alfred-onboarding-rail__brand">
        <span className="alfred-brand-mark size-9 shrink-0">
          <img
            src="/brand/alfred-logo-transparent.png"
            alt=""
            className="alfred-brand-logo size-9 object-contain"
          />
        </span>
        <span className="alfred-onboarding-rail__wordmark">
          <span className="alfred-onboarding-rail__name">Alfred</span>
          <span className="alfred-onboarding-rail__kicker">Autonomous coding agents</span>
        </span>
      </div>

      <div className="alfred-onboarding-rail__promise">
        <p className="alfred-onboarding-rail__eyebrow">Set up in about two minutes</p>
        <h2 className="alfred-onboarding-rail__headline">
          Wake up to shipped work you can trust.
        </h2>
        <p className="alfred-onboarding-rail__sub">
          Alfred opens pull requests, handles reviews, and reports back, all on
          your own machine while you stay in control.
        </p>
      </div>

      {/* Fills the rail with the actual loop, so the intro reads as one
          composed block instead of a headline floating over empty space. */}
      <ol className="alfred-onboarding-rail__loop">
        <li>
          <span className="alfred-onboarding-rail__loopstep">1</span>
          <span>Connect your repositories and local tools.</span>
        </li>
        <li>
          <span className="alfred-onboarding-rail__loopstep">2</span>
          <span>Approve a plan; the team builds and reviews it.</span>
        </li>
        <li>
          <span className="alfred-onboarding-rail__loopstep">3</span>
          <span>Alfred opens the pull request and reports back.</span>
        </li>
      </ol>

      <div className="alfred-onboarding-rail__foot">
        <p className="alfred-onboarding-rail__trust">
          No API keys. Alfred runs on the Claude and Codex subscriptions you
          already pay for.
        </p>
        <p className="alfred-onboarding-rail__cost">
          No per-request bill. Watch live usage and limits any time in the
          sidebar.
        </p>
        <p className="alfred-onboarding-rail__progress">
          {completedCount} of {totalSteps} steps done
        </p>
      </div>
    </aside>
  );
}
