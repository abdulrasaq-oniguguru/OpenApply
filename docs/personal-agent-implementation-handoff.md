# OpenApply personal agent: current status and implementation handoff

Assessment date: 2026-10-09. Baseline commit: `4ab70bb` (`Tidy the repository for open source`).
This describes the working tree, including existing uncommitted changes, not just HEAD.
Status: implementation started. The first local release now includes SQLite migrations,
durable interviews and evidence confirmation, grounded prompt context, saved application-draft
history, deterministic reports, a private FastAPI web desk, bounded career-page discovery,
a leased worker with provider cooldowns, and optional paired Telegram conversation. The deeper
adapter, authorization and hosted-edition work described below remains the implementation plan.

Implemented commands: `openapply interview`, `openapply agent`, `openapply worker`, and
`openapply telegram`. The personal app runs with `openapply agent serve` after installing the
`web` extra. Automatic application submission is deliberately not enabled by this slice; the
existing reviewed `openapply apply` path and an exact, explicitly authorized web-draft dispatch
are the only senders. There is no policy-based or bulk submission mode.

For the concise live delivery status and the next implementable milestone, use
[personal-agent-progress.md](personal-agent-progress.md). Sections that describe the original
assessment are retained as design history and may describe capabilities before this first slice.

## 1. Product objective and scope

Extend OpenApply into a single-user application that interviews the candidate over time,
learns their problem-solving approach and writing preferences, discovers suitable jobs,
prepares evidence-grounded applications, and runs a persistent browser worker on a local
computer or Linux VPS. The user should be able to review activity through a web page and
use their already-authenticated CLI provider without entering an OpenApply API key.

The personal app is also a conversation interface: the user can interview, give directions,
ask what the agent is doing, inspect applications and request explanations in the same place.
Telegram is an optional second channel for that same agent. Default application reports are
short, with detailed evidence and answers available on request. The personal app is the primary
product; an eventual hosted/cloud edition should be possible without rewriting domain services.
The user's phrase "claude option later" is provisionally interpreted as "cloud option later";
if they meant Claude, generation through Claude already exists and remains supported.

The intended experience is: interview -> editable personal knowledge -> discover jobs ->
explain fit -> prepare applications -> review or explicitly authorized submission -> history.
When provider capacity runs out, save progress, release resources, and resume later.

Interpretation: the earlier reference to a page like "Mercer" means an interview and
opportunities dashboard. No particular external site's design, integration, or behavior has
been verified. Build this within the existing Python project, not as a replacement product.

The user also proposed recording typing/mouse mannerisms and eventually doing browser-based
work. Sections 11 and 12 account for these separately; neither is required to ship the core
application agent. Do not claim to clone the person or infer competence from mouse movement.

## 2. Verified current state

OpenApply is a Python 3.12+ CLI, version 0.1.0. It uses Typer/Rich, Pydantic, Playwright,
httpx, and TOML settings. There is no web server, frontend, scheduler, or deployment bundle.
README calls this milestone 5 of 6; the code supports that description, with the qualifications
below. This is a source inspection and local verification, not a live employer-site audit.

| Area | Exists now | Evidence / limitations |
| --- | --- | --- |
| Entry points | `doctor`, `setup`, `analyze`, `apply`, `profile`, `providers`, `browser` | `src/openapply/cli/app.py`; no interview/search/worker/serve commands |
| Candidate profile | Identity, links, skills, experience, projects, education, preferences, explicit eligibility, resume reference | `candidate/models.py`, `storage.py`, `service.py`; schema v1 in `profile.json` |
| Onboarding | Terminal prompts for basic profile, preferences, skills, eligibility and resume | `cli/commands/setup.py`; not an adaptive interview |
| Resume | Copy and verify PDF/DOCX by hash | `candidate/service.py`; `candidate/parser.py` uses `NullResumeExtractor`, so attaching a CV does not extract experience |
| Website import | Structured contact/profile-link import and review | Uncommitted `candidate/site_import.py`, `cli/commands/profile_import.py`, registration in `profile.py`; not general career-history extraction |
| Providers | Codex and Claude subprocess generation; Ollama local HTTP generation | `providers/`; Gemini/Copilot detection only |
| Job reading | Fetch supplied URL, extract structured posting with validation | `jobs/service.py`, `extractor.py`, `browser/browser.py`; no job discovery |
| Matching | Deterministic skills/experience/location/preferences score, explicit eligibility, optional AI notes | `jobs/matcher.py`, `eligibility.py`, `service.py`; AI cannot change score |
| Form preparation | Scan, classify, fill from profile, generate written answers, edit and preview | `applications/service.py`, `engine.py`, `generation.py`; six generated answers by default, one corrective retry each |
| Submission | Typed review confirmation plus required-field/destination checks | `cli/review.py`, `applications/engine.py`, `browser/forms.py`; no unattended submit policy |
| Browser | Headless fetching; apply can be headed or `--headless` | Fresh contexts; no saved login state. `--headless` still enters terminal review |
| Platforms | URL host recognition for Greenhouse, Lever, Ashby, LinkedIn, Indeed | `jobs/platform.py`; detection is not a platform adapter; no general iframe/multi-step support |
| Persistence | Profile, config, verified resume files | `config/paths.py` defines `database_path()`, but there is no implemented database/history repository |
| Quotas | Typed `ProviderRateLimited`, authentication and timeout errors | `providers/errors.py`; no reset timestamp, cooldown store or persistent retry queue |
| Tests/CI | Extensive local fixtures and mocked providers; Ubuntu CI configured | `tests/`, `.github/workflows/ci.yml`; local results recorded in section 14 |
| Conversation/report channels | None | No chat history, conversational task routing, Telegram integration or activity digest implementation |

