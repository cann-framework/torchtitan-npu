"""Manual hosted boundary tests, synthetic APIs only; no live dispatch or devices."""

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml

ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = ROOT / ".gitcode/scripts"
sys.path.insert(0, str(SCRIPTS))
import build_hosted_manual_workflow as builder
import hosted_manual_capture as capture
import hosted_manual_common as common
import hosted_manual_dispatch as dispatch

S = "a" * 40
H = "b" * 40


class FakeAPI:
    def __init__(self):
        self.calls = []
        self.run = {
            "workflow_run_id": "run1",
            "file_path": common.GC_WORKFLOW,
            "workflow_id": "wf1",
            "head_sha": S,
            "head_branch": "master",
            "event": "Manual",
            "attempt": 1,
            "exist_in_default_branch": True,
            "status": "RUNNING",
            "start_time": 990000,
        }
        self.pr = {
            "number": 2,
            "state": "open",
            "merged_at": None,
            "user": {"login": "depeng1994"},
            "head": {"ref": "feature", "sha": H, "repo": {"full_name": common.GC_REPO}},
            "base": {"ref": "master", "sha": "c" * 40, "repo": {"full_name": common.GC_REPO}},
        }
        script = builder.scripts()[0]
        self.job = {
            "id": "job1",
            "identifier": "manual_capture",
            "status": "COMPLETED",
            "is_shadow": False,
            "steps": [
                {
                    "id": "step1",
                    "identifier": "1",
                    "name": "Capture manual request",
                    "job_run_id": "job1",
                    "status": "COMPLETED",
                    "task": "official_shell",
                    "start_time": 995000,
                    "end_time": 1001000,
                    "inputs": [
                        {"key": "script", "value": script},
                        {"key": "shell", "value": "bash"},
                        {"key": "working-directory", "value": "."},
                    ],
                }
            ],
        }
        self.log = ""
        self.gh_run = None
        self.posts = 0
        self.lose_response = False
        self.drop_dispatch = False
        self.completed_jobs = [
            {"name": name, "status": "completed", "conclusion": "success", "run_id": 42, "run_attempt": 1}
            for name in ("prepare-gitcode", "wait-gitcode", "Lite-Actions-GitCode-Smoke")
        ]

    def gc(self, suffix="", *, version=5, body=None):
        self.calls.append(("gc", suffix, body))
        if suffix == "":
            return {"id": 9075802, "full_name": common.GC_REPO, "default_branch": "master"}
        if suffix == "/branches/master":
            return {"name": "master", "commit": {"id": S}}
        if suffix == "/branches/feature":
            return {"name": "feature", "commit": {"id": H}}
        if suffix == "/pulls/2":
            return copy.deepcopy(self.pr)
        if suffix == "/actions/runs/run1":
            return copy.deepcopy(self.run)
        if suffix == "/actions/runs/run1/jobs":
            return {"total_count": 1, "jobs": [copy.deepcopy(self.job)]}
        if suffix == "/actions/runs/run1/jobs/job1":
            return copy.deepcopy(self.job)
        if suffix == "/actions/runs/run1/jobs/job1/logs":
            return {"start_offset": 0, "end_offset": len(self.log), "has_more": False, "log": self.log}
        raise AssertionError(suffix)

    def gh(self, suffix, *, body=None, dispatch=False):
        self.calls.append(("gh", suffix, body))
        if suffix == "":
            return {"id": common.GH_ID, "full_name": common.GH_REPO, "default_branch": "master"}
        if suffix == "/branches/master":
            return {"name": "master", "commit": {"sha": S}}
        if suffix == f"/actions/workflows/{common.GH_WORKFLOW_ID}":
            return {"id": common.GH_WORKFLOW_ID, "path": common.GH_WORKFLOW, "state": "active"}
        if suffix.startswith(f"/actions/workflows/{common.GH_WORKFLOW_ID}/runs?"):
            rows = [] if self.gh_run is None else [copy.deepcopy(self.gh_run)]
            return {"total_count": len(rows), "workflow_runs": rows}
        if suffix.endswith("/dispatches"):
            assert dispatch
            self.posts += 1
            intent = json.loads(body["inputs"]["gitcode_request"])
            if not self.drop_dispatch:
                self.gh_run = {
                    "id": 42,
                    "run_attempt": 1,
                    "event": "workflow_dispatch",
                    "head_sha": S,
                    "head_branch": "master",
                    "workflow_id": common.GH_WORKFLOW_ID,
                    "display_title": "lite-gitcode:" + intent["bridge"]["bridge_nonce"],
                    "repository": {"id": common.GH_ID, "full_name": common.GH_REPO},
                    "actor": {"login": common.ALLOWED_ACTOR},
                    "path": common.GH_WORKFLOW,
                    "status": "completed",
                    "conclusion": "success",
                }
            if self.lose_response:
                raise TimeoutError()
            return {
                "workflow_run_id": 42,
                "run_url": f"https://api.github.com/repos/{common.GH_REPO}/actions/runs/42",
                "html_url": f"https://github.com/{common.GH_REPO}/actions/runs/42",
            }
        if suffix == "/actions/runs/42":
            return copy.deepcopy(self.gh_run)
        if suffix.startswith("/actions/runs/42/jobs?"):
            return {"total_count": len(self.completed_jobs), "jobs": copy.deepcopy(self.completed_jobs)}
        raise AssertionError(suffix)


class HostedManualTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeAPI()
        self.env = {
            "EVENT_NAME": "Manual",
            "ATOMGIT_EVENT_NAME": "Manual",
            "RUN_ATTEMPT": "1",
            "ATOMGIT_RUN_ID": "run1",
            "REPOSITORY": common.GC_REPO,
            "REPOSITORY_ID": common.GC_ID,
            "REF": "refs/heads/master",
            "ACTOR": "depeng1994",
            "CONTROL_SHA": S,
            "PR_NUMBER": "2",
            "TEST_CASE": "worker_os_blue",
        }
        self.now = 1000
        self.patch = patch.object(dispatch, "CAPTURE_SCRIPT_SHA256", builder.scripts()[2])
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def emit(self):
        self.api.log = (
            capture.capture(self.env, self.api, clock=lambda: self.now, nonce_factory=lambda: "n" * 32) + "\n"
        )
        return dispatch.retrieve_intent(self.api, self.env, clock=lambda: self.now)

    def test_two_job_generated_workflow_is_exact_and_secret_is_dispatch_only(self):
        content, digest = builder.render()
        self.assertEqual(builder.OUTPUT.read_text(), content)
        self.assertTrue(all(line == line.rstrip() for line in content.splitlines()))
        doc = yaml.load(content, Loader=yaml.BaseLoader)
        self.assertEqual(set(doc["on"]), {"workflow_dispatch"})
        self.assertEqual(doc["permissions"]["action"], "read")
        jobs = doc["stages"]["manual"]["jobs"]
        self.assertEqual(set(jobs), {"manual_capture", "manual_forward"})
        self.assertEqual(jobs["manual_forward"]["needs"], ["manual_capture"])
        self.assertEqual(len(jobs["manual_capture"]["steps"]), 1)
        for name, job in jobs.items():
            step = job["steps"][0]
            self.assertLessEqual(len(step["env"]), 20)
            self.assertNotIn("uses", step)
            self.assertNotIn("CHECKS", str(step["env"]))
            self.assertEqual("LITE_ACTIONS_GITHUB_TOKEN" in step["env"], name == "manual_forward")
            self.assertIn("python3 -I -B", step["run"])
            self.assertNotIn("from hosted_manual_common", step["run"])
        self.assertEqual(hashlib.sha256(jobs["manual_capture"]["steps"][0]["run"].encode()).hexdigest(), digest)
        capture_script, forward_script, _ = builder.scripts()
        self.assertEqual(jobs["manual_capture"]["steps"][0]["run"], capture_script)
        self.assertEqual(jobs["manual_forward"]["steps"][0]["run"], forward_script)

    def test_capture_matches_real_sdk_emission_and_verifier(self):
        from ci_core.gitcode_manual_capture import ManualCapturePolicy, encode_capture, verify_manual_capture

        intent = self.emit()
        self.assertEqual(self.api.log.strip(), encode_capture(intent, self.now))
        job = copy.deepcopy(self.api.job)
        step = job["steps"][0]
        step.update(script_sha256=builder.scripts()[2], shell="bash", working_directory=".")
        del step["inputs"]
        client = SimpleNamespace(
            repo_id=common.GC_ID,
            action_run=lambda _: copy.deepcopy(self.api.run),
            action_jobs=lambda _: [copy.deepcopy(job)],
            action_job=lambda *a: copy.deepcopy(job),
            action_step_logs=lambda *a, **k: {"complete": True, "log": self.api.log},
        )
        policy = ManualCapturePolicy(
            control=SimpleNamespace(gitcode_repo_id=common.GC_ID, control_sha=S, enabled=True),
            workflow_id="wf1",
            workflow_path=common.GC_WORKFLOW,
            job_identifier="manual_capture",
            step_identifier="1",
            step_name="Capture manual request",
            script_sha256=builder.scripts()[2],
            shell="bash",
            working_directory=".",
            enabled=True,
        )
        proof = verify_manual_capture(intent, policy, client, now=self.now)
        self.assertEqual(proof.intent, intent)
        self.assertEqual(proof.capture_kind, "manual_api")
        self.assertEqual(intent["pr"]["base_sha"], S)  # never PR's stale base.sha

    def test_manual_only_attempt_repo_actor_and_case_boundaries(self):
        for field, value in (
            ("EVENT_NAME", "MR"),
            ("ATOMGIT_EVENT_NAME", "MR"),
            ("RUN_ATTEMPT", "2"),
            ("REPOSITORY", "cann/torchtitan-npu"),
            ("ACTOR", "other"),
            ("TEST_CASE", "dispatch_smoke_16p"),
            ("TEST_CASE", "dsv4_flash_a3_8p_example"),
            ("PR_NUMBER", "2;echo leak"),
        ):
            with self.subTest(field=field, value=value), self.assertRaises(common.ManualForwardError):
                capture.capture({**self.env, field: value}, self.api)

    def test_capture_rejects_external_source_and_unstable_head(self):
        self.api.pr["head"]["repo"]["full_name"] = "other/fork"
        with self.assertRaisesRegex(common.ManualForwardError, "PERSONAL_SOURCE_TARGET"):
            self.emit()
        self.api.pr["head"]["repo"]["full_name"] = common.GC_REPO
        self.api.pr["head"]["sha"] = "c" * 40
        with self.assertRaisesRegex(common.ManualForwardError, "CHANGED_DURING"):
            self.emit()

    def platform_steps(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/gitcode_b472_platform_steps.json").read_text())
        steps = []
        for row in fixture["steps"]:
            step = dict.fromkeys(row["keys"])
            step.update({key: value for key, value in row.items() if key not in {"keys", "inputs_type", "env_type"}})
            step["job_run_id"] = "job1"
            step["env"] = None
            if step["task"] == "official_shell":
                step["inputs"] = copy.deepcopy(self.api.job["steps"][0]["inputs"])
                step["start_time"], step["end_time"] = 995000, 1001000
            steps.append(step)
        return steps

    def test_real_platform_pre_user_post_envelope_selects_only_fixed_user_step(self):
        expected = self.emit()
        self.api.job["steps"] = self.platform_steps()
        self.assertEqual(dispatch.retrieve_intent(self.api, self.env, clock=lambda: self.now), expected)

    def test_platform_step_envelope_rejects_payload_reorder_extra_and_wrong_owner(self):
        self.emit()
        original = self.platform_steps()
        for field, value in (
            ("inputs", [{"key": "script", "value": "evil"}]),
            ("inputs", []),
            ("env", []),
            ("condition", "true"),
            ("task", "official_shell"),
            ("job_run_id", "other"),
            ("name", "custom"),
            ("identifier", "0"),
            ("status", "FAILED"),
            ("continue-on-error", True),
            ("continue-on-error", 0),
            ("continue-on-error", "false"),
            ("script", "evil"),
        ):
            self.api.job["steps"] = copy.deepcopy(original)
            self.api.job["steps"][0][field] = value
            with self.subTest(field=field), self.assertRaises(common.ManualForwardError):
                dispatch.retrieve_intent(self.api, self.env, clock=lambda: self.now)
        for missing in ("identifier", "inputs", "env", "condition", "icon_url"):
            self.api.job["steps"] = copy.deepcopy(original)
            del self.api.job["steps"][0][missing]
            with self.subTest(missing=missing), self.assertRaises(common.ManualForwardError):
                dispatch.retrieve_intent(self.api, self.env, clock=lambda: self.now)
        for rows in (original[::-1], [*original, original[0]], [original[0], original[1], original[1]]):
            self.api.job["steps"] = rows
            with self.assertRaises(common.ManualForwardError):
                dispatch.retrieve_intent(self.api, self.env, clock=lambda: self.now)

    def test_user_step_continue_on_error_requires_exact_null_or_false(self):
        self.emit()
        for value in (0, "false", True):
            self.api.job["steps"][0]["continue-on-error"] = value
            with self.subTest(value=value), self.assertRaisesRegex(common.ManualForwardError, "IDENTITY_MISMATCH"):
                dispatch.retrieve_intent(self.api, self.env, clock=lambda: self.now)

    def test_duplicate_marker_extra_step_or_command_change_rejected(self):
        self.emit()
        self.api.log *= 2
        with self.assertRaisesRegex(common.ManualForwardError, "DUPLICATE"):
            dispatch.retrieve_intent(self.api, self.env, clock=lambda: self.now)
        self.emit()
        self.api.job["steps"].append(copy.deepcopy(self.api.job["steps"][0]))
        with self.assertRaisesRegex(common.ManualForwardError, "SINGLE_STEP"):
            dispatch.retrieve_intent(self.api, self.env, clock=lambda: self.now)
        self.api.job["steps"].pop()
        self.api.job["steps"][0]["inputs"][0]["value"] += "#changed"
        with self.assertRaisesRegex(common.ManualForwardError, "NOT_PINNED"):
            dispatch.retrieve_intent(self.api, self.env, clock=lambda: self.now)

    def test_realistic_capture_dispatch_wait_uses_one_post_and_exact_jobs(self):
        intent = self.emit()
        result = dispatch.forward(self.api, intent, clock=lambda: self.now)
        self.assertEqual(result["historical_execution_result"], "PASS")
        self.assertEqual(result["current_authority"], "NOT_PROVIDED")
        self.assertIs(result["not_current_merge_authorization"], True)
        self.assertNotIn("test_result", result)
        self.assertEqual(self.api.posts, 1)
        self.assertEqual(result["merge_gate"], "NOT_PROVIDED")
        # Same immutable capture recovers same run rather than another POST.
        self.assertEqual(dispatch.forward(self.api, intent, clock=lambda: self.now), result)
        self.assertEqual(self.api.posts, 1)

    def test_exact_platform_timestamp_wrapper_is_supported_once_only(self):
        intent = self.emit()
        original = self.api.log
        stamp = "[2026-11-02 09:00:00.123+00:00] "
        self.api.log = stamp + original
        self.assertEqual(dispatch.retrieve_intent(self.api, self.env, clock=lambda: self.now), intent)
        for prefix in (stamp + stamp, "unexpected ", "unexpected " + stamp):
            self.api.log = prefix + original
            with self.assertRaisesRegex(common.ManualForwardError, "MISSING_OR_DUPLICATE"):
                dispatch.retrieve_intent(self.api, self.env, clock=lambda: self.now)
        self.api.log = stamp + original + original
        with self.assertRaisesRegex(common.ManualForwardError, "MISSING_OR_DUPLICATE"):
            dispatch.retrieve_intent(self.api, self.env, clock=lambda: self.now)

    def test_reassigned_github_repository_is_rejected_before_dispatch(self):
        intent = self.emit()
        original = self.api.gh

        def changed(suffix, **kwargs):
            result = original(suffix, **kwargs)
            if suffix == "":
                result["id"] += 1
            return result

        self.api.gh = changed
        with self.assertRaises(common.ManualForwardError):
            dispatch.forward(self.api, intent, clock=lambda: self.now)
        self.assertEqual(self.api.posts, 0)

    def test_slow_nonce_lookup_cannot_dispatch_expired_capture(self):
        intent = self.emit()
        original = self.api.gh

        def delayed(suffix, **kwargs):
            result = original(suffix, **kwargs)
            if "/runs?" in suffix:
                self.now += 301
            return result

        self.api.gh = delayed
        with self.assertRaisesRegex(common.ManualForwardError, "EXPIRED_BEFORE_DISPATCH"):
            dispatch.forward(self.api, intent, clock=lambda: self.now)
        self.assertEqual(self.api.posts, 0)

    def test_lost_dispatch_response_recovers_nonce_without_repost(self):
        intent = self.emit()
        self.api.lose_response = True
        self.assertEqual(dispatch.forward(self.api, intent, clock=lambda: self.now)["github_run_id"], 42)
        self.assertEqual(self.api.posts, 1)

    def test_unknown_dispatch_does_not_retry_post(self):
        intent = self.emit()
        self.api.lose_response = self.api.drop_dispatch = True
        with self.assertRaisesRegex(common.ManualForwardError, "UNKNOWN_NO_RETRY"):
            dispatch.forward(self.api, intent, clock=lambda: self.now, sleep=lambda _: None)
        self.assertEqual(self.api.posts, 1)

    def test_changed_actor_or_skipped_result_never_returns_pass(self):
        intent = self.emit()
        dispatch.forward(self.api, intent, clock=lambda: self.now)
        self.api.gh_run["actor"] = {"login": "other"}
        with self.assertRaisesRegex(common.ManualForwardError, "OWNER_MISMATCH"):
            dispatch.forward(self.api, intent, clock=lambda: self.now)
        self.api.gh_run["actor"] = {"login": "depeng1994"}
        self.api.completed_jobs[-1]["conclusion"] = "skipped"
        with self.assertRaisesRegex(common.ManualForwardError, "NOT_PASS"):
            dispatch.forward(self.api, intent, clock=lambda: self.now)

    def test_public_github_reads_never_receive_dispatch_credential(self):
        api = common.API({"ATOMGIT_TOKEN": "synthetic-gitcode", "LITE_ACTIONS_GITHUB_TOKEN": "synthetic-github"})
        response = Mock()
        response.__enter__ = lambda _: response
        response.__exit__ = lambda *a: False
        response.read.return_value = b"{}"
        api.opener.open = Mock(return_value=response)
        api.gh("/branches/master")
        request = api.opener.open.call_args.args[0]
        self.assertNotIn("Authorization", dict(request.header_items()))
        api.gh(f"/actions/workflows/{common.GH_WORKFLOW_ID}/dispatches", body={}, dispatch=True)
        self.assertEqual(api.opener.open.call_args.args[0].get_header("Authorization"), "Bearer synthetic-github")
        with self.assertRaisesRegex(common.ManualForwardError, "CANNOT_WRITE"):
            api.gh("/issues", body={})

    def test_generated_inline_programs_execute_same_boundaries_without_source_import(self):
        for script, name in zip(builder.scripts()[:2], ("capture", "forward"), strict=True):
            body = script.split("<<'PY'\n", 1)[1].rsplit("\nPY\n", 1)[0]
            namespace = {"__name__": "inline_test"}
            exec(compile(body, "<reviewed-inline>", "exec"), namespace)
            self.assertTrue(callable(namespace[name]))
            if name == "capture":
                line = namespace[name](self.env, self.api, clock=lambda: self.now, nonce_factory=lambda: "n" * 32)
                self.assertEqual(
                    line, capture.capture(self.env, self.api, clock=lambda: self.now, nonce_factory=lambda: "n" * 32)
                )

    def test_slow_capture_evidence_read_cannot_cross_freshness_window(self):
        self.emit()
        original = self.api.gh
        calls = [0]

        def slow(suffix, **kwargs):
            result = original(suffix, **kwargs)
            if suffix.endswith(str(common.GH_WORKFLOW_ID)):
                calls[0] += 1
                self.now = 1301
            return result

        self.api.gh = slow
        with self.assertRaisesRegex(common.ManualForwardError, "EXPIRED_DURING_READ"):
            dispatch.retrieve_intent(self.api, self.env, clock=lambda: self.now)
        self.assertEqual(self.api.posts, 0)

    def test_slow_final_run_read_cannot_return_pass_after_deadline(self):
        intent = self.emit()
        original = self.api.gh

        def slow(suffix, **kwargs):
            result = original(suffix, **kwargs)
            if suffix == "/actions/runs/42":
                self.now = intent["deadline"]
            return result

        self.api.gh = slow
        with self.assertRaisesRegex(common.ManualForwardError, "DEADLINE_EXCEEDED"):
            dispatch.forward(self.api, intent, clock=lambda: self.now)

    def test_changed_completed_capture_job_is_rejected(self):
        self.emit()
        original = self.api.gc
        count = [0]

        def changed(suffix="", **kwargs):
            result = original(suffix, **kwargs)
            if suffix == "/actions/runs/run1/jobs/job1":
                count[0] += 1
                result["exec_id"] = "first" if count[0] == 1 else "second"
            return result

        self.api.gc = changed
        with self.assertRaisesRegex(common.ManualForwardError, "EVIDENCE_CHANGED"):
            dispatch.retrieve_intent(self.api, self.env, clock=lambda: self.now)

    def test_ambiguous_nonce_never_dispatches_again(self):
        intent = self.emit()
        dispatch.forward(self.api, intent, clock=lambda: self.now)
        original = self.api.gh

        def duplicate(suffix, **kwargs):
            result = original(suffix, **kwargs)
            if "/runs?" in suffix:
                result["workflow_runs"] *= 2
                result["total_count"] = 2
            return result

        self.api.gh = duplicate
        with self.assertRaisesRegex(common.ManualForwardError, "NONCE_AMBIGUOUS"):
            dispatch.forward(self.api, intent, clock=lambda: self.now)
        self.assertEqual(self.api.posts, 1)

    def test_redirects_and_unapproved_api_targets_are_refused(self):
        self.assertIsNone(common.NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.invalid"))
        api = common.API({"ATOMGIT_TOKEN": "synthetic", "LITE_ACTIONS_GITHUB_TOKEN": "synthetic"})
        api.opener.open = Mock()
        for service, path in (
            ("github", "/repos/other/project/branches/master"),
            ("gitcode", "/api/v8/repos/cann/torchtitan-npu/actions/runs/1"),
            ("gitcode", "//evil.invalid"),
        ):
            with self.subTest(service=service, path=path), self.assertRaises(common.ManualForwardError):
                api.request(service, path)
        with self.assertRaises(common.ManualForwardError):
            api.gc("/pulls/2/comments", body={"body": "not allowed"})
        api.opener.open.assert_not_called()

    def test_http_failure_labels_service_route_without_secret_or_raw_target(self):
        import urllib.error

        api = common.API({"ATOMGIT_TOKEN": "sensitive-token"})
        paths = {
            "/actions/runs/run1": "GITCODE_ACTION_RUN",
            "/actions/runs/run1/jobs": "GITCODE_ACTION_JOBS",
            "/actions/runs/run1/jobs/job1": "GITCODE_ACTION_JOB",
            "/actions/runs/run1/jobs/job1/logs": "GITCODE_ACTION_STEP_LOGS",
        }
        for suffix, label in paths.items():
            api.opener.open = Mock(side_effect=urllib.error.HTTPError("sensitive-url", 403, "sensitive-body", {}, None))
            with self.assertRaisesRegex(common.ManualForwardError, "^API_" + label + "_HTTP_403$"):
                api.gc(suffix, version=8)

    def test_error_output_does_not_dump_api_body_or_credentials(self):
        import io
        from contextlib import redirect_stderr

        output = io.StringIO()
        with redirect_stderr(output):
            common.emit_failure(ValueError("synthetic-secret"))
        self.assertNotIn("synthetic-secret", output.getvalue())
