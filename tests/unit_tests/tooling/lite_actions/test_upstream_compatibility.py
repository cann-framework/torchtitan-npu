"""CPU regression: schema-3 additions do not replace upstream schema-2 APIs."""
import importlib.util
import os
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[4]

def test_legacy_plan_and_workflow_call_interface_remain_usable():
    spec = importlib.util.spec_from_file_location("legacy_prepare", ROOT / ".github/scripts/lite_actions/prepare_request.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, {"GITHUB_REPOSITORY": "torchtitan-npu/torchtitan-npu"}):
        spec.loader.exec_module(module)
    plan = module.create_plan(cases=[{"test_id": "sample", "nnodes": 1, "ngpu": 8, "label": "sample"}], run_id=7, attempt=1, sha="a" * 40, repo="torchtitan-npu/torchtitan-npu", pipeline="a3-8p")
    assert plan["schema"] == 2 and plan["pipeline"] == "a3-8p"
    common = yaml.load((ROOT / ".github/workflows/lite_actions_common.yml").read_text(), Loader=yaml.BaseLoader)
    assert "pipeline" in common["on"]["workflow_call"]["inputs"]
    assert "workflow" not in common["on"]["workflow_call"]["inputs"]
    for name in ("lite_actions_dispatcher_smoke.yml",):
        workflow = yaml.load((ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader)
        assert workflow["jobs"]["ci"]["uses"] == "./.github/workflows/lite_actions_common.yml"
        assert workflow["jobs"]["ci"]["with"]["pipeline"] == "dispatcher-smoke"

def test_upstream_recipe_toolkit_and_ema_extension_preserved():
    from tests.integration_tests import OverrideDefinitions
    from tests.integration_tests.nightly_all_models_test import a3_8p_tests, a3_16p_tests
    assert hasattr(OverrideDefinitions(), "verify_ema_checkpoint")
    for module in (a3_8p_tests, a3_16p_tests):
        for case in module.build_test_list():
            assert case.env_vars["ASCEND_SET_ENV_PATH"] == "/mnt/share/Ascend/20260805101249091/ascend-toolkit/latest/set_env.sh"
