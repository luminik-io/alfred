# `alfred serve`

A localhost-only server over `$ALFRED_HOME/state`, saved architect plans, the
local fleet brain, and local planning drafts. It does two jobs: it exposes a
JSON `/api/*` surface, and it serves the built desktop app as the browser UI.
Read endpoints inspect runtime state; state-mutating endpoints are token-gated
and limited to explicit local control actions such as setup, queue decisions,
planning drafts, memory review, and Slack trust settings. It is the operator's
pane of glass for "what is the fleet doing right now".

One UI, two shells. The desktop client under `clients/desktop` is a React app
that talks to the same `/api/*` surface over plain HTTP. `alfred serve` serves
that same built app in the browser, so there is a single UI codebase behind both
the native (Tauri) window and any browser. The older server-rendered dashboard
has been retired.

## Install

`install.sh` provisions the managed `$ALFRED_HOME/venv` with the server
dependencies because Alfred Desktop uses `alfred serve` by default.

For package-only installs, the dependencies are in the base package:

```bash
pip install alfred-os
```

The Python stack is `fastapi`, `httpx`, and `uvicorn`. The browser UI is the
built React app; nothing is loaded from a CDN. `deploy.sh` builds the client
(`npm ci && npm run build`) and ships `dist/` into
`$ALFRED_HOME/clients/desktop/dist` so the server can find it. To build it by
hand:

```bash
cd clients/desktop && npm ci && npm run build
```

If no build is present, `alfred serve` shows a short "UI not built" page and the
JSON API still works. Point `ALFRED_SERVE_UI_DIST` at a `dist/` directory to
override where the server looks for the built app.

## Run

From a checkout:

```bash
python bin/alfred-serve.py
# or
python bin/alfred serve
```

From a deployed checkout:

```bash
alfred serve
```

Defaults:

| flag           | default       | meaning                                                       |
| -------------- | ------------- | ------------------------------------------------------------- |
| `--host`       | `127.0.0.1`   | bind address. Use `0.0.0.0` only on a trusted LAN.            |
| `--port`       | `7010`        | bind port.                                                    |
| `--no-browser` | off           | skip the auto-open browser tab on localhost binds.            |
| `--log-level`  | `info`        | uvicorn log level (`debug` / `info` / `warning` / `error`).   |

On a localhost bind the server opens a browser tab to the app (unless
`--no-browser` is passed). The app polls the `/api/*` surface for live fleet
state.

## What it reads

The default reader walks `$ALFRED_HOME/state` (falling back to `~/.alfred/state` if the env var is unset). All reads are best-effort: missing directories render an empty state, malformed JSONL lines are skipped, the dashboard never throws.

If `$ALFRED_HOME/fleet-brain.db` exists, the reader also asks the fleet brain
for a read-only reliability report. Missing optional dependencies or a missing
brain database degrade to an "unknown" governor panel instead of failing the
page.

Canonical layout (written by `lib/agent_runner/`):

```
$ALFRED_HOME/state/
  <codename>/
    events/<firing_id>.jsonl     # one JSONL per firing
    spend-<YYYY-MM-DD>.json      # per-day per-codename ledger
  transcripts/<codename>/<YYYY-MM>/<firing_id>.jsonl
```

Forward-compatible optional paths the reader also honors if a future runtime writes them:

```
$ALFRED_HOME/state/codenames/<codename>/...
$ALFRED_HOME/state/firings/<firing_id>.json
```

Alfred plan drafts are read from:

```
$ALFRED_HOME/architect-plans/*.md
```

Planning drafts are written to:

```
$ALFRED_HOME/planning-drafts/*.md
$ALFRED_HOME/state/planning-drafts/*.json   # Slack listener intake
```

Registered Slack plan/report/draft threads are stored under:

```
$ALFRED_HOME/state/slack-threads/*.json
$ALFRED_HOME/state/slack-threads/feedback/*.jsonl
```

## The browser UI

### `GET /` - the desktop app in the browser

`GET /` serves the built desktop React app (`clients/desktop/dist/index.html`).
Any client-side deep link (for example `/inbox/some-plan`) falls back to the same
`index.html` so a refresh or a shared link resolves. Hashed assets are served
from `/assets/*` and brand images from `/brand/*`. The app reads live fleet
state, plans, firings, usage, and setup from the JSON `/api/*` surface below; it
is the same app the native (Tauri) window loads.

