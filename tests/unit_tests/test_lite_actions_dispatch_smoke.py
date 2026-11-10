# Copyright (c) 2026 Huawei Technologies Co., Ltd. All rights reserved.
# Licensed under the BSD-style license found in the LICENSE file.

import os
import subprocess
import unittest
from dataclasses import replace
from unittest.mock import patch

from tests.integration_tests import OverrideDefinitions
from tests.integration_tests.nightly_all_models_test import runner
from tests.integration_tests.tools.lite_actions.contract import normalize
from tests.integration_tests.tools.lite_actions.entrypoint import catalog, select


class LiteActionsDispatchSmokeTests(unittest.TestCase):
    def setUp(self):
        self.case = OverrideDefinitions(test_name="renamed_smoke", ci_kind="npu_smoke", ngpu=8, timeout=120)
        self.defaults = dict(worker="blue", target_pool="a3_shared_blue", docker="image:tag")

    # 看护场景：冒烟定义可经模型仓入口查询，且不会误将训练用例当成冒烟用例。
    def test_registered_topologies_and_training_independence(self):
        cases = catalog()
        for test_id, nodes in (("dispatch_smoke_8p", 1), ("dispatch_smoke_16p", 2)):
            case = select(cases, test_id=test_id)[0]
            self.assertEqual((case.nnodes, case.ngpu), (nodes, 8))
            self.assertTrue(runner.is_dispatch_smoke(case))
            self.assertFalse(case.check_loss)
            self.assertIsNone(case.expected_steps)
        self.assertTrue(runner.is_dispatch_smoke(replace(self.case, test_name="dispatch_smoke_32p", nnodes=4)))
        self.assertFalse(runner.is_dispatch_smoke(replace(self.case, test_name="dispatch_smoke_8p", ci_kind="train")))

    # 看护场景：单机手动冒烟使用独立启动，并遵循用例定义的超时预算。
    def test_single_manual_launch_uses_standalone_and_declared_timeout(self):
        for timeout in (120, None):
            case = replace(self.case, timeout=timeout)
            with patch.dict(os.environ, {}, clear=True), patch.object(runner.subprocess, "run") as launch:
                runner.run_dispatch_smoke(case)
            self.assertIn("--standalone", launch.call_args.args[0])
            self.assertEqual(
                launch.call_args.kwargs["timeout"], normalize(case, self.defaults, 0)["execution_timeout_seconds"]
            )

    # 看护场景：双机手动冒烟未指定通信端口时不启动，避免隐式占用端口。
    def test_multinode_missing_explicit_port_fails_before_launch(self):
        env = dict(NODE_IPS="192.0.2.1,192.0.2.2", NODE_RANK="0")
        with patch.dict(os.environ, env, clear=True), patch.object(runner.subprocess, "run") as launch:
            with self.assertRaisesRegex(RuntimeError, "explicit MASTER_PORT"):
                runner.run_dispatch_smoke(replace(self.case, nnodes=2))
            launch.assert_not_called()

    # 看护场景：torchrun 超过用例预算时，冒烟任务按失败退出。
    def test_timeout_returns_failure(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(runner.subprocess, "run", side_effect=subprocess.TimeoutExpired("torchrun", 120)),
            self.assertRaisesRegex(RuntimeError, "120-second"),
        ):
            runner.run_dispatch_smoke(self.case)


if __name__ == "__main__":
    unittest.main()
