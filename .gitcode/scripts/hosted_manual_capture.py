"""Appended to reviewed common code inside the dedicated capture-only step."""

import base64
import hashlib
import os
import re
import secrets
import time

from hosted_manual_common import (
    ALLOWED_ACTOR,
    ALLOWED_CASES,
    API,
    GH_ID,
    GH_WORKFLOW,
    PREFIX,
    canonical,
    context,
    emit_failure,
    release,
    require,
    run_identity,
    snapshot,
)


def capture(environment, api, *, clock=time.time, nonce_factory=lambda: secrets.token_hex(24)):
    run_id, control = context(environment)
    case = environment.get("TEST_CASE")
    require(case in ALLOWED_CASES, "ONLY_OS_OR_SINGLE_NODE_8P")
    number = environment.get("PR_NUMBER", "")
    require(isinstance(number, str) and re.fullmatch(r"[1-9][0-9]{0,9}", number), "INVALID_PR_NUMBER")
    run_identity(api, run_id, control)
    release(api, control)
    pr = snapshot(api, int(number))
    require(pr["base_sha"] == control, "CAPTURE_BASE_NOT_CONTROL")
    at = int(clock())
    nonce = nonce_factory()
    require(bool(re.fullmatch(r"[A-Za-z0-9_-]{16,100}", nonce)), "INVALID_NONCE")
    intent = {
        "schema": 1,
        "source_provider": "gitcode",
        "pr": pr,
        "control_repo_id": GH_ID,
        "control_sha": control,
        "workflow_path": GH_WORKFLOW,
        "pipeline": "smoke",
        "selectors": [{"test_id": case}],
        "bridge": {
            "bridge_event_id": "manual:" + run_id,
            "bridge_action_run": run_id,
            "bridge_attempt": 1,
            "actor": ALLOWED_ACTOR,
            "bridge_nonce": nonce,
            "source_snapshot_id": "manual-capture:" + nonce,
            "dispatch_intent_id": "manual-intent:" + nonce,
            "rerun_intent_id": "initial",
            "event_created_at": at,
        },
        "deadline": at + 900,
    }
    run_identity(api, run_id, control)
    payload = canonical({"schema": 1, "kind": "manual-current-observation", "captured_at": at, "intent": intent})
    require(len(payload) <= 24 * 1024, "CAPTURE_TOO_LARGE")
    return PREFIX + hashlib.sha256(payload).hexdigest() + " " + base64.b64encode(payload).decode()


def main():
    try:
        print(capture(os.environ, API(os.environ)))
        return 0
    except Exception as error:
        return emit_failure(error)


if __name__ == "__main__":
    raise SystemExit(main())
