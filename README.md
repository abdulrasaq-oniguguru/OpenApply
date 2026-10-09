# OpenApply

A local-first AI job application assistant that uses the AI tools **you already have**.

> **Status: alpha.** Provider detection, the local candidate profile, reading and matching job
> postings, reviewed form filling, application history, a private personal-agent app, interviews,
> bounded career-page discovery and a resumable worker work today. Platform-specific multi-step
> adapters and automatic submission policies are still planned.

## Problem

Many job seekers already pay for tools such as ChatGPT, Claude or GitHub Copilot, or run local
models. Most AI job tools ask for yet another subscription or separate API spending.
OpenApply lets you bring your existing AI environment instead. It never asks for an API key.

## Install

Requires Python 3.12+ and a Chromium-family browser. An already-installed Google Chrome or
Microsoft Edge is used automatically; nothing extra needs downloading on most machines.

```bash
git clone https://github.com/abdulrasaq-oniguguru/OpenApply.git
cd OpenApply
uv sync                      # or: pip install -e .
uv run openapply doctor      # shows which AI tools and browser it found
```

You also need at least one AI tool you already use and are signed in to: Codex CLI, Claude Code
or Ollama. OpenApply never asks for an API key.

## Example

```bash
openapply doctor                      # what's installed and ready on this machine
openapply tools --json                # stable command catalog for agents
openapply start                       # run the private desk and worker together
openapply providers list
openapply providers set-default ollama --model qwen3.5:9b
openapply setup                       # create your local candidate profile
openapply profile show
openapply analyze <job-url>           # read a posting, then score it against your profile
openapply analyze <job-url> --no-ai   # deterministic score only (no extra AI call)
openapply apply <form-url>            # fill the form, review it, then (maybe) submit
openapply apply <form-url> --job-url <posting-url>   # when the posting is a different page
openapply apply <form-url> --preview-only            # fill and preview; never submits
openapply interview start                            # build your evidence and work stories
openapply worker discover <career-page-url>           # queue bounded job discovery
openapply worker platforms --json                    # list built-in public job sources
openapply worker discover-platform remotive --query "software engineer"
openapply worker run                                 # process durable work; safe to restart
openapply agent report --timezone Africa/Lagos --details
```

## Personal application desk

Install the optional web dependencies, then start the private app and worker together:

```bash
uv sync --extra web
openapply start                       # http://127.0.0.1:8080
```

`openapply agent serve` and `openapply worker run` remain available when you want the two
processes separately. Agents can call `openapply tools --json` for the supported command catalog
and `openapply worker list --json` for durable task state, errors and leases. The repository also
ships [`skills/cli-anything-openapply/SKILL.md`](skills/cli-anything-openapply/SKILL.md) as a short
operating guide for coding agents.

After changing the profile or upgrading matching rules, `openapply worker rematch --json`
recomputes every saved opportunity locally. It makes no browser or AI call.

Built-in discovery supports Mercor, Outlier AI, DataAnnotation, Alignerr, micro1, Himalayas,
Remotive, Wellfound and We Work Remotely. It uses rendered public pages where appropriate,
Himalayas and Remotive's public JSON APIs, and We Work Remotely's public RSS feed. A role query
keeps the queue focused; if it is omitted, OpenApply uses the first preferred role in the saved
profile when available. Mercor accepts both `/jobs/list_<id>/<slug>` links and legacy
`/explore?listingId=<id>` links.

The app combines conversation, an interview, confirmed evidence, discovery tasks, matched
opportunities and the application ledger. It binds to loopback by default. On a VPS, keep that
default and use an SSH tunnel; the current alpha is not an authenticated public website.

The desk also accepts a PDF or DOCX resume. It extracts text locally, shows an editable profile
preview, and saves only after explicit review. Standard contact, skill, experience and education
sections work without AI; an optional checkbox can send the extracted text to the selected AI for
richer structuring. The **AI CLI** selector in the header detects ready Codex, Claude and Ollama
installations and persists the choice, so a new machine does not require a code change.

The worker is a separate process, so restarting the web page does not lose queued work:

```bash
openapply worker run
```

Expected extraction failures are recorded immediately instead of leaving a task leased as
“running”. If the combined launcher is interrupted, its current task is returned to the queue;
an abrupt process or machine failure still relies on the five-minute recovery lease.

Discovery is intentionally bounded to a company career/listing page you provide. It queues
likely job URLs and analyzes them through the existing browser/provider pipeline. The worker
never submits automatically. From a matched opportunity, the desk can prepare a local draft,
save immutable edits, authorize one exact revision and queue one reviewed submission. A changed
form invalidates approval, and an uncertain dispatch is never retried blindly. The existing
interactive `openapply apply` path remains available.

