#!/usr/bin/env python3
"""Execute the trusted schema-4 Hosted jobs; never import proposed PR source."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def read_object(value):
    if not isinstance(value, str) or not 0 < len(value.encode()) <= 256 * 1024:
        raise ValueError("BOUNDED_JSON_OBJECT_REQUIRED")

    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("DUPLICATE_JSON_KEY")
            result[key] = item
        return result

    document = json.loads(value, object_pairs_hook=unique)
    if not isinstance(document, dict):
        raise ValueError("JSON_OBJECT_REQUIRED")
    return document


def verify_checkout(directory, expected, *, allow_control_dependencies=False):
    if not isinstance(expected, str) or not re.fullmatch("[0-9a-f]{40}", expected):
        raise ValueError("IMMUTABLE_SDK_SHA_REQUIRED")
    directory = Path(directory)
    if not directory.is_absolute() or directory.resolve(strict=True) != directory:
        raise ValueError("TRUSTED_CHECKOUT_PATH_REQUIRED")
    observed = subprocess.check_output(
        ["git", "-C", str(directory), "rev-parse", "HEAD"],
        text=True,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/nonexistent"},
    ).strip()
    if observed != expected:
        raise ValueError("TRUSTED_CHECKOUT_SHA_MISMATCH")
    # Refuse locally modified Python even when HEAD still matches the pin.
    status = subprocess.check_output(
        ["git", "-C", str(directory), "status", "--porcelain", "--untracked-files=no"],
        text=True,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/nonexistent"},
    )
    if status:
        raise ValueError("TRUSTED_CHECKOUT_DIRTY")
    for flags in (("--others", "--exclude-standard"), ("--others", "--ignored", "--exclude-standard")):
        names = (
            subprocess.check_output(
                ["git", "-C", str(directory), "ls-files", "-z", *flags],
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/nonexistent"},
            )
            .decode()
            .split("\0")
        )
        for name in names:
            if not name:
                continue
            if allow_control_dependencies and name.startswith(".github/scripts/lite_actions/gitcode/node_modules/"):
                dependency_root = directory / ".github/scripts/lite_actions/gitcode/node_modules"
                if dependency_root.is_symlink():
                    raise ValueError("CONTROL_DEPENDENCY_SYMLINK_FORBIDDEN")
                continue
            raise ValueError("UNTRACKED_EXECUTABLE_IMPORT_FORBIDDEN")
    return directory


def write_private(path, content):
    with open(path, "x", encoding="utf-8", opener=lambda name, flags: os.open(name, flags, 0o600)) as stream:
        stream.write(content)


def load_factory(environment):
    sdk = verify_checkout(environment["LITE_ACTIONS_SDK_ROOT"], environment["LITE_ACTIONS_SDK_SHA"])
    sys.path.insert(0, str(sdk))
    from ci_core.gitcode_assembly import assemble_hosted

    return assemble_hosted


def execute(phase, *, environment, factory=None, clock=time.time, sleep=time.sleep):
    if phase not in {"prepare", "wait"}:
        raise ValueError("INVALID_HOSTED_PHASE")
    config = read_object(environment["LITE_ACTIONS_RELEASE_CONFIG"])
    if config.get("authority_protocol") != "artifact-check-v1":
        raise ValueError("FRESH_AUTHORITY_PROTOCOL_REQUIRED")
    intent = read_object(environment["LITE_ACTIONS_GITCODE_REQUEST"])
    if environment.get("LITE_ACTIONS_SOURCE_PROVIDER") != "gitcode":
        raise ValueError("GITCODE_PROVIDER_REQUIRED")
    if config.get("sdk_sha") != environment.get("LITE_ACTIONS_SDK_SHA"):
        raise ValueError("SDK_RELEASE_PIN_MISMATCH")
    if factory is None:
        verify_checkout(ROOT.parents[3], environment["GITHUB_SHA"], allow_control_dependencies=True)
        factory = load_factory(environment)
    from hosted import ArtifactUploader, prepare, validate_context, wait

    run_id = environment.get("GITHUB_RUN_ID", "")
    if not re.fullmatch("[1-9][0-9]{0,19}", run_id):
        raise ValueError("INVALID_RUN_ID")
    temporary = Path(environment["RUNNER_TEMP"]).resolve(strict=True)
    state = temporary / ("lite-gitcode-" + run_id + "-" + phase)
    # Deliberately no automatic restart with an empty creation journal. Native
    # reruns are rejected; a failed job requires a NEW trusted Bridge intent.
    state.mkdir(mode=0o700)
    config_path = state / "release.json"
    write_private(config_path, json.dumps(config))
    key_path = state / "checks-app.pem"
    supplied = dict(environment)
    supplied["LITE_ACTIONS_HOSTED_PHASE"] = phase
    if phase == "wait":
        supplied.pop("LITE_ACTIONS_CHECKS_KEY_FILE", None)
    try:
        key = supplied.pop("LITE_ACTIONS_CHECKS_KEY", "")
        if key and phase == "prepare":
            write_private(key_path, key)
            supplied["LITE_ACTIONS_CHECKS_KEY_FILE"] = str(key_path)
        uploader = ArtifactUploader([environment["LITE_ACTIONS_NODE"], str(ROOT / "upload_artifact.cjs")])
        runtime = factory(config_path, environment=supplied, upload=uploader, state_directory=state)
        validate_context(environment, runtime.policy)
        if intent.get("workflow_path") != runtime.policy.workflow_path:
            raise ValueError("WORKFLOW_INTENT_MISMATCH")
        if phase == "prepare":
            attestation_text = environment.get("LITE_ACTIONS_GITCODE_ATTESTATION", "")
            if attestation_text:
                from ci_core.gitcode_contract import canonical

                attestation = read_object(attestation_text)
                if canonical(attestation.get("payload", {}).get("intent")) != canonical(intent):
                    raise ValueError("ATTESTATION_INTENT_MISMATCH")
                attestation_path = state / "attestation.json"
                attestation_path.write_bytes(canonical(attestation, limit=64 * 1024))
                # The Worker independently verifies the signature, provenance,
                # immutable run ownership and content. No signing occurs here.
                # Unknown upload fails this job; a fresh journal is never retried.
                uploader("dispatch-attestation-" + run_id + "-1", attestation_path)
            from build_runner import build

            runner = state / "runner.tar.gz"
            runner.write_bytes(build())
            plan = prepare(
                environment=environment,
                producer=runtime.producer,
                intent=intent,
                runner_path=runner,
                workspace=state / "prepare",
            )
            return {"phase": "prepared", "plan_digest": plan["plan_digest"]}, 0
        result = wait(
            environment=environment,
            reader=runtime.reader,
            workspace=state / "verified",
            current=runtime.current,
            clock=clock,
            sleep=sleep,
            timeout_seconds=runtime.policy.max_duration_seconds,
            resolve_canonical=runtime.resolve_canonical,
        )
        return result, 0 if result["test_result"] == "PASS" else 1
    finally:
        key_path.unlink(missing_ok=True)


SOURCE_ERROR_CODES = frozenset(
    {
        "DEPENDENCY_DECLARATION_MISMATCH",
        "FETCHED_REF_SHA_MISMATCH",
        "GIT_OPERATION_FAILED",
        "GIT_OPERATION_TIMEOUT",
        "GIT_OUTPUT_LIMIT",
        "GIT_UNAVAILABLE",
        "GIT_VERSION_NOT_PINNED",
        "INVALID_BRANCH_REF",
        "INVALID_COMMIT_SHA",
        "MERGE_CONFLICT",
        "SHALLOW_HISTORY_UNSUPPORTED",
        "SOURCE_ALREADY_BUILT",
        "SOURCE_ARCHIVE_METADATA_LIMIT",
        "SOURCE_BUILD_DEADLINE_EXPIRED",
        "SOURCE_BUNDLE_DIGEST_MISMATCH",
        "SOURCE_BUNDLE_SIZE_LIMIT",
        "SOURCE_DESTINATION_EXISTS",
        "SOURCE_DUPLICATE_OR_FILE_LIMIT",
        "SOURCE_EXTRACTION_FAILED",
        "SOURCE_FILE_LIMIT",
        "SOURCE_FILE_UNREADABLE",
        "SOURCE_HANDLE_PROVIDER_MISMATCH",
        "SOURCE_NOT_COMMIT",
        "SOURCE_SIZE_LIMIT",
        "UNMATERIALIZED_DEPENDENCY",
        "UNMATERIALIZED_LFS_OBJECT",
        "UNSAFE_ARCHIVE_MEMBER",
        "UNSAFE_ARCHIVE_PATH",
        "UNSUPPORTED_SOURCE_FILE_MODE",
        "UNTRUSTED_GIT_REMOTE",
    }
)


def safe_failure(error, phase):
    """Expose only audited constants, never exception repr, context or API output."""
    source = type(error).__name__ == "SourceError"
    code = "UNCLASSIFIED"
    if source and len(error.args) == 1 and type(error.args[0]) is str and error.args[0] in SOURCE_ERROR_CODES:
        code = error.args[0]
    result = {
        "phase": phase if phase in {"prepare", "wait"} else "unknown",
        "stage": "SOURCE" if source else "HOSTED",
        "code": code,
    }
    if phase == "wait" and not source:
        # Only fixed labels are exposed: exception messages and API bodies may
        # contain credentials or untrusted data and must remain private.
        allowed = {
            "WaiterError", "AuthorityChannelError", "ChannelError",
            "GitCodeAPIError", "HTTPError", "URLError", "TimeoutError",
            "KeyError", "TypeError", "ValueError", "OSError", "RuntimeError",
        }
        name = type(error).__name__
        result["error_type"] = name if name in allowed else "OTHER"
        if name == "HTTPError" and type(getattr(error, "code", None)) is int:
            status = error.code
            if 100 <= status <= 599:
                result["http_status"] = status
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "wait"))
    args = parser.parse_args(argv)
    try:
        result, exit_code = execute(args.phase, environment=os.environ)
        print(json.dumps(result, sort_keys=True))
        return exit_code
    except Exception as error:
        print(
            "GitCode Hosted failed closed: " + json.dumps(safe_failure(error, args.phase), sort_keys=True),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