### Working-tree changes that predate this handoff

Modified: `.gitignore`, `browser/browser.py`, `browser/page.py`, `cli/commands/profile.py`.
Untracked: `candidate/site_import.py`, `cli/commands/profile_import.py`,
`tests/candidate/test_site_import.py` (paths under `src/openapply/` except tests).
They add bounded page links/Person JSON-LD and website profile import. Preserve these changes;
do not reset them or assume they are already committed. Do not include personal CVs in commits.

### Important implementation details

- Providers currently return text; Python owns Playwright. Giving the provider full browser
  control would be a different architecture. Reuse the current separation for this work.
- `CodexProvider.generate()` uses an empty temporary working directory, stdin prompts,
  `codex exec`, read-only sandbox, ephemeral mode, and ignored user config/rules. Those flags
  are not by themselves proof that every provider tool is unavailable. Audit effective CLI
  capabilities before using it in an unattended service; do not repeat the README's absolute
  "no tools" claim without verification against the installed CLI.
- `AnswerGenerator.generate()` catches `ProviderError` and converts it into `NEEDS_USER`.
  `MatchService.match()` also absorbs provider errors into warnings. A worker cannot currently
  distinguish exhausted quota from ordinary missing information through these results.
- Current answer validation checks length, placeholders, links and email addresses. It is
  not a factual entailment check. A prompt asking the model not to invent facts is insufficient
  evidence for autonomous submission.
- `ApplicationDraft` is an in-memory dataclass; field IDs such as `oa-3` are scan-local.
  Persisted drafts cannot reuse those IDs blindly after restarting a browser.
- `SubmitResult` contains page URLs, navigation, excerpt, blocked hosts. It is not an employer
  receipt. A click or lack of an exception must not become "employer received application".
- Existing browser policy quarantines destinations after scanning and gates outgoing submit
  requests. It is not a guarantee that same-site scripts cannot save values before Submit.
  Preview must say whether it merely generated answers or already filled an external page.
- Known URL/DNS and response-stream size limitations remain in README. Persistent unattended
  browsing needs the hardening described in section 10, not just longer timeouts.

## 3. Integration decisions

1. Keep the existing Python domain services and provider interface. Add an orchestrator that
   calls these services; do not run the interactive `openapply apply` command from a worker.
2. Keep `profile.json` v1 as the canonical basic profile initially. Put interviews, evidence,
   writing preferences, jobs, drafts and tasks in SQLite at the existing `database_path()`.
   Snapshot the validated profile and its content hash when preparing each application.
   If a later profile v2 is needed, add explicit migrations and backup/rollback tests first.
3. Start with one OS user, one candidate and one worker per OpenApply data directory. Use
   SQLite transactions, foreign keys and versioned migrations. No Redis/Celery is necessary
   for this scope. This is not a shared subscription backend for multiple users.
4. Add an optional FastAPI/Jinja2 web interface with small bundled JavaScript for polling or
   SSE. Use the same services as Typer. Proposed dependencies belong in an optional `web`
   extra so existing CLI installations remain usable. Pin compatible versions when implementing.
5. Defaults remain review-before-submit. Add explicitly configured bounded automatic
   submission later, after history, durable authorization, adapters and recovery work.
6. Treat interview material, job pages and imported content as data, never worker instructions.
   Provider responses propose validated records/actions; they cannot directly grant authority.
7. Put chat, reports and command routing in channel-independent services. The personal app and
   Telegram share these services and the same task state; neither launches its own autonomous
   agent loop or treats conversational text as executable shell commands.

```text
CLI / private web UI
        |
        v
Candidate knowledge + discovery + application services
        |                         |
        v                         v
SQLite queue/history         Existing provider registry
        |                    (authenticated CLI / Ollama)
        v
Durable worker -> validated browser adapters -> existing request policy -> sites
        |
        +-> needs-user inbox / cooldown / review / outcome evidence
```

## 4. Data model and storage contract

Add `src/openapply/storage/{database,migrations,repositories}.py`. Use UTC timestamps and
application-generated UUIDs for new records. Validate JSON columns with Pydantic on read and
write. Use `schema_migrations`, numbered transactional migrations and a pre-migration backup.
Keep SQLite and its WAL/SHM files inside the private OpenApply data directory. Do not place
the live database on a network filesystem. A backup must use SQLite's backup mechanism,
not copy a database file while writes are active.

