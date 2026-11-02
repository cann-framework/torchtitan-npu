"""Appended to common code; only this job receives the GH dispatch credential."""

import base64
import hashlib
import json
import os
import re
import time
import urllib.parse

from hosted_manual_common import (
    ALLOWED_ACTOR,
    ALLOWED_CASES,
    API,
    CAPTURE_JOB,
    CAPTURE_NAME,
    CAPTURE_STEP,
    GH_ID,
    GH_REPO,
    GH_WORKFLOW,
    GH_WORKFLOW_ID,
    PREFIX,
    ManualForwardError,
    canonical,
    context,
    emit_failure,
    ident,
    release,
    require,
    run_identity,
    snapshot,
)

# Replaced by the deterministic workflow generator, not by workflow inputs.
CAPTURE_SCRIPT_SHA256 = "__CAPTURE_SCRIPT_SHA256__"


PLATFORM_STEP_KEYS = {
    "business_type",
    "condition",
    "continue-on-error",
    "daily_build_number",
    "end_time",
    "endpoint_ids",
    "env",
    "icon_url",
    "id",
    "identifier",
    "inputs",
    "job_run_id",
    "last_dispatch_id",
    "message",
    "multi_step_editable",
    "name",
    "official_task_version",
    "runtime_attribution",
    "sequence",
    "start_time",
    "status",
    "task",
    "timeout-minutes",
}


def capture_user_step(steps, job_id):
    require(isinstance(steps, list) and len(steps) in (1, 3), "CAPTURE_JOB_NOT_SINGLE_STEP")
    if len(steps) == 1:
        return steps[0]
    require(all(isinstance(step, dict) for step in steps), "CAPTURE_PLATFORM_STEP_INVALID")
    require(len({ident(step.get("id")) for step in steps}) == 3, "CAPTURE_PLATFORM_STEP_IDS_DUPLICATE")
    require(
        all(type(step.get("sequence")) is int for step in steps) and [step["sequence"] for step in steps] == [0, 1, 2],
        "CAPTURE_PLATFORM_STEP_ORDER",
    )
    for step, task, name in (
        (steps[0], "official_pre_step", "初始化步骤"),
        (steps[2], "official_post_step", "后处理步骤"),
    ):
        require(
            set(step) == PLATFORM_STEP_KEYS
            and {"identifier", "inputs", "env", "condition"}.issubset(step)
            and step.get("task") == task
            and step.get("name") == name
            and step.get("identifier") is None
            and step.get("job_run_id") == job_id
            and step.get("status") == "COMPLETED"
            and step.get("inputs") is None
            and step.get("env") is None
            and step.get("condition") is None
            and (step.get("continue-on-error") is None or step.get("continue-on-error") is False),
            "CAPTURE_PLATFORM_STEP_INVALID",
        )
    return steps[1]


def fixed_capture_job(api, run_id):
    listing = api.gc("/actions/runs/" + run_id + "/jobs", version=8)
    jobs = listing.get("jobs")
    require(
        isinstance(jobs, list)
        and type(listing.get("total_count")) is int
        and listing["total_count"] == len(jobs) <= 100,
        "CAPTURE_JOB_LIST_INCOMPLETE",
    )
    matches = [job for job in jobs if job.get("identifier") == CAPTURE_JOB]
    require(len(matches) == 1, "CAPTURE_JOB_AMBIGUOUS")
    job_id = ident(matches[0].get("id"))
    job = api.gc("/actions/runs/" + run_id + "/jobs/" + job_id, version=8)
    require(
        job.get("id") == job_id
        and job.get("identifier") == CAPTURE_JOB
        and job.get("status") == "COMPLETED"
        and job.get("is_shadow") is not True,
        "CAPTURE_JOB_NOT_COMPLETE",
    )
    steps = job.get("steps")
    step = capture_user_step(steps, job_id)
    expected = {
        "identifier": CAPTURE_STEP,
        "name": CAPTURE_NAME,
        "job_run_id": job_id,
        "status": "COMPLETED",
        "task": "official_shell",
    }
    require(
        all(step.get(k) == v for k, v in expected.items())
        and (step.get("continue-on-error") is None or step.get("continue-on-error") is False),
        "CAPTURE_STEP_IDENTITY_MISMATCH",
    )
    inputs = step.get("inputs")
    require(isinstance(inputs, list) and len(inputs) <= 100, "CAPTURE_COMMAND_PROOF_MISSING")
    keys = [row.get("key") for row in inputs]
    require(all(isinstance(key, str) for key in keys) and len(keys) == len(set(keys)), "CAPTURE_COMMAND_DUPLICATE")
    values = {row["key"]: row.get("value") for row in inputs}
    script = values.get("script")
    require(isinstance(script, str) and len(script.encode()) <= 128 * 1024, "CAPTURE_COMMAND_PROOF_MISSING")
    require(
        hashlib.sha256(script.encode()).hexdigest() == CAPTURE_SCRIPT_SHA256
        and values.get("shell") == "bash"
        and values.get("working-directory") in ("", "."),
        "CAPTURE_COMMAND_NOT_PINNED",
    )
    return job, ident(step.get("id"))


