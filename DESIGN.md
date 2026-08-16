---
name: Alfred
description: A calm operations interface for autonomous coding-agent work with visible control and merge boundaries.
colors:
  canvas: "oklch(0.975 0.006 95)"
  ink: "oklch(0.255 0.015 240)"
  surface: "oklch(0.995 0.004 95 / 0.84)"
  surface-muted: "oklch(0.955 0.008 95 / 0.82)"
  primary-teal: "oklch(0.49 0.075 195)"
  primary-ink: "oklch(0.99 0.003 95)"
  muted-ink: "oklch(0.47 0.018 240)"
  hairline: "oklch(0.30 0.015 240 / 0.12)"
  signal-mint: "oklch(0.72 0.105 165)"
  signal-rose: "oklch(0.67 0.15 350)"
  signal-violet: "oklch(0.61 0.13 295)"
  status-ok: "oklch(0.50 0.12 158)"
  status-warn: "oklch(0.61 0.13 78)"
  status-error: "oklch(0.55 0.20 25)"
typography:
  headline:
    fontFamily: "Instrument Sans Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 520
    lineHeight: 1.2
    letterSpacing: "-0.018em"
    fontVariation: "\"wdth\" 92"
  title:
    fontFamily: "Instrument Sans Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.95rem"
    fontWeight: 650
    lineHeight: 1.3
    letterSpacing: "-0.018em"
    fontVariation: "\"wdth\" 92"
  body:
    fontFamily: "Quicksand Variable, Instrument Sans Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.84rem"
    fontWeight: 400
    lineHeight: 1.4
  label:
    fontFamily: "Quicksand Variable, Instrument Sans Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.72rem"
    fontWeight: 600
    lineHeight: 1.35
  mono:
    fontFamily: "Fragment Mono, ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace"
    fontSize: "0.72rem"
    fontWeight: 400
    lineHeight: 1.4
rounded:
  sm: "calc(0.7rem * 0.6)"
  md: "calc(0.7rem * 0.8)"
  lg: "0.7rem"
  xl: "calc(0.7rem * 1.4)"
  square: "0"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
components:
  button-primary:
    backgroundColor: "{colors.primary-teal}"
    textColor: "{colors.primary-ink}"
    typography: "{typography.label}"
    rounded: "{rounded.lg}"
    padding: "0 10px"
    height: "32px"
  button-secondary:
    backgroundColor: "{colors.surface-muted}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.lg}"
    padding: "0 10px"
    height: "32px"
  input:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.lg}"
    padding: "4px 10px"
    height: "32px"
  status-chip:
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.square}"
    padding: "0 8px"
    height: "20px"
  work-card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "10px 11px"
  inspector:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    padding: "14px"
---

# Design System: Alfred

## Overview

**Creative North Star: "Signal Edge"**

Alfred is a quiet operations field that reveals change at its edges. Neutral planes and compact typography carry the working state; narrow mint, rose, and violet signals appear only where work is active, selected, uncertain, or awaiting a decision. The result is calm enough for long sessions while keeping intervention points unmistakable.

Signal Edge is the default appearance. Category Standard maps the same semantic contract to denser graphite panels and conventional status color. Linked Fold maps it to warm paper, fine crease geometry, and gold decision accents. Theme changes may alter material, radius, and heading character, but they do not alter information architecture, control meaning, or status semantics.

### Direction contract

- **THESIS:** Alfred shows autonomous work as a compact lifecycle. It rejects the generic assistant dashboard.
- **OWN-WORLD:** Signal Edge uses quiet glass and spectral state edges. Category Standard uses compressed operations type and hard graphite panels. Linked Fold uses paper grain, connected creases, and gold decisions.
- **STORY:** The operator sees what needs a decision, what runs now, and what shipped, then opens evidence or acts.
- **FIRST VIEWPORT:** A compact rail, four ordered Work lanes, and a separate evidence inspector fill the desktop viewport. Narrow screens keep the same order and move evidence into a sheet.
- **FORM:** Established-world extension, 1 of 1. Seed key `established-world:signal-edge-v1`. The approved Signal Edge, Category Standard, and Linked Fold comps are the authority; a new-world concept roll does not apply.
- **FINISH:** unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, and DESIGN.md

**Key Characteristics:**

