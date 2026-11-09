# Copyright (c) 2026 Huawei Technologies Co., Ltd. All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""CPU checks for the shared smoke catalog and opt-in GitHub Matrix."""

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from tests.integration_tests.nightly_all_models_test import smoke_catalog
from tests.integration_tests.tools.lite_actions import entrypoint
from tests.integration_tests.tools.lite_actions.contract import create_plan, normalize

ROOT = Path(__file__).resolve().parents[4]
DEFAULTS = {
    "worker": "blue",
    "target_pool": "a3_shared_blue",
    "docker": "cann_master_torchtitan_master_xyj",
}


def _producer():
    spec = importlib.util.spec_from_file_location(
        "smoke_prepare", ROOT / ".github/scripts/lite_actions/prepare_request.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_explicit_github_profile_keeps_original_execution_shape():
    cases = _producer().expand_cases([{"suite": "github_smoke"}], DEFAULTS)
    assert [(c["test_id"], c["worker"], c["resources"]) for c in cases] == [
        ("worker_os_blue", "blue", {"nodes": 0, "devices_per_node": 0}),
        ("dispatch_smoke_8p", "blue", {"nodes": 1, "devices_per_node": 8}),
        ("dispatch_smoke_16p", "blue", {"nodes": 2, "devices_per_node": 8}),
    ]
    assert [c["docker"] for c in cases] == [None, DEFAULTS["docker"], DEFAULTS["docker"]]


@pytest.mark.parametrize(
    "selector,profile",
    [
        (None, "fifo_smoke"),
        ("", "fifo_smoke"),
        ('[{"suite":"github_smoke"}]', "github_smoke"),
    ],
)
def test_prepare_uses_catalog_default_or_explicit_profile(tmp_path, monkeypatch, selector, profile):
    data = smoke_catalog.load_smoke_catalog()
    assert data["default_profile"] == "fifo_smoke"
    monkeypatch.chdir(tmp_path)
    env = {
        "CI_WORKFLOW": "lite_actions_dispatcher_smoke.yml",
        "CI_SOURCE_SHA": "a" * 40,
        "GITHUB_REPOSITORY": "cann-framework/torchtitan-npu",
        "GITHUB_RUN_ID": "7",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_OUTPUT": str(tmp_path / "output"),
        **{"CI_DEFAULT_" + k.upper(): v for k, v in DEFAULTS.items()},
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    if selector is None:
        monkeypatch.delenv("CI_TEST_CASES", raising=False)
    else:
        monkeypatch.setenv("CI_TEST_CASES", selector)
    _producer().main()
    output = dict(line.split("=", 1) for line in (tmp_path / "output").read_text().splitlines())
    plan = json.loads((tmp_path / ".ci-request/ci-request.json").read_text())
    assert json.loads(output["matrix"])["include"] == plan["cases"]
    assert [case["test_id"] for case in plan["cases"]] == data["profiles"][profile]
    if profile == "fifo_smoke":
        assert [case["case_id"] for case in plan["cases"][:3]] == [
            "case_000_dispatch_smoke_8p",
            "case_001_dispatch_smoke_16p",
            "case_002_dispatch_smoke_8p",
        ]


def test_repeated_smoke_gets_distinct_cases_and_a_valid_plan():
    cases = _producer().expand_cases([{"suite": "fifo_smoke"}], DEFAULTS)
    declared = smoke_catalog.load_smoke_catalog()["profiles"]["fifo_smoke"]
    assert [c["test_id"] for c in cases] == declared
    assert [c["case_id"] for c in cases] == [f"case_{index:03d}_{name}" for index, name in enumerate(declared)]
    assert [c["case_id"] for c in cases[:3]] == [
        "case_000_dispatch_smoke_8p",
        "case_001_dispatch_smoke_16p",
        "case_002_dispatch_smoke_8p",
    ]
    assert [c["resources"]["nodes"] for c in cases[:3]] == [1, 2, 1]
    plan = create_plan(
        cases, run_id=7, attempt=1, sha="a" * 40, workflow="lite_actions_dispatcher_smoke.yml", defaults=DEFAULTS
    )
    assert plan["cases"] == cases


def test_repeated_smoke_results_cannot_be_swapped():
    spec = importlib.util.spec_from_file_location("smoke_waiter", ROOT / ".github/scripts/lite_actions/wait_result.py")
    waiter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(waiter)
    cases = _producer().expand_cases([{"suite": "fifo_smoke"}], DEFAULTS)
    first, last = cases[0], cases[2]
    common = dict(
        schema=3,
        repo="cann-framework/torchtitan-npu",
        workflow="lite_actions_dispatcher_smoke.yml",
        run_id=7,
        attempt=1,
        sha="a" * 40,
        plan_digest="b" * 64,
    )
    result = {
        **common,
        **{k: last[k] for k in ("case_id", "test_id", "worker", "target_pool", "docker", "resources")},
        "status": "PASS",
        "exit_code": 0,
        "cleanup_verified": True,
        "last_20_lines": [],
    }
    args = dict(
        run_id=7,
        attempt=1,
        sha="a" * 40,
        digest="b" * 64,
        item=first,
        workflow="lite_actions_dispatcher_smoke.yml",
        author="reporter",
    )
    marker = f"Lite-CI-V3 7:1:{first['case_id']}\n"
    comment = {"user": {"login": "reporter"}, "body": marker + json.dumps(result)}
    assert waiter.find_result([comment], **args) is None
    result["case_id"] = first["case_id"]
    comment["body"] = marker + json.dumps(result)
    assert waiter.find_result([comment], **args) == result


@pytest.mark.parametrize("test_id", ["worker_os_blue", "dsv4_flash_a3_8p_example"])
def test_duplicate_probe_and_training_still_rejected(test_id):
    with pytest.raises(ValueError, match="duplicate"):
        _producer().expand_cases([{"test_id": test_id}, {"test_id": test_id}], DEFAULTS)
    test = entrypoint.select(entrypoint.catalog(), test_id=test_id)[0]
    cases = [normalize(test, DEFAULTS, order) for order in range(2)]
    with pytest.raises(ValueError, match="duplicate"):
        create_plan(
            cases, run_id=7, attempt=1, sha="a" * 40, workflow="lite_actions_dispatcher_smoke.yml", defaults=DEFAULTS
        )


def test_new_manifest_case_expands_matrix_and_pinned_plan_without_workflow_changes(tmp_path, monkeypatch):
    data = smoke_catalog.load_smoke_catalog()
    declared = list(data["profiles"]["fifo_smoke"])
    test_id = "cpu_catalog_probe_4p"
    while any(case["test_id"] == test_id for case in data["cases"]):
        test_id += "_x"
    smoke_8p = next(case for case in data["cases"] if case["test_id"] == "dispatch_smoke_8p")
    case = dict(smoke_8p, test_id=test_id, label="4P smoke", devices_per_node=4)
    data["cases"].append(case)
    data["profiles"]["fifo_smoke"].append(case["test_id"])
    data["defaults"].update(target_pool="a3_shared_blue_trial", docker="example:trial")
    catalog_path = tmp_path / "smoke_cases.json"
    catalog_path.write_text(json.dumps(data))
    monkeypatch.setattr(smoke_catalog, "CATALOG_PATH", catalog_path)
    monkeypatch.chdir(tmp_path)
    workflow = ROOT / ".github/workflows/lite_actions_dispatcher_smoke.yml"
    original_workflow = workflow.read_bytes()
    env = {
        "CI_TEST_CASES": '[{"suite":"fifo_smoke"}]',
        "CI_WORKFLOW": "lite_actions_dispatcher_smoke.yml",
        "CI_SOURCE_SHA": "a" * 40,
        "GITHUB_REPOSITORY": "cann-framework/torchtitan-npu",
        "GITHUB_RUN_ID": "7",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_OUTPUT": str(tmp_path / "output"),
        **{"CI_DEFAULT_" + k.upper(): v for k, v in DEFAULTS.items()},
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    _producer().main()
    outputs = dict(line.split("=", 1) for line in (tmp_path / "output").read_text().splitlines())
    plan = json.loads((tmp_path / ".ci-request/ci-request.json").read_text())
    assert json.loads(outputs["matrix"])["include"] == plan["cases"]
    assert [c["case_id"] for c in plan["cases"][:3]] == [
        "case_000_dispatch_smoke_8p",
        "case_001_dispatch_smoke_16p",
        "case_002_dispatch_smoke_8p",
    ]
    assert [c["test_id"] for c in plan["cases"]] == [*declared, test_id]
    assert [c["case_id"] for c in plan["cases"]] == [
        f"case_{index:03d}_{name}" for index, name in enumerate([*declared, test_id])
    ]
    assert plan["cases"][-1]["resources"] == {"nodes": 1, "devices_per_node": 4}
    assert all(c["target_pool"] == "a3_shared_blue_trial" and c["docker"] == "example:trial" for c in plan["cases"])
    assert entrypoint.select(entrypoint.catalog(), test_id=test_id)[0].ngpu == 4
    assert workflow.read_bytes() == original_workflow


def test_duplicate_probe_in_manifest_is_rejected(tmp_path):
    data = smoke_catalog.load_smoke_catalog()
    data["profiles"]["fifo_smoke"] = ["worker_os_blue", "worker_os_blue"]
    manifest = tmp_path / "smoke_cases.json"
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="duplicate"):
        smoke_catalog.load_smoke_catalog(manifest)


def test_trial_uses_one_native_workflow_with_case_ids():
    workflow = yaml.load(
        (ROOT / ".github/workflows/lite_actions_dispatcher_smoke.yml").read_text(), Loader=yaml.BaseLoader
    )
    assert set(workflow["jobs"]) == {"prepare", "case", "result"}
    assert set(workflow["on"]["workflow_dispatch"]["inputs"]) == {"test_cases"}
    assert "refs/heads/ci/" in workflow["jobs"]["prepare"]["if"]
    assert workflow["jobs"]["case"]["name"] == "${{ matrix.case_id }}"
    assert workflow["jobs"]["case"]["needs"] == "prepare"
    assert workflow["jobs"]["result"]["needs"] == ["prepare", "case"]
