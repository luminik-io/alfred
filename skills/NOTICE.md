# Third-party skill attribution

alfred-os is MIT-licensed. The curated skill packs bundle or reference
third-party skills. This file records the source, license, and terms for each,
per the attribution requirements of those licenses.

Every vendored skill under `skills/vendored/` keeps its own `LICENSE` file next
to the copied content. This NOTICE is the index.

## Vendored skills (copied into this repo)

These are permissively licensed (MIT) and copied in with their license text
preserved. MIT-into-MIT is compatible; the only obligation is keeping the
copyright and permission notice with the copied files, which the per-skill
`LICENSE` files satisfy.

| Skill directory | Upstream source | Upstream ref | License | Copyright |
|---|---|---|---|---|
| `vendored/code-review-and-quality` | [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills) | `skills/code-review-and-quality` | MIT | (c) 2025 Addy Osmani |
| `vendored/security-and-hardening` | [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills) | `skills/security-and-hardening` | MIT | (c) 2025 Addy Osmani |
| `vendored/frontend-ui-engineering` | [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills) | `skills/frontend-ui-engineering` | MIT | (c) 2025 Addy Osmani |
| `vendored/debugging-and-error-recovery` | [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills) | `skills/debugging-and-error-recovery` | MIT | (c) 2025 Addy Osmani |
| `vendored/vercel-react-best-practices` | [vercel-labs/agent-skills](https://github.com/vercel-labs/agent-skills) | `skills/react-best-practices` | MIT (declared in README + SKILL.md frontmatter; no upstream LICENSE file) | (c) Vercel, Inc. |

Note on Vercel: the upstream repo declares MIT in its `README.md` and in the
skill's `SKILL.md` frontmatter (`license: MIT`) but ships no standalone
`LICENSE` file. `vendored/vercel-react-best-practices/LICENSE` reconstructs the
MIT grant and copyright line so the vendored copy is explicit and
self-contained.

## First-party skills (Alfred-authored)

The skills under `skills/first_party/` (`spec-to-issues`, `write-tests`,
`review-security`, `add-observability`, `migrate-dependency`,
`changelog-and-release-notes`) are written by the alfred-os project and licensed
MIT under this repo's `LICENSE`. They carry no upstream attribution because they
have no upstream: they are our own work.

## Reference-install skills (fetched from source, not copied in)

These are NOT vendored. The `alfred skills install` command fetches them from
their upstream source at install time. They are reference-only either because
they are large and carry their own runtime (gstack's browse daemon), or because
they are heavy dependencies best pinned to upstream (headroom's ML model).
Their licenses permit vendoring, but reference-install is the deliberate choice.

| Pack | Upstream source | License | Why reference-install |
|---|---|---|---|
| `gstack` | [garrytan/gstack](https://github.com/garrytan/gstack) | MIT (c) 2026 Garry Tan | ~300KB of interdependent skill markdown plus a Bun/TypeScript browse daemon (~58MB binary built at setup). Its `./setup` handles platform-specific symlinking. Vendoring source is allowed but would bloat this repo and still require the daemon. |
| `headroom` | [headroomlabs-ai/headroom](https://github.com/headroomlabs-ai/headroom) | Apache-2.0 (c) 2025 Headroom Contributors | Apache-2.0 is vendorable into MIT (retain LICENSE + NOTICE, note modifications), but the project carries a custom HuggingFace ML compression model and is best pinned to a released version via `pip`/`npm`/MCP rather than copied in. Opt-in token optimization; see `docs/SKILLS.md`. |

## License compatibility summary

- All bundled/referenced sources are permissive (MIT or Apache-2.0).
- No copyleft (GPL/AGPL) sources are vendored. Copyleft would be
  reference-install-only for this MIT repo; none of the audited projects are
  copyleft, so this constraint did not bind.
- Apache-2.0 (headroom) is one-way compatible with MIT: Apache-2.0 code may be
  used in an MIT project provided its LICENSE and NOTICE are retained and
  modifications are noted. It is referenced, not vendored, so that obligation
  falls to the install step, which fetches the upstream release intact.