Native-only affordances (starting or installing the local runtime, the menu-bar
tray) are hidden in the browser because they need the desktop shell. Everything
else, including reads and token-gated mutations, works in the browser.

The server resolves the built app in this order: the `ALFRED_SERVE_UI_DIST`
environment variable, then `$ALFRED_HOME/clients/desktop/dist` (where `deploy.sh`
ships it), then `<repo>/clients/desktop/dist` for a local build. If none is
found, `/` returns a short "UI not built" page and the JSON API is unaffected.

The planning-assistant behavior the old server-rendered page exposed (natural
notes and structured `add repo:` / `remove repo:` / `acceptance:` commands that
refine a draft and record readiness) now lives in the app's Plan and Ask
surfaces, backed by the `POST /api/plans/draft` and `POST /api/compose/converse`
endpoints. In Batman Slack approvals, the same repo add/remove commands amend
execution scope before implementation.

### `GET /healthz`

Returns plain text `ok` with status 200. Useful for liveness probes if you run `alfred serve` behind a process supervisor.

### JSON API

The browser UI and the native client read and write the same localhost data
through JSON endpoints:

```text
GET /api/status
GET /api/schedule
GET /api/actions
GET /api/shipped?days=14
GET /api/usage             # served; backs the desktop capacity rail
GET /api/usage/providers   # served; flat per-engine re-projection of /api/usage
GET /api/firings?codename=<name>&limit=50
GET /api/firings/{firing_id}
GET /api/firings/{firing_id}/tail
GET /api/plans?limit=50
GET /api/plans/drafts
GET /api/plans/{plan_id}
POST /api/queue
GET /api/setup/status
GET /api/setup/repos
POST /api/setup/repos
GET /api/setup/playbooks
POST /api/setup/playbook
POST /api/setup/demo
POST /api/setup/demo/clear
POST /api/plans/{plan_id}/convert-followup
POST /api/plans/{plan_id}/mark-handled
POST /api/plans/{plan_id}/decision
POST /api/plans/{plan_id}/file-issue
POST /api/plans/draft
POST /api/conversation/control
POST /api/compose/converse
POST /api/compose/converse/stream
GET /api/memory/candidates?status=candidate&limit=50
POST /api/memory/candidates/{candidate_id}/promote
POST /api/memory/candidates/{candidate_id}/reject
GET /api/slack/trusted-users
POST /api/slack/trusted-users
POST /api/slack/trusted-users/{user_id}/remove
```

State-mutating `POST`/`DELETE` endpoints require the per-launch token via the
`X-Alfred-Token` header. The desktop shell attaches it through its native
bridge. When the app is served in a browser, the server injects that same token
into the served `index.html` as a `<meta name="alfred-token">` tag: a same-origin
page can read it and echo it back, a cross-origin page cannot read another
origin's document, and the token file stays `0600` on disk. Read endpoints need
no token.

`GET /api/setup/status` includes a `first_run` readiness block for native
onboarding. It rolls up GitHub auth, engine CLIs, repo scope, queue coverage,
local checkout mapping, scheduled fleet deployment, Desktop action token,
recommended code graph memory, context compression, engineering skill packs,
optional Batman parent-repo setup, and optional Slack collaboration into one
contract:

```json
{
  "first_run": {
    "ready": false,
    "status": "needs_action",
    "headline": "1 required setup item needs action.",
    "summary": {
      "required_ready": 6,
      "required_total": 7,
      "recommended_ready": 1,
      "recommended_total": 3,
      "blockers": ["repo_local_paths"]
    },
    "checks": [
      {
        "key": "repo_local_paths",
        "tier": "required",
        "ready": false,
        "detail": "1 selected repo needs local path mapping.",
        "action": "Clone the missing repo locally or set ALFRED_REPO_LOCAL_MAP with repo=path entries."
      }
    ]
  }
}
```

Required rows decide whether the first real run is safe to start. Recommended
rows are visible but do not block: code graph, Headroom-style context
compression, and engineering skill packs can be finished without hiding the core
install state.

