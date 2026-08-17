# Approved spec: cancel a queued job

Status: approved

## Goal

Let an authenticated operator cancel a queued job before a worker starts it.

## Current behavior

The API can create and read jobs but cannot cancel them.

## Target behavior

Add `POST /v1/jobs/{job_id}/cancel` in repository `acme/job-api`.

## Acceptance criteria

- An authenticated operator can cancel a queued job.
- The endpoint returns HTTP 202 with `{ "status": "cancelling" }`.
- An unknown job returns HTTP 404.
- The existing test command stays green.

## Out of scope

- Cancelling a job that has started.
- Worker or scheduler changes.
- Desktop changes.