- Compact, fit-to-viewport operational density.
- Translucent chrome around solid, legible data surfaces.
- State conveyed by words, icons, dots, borders, and color together.
- A restrained spectral edge reserved for changing or selected work.
- Light and dark modes derived from one semantic component contract.

## Colors

The default palette is warm cloud paper and slate ink with a low-chroma teal primary; the three spectral signals are accents, not general decoration.

### Primary

- **Deep Signal Teal:** Drives the main action, focus, active navigation, and interactive emphasis.

### Secondary

- **Diffracted Mint:** Marks live and active work.
- **Diffracted Rose:** Adds uncertainty or transition to the spectral edge.
- **Diffracted Violet:** Completes the selected-state spectrum and supplies restrained depth.

### Tertiary

- **Verified Green:** Communicates shipped or successful state.
- **Decision Gold:** Communicates attention and operator decisions.
- **Failure Red:** Communicates failure and destructive state with more visual weight than decorative accents.

### Neutral

- **Cloud Canvas:** The application floor.
- **Slate Ink:** Primary text and icons.
- **Mist Surface:** Panels, cards, and inspectors.
- **Quiet Surface:** Muted controls and secondary grouping.
- **Slate Hairline:** Dividers and boundaries without heavy boxes.

### Named Rules

**The Signal-at-the-Edge Rule.** Spectral color belongs on active navigation rails, selected work, changing evidence, and status edges. Keep the data field neutral.

**The Failure Has Weight Rule.** Error red must remain distinct from mint, green, and decorative spectral color, and every error includes text or an icon.

**The Semantic Contract Rule.** All appearances consume the same background, foreground, surface, primary, border, and status roles. A theme changes expression, never meaning.

## Typography

**Display Font:** Instrument Sans Variable with a UI sans-serif fallback
**Body Font:** Quicksand Variable with Instrument Sans Variable fallback
**Label/Mono Font:** Fragment Mono with platform monospace fallbacks

**Character:** Signal Edge pairs slightly condensed, controlled headings with a rounder, readable body voice. Monospace is reserved for repositories, commands, branches, hashes, and machine evidence.

### Hierarchy

- **Headline:** Compact page names at the top of a work surface.
- **Title:** Panel and inspector titles, with enough weight to anchor dense evidence.
- **Body:** Outcomes, explanations, and control copy, typically between 0.84rem and 0.95rem.
- **Label:** Metadata, lane headings, and compact control text, typically between 0.68rem and 0.75rem.
- **Mono:** Repository and evidence strings at the same compact scale as labels.

### Named Rules

**The Evidence Voice Rule.** Use monospace only when the content is literal machine evidence. Human-readable outcomes and decisions stay in the body voice.

**The Compact Hierarchy Rule.** Create hierarchy through weight, contrast, and spacing before increasing size. Operational headings remain modest.

## Layout

The desktop shell uses a persistent compact sidebar and a fluid content area. Work surfaces occupy the available viewport instead of producing a long page. The Work board uses four equal lifecycle lanes and adds a clamped 19rem to 22rem inspector when an item is selected at widths of 1280px and above. Narrower windows use a sheet so the inspector cannot compress the board. At widths below 1024px, lanes become two columns. Below 900px, fixed-height lane scrolling yields to page scrolling. Below 640px, lanes and assignment controls stack into one column.

Spacing follows a dense 4px base rhythm, most often appearing as 8px, 12px, 16px, and 24px gaps or padding. Content insets are 16px on small screens, 20px at the next step, and 28px on wide screens. Desktop controls are normally 28px to 36px tall; at phone widths interactive targets have a 36px minimum short axis.

**The Decision Rail Rule.** Evidence and approval controls occupy a dedicated inspector on wide screens and become a reachable sheet on narrow screens. They never obscure or reorder the lifecycle.

## Elevation & Depth

Alfred uses a hybrid depth model. Persistent chrome, inspectors, dialogs, popovers, and lifecycle lanes may use translucent material, backdrop blur, a fine highlight, and a restrained ambient shadow. Work cards remain solid and flat with a single hairline. Opaque fallbacks preserve contrast when backdrop filtering is unavailable.

### Shadow Vocabulary