def capture_log(api, run_id, job_id, step_id):
    offset, size, texts = 0, 0, []
    for _ in range(10):
        page = api.gc(
            "/actions/runs/" + run_id + "/jobs/" + job_id + "/logs",
            version=8,
            body={"step_id": step_id, "offset": offset, "limit": 1000, "sort": "asc"},
        )
        start, end, more, log = (page.get(k) for k in ("start_offset", "end_offset", "has_more", "log"))
        require(
            type(start) is int
            and type(end) is int
            and type(more) is bool
            and isinstance(log, str)
            and start == offset
            and start <= end
            and not (end == start and (more or log)),
            "CAPTURE_LOG_NOT_CONTIGUOUS",
        )
        size += len(log.encode())
        require(size <= 128 * 1024, "CAPTURE_LOG_TOO_LARGE")
        texts.append(log)
        offset = end
        if not more:
            return "".join(texts)
    raise ManualForwardError("CAPTURE_LOG_INCOMPLETE")


def retrieve_intent(api, environment, *, clock=time.time):
    run_id, control = context(environment)
    run = run_identity(api, run_id, control)
    job, step_id = fixed_capture_job(api, run_id)
    log = capture_log(api, run_id, job["id"], step_id)
    # GitCode v8 adds exactly one timestamp wrapper to each output line.
    lines = [
        re.sub(
            r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}\] ",
            "",
            line,
            count=1,
        )
        for line in log.splitlines()
    ]
    lines = [line for line in lines if line.startswith(PREFIX)]
    require(len(lines) == 1, "CAPTURE_LOG_MISSING_OR_DUPLICATE")
    match = re.fullmatch(re.escape(PREFIX) + r"([0-9a-f]{64}) ([A-Za-z0-9+/=]{1,32768})", lines[0])
    require(match is not None, "CAPTURE_LOG_INVALID")
    raw = base64.b64decode(match[2], validate=True)
    require(len(raw) <= 24 * 1024 and hashlib.sha256(raw).hexdigest() == match[1], "CAPTURE_DIGEST_MISMATCH")
    payload = json.loads(raw)
    require(
        set(payload) == {"schema", "kind", "captured_at", "intent"}
        and payload["schema"] == 1
        and payload["kind"] == "manual-current-observation"
        and canonical(payload) == raw,
        "CAPTURE_NOT_CANONICAL",
    )
    intent = payload["intent"]
    require(
        set(intent)
        == {
            "schema",
            "source_provider",
            "pr",
            "control_repo_id",
            "control_sha",
            "workflow_path",
            "pipeline",
            "selectors",
            "bridge",
            "deadline",
        },
        "CAPTURE_INTENT_SHAPE",
    )
    require(
        intent["schema"] == 1
        and intent["source_provider"] == "gitcode"
        and intent["control_repo_id"] == GH_ID
        and intent["control_sha"] == control
        and intent["workflow_path"] == GH_WORKFLOW
        and intent["pipeline"] == "smoke",
        "CAPTURE_CONTROL_MISMATCH",
    )
    require(
        intent["selectors"] == [{"test_id": name} for name in ALLOWED_CASES],
        "CAPTURE_CASE_NOT_ALLOWED",
    )
    bridge = intent["bridge"]
    require(
        set(bridge)
        == {
            "bridge_event_id",
            "bridge_action_run",
            "bridge_attempt",
            "actor",
            "bridge_nonce",
            "source_snapshot_id",
            "dispatch_intent_id",
            "rerun_intent_id",
            "event_created_at",
        },
        "CAPTURE_BRIDGE_SHAPE",
    )
    nonce = bridge["bridge_nonce"]
    require(isinstance(nonce, str) and re.fullmatch(r"[A-Za-z0-9_-]{16,100}", nonce), "CAPTURE_NONCE_INVALID")
    require(
        bridge["bridge_action_run"] == run_id
        and bridge["bridge_attempt"] == 1
        and bridge["actor"] == ALLOWED_ACTOR
        and bridge["rerun_intent_id"] == "initial"
        and bridge["bridge_event_id"] == "manual:" + run_id
        and bridge["source_snapshot_id"] == "manual-capture:" + nonce
        and bridge["dispatch_intent_id"] == "manual-intent:" + nonce,
        "CAPTURE_BRIDGE_MISMATCH",
    )
    at = payload["captured_at"]
    step = capture_user_step(job["steps"], job["id"])
    start, end, run_start = step.get("start_time"), step.get("end_time"), run.get("start_time")
    require(
        type(at) is int
        and all(type(t) is int for t in (start, end, run_start))
        and 0 < run_start <= start <= end <= (int(clock()) + 30) * 1000,
        "CAPTURE_PLATFORM_TIME_INVALID",
    )
    require(
        start // 1000 - 30 <= at <= end // 1000 + 30 and int(clock()) - at <= 300 and at <= int(clock()) + 30,
        "CAPTURE_AGE_INVALID",
    )
    require(
        bridge["event_created_at"] == at and intent["deadline"] == at + 900 and int(clock()) < intent["deadline"],
        "CAPTURE_DEADLINE_INVALID",
    )
    again, again_id = fixed_capture_job(api, run_id)
    require(
        again == job and again_id == step_id and capture_log(api, run_id, job["id"], step_id) == log,
        "CAPTURE_EVIDENCE_CHANGED",
    )
    run_identity(api, run_id, control)
    require(snapshot(api, intent["pr"]["number"]) == intent["pr"], "CAPTURE_PR_STALE")
    release(api, control)
    require(int(clock()) - at <= 300 and int(clock()) < intent["deadline"], "CAPTURE_EXPIRED_DURING_READ")
    return intent


