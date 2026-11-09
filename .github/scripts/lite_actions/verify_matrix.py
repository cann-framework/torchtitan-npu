#!/usr/bin/env python3
# Copyright (c) 2026 Huawei Technologies Co., Ltd. All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Require a completed GitHub Matrix job for every case in the pinned plan."""

import json
import os
from pathlib import Path

from tests.integration_tests.tools.lite_actions.contract import parse_plan


def verify(plan_path, result_dir, *, run_id, attempt, sha, plan_digest):
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    cases = parse_plan(
        plan,
        {"id": run_id, "run_attempt": attempt, "head_sha": sha},
        "lite_actions_dispatcher_smoke.yml",
    )
    if plan["plan_digest"] != plan_digest:
        raise ValueError("matrix plan digest mismatch")
    expected = {f"lite-ci-matrix-{attempt}-{case['case_id']}": case["case_id"] for case in cases}
    result_dir = Path(result_dir)
    found = {path.name: path for path in result_dir.iterdir()} if result_dir.is_dir() else {}
    if set(found) != set(expected):
        raise ValueError("matrix completion set does not match plan")
    for name, case_id in expected.items():
        marker = found[name] / "case_id"
        if not marker.is_file() or marker.read_text(encoding="utf-8") != case_id + "\n":
            raise ValueError(f"invalid matrix completion for {case_id}")
    return len(cases)


def main():
    count = verify(
        ".ci-request/ci-request.json",
        ".ci-matrix-results",
        run_id=int(os.environ["GITHUB_RUN_ID"]),
        attempt=int(os.environ["GITHUB_RUN_ATTEMPT"]),
        sha=os.environ["CI_SOURCE_SHA"],
        plan_digest=os.environ["CI_PLAN_DIGEST"],
    )
    print(f"All {count} planned Matrix jobs completed.")


if __name__ == "__main__":
    main()
