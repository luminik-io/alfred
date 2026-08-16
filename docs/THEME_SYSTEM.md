# Desktop Theme System

Alfred Desktop uses a two-axis theme model:

- `data-theme` selects `signal-edge`, `category-standard`, or `linked-fold`.
- `.dark` / `.light` selects the mode inside that palette.

The base `:root` block in `clients/desktop/src/styles/tokens.css` is the
complete primitive contract and the Signal Edge light default. Each appearance
and mode defines the same primitive set. A single mapping exposes stable
semantic tokens to components.

Signal Edge uses a quiet neutral field with mint, rose, and violet reserved for
active or uncertain edges. The category standard uses compact graphite panels
and familiar blue, green, amber, and red state semantics. Linked Fold uses warm
paper, ink, crease lines, and gold for operator decisions.

## Glass And Flat Surfaces

Use translucent material for persistent chrome, inspectors, the command
palette, dialogs, and popovers. Blur must explain which surface sits above
another. Every glass surface needs an opaque fallback and contrast-safe text.

Use flat surface tokens for dense work surfaces: lifecycle columns, lists,
cards, logs, and tables.

`--theme-glass-blur` and `--theme-glass-saturate` let each appearance tune its
material without rewriting component CSS.

No appearance uses decorative grid or radial-bloom backgrounds. Broad linear
light fields provide enough variation for glass to read while keeping work
surfaces calm.

## Guardrail

`clients/desktop/src/test/theme-tokens.test.ts` reads `styles/tokens.css` and
fails if a theme-mode block omits a primitive. When adding an appearance, add
both modes and update the test's theme list.
