# OpenApply agent CLI refinement test plan

## Planned coverage

- `tests/cli/test_cli.py`: tool-catalog JSON and command discovery.
- `tests/cli/test_start.py`: combined desk/worker startup and coordinated shutdown.
- `tests/discovery/test_service.py`: Mercor detail-link filtering and generic collection filtering.
- `tests/jobs/test_matcher.py`: mandatory professional credentials and unrelated-role score caps.
- `tests/worker/test_queue.py`: expected extraction failure, unexpected exception cleanup, and interruption requeue.

## Workflow scenarios

1. An agent calls `openapply tools --json`, receives valid structured commands, and can choose the read-only or mutating action deliberately.
2. A person calls `openapply start`; the web desk runs in the foreground, the durable worker runs alongside it, and both stop together.
3. Discovery sees Mercor navigation and detail links; only canonical single-job URLs are queued.
4. A software candidate is compared with physician and oncologist roles; missing mandatory qualifications are explicit blockers and cannot receive a high score.
5. A worker encounters a malformed job page or an unexpected implementation error; the task does not remain leased in `running` state.

## Expected result

All focused tests and the existing suite pass. Ruff, mypy, JavaScript syntax checks, and `git diff --check` remain clean. Test results will be appended after validation.

## Validation results

- Focused workflow suite: `65 passed in 5.62s`.
- Full project suite: `787 passed, 2 skipped, 2 warnings in 215.80s`.
- `ruff check src tests`: passed.
- `mypy src tests`: passed with strict settings across 179 source files.
- `skills/cli-anything-openapply`: passed the skill-creator quick validator.
- Installed entry-point checks: `openapply tools --json`, `openapply --help`, and `openapply worker --help` produced the expected command surfaces.

The two skipped tests are existing POSIX-permission checks on Windows. The two warnings are existing FastAPI/Starlette deprecations.
