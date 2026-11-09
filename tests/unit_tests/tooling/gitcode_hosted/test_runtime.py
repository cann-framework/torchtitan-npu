"""Runnable Hosted/Bridge wiring tests with synthetic credentials and API fakes."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[4]
CONTROL = ROOT / ".github/scripts/lite_actions/gitcode"
sys.path.insert(0, str(CONTROL))
import runtime

class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.policy = SimpleNamespace(
            github_repo_id=1210038933,
            control_sha="a" * 40,
            enabled=True,
            workflow_path=".github/workflows/lite_actions_dispatcher_smoke.yml",
            max_duration_seconds=60,
        )
        self.environment = {
            "RUNNER_TEMP": self.tmp.name,
            "GITHUB_RUN_ID": "42",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_REPOSITORY": "cann-framework/torchtitan-npu",
            "GITHUB_REPOSITORY_ID": "1210038933",
            "GITHUB_REF": "refs/heads/master",
            "GITHUB_SHA": "a" * 40,
            "GITHUB_WORKFLOW_SHA": "a" * 40,
            "GITHUB_WORKFLOW_REF": "cann-framework/torchtitan-npu/" + self.policy.workflow_path + "@refs/heads/master",
            "LITE_ACTIONS_RELEASE_CONFIG": json.dumps({"sdk_sha": "b" * 40, "authority_protocol": "artifact-check-v1"}),
            "LITE_ACTIONS_SDK_SHA": "b" * 40,
            "LITE_ACTIONS_SOURCE_PROVIDER": "gitcode",
            "LITE_ACTIONS_GITCODE_REQUEST": json.dumps({"workflow_path": self.policy.workflow_path}),
            "LITE_ACTIONS_NODE": "/usr/bin/node",
            "LITE_ACTIONS_CHECKS_KEY": "SYNTHETIC-KEY-NOT-A-CREDENTIAL",
        }

    def test_source_failure_diagnostics_only_emit_exact_audited_constants(self):
        from ci_core.gitcode_source import SourceError

        for code in runtime.SOURCE_ERROR_CODES:
            self.assertEqual(
                runtime.safe_failure(SourceError(code), "prepare"),
                {"phase": "prepare", "stage": "SOURCE", "code": code},
            )
        for error in (
            SourceError("GIT_OPERATION_FAILED token-secret"),
            SourceError("GIT_OPERATION_FAILED", "token-secret"),
            ValueError("token-secret"),
        ):
            diagnostic = runtime.safe_failure(error, "prepare")
            self.assertEqual(diagnostic["code"], "UNCLASSIFIED")
            self.assertNotIn("token-secret", json.dumps(diagnostic))
        with (
            patch.object(runtime, "execute", side_effect=SourceError("FETCHED_REF_SHA_MISMATCH")),
            patch("sys.stderr") as stderr,
        ):
            self.assertEqual(runtime.main(["prepare"]), 1)
            self.assertIn("FETCHED_REF_SHA_MISMATCH", str(stderr.write.call_args_list))

    def test_wait_failure_diagnostics_only_reveal_fixed_types_and_http_status(self):
        import urllib.error

        error = urllib.error.HTTPError(
            "https://example.invalid/token-secret", 403, "token-secret", {}, None
        )
        self.assertEqual(
            runtime.safe_failure(error, "prepare"),
            {"phase": "prepare", "stage": "HOSTED", "code": "UNCLASSIFIED",
             "error_type": "HTTPError", "http_status": 403},
        )
        error = KeyError("token-secret")
        diagnostic = runtime.safe_failure(error, "wait")
        self.assertEqual(diagnostic["error_type"], "KeyError")
        self.assertNotIn("token-secret", json.dumps(diagnostic))
        class SecretException(Exception):
            pass
        self.assertEqual(runtime.safe_failure(SecretException("token-secret"), "wait")["error_type"], "OTHER")

    def test_prepare_builds_trusted_runner_and_cleans_private_key(self):
        producer = Mock(policy=self.policy)
        producer.prepare.return_value = {"plan_digest": "sealed"}

        def factory(config_path, *, environment, upload, state_directory):
            self.assertEqual(
                json.loads(config_path.read_text()), {"sdk_sha": "b" * 40, "authority_protocol": "artifact-check-v1"}
            )
            key = Path(environment["LITE_ACTIONS_CHECKS_KEY_FILE"])
            self.assertEqual(key.stat().st_mode & 0o777, 0o600)
            self.assertEqual(key.read_text(), "SYNTHETIC-KEY-NOT-A-CREDENTIAL")
            self.assertNotIn("LITE_ACTIONS_CHECKS_KEY", environment)
            return SimpleNamespace(policy=self.policy, producer=producer)

        result, code = runtime.execute("prepare", environment=self.environment, factory=factory)
        self.assertEqual((result, code), ({"phase": "prepared", "plan_digest": "sealed"}, 0))
        args = producer.prepare.call_args.kwargs
        self.assertEqual(args["run_id"], 42)
        self.assertTrue(args["runner_path"].read_bytes().startswith(b"\x1f\x8b"))
        self.assertFalse((args["runner_path"].parent / "checks-app.pem").exists())

    def test_restart_never_recreates_fresh_creation_journal(self):
        producer = Mock(policy=self.policy)
        producer.prepare.return_value = {"plan_digest": "sealed"}
        factory = Mock(return_value=SimpleNamespace(policy=self.policy, producer=producer))
        runtime.execute("prepare", environment=self.environment, factory=factory)
        with self.assertRaises(FileExistsError):
            runtime.execute("prepare", environment=self.environment, factory=factory)
        self.assertEqual(factory.call_count, 1)

    def test_rerun_fails_before_producer_and_cleans_key(self):
        producer = Mock(policy=self.policy)
        factory = Mock(return_value=SimpleNamespace(policy=self.policy, producer=producer))
        self.environment["GITHUB_RUN_ATTEMPT"] = "2"
        with self.assertRaises(ValueError):
            runtime.execute("prepare", environment=self.environment, factory=factory)
        producer.prepare.assert_not_called()
        self.assertFalse((Path(self.tmp.name) / "lite-gitcode-42-prepare/checks-app.pem").exists())

    def test_wait_failure_is_nonzero_without_preparing(self):
        assembled = Mock(policy=self.policy)
        with patch("hosted.wait", return_value={"test_result": "FAIL", "merge_gate": "NOT_PROVIDED"}) as wait:
            result, code = runtime.execute("wait", environment=self.environment, factory=lambda *a, **k: assembled)
        self.assertEqual(code, 1)
        self.assertEqual(result["test_result"], "FAIL")
        wait.assert_called_once()
        assembled.producer.prepare.assert_not_called()

    def test_sdk_pin_mismatch_fails_before_factory(self):
        self.environment["LITE_ACTIONS_SDK_SHA"] = "c" * 40
        factory = Mock()
        with self.assertRaisesRegex(ValueError, "SDK_RELEASE_PIN"):
            runtime.execute("prepare", environment=self.environment, factory=factory)
        factory.assert_not_called()

    def test_duplicate_input_json_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "DUPLICATE"):
            runtime.read_object('{"sdk_sha":"a","sdk_sha":"b"}')

    def test_node_action_supplies_runtime_and_propagates_failure(self):
        script = """