| Record / table | Required information |
| --- | --- |
| `interview_sessions`, `interview_turns` | Topic, status, question, user answer, ordered sequence, timestamps, revision; unique session/sequence and client request ID |
| `evidence_items` | Kind, original source reference, source quote, structured claim/story, tags, confirmation state, revision, created/updated timestamps |
| `writing_samples` | User-authored or explicitly accepted text, context, source turn/edit, reuse permission; distinguish generated text from user writing |
| `writing_preferences` | Explicit tone/length/phrasing preferences, avoid-list, revision; model-suggested preferences require confirmation |
| `search_plans`, `search_sources` | Roles, locations, exclusions, cadence, allowed source URLs, pagination/page limits, enabled flag, last cursor |
| `opportunities` | Canonical URL, platform/external ID when verified, posting snapshot/hash, discovery source, first/last seen, active/closed/unknown |
| `matches` | Opportunity ID, profile/evidence revisions, scoring version, deterministic result, separately labelled advisory fit notes |
| `applications`, `draft_revisions` | Opportunity, state, profile snapshot/hash, evidence IDs/revisions, normalized field signatures and values, resume hash, destination, draft hash |
| `authorizations` | One-off review or policy grant, actor, scope, draft/policy revision, allowed destinations/actions, expiry, revocation, consumption |
| `tasks`, `task_events` | Type, payload schema version, state, checkpoint, attempt count, next-run time, lease owner/expiry, sanitized error, transition history |
| `provider_cooldowns` | Provider/account scope, reason, next probe time, reset time if actually supplied, failure count |
| `submission_attempts` | Application/draft/authorization IDs, durable intent time, dispatch state, observed outcome, sanitized receipt evidence |
| `browser_session_refs` | Site/account scope, encrypted state reference, expiry, last validation; never raw cookies in general task JSON |
| `user_actions` | Missing answer, contradiction, login challenge, changed form, uncertain submission, linked task/draft, resolution |
| `conversations`, `messages` | Owner, channel/source message ID, role, text, timestamps, reply reference, linked task/application/interview, processing status |
| `channel_bindings` | Owner, verified channel identity, enabled capabilities, notification preferences, revocation; secrets stored separately |
| `activity_reports`, `report_items` | Reporting interval/timezone, event watermark, application/task references, outcome counts, short rendering, detail references |
| `notification_outbox` | Report/event ID, destination binding, delivery state, attempts, next retry, provider message reference; unique event/destination key |

Evidence kinds should include `career_fact`, `experience_story`, `problem_solving_example`,
`work_preference` and `writing_preference`. Confirmation states: `proposed`, `confirmed`,
`rejected`, `superseded`. Preserve provenance: the original user answer can support a proposed
summary, but the model's interpretation is not automatically a confirmed fact. A hypothetical
scenario answer must not later become a claim about a real project.

Deduplicate opportunities using platform+verified external ID where available and canonical
URL aliases otherwise. Reuse existing `job_id()` for compatibility, but do not assume its URL
hash deduplicates the same job across different sites. Enforce one active application per
candidate/opportunity. A submitted or uncertain application cannot silently spawn another.

Store only necessary interview/application content, with export and deletion controls. Do not
log raw transcripts, passwords, cookies or complete form bodies. Retain minimal duplicate-
prevention tombstones only when disclosed; full deletion should explain that dedupe history
will also be lost. Evidence deletion/supersession invalidates affected unsubmitted drafts.

## 5. Adaptive interview and personal knowledge

New modules: `interviews/models.py`, `service.py`, `questions.py`,
`candidate/knowledge.py`, `prompts/interview.py` and `prompts/evidence_extraction.py`.

Start from the current profile and ask one focused question at a time. Cover motivation,
constraints, actual projects, problem decomposition, debugging, tradeoffs, collaboration,
mistakes and lessons. Use concrete follow-ups: "What did you try first?", "Why that option?",
"What changed?", "Which result can you substantiate?" Let users skip, stop and resume.
More questions should close information gaps, not maximize interview length.

Service contract (proposed):

```python
start_interview(topic: str) -> InterviewSession
record_answer(session_id: UUID, turn_id: UUID, text: str, request_id: UUID) -> InterviewTurn
async propose_next_turn(session_id: UUID) -> InterviewQuestion
async extract_evidence(turn_id: UUID) -> list[EvidenceProposal]
confirm_evidence(item_id: UUID, expected_revision: int, edited_value: dict) -> EvidenceItem
build_answer_context(question: str, job_id: str, budget_chars: int) -> KnowledgeContext
```

Persist the user's answer before calling AI. A provider failure must never lose the answer or
cause it to be submitted twice on retry. Question generation/extraction are queueable work.
Use `generate_structured()` with bounded schemas. Keep a deterministic fallback question bank
so interviews can continue when the provider is unavailable.

Retrieve a small set of confirmed relevant stories and writing samples using tags/text search
first; no embedding service is required. Store why each item was selected. Use contradictory
or unconfirmed material to ask questions, not to generate assertive application claims.
Corrections to application drafts may suggest new evidence/style preferences, but only
explicitly accepted reusable facts should update knowledge. Employer-specific edits stay
scoped to that application unless the user promotes them.