def validate_github_run(run, intent, *, allow_initial_title_pending=False):
    require(type(run.get("id")) is int and run["id"] > 0, "GITHUB_RUN_ID_INVALID")
    expected = {
        "run_attempt": 1,
        "event": "workflow_dispatch",
        "head_sha": intent["control_sha"],
        "head_branch": "master",
        "workflow_id": GH_WORKFLOW_ID,
    }
    for field, expected_value in expected.items():
        # Field names come only from this fixed mapping; never print actual values.
        require(
            field in run,
            "GITHUB_RUN_MISSING_" + field.upper(),
        )
        require(
            type(run[field]) is type(expected_value),
            "GITHUB_RUN_TYPE_MISMATCH_" + field.upper(),
        )
        require(
            run[field] == expected_value,
            "GITHUB_RUN_VALUE_MISMATCH_" + field.upper(),
        )
    require(
        (run.get("repository") or {}).get("id") == GH_ID
        and (run.get("repository") or {}).get("full_name") == GH_REPO
        and (run.get("actor") or {}).get("login") == ALLOWED_ACTOR
        and run.get("path") in (GH_WORKFLOW, GH_WORKFLOW + "@master"),
        "GITHUB_RUN_OWNER_MISMATCH",
    )
    require("display_title" in run, "GITHUB_RUN_MISSING_DISPLAY_TITLE")
    title = run["display_title"]
    require(type(title) is str, "GITHUB_RUN_TYPE_MISMATCH_DISPLAY_TITLE")
    expected_title = "lite-gitcode:" + intent["bridge"]["bridge_nonce"]
    if title != expected_title:
        if (
            allow_initial_title_pending
            and len(title) <= 200
            and not any(ord(char) < 32 or ord(char) == 127 for char in title)
            and not title.startswith("lite-gitcode:")
            and run.get("status") in {"queued", "in_progress"}
        ):
            return None
        raise ManualForwardError("GITHUB_RUN_VALUE_MISMATCH_DISPLAY_TITLE")
    return run["id"]


