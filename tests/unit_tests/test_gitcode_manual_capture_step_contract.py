# Copyright (c) 2026 Huawei Technologies Co., Ltd. All rights reserved.
# Licensed under the BSD-style license found in the LICENSE file.

import importlib.util
import json
from pathlib import Path
import sys
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT / ".gitcode/workflows/torchtitan_forward_manual.yml"
FIXTURE_PATH = ROOT / "tests/fixtures/lite_actions/manual_capture_platform_steps_b472.json"
BUILDER_PATH = ROOT / ".gitcode/scripts/build_hosted_manual_workflow.py"

spec = importlib.util.spec_from_file_location("manual_workflow_builder", BUILDER_PATH)
builder = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = builder
spec.loader.exec_module(builder)


class GitCodeManualCaptureStepContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.load(WORKFLOW_PATH.read_text(), Loader=yaml.BaseLoader)
        cls.jobs = cls.workflow["stages"]["manual"]["jobs"]
        cls.fixture = json.loads(FIXTURE_PATH.read_text())
        cls.platform_steps = cls.fixture["steps"]
        cls.user_steps = [
            step for step in cls.platform_steps if step.get("task") == "official_shell"
        ]

    def test_job_identity_remains_distinct_from_platform_step_identifier(self):
        self.assertIn("manual_capture", self.jobs)
        self.assertEqual(self.jobs["manual_capture"]["name"], "manual_capture")
        self.assertEqual(len(self.user_steps), 1)
        self.assertEqual(self.user_steps[0]["identifier"], "1")

    def test_generated_step_identifier_matches_real_platform_metadata_fixture(self):
        step = self.jobs["manual_capture"]["steps"][0]
        platform_step = self.user_steps[0]
        self.assertEqual(step["id"], "1")  # SDK common.CAPTURE_STEP for the pinned SDK.
        self.assertEqual(step["id"], platform_step["identifier"])
        self.assertEqual(step["name"], platform_step["name"])
        self.assertEqual(platform_step["sequence"], 1)
        self.assertEqual(platform_step["job_run_id"], self.fixture["job_id"])
        self.assertEqual(platform_step["status"], "COMPLETED")
        self.assertEqual(platform_step["task"], "official_shell")

    def test_shell_working_directory_and_script_digest_remain_exact(self):
        capture = self.jobs["manual_capture"]["steps"][0]
        forward = self.jobs["manual_forward"]["steps"][0]
        _, script_sha = builder.scripts()
        self.assertEqual(capture["shell"], "bash")
        self.assertEqual(capture["working-directory"], ".")
        self.assertEqual(forward["env"]["LITE_GITCODE_CAPTURE_SCRIPT_SHA256"], script_sha)
        self.assertEqual(
            script_sha,
            "3bc85275ff90c6ca8c65c7e1c91459a22b4336644cee5cbc359f297a41ffe641",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
