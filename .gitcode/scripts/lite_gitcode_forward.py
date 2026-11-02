#!/usr/bin/env python3
"""Trusted GitCode Bridge entrypoint: observe, dispatch, wait and publish.

A PR event is only a wakeup. The authenticated authority adapter must bind the
current snapshot to its durable ledger; no event ID is invented as platform
revision proof. This runner shares that ledger with Blue.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / ".github/scripts/lite_actions/gitcode"
sys.path.insert(0, str(CONTROL))
import contextlib

from runtime import read_object, verify_checkout, write_private


def watch(runtime, intent, *, clock=time.time, sleep=time.sleep):
    from ci_core.gitcode_waiter import GitCodeWaiter

    runtime.authority.verify_event(intent)
    binding = runtime.bridge.dispatch(intent, now=int(clock()))
    run_id = binding["run_id"]
    waiter = None
    try:
        while int(clock()) < intent["deadline"]:
            runtime.authority.verify_event(intent)
            runtime.bridge.validate_intent(intent, now=int(clock()))
            if waiter is None:
                verified = runtime.reader.load_results(run_id, now=int(clock()))
                if verified is not None:
                    waiter = GitCodeWaiter(
                        verified,
                        runtime.reader.channel,
                        current=runtime.current,
                        resolve_canonical=runtime.resolve_canonical,
                    )
            if waiter is not None:
                outcome = waiter.poll(now=int(clock()))
                if int(clock()) >= intent["deadline"]:
                    raise ValueError("BRIDGE_DEADLINE_EXCEEDED")
                if outcome["test_result"] != "PENDING":
                    # Publication must check exact publication authority again.
                    # Missing platform capability raises rather than fake-green.
                    effective = waiter.follower or waiter
                    publication = {k: v for k, v in outcome.items() if k != "followed_from_request_id"}
                    runtime.publish_result(effective.plan, publication)
                    return {**binding, **outcome}
            remaining = intent["deadline"] - int(clock())
            if remaining > 0:
                sleep(min(15, remaining))
        raise ValueError("BRIDGE_DEADLINE_EXCEEDED")
    except BaseException:
        # Cancels only this dispatch, never the canonical run of a duplicate.
        with contextlib.suppress(Exception):
            runtime.cancel_run(run_id)
        raise


def select_pipeline_cases(workflow_path, case_ids=None):
    """Select only trusted catalog entries; omission alone means the full pipeline."""
    pipeline = {
        ".github/workflows/lite_actions_dispatcher_smoke.yml": "smoke",
        ".github/workflows/nightly_fulltests.yml": "nightly",
    }.get(workflow_path)
    if pipeline is None:
        raise ValueError("UNKNOWN_BRIDGE_WORKFLOW")
    catalog = read_object((CONTROL / "catalog.json").read_text())
    allowed = catalog["pipelines"][pipeline]
    names = allowed if case_ids is None else case_ids
    if not isinstance(names, (list, tuple)) or not 1 <= len(names) <= len(allowed):
        raise ValueError("INVALID_BRIDGE_CASE_SELECTION")
    seen = set()
    selectors = []
    for name in names:
        if not isinstance(name, str) or name not in allowed or name not in catalog["cases"] or name in seen:
            raise ValueError("UNKNOWN_DUPLICATE_OR_CROSS_PIPELINE_CASE")
        seen.add(name)
        selectors.append({"test_id": name})
    return pipeline, selectors


def execute(*, environment, number, expected_head="", case_ids=None, factory=None, clock=time.time, sleep=time.sleep):
    trigger = environment.get("LITE_ACTIONS_GITCODE_EVENT_NAME", "manual")
    if trigger not in {"manual", "workflow_dispatch"}:
        # A current H/B match is not proof of the original Action snapshot:
        # observed A -> B -> A must never green a delayed A Action.
        raise ValueError("REAL_ACTION_IDENTITY_ADAPTER_REQUIRED")
    config = read_object(environment["LITE_ACTIONS_RELEASE_CONFIG"])
    if factory is None:
        verify_checkout(ROOT, environment["LITE_ACTIONS_BRIDGE_SHA"], allow_control_dependencies=True)
        sdk = verify_checkout(environment["LITE_ACTIONS_SDK_ROOT"], config["sdk_sha"])
        sys.path.insert(0, str(sdk))
        from ci_core.gitcode_assembly import assemble_bridge

        factory = assemble_bridge
    state = Path(environment["LITE_ACTIONS_BRIDGE_STATE"])
    if not state.is_absolute() or state.resolve(strict=True) != state:
        raise ValueError("DURABLE_BRIDGE_STATE_REQUIRED")
    info = state.stat()
    if info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError("OWNER_ONLY_BRIDGE_STATE_REQUIRED")
    if type(number) is not int or number <= 0:
        raise ValueError("INVALID_PR_NUMBER")
    if expected_head and not re.fullmatch("[0-9a-f]{40}", expected_head):
        raise ValueError("INVALID_EVENT_HEAD")
    with tempfile.TemporaryDirectory(prefix="bridge-control-", dir=state) as scratch:
        temporary = Path(scratch)
        config_path = temporary / "release.json"
        write_private(config_path, json.dumps(config))
        supplied = dict(environment)
        key = supplied.pop("LITE_ACTIONS_CHECKS_KEY", "")
        if key:
            key_path = temporary / "checks-app.pem"
            write_private(key_path, key)
            supplied["LITE_ACTIONS_CHECKS_KEY_FILE"] = str(key_path)

        def no_upload(*args):
            raise ValueError("BRIDGE_CANNOT_UPLOAD_HOSTED_ARTIFACTS")

        runtime = factory(config_path, environment=supplied, upload=no_upload, state_directory=state)
        pipeline, selectors = select_pipeline_cases(runtime.policy.workflow_path, case_ids)
        intent = runtime.authority.create_intent(number, selectors, pipeline=pipeline, trigger_kind="periodic")
        if expected_head and intent["pr"]["head_sha"] != expected_head:
            raise ValueError("LATE_EVENT_HEAD_CHANGED_NO_DISPATCH")
        return watch(runtime, intent, clock=clock, sleep=sleep)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--expected-head", default="")
    parser.add_argument(
        "--case",
        dest="case_ids",
        action="append",
        metavar="TEST_ID",
        help="Select a trusted case in this release's pipeline; repeat to select multiple. Default: full pipeline.",
    )
    args = parser.parse_args(argv)
    try:
        result = execute(
            environment=os.environ, number=args.pr, expected_head=args.expected_head, case_ids=args.case_ids
        )
        print(json.dumps(result, sort_keys=True))
        return 0 if result["test_result"] == "PASS" else 1
    except Exception as error:
        print("GitCode Bridge failed closed: " + type(error).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