def bind_initial_run(api, intent, run_id, *, clock, sleep, progress):
    # Only a validated response from THIS dispatch grants this narrow pending path.
    expires = min(int(clock()) + 30, intent["deadline"], intent["bridge"]["event_created_at"] + 300)
    for attempt in range(1, 7):
        require(int(clock()) < expires, "GITHUB_INITIAL_TITLE_TIMEOUT")
        run_identity(api, intent["bridge"]["bridge_action_run"], intent["control_sha"])
        require(int(clock()) < expires, "GITHUB_INITIAL_TITLE_TIMEOUT")
        run = api.gh(f"/actions/runs/{run_id}")
        require(int(clock()) < expires, "GITHUB_INITIAL_TITLE_TIMEOUT")
        require(run.get("id") == run_id and type(run.get("id")) is int, "GITHUB_RUN_RESPONSE_ID_MISMATCH")
        if validate_github_run(run, intent, allow_initial_title_pending=True) is not None:
            return run
        progress({"event": "INITIAL_TITLE_PENDING", "attempt": attempt})
        if attempt < 6:
            sleep(min(5, max(0, expires - int(clock()))))
    raise ManualForwardError("GITHUB_INITIAL_TITLE_TIMEOUT")


def find_run(api, intent):
    query = urllib.parse.urlencode(
        {
            "event": "workflow_dispatch",
            "branch": "master",
            "head_sha": intent["control_sha"],
            "actor": ALLOWED_ACTOR,
            "per_page": 100,
            "page": 1,
        }
    )
    result = api.gh(f"/actions/workflows/{GH_WORKFLOW_ID}/runs?" + query)
    rows = result.get("workflow_runs")
    require(
        isinstance(rows, list) and type(result.get("total_count")) is int and result["total_count"] == len(rows) <= 100,
        "GITHUB_RECOVERY_SCOPE_INCOMPLETE",
    )
    matches = [row for row in rows if row.get("display_title") == "lite-gitcode:" + intent["bridge"]["bridge_nonce"]]
    require(len(matches) <= 1, "GITHUB_NONCE_AMBIGUOUS")
    return validate_github_run(matches[0], intent) if matches else None



CASE_ORDER = {
    "worker_os_blue": "case_000_worker_os_blue",
    "dispatch_smoke_8p": "case_001_dispatch_smoke_8p",
    "dispatch_smoke_16p": "case_002_dispatch_smoke_16p",
}


