# Personal agent progress and next milestone

Updated: 2026-10-09

This is the short, live implementation handoff. The broader product design and data model are
in [personal-agent-implementation-handoff.md](personal-agent-implementation-handoff.md).

## Current outcome

OpenApply now has a usable local personal-agent foundation: durable interviews and confirmed
evidence, bounded job discovery, a resumable worker, a private web desk, application history,
concise reports, and an optional paired Telegram channel. It remains review-first. The web desk
can now take a single-page opportunity through local preparation, immutable edits, exact
authorization and one crash-safe queued submission. Public discovery adapters now cover nine
job platforms; dedicated platform application adapters and automatic submission policy are not
implemented.

## Delivery status

| Capability | Status | What is true now |
| --- | --- | --- |
| SQLite migrations and repositories | Complete | Interviews, evidence, conversations, tasks, opportunities, provider cooldowns, pairings and application history survive restarts. |
| Deep interview and personal knowledge | Complete for the first slice | Sessions resume; answers propose evidence; the user confirms or rejects it before it can be used. |
| Evidence-grounded generation | Partial | Relevant confirmed evidence is included in application prompts. Generated answers do not yet carry evidence IDs or an automated unsupported-claim verdict. |
| Career-page discovery | Complete for the requested public sources | Custom pages are scanned within a fixed link budget. Built-in discovery supports Mercor, Outlier, DataAnnotation, Alignerr, micro1, Himalayas, Remotive, Wellfound and We Work Remotely through public pages, official JSON APIs or RSS. Login-only listings and general pagination remain out of scope. |
| Resumable worker and provider cooldown | Complete for the reviewed single-page slice | Tasks use leases, recover after interruption, pause/resume, prepare drafts and dispatch an exact authorization once. Per-answer generation checkpoints are still future work. |
| Personal web app | Complete for the first slice | Conversation, interview, evidence decisions, discovery tasks, opportunities, worker state and the application ledger are available on loopback. |
| Conversation | Partial | Deterministic commands cover status, interviews and reports. There is no provider-backed open-ended assistant yet. |
| Application history and reports | Complete for the current CLI path | Prepared answers and observed outcomes are snapshotted. `submitted_unverified` records dispatch without claiming an employer receipt. There is no receipt reconciliation. |
| Telegram | Complete for the first slice | One private chat can pair, converse, interview and request reports. Telegram deliberately cannot authorize submission. |
| Reviewed web submission | Complete for the local single-page checkpoint | Preparation does not fill the page; revisions are immutable; authorization binds revision, destination, signature and expiry; intent is persisted and uncertain dispatch is not retried. |
| External browser support | Partial | There are no saved login sessions or reliable iframe/multi-step adapters, and no employer-receipt reconciliation. |
| Public authentication / hosted edition | Not started | The app is private loopback software intended for local use or an SSH tunnel. |
| Mannerism capture and general browser work | Not started / later | These are separate optional products and are not required for the application agent. |

## Where the implementation lives

| Area | Primary paths |
| --- | --- |
| Database and repositories | `src/openapply/storage/` |
| Interviews and confirmed evidence | `src/openapply/interviews/` |
| Conversation and reports | `src/openapply/conversations/`, `src/openapply/reports/` |
| Discovery and durable worker | `src/openapply/discovery/`, `src/openapply/worker/` |
| Personal web desk | `src/openapply/web/` |
| Telegram channel | `src/openapply/channels/telegram.py` |
| Application snapshots | `src/openapply/applications/history.py` |
| CLI entry points | `src/openapply/cli/commands/agent.py`, `interview.py`, `worker.py`, `telegram.py` |

Implemented commands:

```bash
openapply interview start
openapply agent serve
openapply agent report --timezone Africa/Lagos --details
openapply worker discover <career-page-url>
openapply worker platforms --json
openapply worker discover-platform remotive --query "backend engineer" --json
openapply worker run
openapply telegram pair
openapply telegram run --timezone Africa/Lagos
```

## Verification snapshot

- Full tests: **813 passed, 2 skipped** in 304.81 seconds.
- Ruff formatting and lint: passed.
- Strict mypy: passed.
- The complete reviewed loop passed in installed Google Chrome against a local form fixture.
- No live employer submission, paid-provider generation, public deployment or Linux VPS smoke
  test was performed.

