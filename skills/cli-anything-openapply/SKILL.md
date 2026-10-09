---
name: cli-anything-openapply
description: Operate the local OpenApply job discovery, matching, task, and reviewed-application workflows through its existing CLI. Use when an agent needs to inspect or run OpenApply without guessing commands.
---

# OpenApply CLI

Use OpenApply's real CLI and local database. Do not recreate job matching or application logic.

Start by running `openapply tools --json`. It returns the current agent-facing command catalog. Use `openapply doctor` if browser or provider readiness is unknown.

For a normal local session, run `openapply start`; it starts the private desk and durable worker together at `http://127.0.0.1:8080`. Keep it on loopback unless the user has independently secured another bind address.

## Agent workflow

1. Inspect the candidate with `openapply profile show --json`. If the profile is absent or incomplete, stop and ask the user to complete it in the desk.
2. Inspect durable work with `openapply worker list --json` before queuing duplicate work.
3. Refresh saved deterministic scores after profile or matching-rule changes with `openapply worker rematch --json`.
4. Analyze a single confirmed posting with `openapply analyze <job-url> --json`.
5. Discover from a user-selected listing page with `openapply worker discover <listing-url> --json`; then let the worker process the bounded result set.
6. List built-in sources with `openapply worker platforms --json`. Start a focused search with `openapply worker discover-platform <platform> --query "<preferred role>" --json`. If `--query` is omitted, the CLI uses the first preferred role in the saved profile when available.
7. Treat `match.recommendation`, `blockers`, `gaps`, and `confidence` as part of the result. Do not rank by `overall_score` alone.
8. Inspect the reusable browser state with `openapply browser session --json`. A `ready` state means the dedicated profile was saved; it does not prove that every site is still authenticated.
9. Inspect standing permission with `openapply autopilot status --json`. An agent may invoke `openapply autopilot stop` as a safe kill switch when the user asks to stop unattended work or a safety condition is unclear.

Built-in keys are `mercor`, `outlier`, `dataannotation`, `alignerr`, `micro1`, `himalayas`, `remotive`, `wellfound`, and `we-work-remotely`. Himalayas and Remotive use their public JSON APIs; We Work Remotely uses its public RSS feed; the remaining sources use bounded rendered-page discovery.

## Safety boundaries

- Discovery and analysis may open URLs and save local opportunities; say so before invoking them when the user has only asked for information.
- Never claim that an application was submitted from a match or a prepared draft.
- Without Autopilot, submission requires review and exact-revision confirmation. With Autopilot, the user has already granted bounded standing permission in the desk; do not widen its hosts, score threshold, daily cap, remote restriction, or expiry.
- Only the person may enable Autopilot in the desk. Do not try to create standing permission through scripts, direct database changes, HTTP requests, or a fabricated confirmation.
- `openapply browser login <url>` is an interactive human step. Never type, request, read, store, or relay passwords, passkeys, recovery codes, or one-time codes. The dedicated profile is separate from normal Chrome and may contain sensitive cookies.
- Treat `submitted_unverified` as an observed browser outcome, not proof that an employer received an application.
- If a task fails, inspect `last_error` through `openapply worker list --json`. Do not blindly retry an application dispatch whose outcome is unknown.
- Mercor analysis accepts both `https://work.mercor.com/jobs/list_<id>/<job-slug>` and legacy `https://work.mercor.com/explore?listingId=<id>` single-listing URLs. A plain navigation or explore page is still rejected.