def case_check(api, intent, run_id, test_id, *, run_conclusion="failure"):
    require(test_id in CASE_ORDER, "CASE_NOT_IN_FIXED_RESULT_SET")
    case_id = CASE_ORDER[test_id]
    request_id = f"{GH_ID}/{run_id}/1"
    rows, total = [], None
    for page in range(1, 11):
        query = urllib.parse.urlencode({
            "app_id": 5575236, "filter": "all", "per_page": 100, "page": page
        })
        response = api.gh(f"/commits/{intent['control_sha']}/check-runs?{query}")
        checks = response.get("check_runs")
        count = response.get("total_count")
        require(type(count) is int and 0 <= count <= 1000 and isinstance(checks, list) and len(checks) <= 100,
                "CASE_CHECK_LIST_INVALID")
        if total is None:
            total = count
        require(count == total, "CASE_CHECK_LIST_CHANGED")
        rows.extend(checks)
        if len(rows) >= total:
            break
    require(total is not None and len(rows) == total, "CASE_CHECK_LIST_INCOMPLETE")
    prefix = f"lite-v4-{run_id}-1-{case_id}-"
    matches = [row for row in rows if isinstance(row.get("name"), str) and row["name"].startswith(prefix)]
    require(len(matches) == 1, "CASE_CHECK_MISSING_OR_AMBIGUOUS")
    check = matches[0]
    require(
        type(check.get("id")) is int
        and check.get("head_sha") == intent["control_sha"]
        and (check.get("app") or {}).get("id") == 5575236
        and check.get("details_url") == f"https://github.com/{GH_REPO}/actions/runs/{run_id}"
        and isinstance(check.get("external_id"), str)
        and check["external_id"].startswith("lite-actions:v4:"),
        "CASE_CHECK_IDENTITY_MISMATCH",
    )
    html = check.get("html_url")
    require(
        isinstance(html, str)
        and re.fullmatch(r"https://github\.com/" + re.escape(GH_REPO) + r"/runs/[0-9]+(?:\?[^\s]*)?", html),
        "CASE_CHECK_HTML_URL_INVALID",
    )
    if check.get("status") != "completed":
        require(
            check.get("status") in {"queued", "in_progress"}
            and run_conclusion in {"failure", "cancelled", "timed_out"},
            "CASE_CHECK_NOT_TERMINAL",
        )
        require(
            re.fullmatch(re.escape(prefix) + r"[0-9a-f]{12}", check.get("name", "")),
            "CASE_CHECK_NAME_INVALID",
        )
        return {
            "case_id": case_id,
            "test_id": test_id,
            "case_status": "NOT_RUN",
            "reason": "GitHub workflow concluded " + str(run_conclusion) + " before this Case published a terminal result",
            "github_run_id": run_id,
            "github_run_url": f"https://github.com/{GH_REPO}/actions/runs/{run_id}",
            "case_check_url": html,
            "case_log_url": html,
            "check_id": check["id"],
        }
    output = check.get("output") or {}
    try:
        envelope = json.loads(output.get("summary", ""))
    except (TypeError, ValueError):
        raise ManualForwardError("CASE_CHECK_SUMMARY_INVALID") from None
    require(
        isinstance(envelope, dict)
        and envelope.get("schema") == 4
        and envelope.get("phase") == "CASE_RESULT"
        and envelope.get("result", {}).get("case_id") == case_id
        and envelope.get("receipt", {}).get("request_id") == request_id,
        "CASE_CHECK_IDENTITY_MISMATCH",
    )
    receipt = envelope["receipt"]
    spec = receipt.get("request_spec_digest")
    require(
        isinstance(spec, str)
        and re.fullmatch(r"[0-9a-f]{64}", spec)
        and receipt.get("plan_digest") == envelope.get("plan_digest"),
        "CASE_CHECK_SPEC_INVALID",
    )
    require(check["name"] == prefix + spec[:12], "CASE_CHECK_SPEC_MISMATCH")
    expected_external = "lite-actions:v4:" + hashlib.sha256(canonical({
        "repo_id": GH_ID,
        "run_id": run_id,
        "run_attempt": 1,
        "case_id": case_id,
        "request_spec_digest": spec,
    })).hexdigest()
    require(check.get("external_id") == expected_external, "CASE_CHECK_EXTERNAL_ID_MISMATCH")
    result = envelope["result"]
    status = result.get("status")
    require(status in {"PASS", "FAIL", "INFRA_ERROR", "CANCELLED", "TIMED_OUT", "NOT_RUN", "RESOURCE_TIMEOUT"},
            "CASE_STATUS_INVALID")
    require(
        set(result) == {"case_id", "receipt", "identity", "status", "exit_code", "cleanup_verified", "summary"}
        and type(result.get("exit_code")) is int
        and -255 <= result["exit_code"] <= 255
        and type(result.get("cleanup_verified")) is bool
        and isinstance(result.get("identity"), dict)
        and isinstance(result.get("summary"), list)
        and len(result["summary"]) <= 20
        and all(isinstance(item, str) and len(item) <= 180 and not any(ord(c) < 32 for c in item) for item in result["summary"])
        and result.get("receipt") == receipt
        and (status != "PASS" or result["exit_code"] == 0),
        "CASE_RESULT_SHAPE_INVALID",
    )
    require(check.get("status") == "completed", "CASE_CHECK_NOT_TERMINAL")
    expected_conclusion = (
        "success" if status == "PASS" and result.get("cleanup_verified") is True
        else "cancelled" if status == "CANCELLED"
        else "timed_out" if status == "TIMED_OUT"
        else "action_required" if status == "NOT_RUN"
        else "failure"
    )
    require(check.get("conclusion") == expected_conclusion, "CASE_CHECK_CONCLUSION_MISMATCH")
    return {
        "case_id": case_id,
        "test_id": test_id,
        "case_status": status,
        "reason": "; ".join(result["summary"]) or status,
        "github_run_id": run_id,
        "github_run_url": f"https://github.com/{GH_REPO}/actions/runs/{run_id}",
        "case_check_url": html,
        "case_log_url": html,
        "check_id": check["id"],
        "plan_digest": envelope["plan_digest"],
        "receipt": receipt,
    }



