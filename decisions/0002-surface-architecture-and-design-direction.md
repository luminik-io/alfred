# ADR 0002: Surface architecture and native client design direction

- **Status**: Proposed (working baseline, not locked; rethink and refactor freely if a better shape emerges)
- **Date**: 2026-06-12
- **Author**: Alfred maintainers
- **Related**: the native conversation control epic (headless serve, conversational Slack, lifecycle client)

## Context

Alfred has grown four surfaces (Slack listener, Tauri native client, alfred-serve localhost API, alfred CLI) plus a question of whether a separate web UI should exist. The goals: Slack as the primary, fully conversational interface; the native client as the non-developer door and decision surface; no surface kept out of inertia; and a premium visual standard for the client.

## Decision 1: one runtime, four doors

- **alfred-serve stays and becomes the only state-access path.** It is the single FastAPI runtime over `$ALFRED_HOME/state`. The Tauri client, the browser dev mode, and future surfaces all read and mutate through it. Nothing else parses state files directly on the read path (the Slack listener and runners keep their direct access on the write path; consolidating that is out of scope here).
- **There is no web UI at all** (supersedes the earlier bundle-serving idea). The Tauri client is the only GUI; users who skip the client are CLI-driven by definition. The server-rendered dashboard that lived inside alfred-serve was removed; alfred serve is a headless JSON/SSE API. The React app still runs in a browser via Vite for development only, never as a product surface.
- **The CLI stays as the bootstrap and power surface.** `alfred init / doctor / status / claude / engine / schedule` are the setup and depth tools, and what AI-assisted install drives. The CLI never grows UI features the client already owns; the client never grows setup plumbing the CLI already owns (it shells to it via native actions instead).
- **Slack is the primary daily interface.** Conversational control, confirmation cards, morning recaps. The client is for decisions with visual weight: plan sign-off, shipped evidence, lessons review, onboarding.

Consequence: the dashboard templates are gone; every new feature routes through the API contract first.

## Decision 2: design direction for the client

- **Base**: near-black neutral base (not navy gray), one ambient radial gradient at low opacity for depth, content on floating glass panels (backdrop-blur, 1px soft border, inset top highlight). macOS vibrancy in the Tauri shell, CSS fallback in the browser.
- **Accent discipline**: a single brand accent with restrained glow for primary actions and the active state; semantic ok/warn/error reserved for status only. No multi-color decoration.
- **Card grammar for work**: every issue/PR/shipped card carries status chip, repo chip, age, and one-line outcome in plain language. Shipped cards lead with the outcome sentence, never the PR title.
- **Motion**: 120-200ms ease-out transitions only; rise-on-mount for panels; micro-nudges on hover (chevrons, 2px translate); no springs, no bounce. Respect prefers-reduced-motion.
- **Typography**: one variable sans carries the whole UI, headings differentiated by weight and tracking; one mono strictly for transcripts, ids, and code. Numbers in tables use tabular-nums.
- **Status honesty**: an agent with a schedule but no run is Scheduled, not Running. Runs ending in llm-error are Failing, not completed. The UI must never look healthier than the fleet is.

## Decision 3: signing

Desktop builds get Apple Developer ID Application signing + notarization wired into `tauri.conf.json` (signingIdentity) and the desktop release workflow (cert + notarytool keychain profile via repo secrets). Until a signing certificate is configured, local unsigned builds remain fine for testing.

## Alternatives considered

- Separate lightweight web dashboard inside alfred-serve (status quo drift): rejected, two UIs to keep honest.
- Client talks to state files directly via Tauri fs (drop alfred-serve): rejected, loses browser mode, duplicates reader logic in Rust, breaks the one-API contract that Slack tier-2 answers and future surfaces also need.
- Full glassmorphism across every element: rejected, glass is for floating layers (panels, palette, dialogs) over the ambient base; flat surfaces stay flat for readability.
