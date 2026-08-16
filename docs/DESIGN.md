# Design language

This guide describes the shipped visual language for Alfred Desktop and the
public site. Use it when you add or change a surface. For the desktop product
model and build instructions, see [`DESKTOP_CLIENT.md`](DESKTOP_CLIENT.md). For
the desktop token contract, see [`THEME_SYSTEM.md`](THEME_SYSTEM.md).

The source files are:

- Desktop import manifest: `clients/desktop/src/index.css`.
- Desktop appearance and semantic tokens: `clients/desktop/src/styles/tokens.css`.
- Desktop surface rules: the CSS files in `clients/desktop/src/styles/`.
- Site rules: `site/src/styles/custom.css`, `site/src/styles/marketing.css`, and
  `site/src/styles/cookie-banner.css`.

If this guide and the source disagree, use the source and update this guide.

## Desktop appearance system

Alfred Desktop has two independent appearance settings:

- `data-theme` selects `signal-edge`, `category-standard`, or `linked-fold`.
- `.light` or `.dark` selects the mode for that appearance.

`useTheme.ts` stores the appearance in `alfred-theme-name` and the mode in
`alfred-theme`. Signal Edge in light mode is the default.

| Appearance | Visual character | State language |
|---|---|---|
| **Signal Edge** | Quiet neutral fields and clear liquid-glass layers. | Mint, rose, and violet identify active or uncertain edges. |
| **The Category Standard** | Dense graphite operations surfaces with compact spacing. | Blue, green, amber, and red use familiar status meanings. |
| **Linked Fold** | Warm paper, ink, crease lines, and sharper corners. | Gold marks operator decisions and important handoffs. |

Each appearance and mode defines the same `--theme-*` primitive set. The final
mapping in `tokens.css` exposes stable component tokens such as `--background`,
`--card`, `--primary`, `--glass`, `--ok`, `--warn`, and `--error`.

Components must use the stable component tokens. Do not read an appearance
primitive directly from component CSS. Do not add a raw color to solve a local
appearance problem.

### State and workflow edges

Color must describe state or hierarchy. It must not decorate data.

- Use `--ok`, `--warn`, and `--error` for health states.
- Use `--primary` for the normal workflow handoff.
- Use the accent, a dashed line, and a text label for an operator approval edge.
- Keep a text label, icon, border, or layout change with every color signal.

Signal Edge can use spectral edge colors for active or uncertain transitions.
The other appearances keep the same meaning with their own palette.

## Site color system

The public site has a separate light and dark palette in `custom.css` and
`marketing.css`.

| Token | Dark | Light | Purpose |
|---|---|---|---|
| `--sl-color-bg` | `#0d1322` | `#f7f9fc` | Page background |
| `--sl-color-accent` | `#4a78ff` | `#2855c8` | Links and actions |
| `--alfred-ok` | `#2dd4a7` | `#087a5d` | Healthy state |
| `--alfred-warn` | `#f5a524` | `#8f5600` | Caution state |
| `--alfred-alert` | `#ff5d6c` | `#d92d3c` | Failure state |

The light link color is darker so that it keeps AA contrast on white. Check a
new text or control color against its actual background in both modes.

## Typography

The desktop imports Instrument Sans, Quicksand, and Fragment Mono locally. The
appearance selects the heading and body voice:

| Appearance | Headings | Body |
|---|---|---|
| Signal Edge | Instrument Sans | Quicksand |
| The Category Standard | Instrument Sans | Instrument Sans |
| Linked Fold | Iowan Old Style or the configured serif fallback | Instrument Sans |

Fragment Mono is for code, IDs, command previews, timestamps, and logs. Do not
use it for normal interface text.

The public site uses Instrument Sans for headings, Quicksand for body text, and
Fragment Mono for code and literal machine values. The desktop font guard in
`clients/desktop/src/test/directive-guards.test.ts` checks the imports and base
font tokens.

## Liquid glass and flat work surfaces

Use liquid-glass material to show elevation. Suitable surfaces include:

- persistent application chrome and the sidebar
- command palettes, dialogs, sheets, and popovers
- page heroes, inspectors, and overlays

The material combines a translucent fill, a thin border, a top highlight, a
soft shadow, blur, and saturation. Use `--glass`, `--glass-strong`,
`--glass-highlight`, `--glass-shadow`, `--glass-blur`, and
`--glass-saturate`. Each appearance supplies its own values.

Every glass surface must have readable text and an opaque fallback. The shipped
helpers use `@supports` to replace glass with `--surface` or `--popover` when
the browser cannot render `backdrop-filter`.

Do not put every item on glass. Use flat surface tokens for dense work areas:

- lifecycle columns
- repeated cards and lists
- logs and tables
- code and evidence panels

Use broad linear light fields behind glass. Do not add decorative grids,
radial blooms, or floating orbs.

## Shape and spacing

Use the radius and spacing tokens for the active appearance. Signal Edge uses
softer corners. The Category Standard is compact. Linked Fold uses sharper
fold-like edges.

Avoid nested card stacks. Use one containing surface, clear section spacing,
and borders for internal grouping.

## Motion

Motion must show a state change or help the user follow an action.

- Keep hover and selection transitions between 120 and 200 milliseconds.
- Keep movement small. A card can rise by one or two pixels.
- Limit staggered entry so a long list settles quickly.
- Do not use motion as the only state signal.

Every new animation must include a `prefers-reduced-motion` rule. The reduced
mode removes entry, hover, crossfade, and animated-edge movement. Color, border,
text, and layout must still show the state.

## Responsive behavior

The desktop must work from a 320-pixel viewport to a wide desktop window.

- At narrow widths, multi-column command surfaces become one column.
- The roster list uses a responsive card grid and opens details in a drawer.
- The Workflow view reduces its canvas height below 880 pixels and supports a
  full-window view for a wide pipeline.
- Settings tabs use a two-column layout below 440 pixels. The full labels stay
  visible.
- At 480 pixels and below, interactive controls have a minimum height of 36
  pixels. Tab-list shells have a minimum height of 42 pixels.
- Text and controls must wrap or truncate inside their own bounds. A page must
  not require horizontal scrolling for primary content.

Test each appearance in light and dark mode at phone and desktop widths.

## Accessibility

- **Contrast:** Target at least WCAG AA against the rendered background. Glass
  text must also pass when blur is unavailable.
- **Focus:** Keep a visible `:focus-visible` ring with sufficient contrast.
- **Controls:** Use real buttons, links, tabs, and form controls. Do not attach
  actions to a plain `div`.
- **Tabs:** Keep the active tab in the roving tab order. Support arrow, Home,
  and End keys.
- **Touch:** Keep the phone-width target sizes described above.
- **Motion:** Preserve all information when reduced motion is active.
- **State:** Do not use color alone. Add text, shape, position, or an icon.

## Checklist for a new desktop surface

1. Use the semantic component tokens from `tokens.css`.
2. Check all three appearances in light and dark mode.
3. Use glass only for elevated chrome or overlays.
4. Use flat surfaces for dense data.
5. Check the layout at 390 pixels and at a wide desktop size.
6. Check keyboard focus, tab order, control semantics, and reduced motion.
7. Add or update a guard test when you change the token contract.