Application runs now save an immutable snapshot of the prepared answers and the observed
outcome. Ask the app “what did you apply to today?” for a short report, then “show exact
answers” for the saved draft. “Sent but unverified” means the submit flow completed but
OpenApply has no employer receipt.

### Optional Telegram channel

Telegram uses the same conversation and history as the personal app. Create a bot through
Telegram, keep its token in the process environment, then pair one private chat:

```bash
export OPENAPPLY_TELEGRAM_BOT_TOKEN="..."
openapply telegram pair
openapply telegram run --timezone Africa/Lagos
```

Pairing codes expire after 15 minutes and work once. Groups and unpaired users are ignored.
Telegram can request reports, details and interview questions, but cannot approve an
application. Message content sent through this option is necessarily shared with Telegram.

## Privacy

Candidate information stays on your machine by default (`~/.openapply/`, or `OPENAPPLY_HOME`).
The basic profile remains JSON; interviews, conversations, tasks and application history use a
private local SQLite database. There is no hosted OpenApply backend. Text is sent only to the AI
tool you choose and, if you explicitly enable it, Telegram. Those services' privacy terms apply.
Logs redact bearer tokens, cookies, API keys and similar secrets.

## Reading job postings

`openapply analyze <url>` opens the page in a headless browser, reads the *visible* text (plus
any JobPosting JSON-LD), and asks your chosen AI provider for a structured posting. The reply is
validated against a schema and retried once if malformed; it is never trusted blindly.

The browser is whatever you already have: Playwright's Chromium if installed, otherwise Google
Chrome, otherwise Microsoft Edge. Nothing needs downloading on most machines. To get Playwright's
own Chromium instead, run `openapply browser install`.

Job pages are treated as hostile input:

- Page text is sent as clearly delimited **data**, separated from the instructions, and the AI
  is run with no tools, so a page cannot make it read files or contact anyone.
- Text that looks like instructions to an AI ("ignore previous instructions...") triggers a
  warning. This is a heuristic signal only; the delimiting above is the real defence.
- Only `http(s)` URLs are opened. Private and local addresses (`localhost`, `10.x`, `192.168.x`,
  link-local) are refused, including on every redirect hop, unless you pass `--allow-local`.
  Hostnames are not resolved, so a public name that points at a private address is not caught.
- The apply link the AI reports is untrusted too: it must pass the same URL rules (no private
  addresses, no embedded credentials, http(s) only) or the posting page is used instead.
- Page text, JSON-LD blocks and declared response sizes are capped inside the browser, so an
  oversized page cannot flood memory. Responses without a declared length are bounded by the
  navigation timeout only.
- Each run uses a fresh browser context: no cookies, no saved logins, no downloads. Your
  candidate profile is never included in the job-extraction prompt.

`--timeout` applies to each AI call, so a retry can take up to twice as long.

## How matching works

If you have a profile (`openapply setup`), `analyze` scores the job against it. The score is
**deterministic**: the same profile and posting always give the same result, and it explains
itself factor by factor.

| Factor | Weight | What is compared |
|---|---|---|
| Skills | 45% | Skills in the posting vs. your listed skills (full credit) and skills only mentioned in your experience text (60%). Required skills count most. |
| Experience | 20% | Years the posting asks for vs. years of dated work history (overlapping roles are merged, not double-counted). |
| Location | 15% | Remote/onsite policy and region vs. your location, remote preference and willingness to relocate. |
| Preferences | 20% | Target roles, employment type and salary (annualized, same currency only). |

- **Unknown is not zero.** A factor with no data (for example a posting that states no years of
  experience) is left out of the overall score and lowers the reported *confidence* instead.
  This covers salary (both sides must state the same currency, and the posting must say per
  year, month or hour: otherwise it is not compared) and location (an onsite or hybrid role
  with no stated place cannot be a mismatch or raise a relocation question).
- **Caps are explicit.** A recommendation can be capped below what the raw score suggests (no
  required skill covered, half the required skills missing, experience far below the ask,
  location that does not work, or a title unrelated to your target roles). Regulated roles such
  as physician, oncologist, nurse, pharmacist, dentist and lawyer are also checked for explicit
  qualification evidence in education, certifications and work history. A missing mandatory
  credential is a blocker and caps the displayed score; OpenApply says “not found in your
  profile” rather than claiming you do not hold it. When a cap changes the outcome, the reason
  is shown.
- **Eligibility is never guessed.** If a posting says it will not sponsor visas and your profile
  *explicitly* says you need sponsorship, that is a blocker. If you have not answered, you are
  shown a question under "Needs your confirmation" instead. The same applies to relocation,
  work authorization, security clearances and citizenship requirements. Page text cannot be
  authenticated, so a statement found only in the raw page text never forces a verdict: it
  becomes a concern. A blocker needs the statement in the extracted posting too, and the
  matching sentence is always quoted so you can check it yourself.