- **Chrome Lift:** `inset 0 1px 0 var(--glass-highlight), 0 2px 8px color-mix(in oklch, var(--glass-shadow), transparent 40%), 0 22px 60px var(--glass-shadow)` for persistent glass chrome.
- **Inspector Lift:** `inset 0 1px 0 var(--glass-highlight), 0 18px 48px color-mix(in oklch, var(--glass-shadow), transparent 42%)` for the selected-item inspector.
- **Primary Glow:** A one-pixel accent ring plus a compact ambient glow for the single primary action on a surface.

### Named Rules

**The Chrome-Only Glass Rule.** Blur communicates persistent or overlaid chrome. Do not put long-form text directly on weak translucent material.

**The Flat Data Rule.** Cards that carry work, evidence, or metrics stay flat at rest. Hover changes border and surface tone, not elevation.

## Shapes

Signal Edge uses gently curved container corners derived from a 0.7rem base radius. Small controls and cards use scaled versions of that radius, while status chips, repository chips, assignment fields, segmented controls, and structural rails often remain square. Circular dots and avatars use a full pill radius. Category Standard tightens the base radius; Linked Fold uses sharp corners and clipped folded corners on selected structures.

**The Structural Shape Rule.** Radius follows hierarchy: chrome may curve, dense metadata stays square, and circles are reserved for compact identity or status marks.

## Components

### Buttons

- **Shape:** Compact curved controls, normally 32px high; phone-width controls grow to at least 36px.
- **Primary:** Teal fill with high-contrast ink and a restrained glow. Use once per decision surface.
- **Hover / Focus:** Hover strengthens brightness or the accent glow. Keyboard focus uses a visible primary-colored ring; active controls may shift by one pixel.
- **Secondary / Ghost:** Secondary controls use a quiet surface and hairline. Ghost controls acquire surface contrast only on interaction.

### Chips

- **Style:** Square, 20px-high labels with an outline, a weak tonal fill, text, and a circular status dot.
- **State:** Success, working, attention, error, and idle each have a distinct semantic tone. Idle remains neutral and error never collapses into success.

### Cards / Containers

- **Corner Style:** Work cards use the medium derived radius; their metadata chips remain square.
- **Background:** Solid surface color for work cards. Translucent material is reserved for lane containers and surrounding chrome.
- **Shadow Strategy:** No resting shadow on work cards.
- **Border:** One semantic hairline, strengthened by selection or interaction.
- **Internal Padding:** Roughly 10px vertically and 11px horizontally for canonical work cards.

### Inputs / Fields

- **Style:** Compact transparent or surface-adjacent field with a one-pixel input border.
- **Focus:** A two-pixel primary outline with a small offset, or the shared focus ring on primitive inputs.
- **Error / Disabled:** Invalid fields shift the border and ring to failure red. Disabled controls preserve their shape and reduce opacity.

### Navigation

The sidebar is persistent glass chrome on desktop and a compact top bar on mobile. The active item uses a soft wash and a narrow semantic rail. Signal Edge renders that rail as a three-color spectral edge. Labels and icons remain visible so color is never the only selected-state cue.

### Lifecycle Lane

Each lane combines a label, count, semantic edge, and internally scrolling card list. Needs approval, queued, working, and shipped retain fixed meaning across every appearance. At narrow widths the lanes stack in lifecycle order and the entire board becomes scrollable.

### Work Inspector

The inspector is stronger glass than the board lanes. It groups outcome, repository, review state, evidence, checks, files, and operator controls with hairline dividers. It uses an opaque fallback and remains independently scrollable on wide screens.

## Do's and Don'ts

### Do:

- **Do** use neutral surfaces for sustained reading and reserve spectral color for state edges.
- **Do** pair every status color with a label, icon, dot, or structural change.
- **Do** keep data cards flat and use glass only where layering has a navigational purpose.
- **Do** preserve lifecycle order and evidence access at every breakpoint.
- **Do** disable entry and crossfade motion when reduced motion is requested.

### Don't:

- **Don't** use gradients as decoration detached from status, selection, focus, or material depth.
- **Don't** turn the interface into a metrics dashboard with oversized numbers or presentation-scale headings.
- **Don't** use color alone to indicate approval, failure, selection, or progress.
- **Don't** add independent theme-specific component behavior. Map new components through the shared semantic contract.
- **Don't** place body copy on translucency that depends on backdrop blur for contrast.
