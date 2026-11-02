# Task H — Hosted wait timing instrumentation

## Scope

- Control base: `b77ed30a9ce3898db984698e6ee0d99aaf11b195`.
- Independent worktree: `task-h-wait-control-20261102`.
- `hosted.py` wait-loop instrumentation, its CPU/CLI regression tests, and this report are changed.
- No wait protocol, poll interval, timeout, authority, artifact, or receipt behavior is changed.

## Log schema

Each measured operation emits one `INFO` record using the fixed schema:

Load/poll/sleep records:

`hosted_wait_timing stage=<reader.load|waiter.poll|sleep> elapsed_ms=<integer> run_id=<integer> sequence=<integer> outcome=<ok|error>`

Final wait-exit record:

`hosted_wait_timing stage=wait.final elapsed_ms=<integer> run_id=<integer> outcome=<PASS|FAIL|STALE|PENDING|ERROR> reason=<terminal|timeout|hosted_error|invalid_outcome|error>`

- `stage` is one of four source literals, never user-derived.
- `elapsed_ms` uses `time.monotonic()`; it measures operation wall duration, including time spent in the call.
- `run_id` is the validated numeric GitHub run ID.
- `sequence` is a local integer poll counter (0 for pre-load and pre-first-poll sleep; increments for each poll).
- The dedicated `lite_actions.hosted_wait_timing` logger explicitly sets its own INFO level, installs one stderr handler and disables propagation. It does not configure the root logger or enable third-party INFO output. This is required because production starts `runtime.py` with `python3 -I -B` and does not load logging configuration.
- The log does not include request/response objects, exception text, request paths, URLs, query strings, artifact IDs/URLs, env vars, headers, credentials, signatures, tokens, Plan contents, or PASS/result payloads.
- `wait.final` records only fixed outcome values (`PASS`, `FAIL`, `STALE`, `PENDING`, `ERROR`) and a fixed reason class (`terminal`, `timeout`, `hosted_error`, `invalid_outcome`, `error`).
- `finally` emits duration for both successful and failing operations without serializing exception details.

## Behavior invariants

- Default poll interval remains 15 seconds; no faster polling or extra API calls are introduced.
- Existing global and Plan deadlines are checked exactly as before, including the post-poll deadline check.
- `reader.load` is measured only while the Plan is not yet loaded; `waiter.poll` is measured for each existing poll; sleep duration is measured around the existing bounded sleep.
- No positive result or authority is cached by instrumentation.

## Tests

`test_wait_timing_logs_fixed_safe_schema_and_uses_monotonic_clock` uses an injected monotonic clock and asserts exact stage order, numeric elapsed time/run/sequence schema, absence of URL/query/secret/response/auth strings, and unchanged 15-second polling. `test_real_runtime_cjs_python_isolated_cli_emits_safe_wait_timing` creates clean synthetic control/SDK checkouts and runs the actual Node `runtime.cjs` adapter, which launches the real `python3 -I -B runtime.py wait` entrypoint in a subprocess; it verifies timing lines are visible on stderr and synthetic credentials are absent. Existing tests continue to cover slow load, slow poll, global deadline, Plan deadline, terminal FAIL/STALE, and absent artifact timeout.

## Current historical await window

The supplied OS7 timeline places the Hosted `test_result=PASS` output at 14:44:19.6405163Z and the reported `wait-gitcode` end at 14:44:31, a remaining interval of approximately 11.36 seconds. Because the PASS line is printed by `main()` only after `wait()` returns, and `wait()` returns only after `waiter.poll()` produces a terminal result, the terminal waiter validation had completed before that line. The exact instant inside the final poll when the fresh authority proof was consumed remains unknown. The 11.36-second interval is post-validation output/step completion, not measured polling delay. New instrumentation distinguishes load, poll, sleep and final wait-return duration on the next PM-authorized integrated run.


Final pre-commit validation: Ruff 0.14.14 format/check passed, compileall passed, and the complete GitCode Hosted unittest suite passed **91 tests**.