def result_main():
    case = os.environ.get("RESULT_CASE", "unknown")
    run_id = None
    try:
        api = API(os.environ)
        require(case in CASE_ORDER, "CASE_NOT_IN_FIXED_RESULT_SET")
        capture_result = os.environ.get("CAPTURE_RESULT", "success")
        if capture_result != "success":
            payload = {
                "test_id": case,
                "case_status": "NOT_RUN",
                "reason": "GitCode Capture did not complete; no GitHub execution was authorized",
                "github_run_id": None,
                "github_run_url": None,
                "case_log_url": None,
            }
            print(json.dumps(payload, sort_keys=True))
            return 1
        intent = retrieve_intent(api, os.environ)
        run_id = find_run(api, intent)
        require(run_id is not None, "EXACT_GITHUB_RUN_NOT_FOUND")
        run = api.gh(f"/actions/runs/{run_id}")
        validate_github_run(run, intent)
        require(run.get("status") == "completed", "GITHUB_RUN_NOT_TERMINAL")
        result = case_check(api, intent, run_id, case, run_conclusion=run.get("conclusion"))
        print(json.dumps(result, sort_keys=True))
        return 0 if result["case_status"] == "PASS" else 1
    except Exception as error:
        code = str(error) if isinstance(error, ManualForwardError) else type(error).__name__
        payload = {
            "test_id": case,
            "case_status": "INFRA_ERROR",
            "reason": "MANUAL_FORWARD_FAILED:" + code,
            "github_run_id": run_id,
            "github_run_url": (
                f"https://github.com/{GH_REPO}/actions/runs/{run_id}" if run_id else None
            ),
            "case_log_url": None,
        }
        print(json.dumps(payload, sort_keys=True))
        return 1