## 6. Application answers and richer matching

Extend `AnswerGenerator` and `build_application_answer_prompt()` to accept an optional
`KnowledgeContext`, preserving existing behavior when absent. Keep `minimal_profile()`'s
privacy boundary; do not send the whole interview database with each question.

Introduce a structured generated answer result: `text`, `evidence_ids`, `unsupported_claims`,
`needs_user`, `reason`. Validate cited IDs against the supplied confirmed evidence and preserve
existing text/link/length checks. Evidence IDs alone do not prove an answer faithfully uses
the evidence. Flag new numbers, dates, employers or credentials for review; keep novel free-text
answers in review mode until evaluation demonstrates acceptable grounding. Automatic mode
initially uses deterministic profile fields and user-approved reusable answers with compatible
question context. It must not turn every generated answer into an approved answer.

Retain the deterministic fit score and eligibility constraints. Add separate advisory
`work_style_fit` notes with evidence references and uncertainty. An interview cannot establish
that an employer has a certain culture unless the posting supplies supporting information.
Do not silently change score weights, eligibility or recommendation caps based on AI notes.
Explicit new dealbreakers should be implemented as deterministic filters with explanations.

Refactor recoverable errors into typed preparation results (for example `ProviderIssue` with
rate-limit/auth/timeout kind) or add a worker-specific propagation policy. Existing interactive
CLI fallback can remain; worker mode needs to checkpoint and retry a missing generated answer
after quota recovery without relabelling it as a missing personal fact.

## 7. Browser-based job discovery

New `discovery/` package: source protocol, browser listing adapters, planner and service.
Implement both user-selected career pages and browser search-result discovery. No paid search
API is required by the design; compatibility and access rules still vary by source.

The first release supports bounded company career pages/Greenhouse/Lever listings. A generic
search-page adapter is a subsequent step with explicit query, result count and pagination
limits. Do not market initial source-list crawling as comprehensive internet search.

`DiscoverySource.discover(plan, cursor) -> DiscoveryPage` returns candidate URLs and source
metadata, not trusted job records. Validate every URL, dedupe before extraction, call existing
`JobService.analyze()`, then `MatchService.match()`. Cache unchanged postings and score against
current profile revisions. Record source coverage, inaccessible pages and expiry status.
Use the bounded `FetchedPage.links` work already in progress rather than another extractor.

Exclude login walls and challenges from automatic retries; create a user action where needed.
All searches and employer interactions run through the controlled browser path. AI generates
bounded search terms/classifications; a website cannot expand source scope or worker permissions.

## 8. Durable worker, limits and recovery

New `worker/{models,queue,runner,retry}.py`; default concurrency one. Use short SQLite
transactions to acquire/renew leases. Never hold a database transaction while awaiting AI or
browser I/O. A second worker must not acquire an unexpired lease. Limit each task's provider
calls, page visits, wall time and retries, plus a configurable daily application cap.

Task states: `queued`, `running`, `waiting_provider`, `needs_user`, `completed`, `failed`,
`cancelled`. A saved checkpoint identifies the next safe operation, not a browser object.
Keep application state separate: `discovered`, `shortlisted`, `preparing`, `awaiting_review`,
`authorized`, `submitting`, `submitted_unverified`, `confirmed`, `submission_unknown`,
`rejected`, `withdrawn`. Here `rejected` means the user chose not to apply, not an employer reply.

| Condition | Required behavior |
| --- | --- |
| Explicit provider quota/rate error | Save completed work; set account/provider cooldown; release task lease and browser; retry after supplied reset or conservative exponential backoff with jitter |
| Reset time unknown | Label retry time as an estimate, not actual quota reset; start with 15 minutes, cap at 6 hours, allow user configuration; repeated failure stays paused with visible status |
| Authentication required | `needs_user`; do not repeatedly launch login flows |
| Provider timeout/transient browser read error | Bounded retry of safe reads/generation only; no blind retry of a submit |
| Malformed model output | Existing one corrective retry, then actionable failure/review; do not loop indefinitely |
| Worker restart before submission | Recover expired lease and revalidate checkpoint, profile, form and authorization before continuing |
| Crash/timeout after submission intent was recorded | `submission_unknown`; inspect available receipt/status evidence or request user resolution; never automatically click again |
| User pause/cancel | Stop scheduling new actions, persist state; if dispatch may already have happened, preserve uncertain outcome rather than claiming cancellation prevented it |

Extend `ProviderRateLimited` with optional `retry_after_seconds`/`reset_at` only when supported
by genuine provider output. Do not parse arbitrary times in job text as quota information.
Track call counts/durations and available usage metadata; do not fabricate remaining tokens.
No automatic switch to a paid API or another provider/account on quota exhaustion.

The scheduler sleeps outside the provider process. Do not keep a model turn or browser open
for hours just to wait for reset. Checkpoint successful generated answers individually so a
retry does not repeat extraction and every earlier answer.

