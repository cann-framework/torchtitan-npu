"""Bridge selection through real SDK authority and Prepare, with synthetic APIs."""

import copy
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import ci_core
from ci_core.gitcode_contract import digest, select_cases, spec_digest
from ci_core.gitcode_platform import ObservedGitCodeAuthority

# The model and SDK both own a "tests" package. Isolate SDK API fixtures
# while importing them, restoring the model import cache immediately afterwards.
sdk_tests = ModuleType("tests")
sdk_tests.__path__ = [str(Path(ci_core.__file__).resolve().parent.parent / "tests")]
with patch.dict(sys.modules, {"tests": sdk_tests}):
    from tests.test_gitcode_pipeline import HostingAPI

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location("selection_bridge", ROOT / ".gitcode/scripts/lite_gitcode_forward.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class CaseSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.api = HostingAPI(self.root)
        self.catalog = json.loads((bridge.CONTROL / "catalog.json").read_text())
        self.api.reader.catalog = self.catalog["cases"]
        self.api.producer.catalog = self.catalog["cases"]
        self.api.producer.bridge.catalog = self.catalog["cases"]
        self.api.producer.reader.catalog = self.catalog["cases"]
        self.client = Mock(repo_id=self.api.policy.gitcode_repo_id)
        self.client.authenticated_actor.return_value = "depeng1994"
        self.client.authority_snapshot.side_effect = lambda *a, **k: {
            "identity": copy.deepcopy(self.api.intent["pr"]),
            "state": "open",
            "authorized": True,
        }
        self.authority = ObservedGitCodeAuthority(
            self.client,
            self.api.policy,
            self.catalog["cases"],
            self.root / "authority.sqlite3",
            clock=lambda: 1100,
        )
        self.runtime = SimpleNamespace(policy=self.api.policy, authority=self.authority)
        self.environment = {
            "LITE_ACTIONS_RELEASE_CONFIG": "{}",
            "LITE_ACTIONS_BRIDGE_STATE": str(self.root),
        }

    def intent(self, case_ids=None):
        with patch.object(bridge, "watch", side_effect=lambda runtime, intent, **kwargs: intent):
            return bridge.execute(
                environment=self.environment,
                number=1,
                expected_head="a" * 40,
                case_ids=case_ids,
                factory=lambda *a, **k: self.runtime,
            )

    def test_single_os_case_survives_authority_prepare_and_verified_artifact_without_npu_resources(self):
        self.api.intent = self.intent(["worker_os_blue"])
        self.authority.verify_event(self.api.intent)
        self.api.run["display_title"] = "lite-gitcode:" + self.api.intent["bridge"]["bridge_nonce"]
        plan = self.api.produce()
        self.api.job.update(status="completed", conclusion="success")
        verified = self.api.reader.load(100, self.root / "read", now=1100)

        self.assertEqual(verified.verified.plan, plan)
        self.assertEqual([case["test_id"] for case in plan["cases"]], ["worker_os_blue"])
        self.assertEqual(plan["cases"][0]["resources"], {"nodes": 0, "devices_per_node": 0})
        self.assertIsNone(plan["cases"][0]["target_pool"])
        self.assertEqual(set(plan["result_slots"]), {"aggregate", "case_000_worker_os_blue"})
        self.assertEqual(plan["artifacts"]["intent"]["content_sha256"], digest(self.api.intent))

    def test_omitted_selector_preserves_full_smoke_pipeline(self):
        intent = self.intent()
        self.assertEqual(
            intent["selectors"],
            [
                {"test_id": "worker_os_blue"},
                {"test_id": "dispatch_smoke_8p"},
                {"test_id": "dispatch_smoke_16p"},
            ],
        )

    def test_each_hardware_and_training_case_is_individually_selectable(self):
        for pipeline, names in self.catalog["pipelines"].items():
            workflow = (
                ".github/workflows/lite_actions_dispatcher_smoke.yml"
                if pipeline == "smoke"
                else ".github/workflows/nightly_fulltests.yml"
            )
            policy = replace(self.api.policy, workflow_path=workflow)
            self.runtime.policy = policy
            self.authority.policy = policy
            for name in names:
                with self.subTest(pipeline=pipeline, case=name):
                    intent = self.intent([name])
                    self.authority.verify_event(intent)
                    cases = select_cases(intent["selectors"], self.catalog["cases"])
                    self.assertEqual(intent["pipeline"], pipeline)
                    self.assertEqual([case["test_id"] for case in cases], [name])

    def test_empty_unknown_duplicate_and_cross_pipeline_selection_never_creates_intent(self):
        for names in ([], [""], ["unknown"], ["worker_os_blue"] * 2, ["dsv4_flash_a3_8p_example"], "worker_os_blue"):
            with (
                self.subTest(names=names),
                patch.object(self.authority, "create_intent", wraps=self.authority.create_intent) as create,
            ):
                with self.assertRaises(ValueError):
                    self.intent(names)
                create.assert_not_called()
        self.client.authenticated_actor.assert_not_called()
        self.client.authority_snapshot.assert_not_called()

    def test_unknown_workflow_never_falls_back_to_nightly(self):
        self.runtime.policy = SimpleNamespace(workflow_path="untrusted/lite_actions_dispatcher_smoke.yml")
        with (
            patch.object(self.authority, "create_intent") as create,
            self.assertRaisesRegex(ValueError, "UNKNOWN_BRIDGE_WORKFLOW"),
        ):
            self.intent()
        create.assert_not_called()

    def test_selection_is_immutable_and_new_selection_revokes_old_intent(self):
        names = ["worker_os_blue"]
        original = self.intent(names)
        names.append("dispatch_smoke_8p")
        self.assertEqual(original["selectors"], [{"test_id": "worker_os_blue"}])
        self.authority.verify_event(original)
        altered = copy.deepcopy(original)
        altered["selectors"].append({"test_id": "dispatch_smoke_8p"})
        with self.assertRaises(ValueError):
            self.authority.verify_event(altered)
        replacement = self.intent(["dispatch_smoke_8p"])
        self.assertNotEqual(original["bridge"]["dispatch_intent_id"], replacement["bridge"]["dispatch_intent_id"])
        with self.assertRaises(ValueError):
            self.authority.verify_event(original)
        self.authority.verify_event(replacement)

    def test_selected_plan_spec_digest_cannot_alias_whole_pipeline(self):
        self.api.intent = self.intent(["worker_os_blue"])
        self.api.run["display_title"] = "lite-gitcode:" + self.api.intent["bridge"]["bridge_nonce"]
        plan = self.api.produce()
        widened = copy.deepcopy(plan)
        widened["cases"] = select_cases(
            [{"test_id": "worker_os_blue"}, {"test_id": "dispatch_smoke_8p"}, {"test_id": "dispatch_smoke_16p"}],
            self.catalog["cases"],
        )
        self.assertNotEqual(spec_digest(plan), spec_digest(widened))

    def test_nightly_rejects_smoke_case_before_authority(self):
        self.runtime.policy = replace(self.api.policy, workflow_path=".github/workflows/nightly_fulltests.yml")
        with patch.object(self.authority, "create_intent") as create, self.assertRaises(ValueError):
            self.intent(["worker_os_blue"])
        create.assert_not_called()

    def test_case_selector_does_not_bypass_original_event_guard(self):
        self.environment["LITE_ACTIONS_GITCODE_EVENT_NAME"] = "pull_request_target"
        with (
            patch.object(self.authority, "create_intent") as create,
            self.assertRaisesRegex(ValueError, "REAL_ACTION_IDENTITY_ADAPTER_REQUIRED"),
        ):
            self.intent(["worker_os_blue"])
        create.assert_not_called()

    def test_case_selector_does_not_bypass_expected_head_guard(self):
        with (
            patch.object(bridge, "watch") as watch,
            self.assertRaisesRegex(ValueError, "LATE_EVENT_HEAD_CHANGED_NO_DISPATCH"),
        ):
            bridge.execute(
                environment=self.environment,
                number=1,
                expected_head="b" * 40,
                case_ids=["worker_os_blue"],
                factory=lambda *a, **k: self.runtime,
            )
        watch.assert_not_called()

    def test_repeatable_cli_selector_reaches_real_authority_in_requested_order(self):
        execute = bridge.execute
        captured = []

        def execute_with_fixture(**kwargs):
            kwargs.update(environment=self.environment, factory=lambda *a, **k: self.runtime)
            return execute(**kwargs)

        def stop_before_dispatch(runtime, intent, **kwargs):
            runtime.authority.verify_event(intent)
            captured.append(intent)
            return {"test_result": "PASS"}

        with (
            patch.object(bridge, "execute", side_effect=execute_with_fixture),
            patch.object(bridge, "watch", side_effect=stop_before_dispatch),
            redirect_stdout(io.StringIO()),
        ):
            code = bridge.main(
                ["--pr", "1", "--expected-head", "a" * 40, "--case", "dispatch_smoke_16p", "--case", "worker_os_blue"]
            )
        self.assertEqual(code, 0)
        self.assertEqual(captured[0]["selectors"], [{"test_id": "dispatch_smoke_16p"}, {"test_id": "worker_os_blue"}])