def forward(api, intent, *, clock=time.time, sleep=time.sleep, progress=lambda _: None):
    require(int(clock()) < intent["deadline"], "MANUAL_DEADLINE_EXCEEDED")
    release(api, intent["control_sha"])
    require(int(clock()) < intent["deadline"], "MANUAL_DEADLINE_EXCEEDED")
    # A completed capture is the retry identity. Never invent a new nonce.
    run_id = find_run(api, intent)
    initial_response_run = False
    if run_id is None:
        require(
            int(clock()) < intent["deadline"] and int(clock()) - intent["bridge"]["event_created_at"] <= 300,
            "CAPTURE_EXPIRED_BEFORE_DISPATCH",
        )
        body = {
            "ref": "master",
            "return_run_details": True,
            "inputs": {"source_provider": "gitcode", "gitcode_request": canonical(intent).decode()},
        }
        try:
            response = api.gh(f"/actions/workflows/{GH_WORKFLOW_ID}/dispatches", body=body, dispatch=True)
        except (OSError, TimeoutError, ManualForwardError):
            response = None
        if isinstance(response, dict) and type(response.get("workflow_run_id")) is int:
            run_id = response["workflow_run_id"]
            require(
                response.get("run_url") == f"https://api.github.com/repos/{GH_REPO}/actions/runs/{run_id}"
                and response.get("html_url") == f"https://github.com/{GH_REPO}/actions/runs/{run_id}",
                "DISPATCH_RESPONSE_IDENTITY_MISMATCH",
            )
            initial_response_run = True
        if run_id is None:
            for _ in range(6):
                require(int(clock()) < intent["deadline"], "MANUAL_DEADLINE_EXCEEDED")
                run_id = find_run(api, intent)
                if run_id is not None:
                    break
                sleep(5)
            require(run_id is not None, "DISPATCH_OUTCOME_UNKNOWN_NO_RETRY")
    first_bound = (
        bind_initial_run(api, intent, run_id, clock=clock, sleep=sleep, progress=progress)
        if initial_response_run
        else None
    )
    reported_run = False
    while int(clock()) < intent["deadline"]:
        run_identity(api, intent["bridge"]["bridge_action_run"], intent["control_sha"])
        run = first_bound if first_bound is not None else api.gh(f"/actions/runs/{run_id}")
        first_bound = None
        require(run.get("id") == run_id and type(run.get("id")) is int, "GITHUB_RUN_RESPONSE_ID_MISMATCH")
        validate_github_run(run, intent)
        if not reported_run:
            progress(
                {
                    "event": "GITHUB_RUN_BOUND",
                    "test_id": intent["selectors"][0]["test_id"],
                    "github_run_id": run_id,
                    "github_run_url": f"https://github.com/{GH_REPO}/actions/runs/{run_id}",
                    "not_current_merge_authorization": True,
                }
            )
            reported_run = True
        require(int(clock()) < intent["deadline"], "MANUAL_DEADLINE_EXCEEDED")
        if run.get("status") == "completed":
            require(run.get("conclusion") == "success", "GITHUB_FORWARD_FAILED")
            jobs = api.gh(f"/actions/runs/{run_id}/jobs?filter=latest&per_page=100")
            rows = jobs.get("jobs")
            require(isinstance(rows, list) and jobs.get("total_count") == len(rows) <= 100, "GITHUB_JOBS_INCOMPLETE")
            for name in ("prepare-gitcode", "wait-gitcode", "Lite-Actions-GitCode-Smoke"):
                matches = [job for job in rows if job.get("name") == name]
                require(
                    len(matches) == 1
                    and matches[0].get("status") == "completed"
                    and matches[0].get("conclusion") == "success"
                    and matches[0].get("run_id") == run_id
                    and matches[0].get("run_attempt") == 1,
                    "GITHUB_FIXED_RESULT_JOB_NOT_PASS",
                )
            case_results = [
                case_check(api, intent, run_id, test_id, run_conclusion=run.get("conclusion")) for test_id in ALLOWED_CASES
            ]
            require(
                len({item["plan_digest"] for item in case_results}) == 1
                and all(item["receipt"] == case_results[0]["receipt"] for item in case_results)
                and all(item["case_status"] == "PASS" for item in case_results),
                "THREE_CASE_RESULT_SET_NOT_GREEN",
            )
            require(snapshot(api, intent["pr"]["number"]) == intent["pr"], "FINAL_PR_STALE")
            release(api, intent["control_sha"])
            run_identity(api, intent["bridge"]["bridge_action_run"], intent["control_sha"])
            require(int(clock()) < intent["deadline"], "MANUAL_DEADLINE_EXCEEDED")
            return {
                "scope": "PERSONAL_MANUAL_FORWARD_NOT_OFFICIAL_PR_GATE",
                "github_run_id": run_id,
                "control_sha": intent["control_sha"],
                "historical_execution_result": "PASS",
                "current_authority": "NOT_PROVIDED",
                "not_current_merge_authorization": True,
                "merge_gate": "NOT_PROVIDED",
            }
        sleep(min(30, max(0, intent["deadline"] - int(clock()))))
    raise ManualForwardError("MANUAL_DEADLINE_EXCEEDED")


def main():
    if os.environ.get("RESULT_CASE"):
        return result_main()
    try:
        api = API(os.environ)
        intent = retrieve_intent(api, os.environ)
        print(
            json.dumps(
                forward(api, intent, progress=lambda row: print(json.dumps(row, sort_keys=True), flush=True)),
                sort_keys=True,
            )
        )
        return 0
    except Exception as error:
        return emit_failure(error)


if __name__ == "__main__":
    raise SystemExit(main())
