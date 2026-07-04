---
name: review-security
description: Reviews a change through a STRIDE-lite security lens (injection, authorization, secret handling, SSRF and egress) and reports concrete findings with severity and a fix. Use when asked to "security review", "check this for vulnerabilities", "review the auth path", "is this safe to merge", or when a change touches untrusted input, authentication, secrets, or outbound network calls. Use before merging any change on a trust boundary.
license: MIT
---

# Review security

## When to use

- A PR handles untrusted input (request bodies, query params, uploaded files,
  webhook payloads, LLM output that is then executed).
- A change touches authentication, authorization, or session handling.
- Code reads, writes, or logs a secret, token, or credential.
- A change makes an outbound request to a URL derived from user input.

This is a focused security pass, not a full audit. It looks at the four lenses
below and reports what it finds. If the repo has a threat model
(`docs/THREAT_MODEL.md`), read it first and check the change against the
boundaries it states.

## Procedure

Walk the diff through each lens. For every finding, record the file and line,
the concrete risk, a severity (P0 exploitable now / P1 exploitable with effort /
P2 hardening), and a specific fix.

1. **Injection.** Any place user input reaches an interpreter: SQL, shell, a
   template, an eval, or a prompt to an LLM whose output is then trusted. Look
   for string-concatenated queries, `shell=True`, and LLM output used as a
   command or path without validation. Fix: parameterize, allowlist, or escape
   at the boundary, never by sanitizing the input string.
2. **Authorization.** Does every mutating path check that the caller may act on
   THIS object, not merely that they are logged in? Look for missing
   object-level checks (IDOR): `GET /orgs/{id}/secrets` that never verifies the
   caller belongs to `{id}`. Fix: enforce the check server-side, close to the
   data.
3. **Secret handling.** Secrets must come from the environment or a secret
   manager, never be hardcoded, logged, echoed in errors, or committed. Look for
   tokens in log lines, secrets in exception messages, and `.env` values written
   to disk. Fix: read from config, redact in logs, keep out of version control.
4. **SSRF and egress.** Any request to a user-controlled URL can be pointed at
   internal metadata endpoints or private hosts. Look for `fetch(userUrl)` with
   no allowlist. Fix: validate the host against an allowlist, block private and
   link-local ranges, disable redirects to new hosts.

## Output

A findings list ordered by severity. Each finding: `file:line`, lens, severity,
the risk in one sentence, and the fix. If a lens is clean, say so explicitly
("Authorization: object-level checks present on all three mutating routes") so a
reviewer knows it was checked, not skipped. End with a merge recommendation:
block on any P0, fix-before-merge on P1, note P2 as follow-up. Never claim code
is "secure"; report what was checked and what was found.