## Completed checkpoint: reviewed application loop

The implemented path through the personal app is:

`discover -> inspect fit -> prepare locally -> review/edit -> authorize exact revision -> submit once -> report`

It is deliberately limited to the existing single-page form support. The end-to-end test uses
installed Google Chrome and a local fixture; automatic policy-based submission remains disabled.

### Implemented tasks

1. **Browser-independent draft service.** Scan/classify/answer planning now creates and persists
   a normalized draft without filling or submitting the external page. The snapshot contains
   the destination, form signature, answers, omissions and revision hash.

2. **Opportunity preparation.** A `prepare_application` task and dashboard action connect a
   matched opportunity to its persisted application record.

3. **Draft review and immutable revisions.** The user can inspect and edit every answer in
   the web app. Each save creates a revision; history remains immutable.

4. **Explicit authorization.** Approval names the exact draft revision,
   destination host/form and expiry. Editing the draft or observing a changed field signature
   invalidates it. Revocation is immediate. Telegram cannot create this authorization.

5. **Crash-safe reviewed dispatch.** The worker reopens and rescans the form, rematches normalized
   signatures, requires a valid authorization, and records dispatch intent before clicking.
   It records the observed result afterward. If the process dies after intent, it marks
   `submission_unknown`; never retry that attempt automatically.

6. **Exact results in reports.** The detail view uses the stable application ID and shows the
   saved revision, exact values, authorization timestamps and outcome without claiming an
   employer receipt.

Likely primary changes: `src/openapply/applications/`, `src/openapply/storage/repositories.py`,
`src/openapply/worker/`, `src/openapply/web/`, `src/openapply/conversations/`, and new focused
authorization/resume/recovery tests under `tests/applications/` and `tests/web/`.

### Verified acceptance criteria

- A user can discover a fixture job, prepare its application from the web desk, edit it,
  authorize that exact revision and submit it once to a local form fixture.
- Preparation does not fill or mutate an employer page before authorization.
- A draft edit, destination change or form-signature change invalidates authorization.
- Two workers cannot dispatch the same authorized attempt.
- A crash after dispatch intent produces `submission_unknown` and no blind retry.
- The ledger and conversation return the exact saved answers and honest observed outcome.
- Existing CLI behavior remains compatible and the full test, lint, format and type suites pass.

Per-answer provider checkpointing is not part of this checkpoint: a provider limit can defer a
worker task, but generated answers within a partially prepared draft are not yet individually
persisted. Implement that before claiming perfect mid-generation resume behavior.

## Immediate next step: other-PC validation

Clone the checkpoint on a second Windows PC and run the isolated Google Chrome test in
[pc-test-checkpoint.md](pc-test-checkpoint.md). Then start the desk and worker with a fresh local
profile and report installation, browser or layout defects before platform-adapter work begins.

## Sequence after that milestone

1. Platform adapters and persisted browser login sessions for Greenhouse, Lever and Ashby,
   including iframe and multi-step fixtures.
2. Bounded automatic-submission policy with revocable scope, expiry, daily caps, sensitive-field
   pauses and atomic enforcement.
3. Linux VPS deployment package and systemd/SSH-tunnel restart smoke tests.
4. Durable Telegram outbox, digests and per-channel disclosure preferences.
5. Hosted web UI paired to a user-owned worker; only later evaluate managed multi-user workers.
6. Separately evaluate mannerism capture or general browser work automation. Neither should be
   coupled to application authorization.

## Resume checklist for the next agent

1. Read this file, the broader handoff, `README.md`, `CONTRIBUTING.md`, and `git diff` before
   editing. Preserve existing uncommitted profile-import work.
2. Install the web extra and inspect the current local flow:

   ```bash
   uv sync --extra web
   openapply agent serve
   openapply worker run
   ```

3. Run the other-PC checkpoint, record defects, and use only local HTML fixtures for submission
   regression tests. Never automate a real employer application in tests.
4. Verify before handoff:

   ```bash
   uv run pytest -q
   uv run ruff format --check .
   uv run ruff check .
   uv run mypy
   ```

5. Do not commit a personal CV, provider transcript, Telegram token, cookies, browser session or
   the private SQLite database.
