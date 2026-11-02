"""CPU regression: preserve the current organization schema-3 native interfaces."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[4]


def test_organization_schema3_workflow_call_remains_separate_from_gitcode_jobs():
    common = yaml.load((ROOT / ".github/workflows/lite_actions_common.yml").read_text(), Loader=yaml.BaseLoader)
    assert "workflow" in common["on"]["workflow_call"]["inputs"]
    assert "pipeline" not in common["on"]["workflow_call"]["inputs"]
    for name in ("lite_actions_dispatcher_smoke.yml", "nightly_fulltests.yml"):
        workflow = yaml.load((ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader)
        native = workflow["jobs"]["ci"]
        assert native["uses"] == "./.github/workflows/lite_actions_common.yml"
        assert native["with"]["workflow"] == name
        assert native["if"] == "inputs.source_provider != 'gitcode'"
        assert workflow["jobs"]["result"]["if"] == "always() && inputs.source_provider != 'gitcode'"
        assert workflow["jobs"]["prepare-gitcode"]["name"] == "prepare-gitcode"
        assert workflow["jobs"]["wait-gitcode"]["needs"] == "prepare-gitcode"


def test_organization_recipe_runtime_toolkit_and_upstream_ema_extension_preserved():
    from tests.integration_tests import OverrideDefinitions
    from tests.integration_tests.nightly_all_models_test import a3_8p_tests, a3_16p_tests

    assert hasattr(OverrideDefinitions(), "verify_ema_checkpoint")
    for module in (a3_8p_tests, a3_16p_tests):
        for case in module.build_test_list():
            assert case.env_vars["ASCEND_SET_ENV_PATH"] == "/usr/local/Ascend/ascend-toolkit/set_env.sh"
