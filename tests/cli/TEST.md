# OpenApply agent CLI refinement test plan

## Planned coverage

- `tests/cli/test_cli.py`: tool-catalog JSON and command discovery.
- `tests/cli/test_start.py`: combined desk/worker startup and coordinated shutdown.
- `tests/discovery/test_service.py`: Mercor detail-link filtering and generic collection filtering.
- `tests/discovery/test_platform_sources.py`: supported-platform metadata, page discovery,
  official JSON API parsing, RSS parsing, query filtering, and malformed-feed errors.
- `tests/jobs/test_models_prompt.py`: deterministic recognition of every supported source host.
- `tests/cli/test_cli.py`: platform-list and platform-discovery JSON commands.
- `tests/web/test_app.py`: platform discovery is selectable from the local desk.
- `tests/jobs/test_matcher.py`: mandatory professional credentials and unrelated-role score caps.
- `tests/worker/test_queue.py`: expected extraction failure, unexpected exception cleanup, and interruption requeue.

## Workflow scenarios

1. An agent calls `openapply tools --json`, receives valid structured commands, and can choose the read-only or mutating action deliberately.
2. A person calls `openapply start`; the web desk runs in the foreground, the durable worker runs alongside it, and both stop together.
3. Discovery sees Mercor navigation and detail links; only canonical single-job URLs are queued.
4. A software candidate is compared with physician and oncologist roles; missing mandatory qualifications are explicit blockers and cannot receive a high score.
5. A worker encounters a malformed job page or an unexpected implementation error; the task does not remain leased in `running` state.
6. An agent lists supported platforms, queues a targeted role search, and the worker turns
   official page/API/feed results into bounded analysis tasks without submitting anything.
7. Both Mercor URL forms (`/jobs/list_.../<slug>` and `/explore?listingId=...`) are
   recognized as one-job URLs, while a plain `/explore` navigation page is rejected.

## Expected result

All focused tests and the existing suite pass. Ruff, mypy, JavaScript syntax checks, and `git diff --check` remain clean. Test results will be appended after validation.

## Validation results

- Focused platform, worker, CLI and web suite: `75 passed, 2 warnings in 14.15s`.
- Real-browser fetcher and discovery suite: `23 passed in 40.97s`.
- Full project suite: `813 passed, 2 skipped, 2 warnings in 304.81s`.
- `ruff check src tests`: passed.
- `mypy src tests`: passed with strict settings across 182 source files.
- `skills/cli-anything-openapply`: passed the skill-creator quick validator.
- Installed entry-point checks: `openapply worker platforms --json` and
  `openapply worker discover-platform --help` produced the expected command surfaces.
- Live discovery checks returned bounded URLs from Mercor, DataAnnotation, Alignerr, micro1,
  Himalayas, Remotive, Wellfound and We Work Remotely. Outlier's public opportunity page
  returned a location restriction on this machine, so no Outlier listings were available here.

The two skipped tests are existing POSIX-permission checks on Windows. The two warnings are existing FastAPI/Starlette deprecations.
