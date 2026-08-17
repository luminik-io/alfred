# Approved spec: service health endpoint

Status: approved

## Goal

Let deployment checks read the API process state without authentication.

## Current behavior

The service has no health endpoint.

## Target behavior

Add `GET /v1/health` in repository `acme/service-api`.

## Acceptance criteria

- `GET /v1/health` returns HTTP 200.
- The JSON response is exactly `{ "status": "ok" }`.
- The endpoint does not require authentication.
- The existing test command stays green.

## Out of scope

- Metrics endpoints.
- Readiness checks for external services.
- Changes to authentication middleware.
