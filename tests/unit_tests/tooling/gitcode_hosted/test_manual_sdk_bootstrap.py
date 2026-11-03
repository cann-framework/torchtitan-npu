"""Contract and real subprocess checks for the thin SDK-bootstrap workflow."""

import contextlib
import hashlib
import io
import os
import signal
import subprocess
import sys
import tarfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = ROOT / ".gitcode/scripts"
sys.path.insert(0, str(SCRIPTS))
import build_hosted_manual_workflow as builder


class ManualSdkBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.content, self.capture_sha = builder.render()
        self.document = yaml.load(self.content, Loader=yaml.BaseLoader)
        self.jobs = self.document["stages"]["manual"]["jobs"]

    def test_six_jobs_use_same_small_pinned_sdk_bootstrap(self):
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
        self.assertRegex(builder.SDK_SHA, r"^[0-9a-f]{40}$")
        self.assertRegex(builder.SDK_ARCHIVE_SHA256, r"^[0-9a-f]{64}$")
        scripts = [job["steps"][0]["run"] for job in self.jobs.values()]
        self.assertEqual(len(set(scripts)), 1)
        self.assertLess(len(scripts[0].encode()), 16384)
        self.assertEqual(hashlib.sha256(scripts[0].encode()).hexdigest(), self.capture_sha)
        self.assertEqual(
            self.jobs["manual_forward"]["steps"][0]["env"]["LITE_GITCODE_CAPTURE_SCRIPT_SHA256"],
            self.capture_sha,
        )
        self.assertEqual(self.jobs["manual_capture"]["steps"][0]["name"], "Capture manual request")
        self.assertEqual(self.jobs["manual_capture"]["steps"][0]["shell"], "bash")
        self.assertEqual(self.jobs["manual_capture"]["steps"][0]["working-directory"], ".")
        for job in self.jobs.values():
            self.assertLessEqual(len(job["steps"][0]["env"]), 20)
            self.assertIn("python3 -I -B - <<'PY'", job["steps"][0]["run"])
            self.assertNotIn("hosted_manual_common", job["steps"][0]["run"])
            self.assertNotIn("def forward(", job["steps"][0]["run"])

    def test_credentials_are_confined_to_capture_and_forward_jobs(self):
        for name, job in self.jobs.items():
            env = job["steps"][0]["env"]
            self.assertEqual("ATOMGIT_TOKEN" in env, name in {"manual_capture", "manual_forward"})
            self.assertEqual("LITE_ACTIONS_GITHUB_TOKEN" in env, name == "manual_forward")
        for name in ("case_os_result", "case_8p_result", "case_16p_result", "aggregate_gate"):
            env = self.jobs[name]["steps"][0]["env"]
            self.assertNotIn("ATOMGIT_TOKEN", env)
            self.assertNotIn("LITE_ACTIONS_GITHUB_TOKEN", env)

    def test_workflow_keeps_os_only_default_and_explicit_three_case_choice(self):
        inputs = self.document["on"]["workflow_dispatch"]["inputs"]
        self.assertEqual(inputs["test_case"]["default"], "worker_os_blue")
        self.assertEqual(inputs["test_case"]["options"], ["worker_os_blue", "os_8p_16p"])
        self.assertEqual(self.jobs["manual_forward"]["needs"], ["manual_capture"])
        for name in ("case_os_result", "case_8p_result", "case_16p_result"):
            self.assertEqual(self.jobs[name]["needs"], ["manual_capture", "manual_forward"])
        self.assertEqual(
            self.jobs["aggregate_gate"]["needs"],
            ["manual_capture", "manual_forward", "case_os_result", "case_8p_result", "case_16p_result"],
        )

    def _run_synthetic_archive(self, members, expected_files):
        sdk_sha = "a" * 40
        archive_stream = io.BytesIO()
        with tarfile.open(fileobj=archive_stream, mode="w:gz") as bundle:
            for name, kind, payload in members:
                info = tarfile.TarInfo(f"lite-actions-{sdk_sha}/{name}")
                if kind == "dir":
                    info.type = tarfile.DIRTYPE
                    bundle.addfile(info)
                else:
                    info.size = len(payload)
                    bundle.addfile(info, io.BytesIO(payload))
        archive = archive_stream.getvalue()
        source = (ROOT / ".gitcode/workflow-templates/manual_sdk_bootstrap.py").read_text()
        source = source.replace("__SDK_SHA__", sdk_sha)
        source = source.replace("__SDK_ARCHIVE_SHA256__", hashlib.sha256(archive).hexdigest())
        source = source.replace("__EXPECTED_FILES__", repr(tuple(sorted(expected_files))))

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size=-1):
                return archive if size < 0 else archive[:size]

        stdout, stderr = io.StringIO(), io.StringIO()
        before = list(sys.path)
        with (
            patch("urllib.request.urlopen", return_value=Response()),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            try:
                exec(compile(source, "manual_sdk_bootstrap.py", "exec"), {"__name__": "__main__"})
            except SystemExit as error:
                code = error.code
            else:
                code = 0
        for entry in list(sys.path):
            if entry not in before:
                sys.path.remove(entry)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_duplicate_normalized_archive_member_is_rejected(self):
        expected = {"__init__.py", "gitcode_manual/__init__.py", "gitcode_manual/cli.py"}
        members = [
            ("ci_core/__init__.py", "file", b""),
            ("ci_core/gitcode_manual/__init__.py", "file", b""),
            ("ci_core/gitcode_manual/cli.py", "file", b"def main(): return 0\n"),
            ("ci_core/gitcode_manual/cli.py", "file", b"def main(): return 1\n"),
        ]
        code, stdout, stderr = self._run_synthetic_archive(members, expected)
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertRegex(
            stderr.strip(),
            r"^MANUAL_SDK_BOOTSTRAP_FAILED phase=manifest error=archive_member_duplicate "
            r"elapsed_ms=\d+ download_ms=\d+ total_ms=\d+$",
        )

    def test_file_directory_prefix_collision_is_rejected(self):
        expected = {"__init__.py", "gitcode_manual", "gitcode_manual/cli.py"}
        members = [
            ("ci_core/__init__.py", "file", b""),
            ("ci_core/gitcode_manual", "file", b"not a directory"),
            ("ci_core/gitcode_manual/cli.py", "file", b"def main(): return 0\n"),
        ]
        code, stdout, stderr = self._run_synthetic_archive(members, expected)
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertRegex(
            stderr.strip(),
            r"^MANUAL_SDK_BOOTSTRAP_FAILED phase=manifest error=archive_manifest_invalid "
            r"elapsed_ms=\d+ download_ms=\d+ total_ms=\d+$",
        )

    def test_missing_or_unexpected_sdk_file_is_rejected(self):
        expected = {"__init__.py", "gitcode_manual/__init__.py", "gitcode_manual/cli.py"}
        members = [
            ("ci_core/__init__.py", "file", b""),
            ("ci_core/gitcode_manual/__init__.py", "file", b""),
            ("ci_core/gitcode_manual/cli.py", "file", b"def main(): return 0\n"),
            ("ci_core/extra.py", "file", b"pass\n"),
        ]
        code, stdout, stderr = self._run_synthetic_archive(members, expected)
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertRegex(
            stderr.strip(),
            r"^MANUAL_SDK_BOOTSTRAP_FAILED phase=manifest error=archive_member_unexpected "
            r"elapsed_ms=\d+ download_ms=\d+ total_ms=\d+$",
        )

    def test_download_retries_timeout_and_returns_archive(self):
        source = (ROOT / ".gitcode/workflow-templates/manual_sdk_bootstrap.py").read_text()
        source = source.replace("__EXPECTED_FILES__", "()")
        namespace = {"__name__": "bootstrap_test"}
        exec(compile(source, "manual_sdk_bootstrap.py", "exec"), namespace)
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"archive"
        with patch("urllib.request.urlopen", side_effect=[TimeoutError(), response]) as opener:
            result = namespace["download"](urllib.request.Request("https://example.com"))
        self.assertEqual(result, b"archive")
        self.assertEqual(opener.call_count, 2)

    def test_download_does_not_retry_permanent_http_error(self):
        source = (ROOT / ".gitcode/workflow-templates/manual_sdk_bootstrap.py").read_text()
        source = source.replace("__EXPECTED_FILES__", "()")
        namespace = {"__name__": "bootstrap_test"}
        exec(compile(source, "manual_sdk_bootstrap.py", "exec"), namespace)
        failure = urllib.error.HTTPError("https://example.com", 404, "missing", {}, None)
        stderr = io.StringIO()
        with (
            patch("urllib.request.urlopen", side_effect=failure) as opener,
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit),
        ):
            namespace["download"](urllib.request.Request("https://example.com"))
        self.assertEqual(opener.call_count, 1)
        self.assertIn("error=http_error", stderr.getvalue())

    def test_download_deadline_stops_blocked_operation(self):
        source = (ROOT / ".gitcode/workflow-templates/manual_sdk_bootstrap.py").read_text()
        namespace = {"__name__": "bootstrap_test"}
        exec(compile(source.replace("__EXPECTED_FILES__", "()"), "manual_sdk_bootstrap.py", "exec"), namespace)

        def blocked(*_args, **_kwargs):
            os.kill(os.getpid(), signal.SIGALRM)

        before = signal.getsignal(signal.SIGALRM)
        stderr = io.StringIO()
        with (
            patch("urllib.request.urlopen", side_effect=blocked) as opener,
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit),
        ):
            namespace["download"](urllib.request.Request("https://example.com"))
        self.assertEqual(opener.call_count, 1)
        self.assertIn("error=download_timeout", stderr.getvalue())
        self.assertEqual(signal.getsignal(signal.SIGALRM), before)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))

    def test_real_isolated_sdk_archive_bootstrap_executes_aggregate_without_credentials(self):
        workflow = ROOT / ".gitcode/workflows/torchtitan_forward_manual.yml"
        self.assertEqual(workflow.read_text(), self.content)
        script = self.jobs["aggregate_gate"]["steps"][0]["run"]
        environment = {
            **os.environ,
            "LITE_GITCODE_MANUAL_PHASE": "aggregate",
            "CAPTURE_RESULT": "success",
            "FORWARD_RESULT": "success",
            "FORWARD_GATE": "success",
            "FORWARD_GATE_REASON": "OK",
            "OS_RESULT": "success",
            "EIGHT_P_RESULT": "success",
            "SIXTEEN_P_RESULT": "success",
        }
        environment.pop("ATOMGIT_TOKEN", None)
        environment.pop("LITE_ACTIONS_GITHUB_TOKEN", None)
        result = subprocess.run(
            ["bash", "-c", script],
            env=environment,
            text=True,
            capture_output=True,
            timeout=90,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"aggregate_status": "PASS"', result.stdout)
        self.assertIn('"current_authority": "NOT_PROVIDED"', result.stdout)
        self.assertNotIn("ATOMGIT_TOKEN", result.stdout + result.stderr)
        self.assertNotIn("LITE_ACTIONS_GITHUB_TOKEN", result.stdout + result.stderr)
        self.assertNotRegex(result.stdout + result.stderr, r"https?://[^ ]+\?")


if __name__ == "__main__":
    unittest.main()
