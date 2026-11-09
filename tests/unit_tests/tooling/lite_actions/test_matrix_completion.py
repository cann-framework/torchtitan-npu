# Copyright (c) 2026 Huawei Technologies Co., Ltd. All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""CPU checks for fail-closed Matrix completion evidence."""

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.integration_tests.nightly_all_models_test import smoke_catalog
from tests.integration_tests.tools.lite_actions.contract import create_plan

ROOT = Path(__file__).resolve().parents[4]


def _load(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def prepared_run(tmp_path):
    producer = _load("matrix_producer", ".github/scripts/lite_actions/prepare_request.py")
    verifier = _load("matrix_verifier", ".github/scripts/lite_actions/verify_matrix.py")
    defaults = dict(worker="blue", target_pool="a3_shared_blue", docker="cann_master_torchtitan_master_xyj")
    cases = producer.expand_cases([{"suite": "fifo_smoke"}], defaults)
    plan = create_plan(
        cases, run_id=7, attempt=2, sha="a" * 40, workflow="lite_actions_dispatcher_smoke.yml", defaults=defaults
    )
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    for case in cases:
        marker_dir = result_dir / f"lite-ci-matrix-2-{case['case_id']}"
        marker_dir.mkdir()
        (marker_dir / "case_id").write_text(case["case_id"] + "\n")
    args = dict(run_id=7, attempt=2, sha="a" * 40, plan_digest=plan["plan_digest"])
    return verifier, plan_path, result_dir, cases, args


def test_all_planned_cases_with_distinct_ids_succeed(prepared_run):
    verifier, plan_path, results, cases, args = prepared_run
    declared = smoke_catalog.load_smoke_catalog()["profiles"]["fifo_smoke"]
    assert declared[:3] == ["dispatch_smoke_8p", "dispatch_smoke_16p", "dispatch_smoke_8p"]
    assert [case["test_id"] for case in cases] == declared
    assert [case["case_id"] for case in cases] == [f"case_{index:03d}_{name}" for index, name in enumerate(declared)]
    assert verifier.verify(plan_path, results, **args) == len(declared)


def test_missing_matrix_job_cannot_report_success(prepared_run):
    verifier, plan_path, results, cases, args = prepared_run
    for case in cases:
        missing = results / f"lite-ci-matrix-2-{case['case_id']}"
        (missing / "case_id").unlink()
        missing.rmdir()
        with pytest.raises(ValueError, match="completion set"):
            verifier.verify(plan_path, results, **args)
        missing.mkdir()
        (missing / "case_id").write_text(case["case_id"] + "\n")


def test_unexpected_or_old_attempt_artifact_is_rejected(prepared_run):
    verifier, plan_path, results, cases, args = prepared_run
    (results / f"lite-ci-matrix-1-{cases[0]['case_id']}").mkdir()
    with pytest.raises(ValueError, match="completion set"):
        verifier.verify(plan_path, results, **args)


def test_completion_cannot_impersonate_another_case(prepared_run):
    verifier, plan_path, results, cases, args = prepared_run
    marker = results / f"lite-ci-matrix-2-{cases[0]['case_id']}" / "case_id"
    marker.write_text(cases[2]["case_id"] + "\n")
    with pytest.raises(ValueError, match="invalid matrix completion"):
        verifier.verify(plan_path, results, **args)


def test_completion_plan_digest_and_identity_must_match(prepared_run):
    verifier, plan_path, results, _, args = prepared_run
    for changed in ({"plan_digest": "b" * 64}, {"sha": "b" * 40}, {"attempt": 1}):
        with pytest.raises(ValueError):
            verifier.verify(plan_path, results, **{**args, **changed})


@pytest.mark.parametrize(
    "prepare,matrix,event,expected",
    [
        ("success", "success", "workflow_dispatch", 0),
        ("success", "abandoned", "workflow_dispatch", 1),
        ("success", "failure", "workflow_dispatch", 1),
        ("failure", "success", "workflow_dispatch", 1),
        ("success", "skipped", "pull_request", 0),
        ("failure", "skipped", "pull_request", 1),
    ],
)
def test_workflow_result_gate_fails_closed_for_abandoned_matrix(prepare, matrix, event, expected):
    workflow = yaml.load(
        (ROOT / ".github/workflows/lite_actions_dispatcher_smoke.yml").read_text(), Loader=yaml.BaseLoader
    )
    gate = workflow["jobs"]["result"]["steps"][0]["run"]
    env = {**os.environ, "PREPARE_RESULT": prepare, "MATRIX_RESULT": matrix, "EVENT_NAME": event}
    result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", gate], env=env, check=False)
    assert result.returncode == expected


def test_completion_evidence_follows_worker_success_and_is_checked_against_plan():
    workflow = yaml.load(
        (ROOT / ".github/workflows/lite_actions_dispatcher_smoke.yml").read_text(), Loader=yaml.BaseLoader
    )
    case_steps = workflow["jobs"]["case"]["steps"]
    wait_index = next(i for i, step in enumerate(case_steps) if step.get("name") == "Wait for assigned worker")
    upload_index = next(
        i for i, step in enumerate(case_steps) if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert wait_index < upload_index
    assert "${{ github.run_attempt }}-${{ matrix.case_id }}" in case_steps[upload_index]["with"]["name"]
    assert "if" not in case_steps[upload_index]
    result_steps = workflow["jobs"]["result"]["steps"]
    assert any("verify_matrix.py" in step.get("run", "") for step in result_steps)