## 9. Review and optional automatic submission

Current `CONTRIBUTING.md` says: "Nothing is submitted without the user" and directs all
submissions through `cli/review.py`. This handoff proposes a deliberate future behavior change,
not a bypass of that implementation. Implementing automatic mode requires updating that
document, README, enforcement code and tests together. This documentation turn authorizes no
real applications or background worker activation.

Extract a shared `applications/authorization.py` service used by both CLI and web review.
Replace the worker's prospective `confirmed=True` shortcut with a validated authorization
record. Preserve the existing terminal confirmation experience through the shared service.

Two authorization modes:

- **Review:** user approves the exact draft, resume, opportunity and destination. Approval has
  a revision/hash and expiry. Any material change invalidates it.
- **Bounded automatic:** user explicitly enables a policy specifying allowed roles, sites,
  destinations, minimum deterministic fit/confidence, exclusions, daily cap, permitted answer
  sources and expiry. No unresolved eligibility, sensitive prefill or consent may slip through.
  Initially restrict this to tested adapters and reusable approved answers. Pause for unknown
  fields, new attestations, changed destinations, login challenges and personal assessments.

Policy activation is a product action performed by the user; a worker or AI suggestion cannot
activate it. Store revocation and check it immediately before dispatch. Reserve daily quota
and consume one-off authorization atomically with creation of the submission attempt. Record
`submitting` durably before clicking. A local transaction cannot make an external website
submission exactly-once; uncertain attempts require reconciliation rather than retry.

Rescan before submission. Match fields by adapter-scoped semantic signature: form/step,
name, type, normalized label, option values and required status. If matching is ambiguous or
material content changed, create a new draft for review. Never replay old `oa-*` IDs.
Revalidate resume hash, current values, blockers, posting availability and destination.
Keep network destination enforcement in `browser/policy.py`, including redirects.

Add Greenhouse/Lever adapters behind a `FormAdapter` protocol, with bounded `scan_step`,
`fill_step`, `advance_step`, `submit`, and `observe_outcome` operations. Intermediate Next
buttons can transmit data too: classify and authorize their destinations, not only the final
Submit. Support iframes explicitly; never silently treat a partial scan as the whole form.
Unknown flows go to the user rather than unrestricted model-driven clicks.

## 10. Web interface and Linux deployment

Proposed pages: Conversation/Overview, Interview, Personal knowledge, Opportunities, Application review,
History, Worker/settings. Overview shows current activity, pending user actions, provider
cooldown/retry time and pause control. Review shows exact answers, provenance, missing facts,
resume, destination and generated/approved distinctions. History distinguishes request sent,
site confirmation and unknown outcome.

New `web/` package with routes, schemas, templates and bundled static assets. Proposed routes:

| Route | Purpose |
| --- | --- |
| `POST /api/interviews`, `POST /api/interviews/{id}/answers` | Start interview / persist an answer with idempotency key |
| `GET /api/knowledge`, `PATCH /api/knowledge/{id}` | Inspect/confirm/correct with expected revision |
| `POST /api/search-plans`, `POST /api/search-plans/{id}/runs` | Configure search / enqueue bounded discovery |
| `GET /api/opportunities`, `POST /api/opportunities/{id}/applications` | Inspect matches / enqueue preparation |
| `GET /api/applications/{id}`, `PATCH /api/applications/{id}/draft` | Review and edit versioned draft |
| `POST /api/applications/{id}/authorize` | Authorize exact reviewed revision/destination; requires authenticated user action |
| `POST /api/automation-policies`, `POST /api/automation-policies/{id}/revoke` | Explicit bounded policy creation/activation and revocation |
| `GET /api/tasks`, `POST /api/tasks/{id}/resume`, `POST /api/tasks/{id}/cancel` | Worker visibility and recovery controls |
| `POST /api/worker/pause`, `POST /api/worker/resume`, `GET /api/events` | Global scheduling control and progress stream |
| `POST /api/conversations/{id}/messages`, `GET /api/conversations/{id}/messages` | Chat with stable client message IDs and cursor-based history |
| `GET /api/reports`, `GET /api/reports/{id}` | Short digest and drill-down records from persisted outcomes |
| `POST /api/channels/telegram/pair`, `DELETE /api/channels/telegram` | User-initiated verified pairing / immediate revocation |

Use 409 for stale revisions and 422 for validation failures; long-running operations return
202 with a task ID. Every mutation needs authentication and CSRF/origin protection. Escape
page/model text in HTML; do not render arbitrary scraped HTML. Do not expose raw SQL, shell
commands, filesystem paths or Playwright/CDP through an endpoint.

New proposed CLI commands: `interview start/resume`, `knowledge show`, `search`,
`applications list/show`, `worker run/status/pause/resume`, `serve`, `sessions login/forget`.
Register them in `cli/app.py`; make commands thin service wrappers. `serve` should not start
an extra worker implicitly when multiple web processes start.

