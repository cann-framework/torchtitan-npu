# Upstream integration baseline (2026-11-02)

Official GitCode master: `02819e8fd6035f5577fe66a7e3694f64230cbc14`.
Personal candidate: `f120e8219727cfa9f427f181c7402119dec7000e`.
Merge base: `c6fb6b956249028a59dfb0cc6f55a83ad44fae3a`; official has 125 subsequent commits, candidate 11.

The local review baseline merged both histories as `9ebacc2d356b98c0c867e2bbbc74e05d37f134fe`. The publication candidate is a new single commit on official master `02819e8fd6035f5577fe66a7e3694f64230cbc14`, using the user-confirmed author identity; original branches and PR records remain intact. The reviewed tree preserves the official 125-commit content rather than replacing it with the old personal baseline. Eight original add/add conflicts were resolved deliberately:
- Keep upstream `prepare_request.py`, `wait_result.py`, `lite_actions_common.yml`, and native Dispatcher-Smoke unchanged. Existing A3/A5 callers continue using the schema-2 `pipeline` interface and existing repository policy.
- Add candidate schema-3 producer/waiter/common as `prepare_request_v3.py`, `wait_result_v3.py`, `lite_actions_common_v3.yml`. New Nightly references that versioned interface. Do not silently widen either repository allowlist.
- Keep upstream A3 8P/16P recipes and configured toolkit paths; candidate's different installation path has no validation here.
- Retain additive candidate smoke execution and inspection metadata in model-owned runner/entrypoint, including dispatcher port lease forwarding. Preserve upstream `verify_ema_checkpoint` dataclass extension.

The candidate schema-3 smoke case catalog remains available but does not replace the existing native Dispatcher-Smoke workflow. Schema-4 GitCode runtime jobs are layered separately under the original top-level Smoke/Nightly paths after review.

Tests are CPU contracts only. The port-forwarding test substitutes the training executor rather than importing its torch/EMA dependencies; this verifies argument/environment propagation, not training. No NPU execution, real GitCode Action identity, live authority transport, publication, or merge-gate acceptance is claimed.