`GET /api/usage` is served by `alfred serve` today and backs Alfred Desktop's
capacity rail. It reports your real Claude subscription headroom for
the rolling 5-hour and weekly windows, plus Codex's latest-day token usage. Codex
exposes no rolling-window or weekly headroom, so the API reports its latest-day
token total rather than inventing a Codex quota percentage. All of it is read
from the engines' own local CLI state files on the host. Alfred drives Claude Code and Codex through
their local subscription CLIs rather than API keys, so there is no billing API
and no per-token dollar figure (it is meaningless under a Max or Pro
subscription). A provider whose local state cannot be read degrades to
`available: false` with a reason rather than guessing, and any single window the
CLI does not persist reads as not synced rather than a fabricated number. Reads
run in a worker thread so filesystem work never stalls the event loop.

`GET /api/usage/providers` is served by `alfred serve` (a flat per-engine
re-projection of `/api/usage`), and the same usage numbers are available from the
command line with `alfred usage` (see [`CLI.md`](CLI.md)).

The follow-up action
endpoints are local-file actions only: they convert captured feedback into a
planning draft JSON or archive it as handled. They do not call GitHub, Slack,
or an engine, and they do not approve execution. Memory candidate endpoints
only read or review rows in the local fleet-brain database. `promote` turns a
candidate into a recalled lesson, and `reject` keeps it out of future prompts.
Slack trusted-user endpoints only read or update
`$ALFRED_HOME/state/slack-trust/trusted-users.json`; they do not grant approval
rights, call Slack, call GitHub, or run an agent.

## Architecture

Thin modules behind a single factory:

```
lib/server/
  __init__.py       # re-exports public surface
  reader.py         # FleetReader Protocol + FilesystemReader
  app.py            # create_app(reader) -> FastAPI
  views.py          # JSON /api/* routes + /healthz
  static_ui.py      # serves the built React app + SPA fallback + token inject
  static/           # server-owned static assets
bin/alfred-serve.py # argparse driver, runs uvicorn
```

The reader is injected into the FastAPI app via `create_app(reader)`. Tests pass
a tmp-dir-backed `FilesystemReader` (or any stub matching the `FleetReader`
Protocol), so the test suite never touches a real fleet. `static_ui.register_ui`
runs after the API routes so `/api/*` and `/healthz` always win over the UI's
SPA catch-all.

## Security model

Default bind is `127.0.0.1`. Runtime-state routes are read-only. Mutating
endpoints require the per-launch `X-Alfred-Token` header (see the JSON API auth
note above) and are compared in constant time; a missing token fails closed.
Compose/plan `POST`s only write markdown/JSON drafts under
`$ALFRED_HOME/planning-drafts` and `$ALFRED_HOME/spec-drafts`. Follow-up actions
only move captured follow-up files into `handled/` or create local planning
draft JSON. Memory candidate actions only mutate the local fleet-brain
database. Slack collaborator actions only mutate the local trust JSON file.
They do not call GitHub or Slack. The planning assistant only calls a model
provider when `ALFRED_PLANNING_ASSISTANT_ENGINE` is explicitly set. The reader's
path-traversal guard rejects firing ids containing `/`, `\\`, or a leading `.`
before any filesystem read, and the UI's SPA fallback refuses to serve files
outside the built app directory.

That said: the app surfaces repo URLs, file paths, and event payloads that may
contain operator context. Treat `--host 0.0.0.0` like exposing the raw state
directory over HTTP, only do it on a network you trust.

## Tests

```bash
pytest tests/test_server.py tests/test_server_static_ui.py -q
```

`test_server.py` covers empty and populated state via `tmp_path`, the JSON
firing/plan surfaces, 404 on unknown firing, path-traversal rejection, saved
plan listing, planning draft readiness/saving, memory-candidate proposal,
malformed-JSONL tolerance, header-token mutation guards, Slack trusted-user API
guards, and `/healthz`. `test_server_static_ui.py` covers serving the built app
at `/` with the injected token, asset and brand resolution, SPA deep-link
fallback, the API never being shadowed by the UI catch-all, the "UI not built"
page, and the traversal guard.