- **The AI only adds notes.** Unless you pass `--no-ai`, one extra AI call writes qualitative
  strengths, gaps and concerns. They are labelled advisory and can never change the score or
  the recommendation, so a hostile posting cannot talk its way into a "strong match". Only the
  career-relevant parts of your profile are sent: never your name, contact details, location,
  links, eligibility answers or salary expectations.
- Skills are matched against a built-in vocabulary of common technologies. A skill outside it
  can be matched (if you list it) but cannot be flagged as *missing*; the AI notes may mention it.

## Applying

`openapply apply <url>` opens the application form in a visible browser window, fills in what
your profile *explicitly* knows, optionally has your AI write the free-text answers, and then
shows you a preview. **Nothing is sent unless you review it and type a confirmation.**

```
[R]eview in browser  [E]dit values  [S]ubmit  [Q]uit
```

What it will and will not do:

- **Identity and contact details** (name, email, phone, city, links, current company, your
  stored résumé) are filled from the profile. A value in the profile always wins over a
  default the page pre-selected.
- **Eligibility questions** (visa sponsorship, work authorization, relocation) are answered only
  from an explicit profile answer, and respect the question's direction ("will you *require*
  sponsorship" versus "can you work *without* it"). A "no" is never inferred from a missing
  "yes": a country absent from your list of authorized countries is asked, not answered.
- **Never answered or ticked for you:** gender and other demographic questions, criminal
  history, security clearance, government or legal restrictions, and every consent or
  acknowledgement checkbox. A required consent blocks submitting until you tick it yourself.
  Anything sensitive that the *page already set* (a pre-ticked marketing box, a pre-selected
  eligibility answer) also blocks submitting until you decide: keep it knowingly, or change it.
  A pre-ticked box is never sent just because the page ticked it.
- **Free-text answers** are written by the AI from your profile and the posting. They are
  checked before they reach the form: no placeholders like `[Company]`, no links or email
  addresses that are not yours or the posting's, and the field's length limit is respected. If
  the AI cannot produce a usable answer, the question goes to you. Written answers are marked
  *Generated*; read them before you submit.
- **Fields are classified by rules, not by the AI.** The AI is consulted only for fields no rule
  recognised, may choose only from a closed list that excludes every sensitive category, and
  its guesses are flagged in the preview. Questions about other people ("Reference email",
  "Emergency contact phone") are never filled with your details.
- **The page cannot phone home with your details.** Page scripts can read whatever gets filled
  in and send it anywhere with a plain GET (an image beacon, a `fetch`, a script tag), long before
  you have approved anything. So from the moment the form is read until the session ends, the
  page may only talk to its own site (plus the narrow set of CAPTCHA providers forms need:
  reCAPTCHA, hCaptcha, Cloudflare Turnstile). Everything else is blocked whatever the method,
  redirects and WebSockets included, and the preview lists any site the page tried to contact.
  CAPTCHA providers are trusted with their own traffic, not with your details: a request to one
  whose URL or body contains a value OpenApply entered is blocked and reported. Two honest
  limits: values under 4 characters (a short first name) are not used to recognise requests,
  because they would false-positive on random CAPTCHA tokens, and data a page deliberately
  encodes or hashes before sending cannot be recognised, so those few providers remain a narrow
  channel for a determined page.
  The form's own declared destination is *not* trusted here: the page chose it, so it only
  becomes reachable when you type its name to confirm the submit. The cost: while your details
  are filled in, links to other sites (a privacy policy on another domain, say) will not load.
- **Hidden fields are left alone.** Honeypot fields (invisible to people), password fields, and
  search and newsletter boxes are skipped.
- **Submitting needs all of:** no unresolved required item, a typed confirmation, and, if the
  form posts to a *different site* than the page you are on, typing that site's name instead.
  The preview always shows where the form submits to, and that is the destination of the submit
  *button* (a button's own `formaction` overrides the form's). The destination is enforced where
  the data leaves, not just checked beforehand: page scripts run between your click and the
  browser's own post, so during the submit any POST to a site other than the one you confirmed
  is aborted before it is sent, and so is a 307/308 redirect that would carry the application
  to another site. If the form's own submission was refused, the submit fails and says which
  site the page tried to use, even if a stray same-site request got through; it only counts as
  sent when the form's own request, carrying your data, reached the confirmed site. Unrelated
  requests to other sites (an analytics ping, say) are blocked and reported while the
  application itself still goes. OpenApply cannot verify that an employer received an
  application; it shows you what the page said afterwards.

The browser session is fresh and ephemeral (no cookies or saved logins). If a site needs you to
sign in, do it in the visible window yourself; OpenApply never reads or stores passwords.