Deployment target: single-user Linux VPS, non-root service account, Python environment,
installed supported CLI and Chromium dependencies. Add deployment instructions and systemd
units for separate web/worker processes, restart-on-failure and graceful SIGTERM checkpoints.
Use a persistent private `OPENAPPLY_HOME`; keep the provider's own authentication under the
service user's normal provider-managed configuration. OpenApply must not read/copy tokens.

Bind the UI to loopback by default and access it over an SSH tunnel. Public hosting requires
HTTPS and real authentication; a secret URL is insufficient. Keep browser debugging ports
private. Use an SSH-tunneled remote desktop for manual login/challenge handoff if required;
a headless browser alone is not a visible browser the user can take over.

Persist browser storage only through opt-in site/account-scoped sessions. Encrypt saved state
with a key outside the database, restrict file permissions, expire/delete it, and never give
cookies to the model. Default existing CLI fetch/apply behavior remains ephemeral. Fresh
discovery contexts must not inherit application login sessions. Recheck and reattach network
policy for every context, including resumed contexts.

Before unattended external browsing, close known DNS/private-address bypasses and unbounded
chunked-response reads; test redirects and IPv4/IPv6/private/link-local destinations. Enforce
an egress network boundary as well as application validation so DNS rebinding cannot reach
VPS metadata/internal services. Do not enable `--allow-local` in production worker browsing.
Local fixture servers and local provider endpoints need separate, explicit allowances.