const action=require(process.argv[1]);
let called=false;
const code=action.run({'INPUT_PHASE':'prepare','INPUT_RELEASE-CONFIG':'{}',
 'INPUT_SDK-ROOT':'/trusted/sdk','INPUT_SDK-SHA':'pin',
 ACTIONS_RUNTIME_TOKEN:'synthetic-runtime'}, (binary,args,options)=>{
 if(binary!=='python3'||args.at(-1)!=='prepare'||options.shell!==false) throw Error('argv');
 if(options.env.ACTIONS_RUNTIME_TOKEN!=='synthetic-runtime') throw Error('runtime token missing');
 if(options.env.LITE_ACTIONS_NODE!==process.execPath) throw Error('node runtime mismatch');
 called=true;return {status:7};
});
if(code!==7||!called)process.exit(2);
"""
        subprocess.run(["node", "-e", script, str(CONTROL / "runtime.cjs")], check=True)

    def test_pinned_checkout_rejects_untracked_and_ignored_python(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "HOME": directory}

            def git(*args):
                return (
                    subprocess.check_output(["git", "-C", directory, *args], env=env, stderr=subprocess.DEVNULL)
                    .decode()
                    .strip()
                )

            git("init")
            (root / "trusted.py").write_text("VALUE = 1\n")
            (root / ".gitignore").write_text("__pycache__/\n")
            git("add", ".")
            git("-c", "user.name=CPU Test", "-c", "user.email=cpu@example.invalid", "commit", "-m", "trusted fixture")
            commit = git("rev-parse", "HEAD")
            self.assertEqual(runtime.verify_checkout(root, commit), root)
            for relative in ("socket.py", "__pycache__/shadow.pyc"):
                path = root / relative
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(b"untrusted executable bytes")
                with self.assertRaisesRegex(ValueError, "UNTRACKED_EXECUTABLE_IMPORT"):
                    runtime.verify_checkout(root, commit)
                path.unlink()
            with tempfile.TemporaryDirectory() as outside:
                (Path(outside) / "__init__.py").write_text("raise RuntimeError('must not import')")
                (root / "socket").symlink_to(outside, target_is_directory=True)
                with self.assertRaisesRegex(ValueError, "UNTRACKED_EXECUTABLE_IMPORT"):
                    runtime.verify_checkout(root, commit)

    def test_hosted_release_cannot_omit_fresh_authority_protocol(self):
        self.environment["LITE_ACTIONS_RELEASE_CONFIG"] = json.dumps({"sdk_sha": "b" * 40})
        factory = Mock()
        with self.assertRaisesRegex(ValueError, "FRESH_AUTHORITY_PROTOCOL_REQUIRED"):
            runtime.execute("prepare", environment=self.environment, factory=factory)
        factory.assert_not_called()

    def test_signed_envelope_is_uploaded_unchanged_before_prepare(self):
        intent = json.loads(self.environment["LITE_ACTIONS_GITCODE_REQUEST"])
        envelope = {"payload": {"intent": intent}, "signature": "synthetic-public-signature"}
        self.environment["LITE_ACTIONS_GITCODE_ATTESTATION"] = json.dumps(envelope)
        order = []
        producer = Mock(policy=self.policy)

        def prepared(**kwargs):
            order.append("prepare")
            return {"plan_digest": "sealed"}

        producer.prepare.side_effect = prepared

        def upload(name, path):
            order.append("attestation")
            self.assertEqual(name, "dispatch-attestation-42-1")
            self.assertEqual(path.name, "attestation.json")
            self.assertEqual(json.loads(path.read_text()), envelope)
            return 77

        with patch("hosted.ArtifactUploader", return_value=upload):
            runtime.execute(
                "prepare",
                environment=self.environment,
                factory=lambda *a, **k: SimpleNamespace(policy=self.policy, producer=producer),
            )
        self.assertEqual(order, ["attestation", "prepare"])

    def test_attestation_for_different_intent_never_uploads_or_prepares(self):
        self.environment["LITE_ACTIONS_GITCODE_ATTESTATION"] = json.dumps({"payload": {"intent": {"wrong": True}}})
        producer = Mock(policy=self.policy)
        with (
            patch("hosted.ArtifactUploader") as uploader,
            self.assertRaisesRegex(ValueError, "ATTESTATION_INTENT_MISMATCH"),
        ):
            runtime.execute(
                "prepare",
                environment=self.environment,
                factory=lambda *a, **k: SimpleNamespace(policy=self.policy, producer=producer),
            )
        uploader.return_value.assert_not_called()
        producer.prepare.assert_not_called()

    def test_actual_clean_sibling_checkouts_verify_without_untracked_exceptions(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            commits = {}
            for name in ("control", "worker-sdk"):
                checkout = workspace / name
                checkout.mkdir()

                def git(*args, checkout=checkout):
                    return (
                        subprocess.check_output(
                            ["git", "-C", str(checkout), *args],
                            stderr=subprocess.DEVNULL,
                            env={**os.environ, "HOME": directory, "GIT_CONFIG_NOSYSTEM": "1"},
                        )
                        .decode()
                        .strip()
                    )

                git("init")
                (checkout / "trusted.py").write_text("VALUE = 1\n")
                git("add", ".")
                git(
                    "-c", "user.name=CPU Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "trusted"
                )
                commits[name] = git("rev-parse", "HEAD")
            for name in commits:
                self.assertEqual(runtime.verify_checkout(workspace / name, commits[name]), workspace / name)
            (workspace / "worker-sdk/socket.py").write_text("raise RuntimeError('untrusted')")
            with self.assertRaisesRegex(ValueError, "UNTRACKED_EXECUTABLE_IMPORT"):
                runtime.verify_checkout(workspace / "worker-sdk", commits["worker-sdk"])


class WorkflowSyntaxTests(unittest.TestCase):
    def test_github_context_expressions_are_not_literal_single_braces(self):
        import re

        for name in ("nightly_fulltests.yml",):
            text = (ROOT / ".github/workflows" / name).read_text()
            self.assertIsNone(re.search(r"\$\{\s*(?:vars|github|inputs|secrets|needs)\.", text))
            self.assertIn("ref: ${{ github.sha }}", text)
            self.assertIn("gitcode-request: ${{ inputs.gitcode_request }}", text)
            self.assertIn("sdk-sha: ${{ vars.LITE_GITCODE_SDK_SHA }}", text)

    def test_checks_private_key_is_only_supplied_to_prepare(self):
        for name in ("nightly_fulltests.yml",):
            text = (ROOT / ".github/workflows" / name).read_text()
            self.assertEqual(text.count("checks-key: ${{ secrets.LITE_GITCODE_CHECKS_APP_KEY }}"), 1)
            self.assertNotIn("checks-key:", text.split("  wait-gitcode:")[1])
        script = """
const action=require(process.argv[1]);
const code=action.run({'INPUT_PHASE':'wait','INPUT_CHECKS-KEY':'synthetic-private-key'},(bin,args,options)=>{
 if(options.env.LITE_ACTIONS_CHECKS_KEY || options.env['INPUT_CHECKS-KEY']) throw Error('key leaked to waiter');
 return {status:0};
});
if(code!==0)process.exit(2);
"""
        subprocess.run(["node", "-e", script, str(CONTROL / "runtime.cjs")], check=True)

    def test_control_and_sdk_are_sibling_checkouts_and_unproven_action_is_unregistered(self):
        import yaml

        for name in ("nightly_fulltests.yml",):
            document = yaml.load((ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader)
            for phase in ("prepare-gitcode", "wait-gitcode"):
                steps = document["jobs"][phase]["steps"]
                checkouts = [step["with"] for step in steps if step.get("uses", "").startswith("actions/checkout@")]
                self.assertEqual([checkout["path"] for checkout in checkouts], ["control", "worker-sdk"])
                action = steps[-1]
                self.assertEqual(action["uses"], "./control/.github/scripts/lite_actions/gitcode")
        self.assertFalse((ROOT / ".gitcode/workflows/lite_gitcode_forward.yml").exists())
