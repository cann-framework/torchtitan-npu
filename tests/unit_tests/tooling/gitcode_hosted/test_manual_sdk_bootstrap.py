"""Offline SDK bundle and GitCode Manual workflow contract checks."""

import base64
import hashlib
import json
import os
import subprocess
import sys
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / ".gitcode/scripts"))
import build_hosted_manual_workflow as builder


class ManualSdkBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.content, self.digest = builder.render()
        self.document = yaml.load(self.content, Loader=yaml.BaseLoader)
        self.jobs = self.document["stages"]["manual"]["jobs"]
        self.script = self.jobs["manual_capture"]["steps"][0]["run"]

    def run_script(self, script, **values):
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HTTPS_PROXY": "http://127.0.0.1:1",
            "HTTP_PROXY": "http://127.0.0.1:1",
            "NO_PROXY": "",
            **values,
        }
        return subprocess.run(["bash", "-c", script], env=env, text=True, capture_output=True, timeout=30, check=False)

    def test_generated_jobs_keep_single_identical_pinned_step(self):
        self.assertEqual(
            set(self.jobs),
            {
                "manual_capture",
                "manual_forward",
                "case_os_result",
                "case_8p_result",
                "case_16p_result",
                "aggregate_gate",
            },
        )
        scripts = [j["steps"][0]["run"] for j in self.jobs.values()]
        self.assertEqual(len(set(scripts)), 1)
        self.assertTrue(all(len(j["steps"]) == 1 for j in self.jobs.values()))
        self.assertLess(len(self.script.encode()), 64000)
        self.assertEqual(hashlib.sha256(self.script.encode()).hexdigest(), self.digest)
        self.assertEqual(
            self.jobs["manual_forward"]["steps"][0]["env"]["LITE_GITCODE_CAPTURE_SCRIPT_SHA256"], self.digest
        )
        self.assertEqual(self.jobs["manual_capture"]["steps"][0]["name"], "Capture manual request")
        self.assertEqual(self.jobs["manual_capture"]["steps"][0]["id"], "1")
        self.assertEqual(self.jobs["manual_capture"]["steps"][0]["shell"], "bash")
        self.assertEqual(self.jobs["manual_capture"]["steps"][0]["working-directory"], ".")
        self.assertNotIn("urllib.request", self.script)
        self.assertEqual((ROOT / ".gitcode/workflows/torchtitan_forward_manual.yml").read_text(), self.content)
        self.assertEqual(
            (ROOT / ".gitcode/workflows/torchtitan_bootstrap_diagnostic.yml").read_text(), builder.render_diagnostic()
        )

    def test_credentials_remain_confined_to_capture_and_forward(self):
        for name, job in self.jobs.items():
            env = job["steps"][0]["env"]
            self.assertEqual("ATOMGIT_TOKEN" in env, name in {"manual_capture", "manual_forward"})
            self.assertEqual("LITE_ACTIONS_GITHUB_TOKEN" in env, name == "manual_forward")

    def test_run_identity_and_case_dependencies_are_unchanged(self):
        for name in ("manual_capture", "manual_forward", "case_os_result", "case_8p_result", "case_16p_result"):
            self.assertEqual(self.jobs[name]["steps"][0]["env"]["CONTROL_SHA"], "${{ atomgit.sha }}")
        self.assertEqual(self.jobs["manual_forward"]["needs"], ["manual_capture"])
        for name in ("case_os_result", "case_8p_result", "case_16p_result"):
            self.assertEqual(self.jobs[name]["needs"], ["manual_capture", "manual_forward"])
        self.assertEqual(
            self.jobs["aggregate_gate"]["needs"],
            ["manual_capture", "manual_forward", "case_os_result", "case_8p_result", "case_16p_result"],
        )
        self.assertEqual(self.document["on"]["workflow_dispatch"]["inputs"]["test_case"]["default"], "worker_os_blue")

    def test_real_embedded_sdk_aggregate_passes_without_network(self):
        result = self.run_script(
            self.script,
            LITE_GITCODE_MANUAL_PHASE="aggregate",
            CAPTURE_RESULT="success",
            FORWARD_RESULT="success",
            FORWARD_GATE="success",
            FORWARD_GATE_REASON="OK",
            OS_RESULT="success",
            EIGHT_P_RESULT="success",
            SIXTEEN_P_RESULT="success",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["aggregate_status"], "PASS")
        self.assertEqual(json.loads(result.stdout)["current_authority"], "NOT_PROVIDED")

    def test_real_embedded_sdk_aggregate_rejects_missing_result(self):
        result = self.run_script(
            self.script,
            LITE_GITCODE_MANUAL_PHASE="aggregate",
            CAPTURE_RESULT="success",
            FORWARD_RESULT="success",
            FORWARD_GATE="success",
            FORWARD_GATE_REASON="OK",
            OS_RESULT="success",
            EIGHT_P_RESULT="success",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("PASS", result.stdout)
        self.assertIn("16P_CASE_DISPLAY_FAILED", result.stderr)

    def test_capture_without_manual_identity_fails_closed_offline(self):
        result = self.run_script(self.script, LITE_GITCODE_MANUAL_PHASE="capture")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MANUAL_FORWARD_FAILED:MANUAL_ONLY", result.stderr)
        self.assertNotIn("LITE_GITCODE_MANUAL_CAPTURE_V1", result.stdout)

    def test_result_without_case_identity_fails_closed_offline(self):
        result = self.run_script(self.script, LITE_GITCODE_MANUAL_PHASE="result")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MANUAL_FORWARD_FAILED:RESULT_CASE_REQUIRED", result.stderr)
        self.assertNotIn("PASS", result.stdout)

    def test_diagnostic_verifies_bundle_offline(self):
        result = self.run_script(self.script, LITE_GITCODE_BOOTSTRAP_DIAGNOSTIC_ONLY="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MANUAL_SDK_BOOTSTRAP_OK phase=sdk_entry", result.stdout)

    def test_corrupted_compressed_bundle_is_rejected(self):
        encoded = builder.BUNDLE.read_text().strip()
        changed = ("A" if encoded[0] != "A" else "B") + encoded[1:]
        result = self.run_script(self.script.replace(encoded, changed), LITE_GITCODE_BOOTSTRAP_DIAGNOSTIC_ONLY="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("error=bundle_digest_mismatch", result.stderr)
        self.assertNotIn("BOOTSTRAP_OK", result.stdout)

    def test_unexpected_file_fails_manifest_even_with_valid_digests(self):
        encoded = builder.BUNDLE.read_text().strip()
        files = json.loads(zlib.decompress(base64.b64decode(encoded)))
        files["ci_core/unexpected.py"] = "pass\n"
        raw = json.dumps(files, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        compressed = zlib.compress(raw, 9)
        script = self.script.replace(encoded, base64.b64encode(compressed).decode())
        script = script.replace(builder.BUNDLE_SHA256, hashlib.sha256(compressed).hexdigest())
        script = script.replace(builder.RAW_SHA256, hashlib.sha256(raw).hexdigest())
        result = self.run_script(script, LITE_GITCODE_BOOTSTRAP_DIAGNOSTIC_ONLY="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("error=bundle_manifest_invalid", result.stderr)

    def test_duplicate_json_name_fails_manifest(self):
        encoded = builder.BUNDLE.read_text().strip()
        raw = zlib.decompress(base64.b64decode(encoded))
        raw = raw[:-1] + bytes([44, 34]) + b"ci_core/__init__.py" + bytes([34, 58, 34, 34, 125])
        compressed = zlib.compress(raw, 9)
        script = self.script.replace(encoded, base64.b64encode(compressed).decode())
        script = script.replace(builder.BUNDLE_SHA256, hashlib.sha256(compressed).hexdigest())
        script = script.replace(builder.RAW_SHA256, hashlib.sha256(raw).hexdigest())
        result = self.run_script(script, LITE_GITCODE_BOOTSTRAP_DIAGNOSTIC_ONLY="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("error=bundle_manifest_invalid", result.stderr)

    def test_generator_rejects_modified_bundle(self):
        with (
            patch.object(builder, "BUNDLE_SHA256", "0" * 64),
            self.assertRaisesRegex(ValueError, "SDK_BUNDLE_DIGEST_MISMATCH"),
        ):
            builder.scripts()


if __name__ == "__main__":
    unittest.main()