## Known limitations and follow-ups

- **Forms inside iframes and multi-step forms are not supported yet.** If no fields are found
  but the page has embedded frames, you are told. Greenhouse- and Lever-specific handling is the
  next milestone.
- **Outlier availability is location-dependent.** Its public opportunity page may return a
  location restriction instead of listings. OpenApply reports an empty discovery result and
  does not try to bypass the platform restriction.
- **Redirects are followed by OpenApply, not by the browser.** Chromium only lets us see the
  first request of a redirect chain, so handing a redirect back would let the rest of the chain
  run unobserved (it could end at an internal address). Each hop is checked against the URL
  policy and the page is served the final response. Consequences: a redirected page keeps the
  address you asked for (a `<base>` tag is added so its relative links still resolve), and job
  ids and platform detection use the address it ended up at.
- **Files can only be uploaded through OpenApply.** Chromium hides uploaded file *contents* from
  request interception, so for the submit OpenApply restores them from the files it attached
  itself. A file you attach by hand in the browser window cannot be vouched for, so the submit
  is refused with a message to use the Edit option instead. Nothing is sent in that case.
- **WebSockets opened by a page do not work** (they are blocked for forbidden addresses, and
  Chromium refuses the handshake outright once requests are being routed). Application forms
  do not need them.
- **Script-driven forms that post to a different site than the one shown are blocked,** not
  allowed. Some application sites submit through an API on another host; for those the submit
  will fail with the host named. Site-specific handling (Greenhouse, Lever) is the next
  milestone and is where known API hosts can be recognised safely.

- **Responses without a declared length (chunked) are bounded only by the navigation timeout.**
  Page text and JSON-LD are capped inside the browser, and responses that declare a length over
  10 MB are refused, but a server that streams without a `Content-Length` can still send a large
  body. Closing this needs byte-limited streaming and is tracked as a browser-hardening
  follow-up.
- A page can only be believed about itself. If hostile text on a page makes it into the extracted
  posting, it is treated as what the posting says; the quoted evidence is how you spot that. It
  can only mislead the advice you read (the tool takes no external action), never act for you.
- Hostnames are not resolved when checking URLs, so a public name that points at a private
  address is not caught.
- LinkedIn and Indeed URLs are recognised but not specially handled; login walls usually come
  back as "not a job posting".
- Gemini and GitHub Copilot are detected but cannot generate yet.
- Skill matching uses a modest built-in vocabulary (precision over recall).

## Providers

OpenApply invokes supported locally installed AI tools as subprocesses and reads their normal
output. It never reads, copies or reuses their credentials.

| Provider | How it is used | Status |
|---|---|---|
| Codex CLI | `codex exec` (read-only sandbox, ephemeral, prompt on stdin) | working |
| Claude Code | `claude -p --output-format json` (all tools disabled) | working |
| Ollama | local HTTP API, default `http://localhost:11434` | working |
| Gemini CLI | detected only | generation planned |
| GitHub Copilot CLI | detected only (optional) | generation planned |

Detection looks for the executable on `PATH`, reads its version, and asks the tool's own
login/status command whether it is signed in. Only a yes/no is kept.

Usage remains subject to the terms, quotas and limits of each provider. OpenApply makes no
guarantee of unlimited usage, provider-terms compliance, or job-board compatibility.

## Development

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

Tests mock all subprocess and HTTP calls, so no paid provider is needed.

## Design notes

For the proposed interview, job discovery, personal conversation app, Telegram reports and
resumable VPS worker, see the [current-state assessment and technical implementation handoff](docs/personal-agent-implementation-handoff.md).
For a concise record of what is implemented and the exact next milestone, see the
[personal-agent progress ledger](docs/personal-agent-progress.md). The handoff records the full
design; the progress ledger is the live implementation status.
To validate this checkpoint after cloning onto another computer, follow the
[other-PC Chrome test](docs/pc-test-checkpoint.md).

- **Prompts go over stdin**, never on the command line, so they don't show up in process lists.
- **Providers get no tools.** Job pages are untrusted, so provider calls run in an empty
  temporary directory with read-only or tool-less settings.
- **No automatic fallback in v1.** Errors are typed (`ProviderRateLimited`,
  `ProviderAuthenticationRequired`, ...) so a fallback policy can be added to the registry later.
  Rate limits are only reported when the output clearly says so.

## License

MIT. It is short, permissive and widely understood, which suits a tool people may embed or fork.

## Responsible use

OpenApply helps you apply to jobs you actually want, with answers you have read. It is not for
mass-submitting applications. Respect each site's terms and automation rules, and remember that
you are responsible for everything sent in your name. See [CONTRIBUTING.md](CONTRIBUTING.md) to
help, and [SECURITY.md](SECURITY.md) to report a vulnerability.
