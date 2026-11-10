# Copyright (c) 2026 Huawei Technologies Co., Ltd. All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""CPU checks for the shared smoke catalog and opt-in GitHub Matrix."""

import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import pytest

from tests.integration_tests.nightly_all_models_test import smoke_catalog
from tests.integration_tests.tools.lite_actions import entrypoint
from tests.integration_tests.tools.lite_actions.contract import create_plan, normalize

pytestmark = pytest.mark.tooling

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


def test_smoke_profile_uses_model_defaults_and_expected_execution_shape():
    other_defaults = {"worker": "yellow", "target_pool": "other", "docker": "other:tag"}
    cases = _producer().expand_cases([{"suite": "dispatch_smoke"}], other_defaults)
    assert [(c["test_id"], c["worker"], c["resources"]) for c in cases] == [
        ("worker_os_blue", "blue", {"nodes": 0, "devices_per_node": 0}),
        ("dispatch_smoke_8p", "blue", {"nodes": 1, "devices_per_node": 8}),
        ("dispatch_smoke_16p", "blue", {"nodes": 2, "devices_per_node": 8}),
        ("dispatch_smoke_8p", "blue", {"nodes": 1, "devices_per_node": 8}),
    ]
    assert [c["docker"] for c in cases] == [None, DEFAULTS["docker"], DEFAULTS["docker"], DEFAULTS["docker"]]
    assert all(c["target_pool"] == DEFAULTS["target_pool"] for c in cases[1:])


@pytest.mark.parametrize(
    "selector,profile",
    [
        (None, "dispatch_smoke"),
        ("", "dispatch_smoke"),
        ('[{"suite":"dispatch_smoke"}]', "dispatch_smoke"),
    ],
)
def test_prepare_uses_catalog_default_or_explicit_profile(tmp_path, monkeypatch, selector, profile):
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
    assert [case["test_id"] for case in plan["cases"]] == smoke_catalog.SMOKE_PROFILES[profile]
    assert [case["case_id"] for case in plan["cases"]] == [
        "case_000_worker_os_blue",
        "case_001_dispatch_smoke_8p",
        "case_002_dispatch_smoke_16p",
        "case_003_dispatch_smoke_8p",
    ]
    assert [case["resources"]["nodes"] for case in plan["cases"]] == [0, 1, 2, 1]


def test_repeated_smoke_results_cannot_be_swapped():
    spec = importlib.util.spec_from_file_location("smoke_waiter", ROOT / ".github/scripts/lite_actions/wait_result.py")
    waiter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(waiter)
    cases = _producer().expand_cases([{"suite": "dispatch_smoke"}], DEFAULTS)
    first, last = cases[1], cases[3]
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
    valid = dict(comment)
    assert waiter.find_result([comment], **args) == result
    failure = {"user": {"login": "reporter"}, "body": marker + json.dumps({**result, "status": "FAIL", "exit_code": 1})}
    for comments in ([comment, failure], [failure, comment]):
        with pytest.raises(ValueError, match="conflicting"):
            waiter.find_result(comments, **args)
    assert waiter.find_result([comment, comment], **args) == result
    assert waiter.find_result([comment, {**failure, "user": {"login": "other"}}], **args) == result
    other_run = {**failure, "body": failure["body"].replace("Lite-CI-V3 7:1:", "Lite-CI-V3 8:1:", 1)}
    assert waiter.find_result([comment, other_run], **args) == result
    for mismatch in (
        {"attempt": True},
        {"resources": {**first["resources"], "nodes": True}},
        {"sha": "c" * 40},
        {"status": "UNKNOWN"},
        {"cleanup_verified": False},
    ):
        comment["body"] = marker + json.dumps({**result, **mismatch})
        assert waiter.find_result([comment], **args) is None
        for comments in ([valid, comment], [comment, valid]):
            with pytest.raises(ValueError, match="invalid case result"):
                waiter.find_result(comments, **args)

    probe = _producer().expand_cases([{"test_id": "worker_os_blue"}], DEFAULTS)[0]
    probe_result = {
        **result,
        **{k: probe[k] for k in ("case_id", "test_id", "worker", "target_pool", "docker", "resources")},
    }
    del probe_result["target_pool"]
    probe_marker = f"Lite-CI-V3 7:1:{probe['case_id']}\n"
    comment["body"] = probe_marker + json.dumps(probe_result)
    assert waiter.find_result([comment], **{**args, "item": probe}) is None


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


def test_new_profile_case_expands_matrix_and_pinned_plan_without_workflow_changes(tmp_path, monkeypatch):
    declared = list(smoke_catalog.SMOKE_PROFILES["dispatch_smoke"])
    definitions = smoke_catalog.build_smoke_list()
    test_id = "cpu_catalog_probe_4p"
    smoke_8p = next(case for case in definitions if case.test_name == "dispatch_smoke_8p")
    case = replace(smoke_8p, test_name=test_id, test_descr="4P smoke", ngpu=4)
    monkeypatch.setattr(entrypoint, "build_smoke_list", lambda: [*definitions, case])
    monkeypatch.setitem(smoke_catalog.SMOKE_PROFILES, "dispatch_smoke", [*declared, test_id])
    monkeypatch.setitem(smoke_catalog.SMOKE_DEFAULTS, "target_pool", "a3_shared_blue_trial")
    monkeypatch.setitem(smoke_catalog.SMOKE_DEFAULTS, "docker", "example:trial")
    monkeypatch.chdir(tmp_path)
    env = {
        "CI_TEST_CASES": '[{"suite":"dispatch_smoke"}]',
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
    assert [c["test_id"] for c in plan["cases"]] == [*declared, test_id]
    assert [c["case_id"] for c in plan["cases"]] == [
        f"case_{index:03d}_{name}" for index, name in enumerate([*declared, test_id])
    ]
    assert plan["cases"][-1]["resources"] == {"nodes": 1, "devices_per_node": 4}
    assert all(c["target_pool"] == "a3_shared_blue_trial" and c["docker"] == "example:trial" for c in plan["cases"][1:])
    assert entrypoint.select(entrypoint.catalog(), test_id=test_id)[0].ngpu == 4


def test_duplicate_probe_in_profile_is_rejected(monkeypatch):
    monkeypatch.setitem(smoke_catalog.SMOKE_PROFILES, "dispatch_smoke", ["worker_os_blue", "worker_os_blue"])
    with pytest.raises(ValueError, match="duplicate"):
        _producer().expand_cases([{"suite": "dispatch_smoke"}], DEFAULTS)
