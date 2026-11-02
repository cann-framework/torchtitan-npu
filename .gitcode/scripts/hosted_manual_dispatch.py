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
        isinstance(intent["selectors"], list)
        and len(intent["selectors"]) == 1
        and intent["selectors"][0] in [{"test_id": name} for name in ALLOWED_CASES],
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


def validate_github_run(run, intent):
    require(type(run.get("id")) is int and run["id"] > 0, "GITHUB_RUN_ID_INVALID")
    expected = {
        "run_attempt": 1,
        "event": "workflow_dispatch",
        "head_sha": intent["control_sha"],
        "head_branch": "master",
        "workflow_id": GH_WORKFLOW_ID,
        "display_title": "lite-gitcode:" + intent["bridge"]["bridge_nonce"],
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
    return run["id"]


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


def forward(api, intent, *, clock=time.time, sleep=time.sleep, progress=lambda _: None):
    require(int(clock()) < intent["deadline"], "MANUAL_DEADLINE_EXCEEDED")
    release(api, intent["control_sha"])
    require(int(clock()) < intent["deadline"], "MANUAL_DEADLINE_EXCEEDED")
    # A completed capture is the retry identity. Never invent a new nonce.
    run_id = find_run(api, intent)
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
        if run_id is None:
            for _ in range(6):
                require(int(clock()) < intent["deadline"], "MANUAL_DEADLINE_EXCEEDED")
                run_id = find_run(api, intent)
                if run_id is not None:
                    break
                sleep(5)
            require(run_id is not None, "DISPATCH_OUTCOME_UNKNOWN_NO_RETRY")
    reported_run = False
    while int(clock()) < intent["deadline"]:
        run_identity(api, intent["bridge"]["bridge_action_run"], intent["control_sha"])
        run = api.gh(f"/actions/runs/{run_id}")
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