Subscription constraints are runtime facts, not a product guarantee. The design supports
authenticated Codex CLI use, but does not promise "$20 covers unlimited applications" or a
fixed reset cadence. VPS/browser resources cost separately. Verify installed CLI flags,
headless authentication and effective tool permissions before deployment. Official references
checked during the preceding feasibility discussion: [authentication](https://learn.chatgpt.com/docs/auth),
[non-interactive execution](https://learn.chatgpt.com/docs/non-interactive-mode),
[usage limits](https://learn.chatgpt.com/docs/pricing). These describe provider features, not
proof that this proposed OpenApply integration has been deployed or validated.

### 10.1 Conversation and progressive reports

Add `conversations/{models,service,router}.py`, `reports/{models,service,render}.py` and
`channels/{base,web,telegram,outbox}.py`. Proposed shared contracts:

```python
receive_message(binding_id: UUID, external_id: str, text: str) -> MessageReceipt
async interpret_message(message_id: UUID) -> ProposedIntent
execute_validated_intent(intent: ProposedIntent, actor: ActorContext) -> CommandResult
build_report(owner_id: UUID, start: datetime, end: datetime) -> ActivityReport
render_report(report: ActivityReport, detail: str) -> ChannelMessage
```

Persist incoming messages before interpretation. Deduplicate delivery by channel binding and
external message ID. Persist the resolved command and effect so redelivery cannot enqueue
duplicate work. Channel identity supplies the actor; model output cannot select another actor,
invent an approval or expand permissions. Support a closed intent set: `interview_answer`,
`search_jobs`, `explain_match`, `show_application`, `show_report`, `edit_preference`,
`pause_worker`, `resume_worker`, `request_review`, plus a non-action conversational response.
Vague instructions produce a concrete proposed action/card for clarification. Submission
still uses section 9's authorization flow, not a model-classified casual "yes".

Examples the first version should handle:

- "Find remote Python roles that fit my background" -> show the interpreted search scope,
  use known preferences, and enqueue bounded discovery within the configured sources.
- "What did you apply to today?" -> query durable submission outcomes for the user's timezone.
- "Why did you choose the second one?" -> resolve the opportunity ID attached to the previous
  report item, then show score factors, evidence and uncertainties.
- "Show the exact answers you sent" -> retrieve that attempt's immutable draft snapshot,
  not regenerate an approximate answer from the current profile.
- "Pause applications, keep looking for jobs" -> pause submission tasks independently of
  discovery, subject to any dispatch already in progress.

Use a concise default report, for example:

> Today: 2 application requests sent, 3 drafts ready, 1 needs your answer.
> Acme — Backend Engineer: site showed a confirmation.
> Example Co — Python Developer: request sent; receipt unverified.
> Next: waiting for provider capacity. Estimated retry 15:00.

This is illustrative, not an actual application report. Real counts and company names must
come from persisted records. Separate confirmed, sent-unverified and uncertain outcomes;
never count discovery, a prepared draft or a clicked button as a confirmed application.

First expansion: role/company/link, why it matched, submission time, current status and next
action. Full detail: exact submitted answers, resume version/hash, evidence provenance,
authorization used, destination, observed receipt and relevant sanitized error/event history.
Only include fields that actually exist. Do not expose hidden reasoning or raw credentials.
The model can explain recorded decisions; the event ledger is the factual source of reports.

Offer immediate needs-user alerts, an optional completion summary and a scheduled daily
digest with timezone/quiet-hours settings. Default to in-app updates until the user enables
external delivery. Coalesce routine events. Build deterministic short reports without AI so
quota exhaustion cannot prevent status updates. Read-only report questions should also have
a deterministic fallback when natural-language generation is unavailable.

### 10.2 Optional Telegram channel

The same conversation can be accessed through a verified private Telegram binding; link each
message to its originating channel while keeping one application/task history. The personal
app should show Telegram-originated actions and responses too. Cross-channel answers must
resolve the same task/version, not consume a pending question twice.

Start with private-chat notifications, status queries, detail requests, interview replies and
pause controls. Keep exact application approval and policy activation in the authenticated
personal app for the first Telegram release, using a normal authenticated app link. A later
Telegram approval flow would need expiring action nonces bound to user, draft and destination;
do not accept free-text "yes" as a transferable authorization.

Pair by an expiring single-use code created inside the authenticated app. Verify and bind
the sender/chat identifier; possession of the bot name or a forwarded message is not identity.
Reject unbound senders and group chats initially. Apply request-size and message-rate limits.
Store any bot token as a deployment secret, separate from settings/model prompts/logs. This
token is a channel credential, not an AI API key; state this clearly during setup.

Use a channel adapter and durable notification outbox. Choose long polling for the initial
private VPS deployment to avoid requiring a public webhook; verify current Telegram API
semantics and library compatibility from official documentation during implementation.
A later webhook mode must authenticate incoming deliveries and handle duplicate updates.
Do not assume Telegram provides exactly-once sending: after a delivery timeout record the
uncertainty and avoid uncontrolled retries. An uncertain report send must never repeat the
underlying application task. Handle message-length limits by sending a compact report and
an authenticated link to details. Full answers/transcripts are sent externally only when the
user requests them and the channel is configured to allow that disclosure.

Telegram delivery necessarily shares message content with that service. Explain that during
opt-in, provide disconnect/delete controls and make the feature optional. No Telegram bot,
credentials, pairing or messages are created/sent as part of this documentation task.

### 10.3 Later hosted/cloud edition

Ship the personal app first. Put an owner identifier in new repositories/service interfaces
even while there is only one owner, to avoid relying on global candidate state in the web
layer. This is preparation, not evidence of tenant isolation or multi-user readiness.

The first hosted architecture to evaluate is a hosted conversation/report UI paired with
each user's own worker, authenticated provider CLI and browser. Use an authenticated outbound
worker connection with narrowly scoped task commands; never offer a remote shell endpoint.
This preserves the personal-worker approach without uploading CLI credentials to a shared
server. Disclose which profile/draft/report data the hosted UI receives.

A fully managed worker edition is a separate milestone needing verified provider integration
rights, per-user execution/browser isolation, authentication, quotas, encrypted secret storage,
tenant-access tests, backups/deletion and appropriate database/queue infrastructure. Do not
share one personal provider subscription among unrelated customers or advertise a fixed
subscription price as the product's guaranteed cost. Migrate single-user JSON profile access
and SQLite repositories deliberately before enabling multiple tenants. Keep Codex, Claude
and Ollama behind the existing provider abstraction in either edition.

## 11. Optional mannerism capture

Writing-style learning belongs in the core through user answers and accepted edits. Mouse
and typing telemetry is an optional later experiment, not a dependency for finding jobs or
filling forms. If implemented, capture only inside OpenApply's own explicitly started sample
session with a visible recording indicator and stop/delete controls. Never install an OS-wide
keylogger or record passwords, employer login fields or unrelated websites.

Store only consented coarse timing/edit statistics and pointer trajectories for the sample,
with short retention and deletion. Do not infer personality, disability, intelligence or
professional competence from those signals. Do not use recordings to defeat bot detection,
impersonate a person during an assessment, or claim browser actions are human-performed.
Any playback used for a local demonstration must be labelled. Prioritize measurable answer
quality from the interview before spending effort on this experiment.

## 12. Future browser work automation

This is a separate later workflow package, not an extension of permission to apply for a job.
It may reuse the worker, browser sessions and audit history, but needs its own explicit task
scope, permitted sites/data/actions, completion checks and handling for irreversible actions.
Do not infer permission to work in an employer account from an application authorization.
Personal interviews and assessments remain user-performed. Initial release acceptance does
not include arbitrary browser jobs or an unrestricted "act as me" mode.

## 13. Implementation sequence and acceptance criteria

Each phase should be independently reviewable. Preserve the existing CLI and user changes.
The implementing agent should first recheck this working tree and run the baseline suite.
Current completion and partial-completion status is tracked in
[personal-agent-progress.md](personal-agent-progress.md); the table below is the broader roadmap.

| Phase | Work and primary files | Acceptance evidence |
| --- | --- | --- |
| 1. Durable foundation | `storage/`, application repositories, profile snapshots | Migration/rollback tests; restart preserves tasks/drafts; duplicate discovery yields one opportunity; no user profile mutation on read |
| 2. Interview and knowledge | `interviews/`, `candidate/knowledge.py`, prompts, CLI | Stop/resume and quota failure lose no answers; extracted stories retain source; hypothetical answers never become real achievements; confirmation/correction invalidates stale drafts |
| 3. Grounded answers | Extend `generation.py`, `service.py`, answer prompts and review | Relevant confirmed stories appear with provenance; unsupported facts go to review; no contact/eligibility/transcript leakage into minimal prompts; old CLI tests still pass |
| 4. Discovery and ranking | `discovery/`, opportunity/match repositories | Local listing/pagination fixtures; dedupe aliases; closed jobs; access challenge state; bounded crawl; unchanged deterministic matching and visible work-style uncertainty |
| 5. Worker and cooldown | `worker/`, typed provider outcomes | Fake-clock quota/reset tests; restart resumes unfinished answer only; expired lease recovery; two workers cannot dispatch same task; no automatic paid fallback |
| 6. Web review | `web/`, shared authorization service, optional web extra | Full interview -> shortlist -> draft -> explicit authorization -> local fixture submission; stale approval/CSRF/unauthenticated requests rejected; pause/status visible |
| 6a. Personal-app conversation and reports | `conversations/`, `reports/`, web channel | Chat references resolve stable application IDs; concise report counts match ledger; requested details show exact historical answers; reports still work during AI cooldown |
| 7. Browser sessions and adapters | `browser/sessions.py`, `applications/adapters/`, policy hardening | Login expiry handoff; session isolation; iframe/multi-step fixtures; changed destination blocked; external submit uncertainty retained; no cookie exposure in logs/prompts |
| 8. Bounded automatic mode + VPS | Policy enforcement, worker dispatch, deployment docs/units | Revoked/expired/out-of-scope grants cannot send; daily cap atomic; sensitive unknowns pause; kill/restart after dispatch never resubmits; Linux restart/login/cooldown smoke test |
| 9. Optional Telegram | `channels/`, pairing, outbox, report preferences | Only paired user can act; duplicate input has one effect; uncertain notification send never repeats submission; disconnect works; sensitive detail disclosure follows settings |
| Later | Optional capture and separately scoped work automation | Separate evaluation and explicit product scope; not required to call phases 1-8 complete |
| Later hosted edition | Hosted UI + paired personal worker first | Explicit owner/worker authorization; no shared provider credentials; tenant-isolation evaluation before managed multi-user workers |

Phase 6 fixture submission can use the existing single-page browser implementation; phase 7
is required before claiming reliable support for external platform workflows. UI layout and
styling can improve after the service contracts are stable; do not replace the domain model
with frontend-only state.

Required test additions: `tests/interviews/`, `tests/discovery/`, `tests/worker/`,
`tests/storage/`, `tests/web/`, `tests/applications/test_authorization.py`,
`test_resume_draft.py`, `test_submission_recovery.py`, and session/adapter browser fixtures.
Add `tests/conversations/`, `tests/reports/`, `tests/channels/` for cross-channel identity,
message deduplication, stale replies, report counts/timezones, outbox retry and disclosure.
Use fake providers, injected clock/scheduler, temporary data homes and local HTML servers.
Do not use real employer applications as automated test fixtures.

Critical end-to-end scenario: interview -> confirm a real project story -> discover and match
a fixture job -> create a draft in the user's preferred style -> provider limit halfway through
preparation -> worker restart -> resume without repeating completed answers -> user reviews ->
submit once -> preserve observed outcome. Repeat with crash after dispatch and require
`submission_unknown`, not an automatic second submission. Then ask for a short report and
exact answers in the personal app; they must reflect the saved attempt. Mirror status through
a fake paired Telegram adapter, redeliver the same inbound update and prove no repeated task.

## 14. Baseline verification from this assessment

- Ruff lint: passed (`.venv/Scripts/ruff.exe check .`).
- Ruff formatting: passed (131 files already formatted).
- Mypy: passed (128 source files).
- Final implementation verification: **767 passed, 2 skipped** in 202.69 seconds, using
  `.venv/Scripts/python.exe -m pytest -q`. Both skips are POSIX-permission tests on Windows.
- Ruff format/lint and strict mypy pass across the implementation. The personal app was also
  launched locally and inspected at desktop resolution. Its complete reviewed application loop
  passed in installed Google Chrome against a local form fixture.
- The initial sandboxed pytest run failed in fixture setup because the existing Windows temp
  directory was inaccessible. A permitted run outside the sandbox is used for the actual
  baseline; these initial permission errors are not application regression findings.
- No live paid-provider generation, employer-site submission, Linux VPS deployment or external
  account changes were performed. Local tests cannot establish compatibility with every site.

## 15. Implementer starting checklist

Read [personal-agent-progress.md](personal-agent-progress.md), this document, `README.md`,
`CONTRIBUTING.md`, and current diffs first. Reconcile any changes since the assessment. Phases
1-5, the reviewed single-page portion of phase 6, and parts of phases 6a and 9 now have a working
first slice. Run the other-PC checkpoint in the progress ledger before starting external platform
adapters. Keep each deliverable runnable via CLI.
Do not start by giving Codex unrestricted browser access, replacing the project, changing
score semantics, or calling `submit(confirmed=True)` from a timer. The deliverable is an
auditable, resumable personal application workflow built on the existing engine.
