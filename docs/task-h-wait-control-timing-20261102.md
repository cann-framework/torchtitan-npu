# Task H — Hosted wait timing instrumentation

## Scope

- Control base: `b77ed30a9ce3898db984698e6ee0d99aaf11b195`.
- Independent worktree: `task-h-wait-control-20261102`.
- Only `hosted.py` wait-loop instrumentation and its CPU regression test are changed.
- No wait protocol, poll interval, timeout, authority, artifact, or receipt behavior is changed.

## Log schema

Each measured operation emits one `INFO` record using the fixed schema:

`hosted_wait_timing stage=<reader.load|waiter.poll|sleep> elapsed_ms=<integer> run_id=<integer> sequence=<integer>`

- `stage` is one of three source literals, never user-derived.
- `elapsed_ms` uses `time.monotonic()`; it measures operation wall duration, including time spent in the call.
- `run_id` is the validated numeric GitHub run ID.
- `sequence` is a local integer poll counter (0 for pre-load and pre-first-poll sleep; increments for each poll).
- The log does not include request/response objects, exception text, request paths, URLs, query strings, artifact IDs/URLs, env vars, headers, credentials, signatures, tokens, Plan contents, or PASS/result payloads.
- `finally` emits duration for both successful and failing operations without serializing exception details.

## Behavior invariants

- Default poll interval remains 15 seconds; no faster polling or extra API calls are introduced.
- Existing global and Plan deadlines are checked exactly as before, including the post-poll deadline check.
- `reader.load` is measured only while the Plan is not yet loaded; `waiter.poll` is measured for each existing poll; sleep duration is measured around the existing bounded sleep.
- No positive result or authority is cached by instrumentation.

## Tests

`test_wait_timing_logs_fixed_safe_schema_and_uses_monotonic_clock` uses an injected monotonic clock and asserts exact stage order, numeric elapsed time/run/sequence schema, absence of URL/query/secret/response/auth strings, and unchanged 15-second polling. Existing tests continue to cover slow load, slow poll, global deadline, Plan deadline, terminal FAIL/STALE, and absent artifact timeout.

## Current historical await window

The supplied OS7 timeline places the Hosted `test_result=PASS` line at 14:44:19.6405163Z and the reported `wait-gitcode` end at 14:44:31, a remaining interval of approximately 11.36 seconds. This is only a post-PASS log-to-job-end interval; it is not a measured `waiter.poll` or sleep duration. New instrumentation distinguishes load, poll and sleep on the next PM-authorized integrated run.
