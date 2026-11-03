"""Trusted Hosted job boundary for schema-4 Prepare and fixed-ID Waiter.

The deployment supplies the reviewed Worker SDK and authenticated clients; never
resolve these from dispatch input or from the proposed source bundle.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from ci_core.gitcode_contract import MODEL_REPOSITORIES
from ci_core.gitcode_waiter import GitCodeWaiter


class HostedError(ValueError):
    pass


def validate_context(environment, policy):
    """Reject native reruns and non-default control code before side effects."""
    repo, branch = MODEL_REPOSITORIES[policy.github_repo_id]
    expected = {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_REPOSITORY": repo,
        "GITHUB_REPOSITORY_ID": str(policy.github_repo_id),
        "GITHUB_REF": "refs/heads/" + branch,
        "GITHUB_SHA": policy.control_sha,
        "GITHUB_WORKFLOW_SHA": policy.control_sha,
        "GITHUB_WORKFLOW_REF": repo + "/" + policy.workflow_path + "@refs/heads/" + branch,
    }
    if not policy.enabled:
        raise HostedError("PROVIDER_DISABLED")
    if environment.get("GITHUB_RUN_ATTEMPT") != "1":
        raise HostedError("NATIVE_RERUN_REJECTED_USE_GITCODE_BRIDGE_RERUN")
    if any(environment.get(key) != value for key, value in expected.items()):
        raise HostedError("HOSTED_CONTROL_CONTEXT_MISMATCH")
    run = environment.get("GITHUB_RUN_ID", "")
    if not re.fullmatch(r"[1-9][0-9]{0,19}", run):
        raise HostedError("INVALID_RUN_ID")
    return int(run)


class ArtifactUploader:
    """One SDK invocation per call; Producer owns durable recovery and readback."""

    def __init__(self, command, *, timeout_seconds=300):
        if (
            not isinstance(command, (list, tuple))
            or not command
            or not all(isinstance(part, str) and part and "\0" not in part for part in command)
        ):
            raise HostedError("TRUSTED_UPLOAD_COMMAND_REQUIRED")
        self.command = tuple(command)
        self.timeout_seconds = timeout_seconds

    def __call__(self, name, path):
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            raise HostedError("ARTIFACT_ORDINARY_FILE_REQUIRED")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,99}", name):
            raise HostedError("INVALID_ARTIFACT_NAME")
        try:
            result = subprocess.run(
                [*self.command, name, str(path.resolve())],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=self.timeout_seconds,
                env={
                    key: value
                    for key, value in os.environ.items()
                    if key
                    in {
                        "PATH",
                        "HOME",
                        "TMPDIR",
                        "RUNNER_TEMP",
                        "ACTIONS_RUNTIME_TOKEN",
                        "ACTIONS_RESULTS_URL",
                        "ACTIONS_RUNTIME_URL",
                        "GITHUB_RUN_ID",
                        "GITHUB_RUN_ATTEMPT",
                        "GITHUB_REPOSITORY",
                    }
                },
            )
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
            # An upload may have committed before a lost response. Producer
            # recovers by exact immutable name; never blindly repeats upload.
            raise OSError("ARTIFACT_UPLOAD_OUTCOME_UNKNOWN") from exc
        try:
            reply = json.loads(result.stdout)
        except (ValueError, UnicodeError) as exc:
            raise OSError("ARTIFACT_UPLOAD_REPLY_UNKNOWN") from exc
        if not isinstance(reply, dict) or set(reply) != {"id"}:
            raise OSError("ARTIFACT_UPLOAD_REPLY_UNKNOWN")
        if type(reply["id"]) is not int or reply["id"] <= 0:
            raise OSError("ARTIFACT_UPLOAD_REPLY_UNKNOWN")
        return reply["id"]


def prepare(*, environment, producer, intent, runner_path, workspace):
    run_id = validate_context(environment, producer.policy)
    return producer.prepare(run_id=run_id, intent=intent, runner_path=runner_path, workspace=workspace)


def _wait(
    *,
    environment,
    reader,
    workspace,
    current,
    clock,
    sleep,
    timeout_seconds,
    poll_seconds=15,
    resolve_canonical=None,
    monotonic=time.monotonic,
):
    """Only verified artifacts/fixed Check IDs determine the terminal outcome.

    Run this in a separate job with a successful Prepare dependency. Clock and
    sleep are injectable for deterministic CPU checks, not dispatch inputs.
    """
    run_id = validate_context(environment, reader.policy)
    if (
        type(timeout_seconds) is not int
        or not 0 < timeout_seconds <= reader.policy.max_duration_seconds
        or type(poll_seconds) is not int
        or not 1 <= poll_seconds <= 60
    ):
        raise HostedError("INVALID_WAITER_BUDGET")
    expires = int(clock()) + timeout_seconds
    waiter = None
    poll_sequence = 0
    timing_logger = logging.getLogger("lite_actions.hosted_wait_timing")
    # The production runtime uses `python3 -I -B runtime.py`; no logging config
    # is loaded there. Configure only this dedicated logger, writing fixed
    # sanitized records to stderr. Do not enable root/third-party INFO logging.
    timing_logger.setLevel(logging.INFO)
    timing_logger.propagate = False
    if not any(getattr(handler, "_lite_actions_timing", False) for handler in timing_logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler._lite_actions_timing = True
        timing_logger.addHandler(handler)

    def timed(stage, sequence, operation):
        started = monotonic()
        try:
            return operation()
        finally:
            elapsed_ms = max(0, round((monotonic() - started) * 1000))
            # Fixed stage labels and numeric run/sequence IDs only. Never log
            # responses, exceptions, request data, environment, or artifact URLs.
            timing_logger.info(
                "hosted_wait_timing stage=%s elapsed_ms=%d run_id=%d sequence=%d outcome=%s",
                stage,
                elapsed_ms,
                run_id,
                sequence,
                "ok" if sys.exc_info()[0] is None else "error",
            )

    while True:
        delay = poll_seconds
        now = int(clock())
        if now >= expires:
            raise HostedError("HOSTED_WAITER_TIMEOUT")
        if waiter is None:
            loaded = timed(
                "reader.load",
                poll_sequence,
                lambda now=now: reader.load(run_id, Path(workspace), now=now),
            )
            if loaded is not None:
                loaded.verify_files()
                waiter = GitCodeWaiter(
                    loaded.verified,
                    reader.channel,
                    current=current,
                    resolve_canonical=resolve_canonical,
                )
                expires = min(expires, waiter.plan["deadline"])
        if waiter is not None:
            now = int(clock())
            if now >= expires:
                raise HostedError("HOSTED_WAITER_TIMEOUT")
            poll_sequence += 1
            outcome = timed("waiter.poll", poll_sequence, lambda waiter=waiter, now=now: waiter.poll(now=now))
            if int(clock()) >= expires:
                raise HostedError("HOSTED_WAITER_TIMEOUT")
            if outcome["test_result"] != "PENDING":
                return outcome
            # A 15-second authority proof can expire between 15-second polls.
            # Increase cadence only while awaiting that short-lived proof.
            if outcome.get("reason") == "AWAITING_FRESH_AUTHORITY":
                delay = min(poll_seconds, 3)
        remaining = expires - int(clock())
        if remaining > 0:
            timed(
                "sleep",
                poll_sequence,
                lambda remaining=remaining, delay=delay: sleep(min(delay, remaining)),
            )


def wait(
    *,
    environment,
    reader,
    workspace,
    current,
    clock,
    sleep,
    timeout_seconds,
    poll_seconds=15,
    resolve_canonical=None,
    monotonic=time.monotonic,
):
    """Measure complete wait exit without exposing exception or result payloads."""
    started = monotonic()
    outcome, reason = "ERROR", "error"
    try:
        result = _wait(
            environment=environment,
            reader=reader,
            workspace=workspace,
            current=current,
            clock=clock,
            sleep=sleep,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
            resolve_canonical=resolve_canonical,
            monotonic=monotonic,
        )
        value = result.get("test_result") if isinstance(result, dict) else None
        outcome = value if value in {"PASS", "FAIL", "STALE", "PENDING"} else "ERROR"
        reason = "terminal" if outcome != "ERROR" else "invalid_outcome"
        return result
    except HostedError as exc:
        code = exc.args[0] if len(exc.args) == 1 and type(exc.args[0]) is str else ""
        reason = "timeout" if code in {"HOSTED_WAITER_TIMEOUT"} else "hosted_error"
        raise
    except Exception:
        reason = "error"
        raise
    finally:
        logger = logging.getLogger("lite_actions.hosted_wait_timing")
        if not any(getattr(handler, "_lite_actions_timing", False) for handler in logger.handlers):
            logger.setLevel(logging.INFO)
            logger.propagate = False
            handler = logging.StreamHandler(sys.stderr)
            handler.setFormatter(logging.Formatter("%(message)s"))
            handler._lite_actions_timing = True
            logger.addHandler(handler)
        run_text = environment.get("GITHUB_RUN_ID", "")
        safe_run_id = int(run_text) if isinstance(run_text, str) and re.fullmatch(r"[1-9][0-9]{0,19}", run_text) else 0
        logger.info(
            "hosted_wait_timing stage=wait.final elapsed_ms=%d run_id=%d outcome=%s reason=%s",
            max(0, round((monotonic() - started) * 1000)),
            safe_run_id,
            outcome,
            reason,
        )
