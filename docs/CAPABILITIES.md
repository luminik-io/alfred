# Capability Doctor

`alfred capabilities` is a read-only inventory of the local features that make
the fleet useful beyond a bare scheduler. It does not install packages or make
network calls. The native onboarding flow uses the same payload on the Tools
step, whether you set Alfred up by chatting or by stepping through the form (see
[`ONBOARDING.md`](ONBOARDING.md)), so a user can see whether code graph memory,
the built-in context governor, and engineering skill packs are ready before they
let the fleet run real work.

```sh
alfred capabilities
alfred capabilities --json
```

## Current Capabilities

| Capability | Why it matters | Source |
| --- | --- | --- |
| Code graph memory | Gives agents structural code search, call paths, impact checks, and route ownership through the optional code-memory MCP layer. | [`DeusData/codebase-memory-mcp`](https://github.com/DeusData/codebase-memory-mcp), MIT |
| Context governor | Keeps every agent firing inside Alfred's local prompt budget before engine invocation. Headroom can still be detected as an optional external compression layer, but it is not required for this row to be ready. | Alfred built-in, optional [`headroomlabs-ai/headroom`](https://github.com/headroomlabs-ai/headroom), Apache-2.0 |
| Engineering skill packs | Gives local agent hosts repeatable review, QA, security, frontend, docs, and shipping workflows. | [`garrytan/gstack`](https://github.com/garrytan/gstack), `vercel-labs/agent-skills`, `addyosmani/agent-skills` |

The JSON shape is versioned:

```json
{
  "version": 1,
  "summary": {"ready": 2, "actionable": 1, "disabled": 0, "total": 3},
  "capabilities": []
}
```

Each row has a stable `key`, `state`, `detail`, `detected` object, and
`install_hint`. A future repair action can install or configure a missing row,
but the doctor itself stays read-only. The desktop intentionally displays the
hint and source attribution rather than hiding a missing capability behind a
generic setup warning.
