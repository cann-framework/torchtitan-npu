"""Create the immutable offline SDK payload from an audited Git object."""

import argparse
import base64
import hashlib
import json
import os
import subprocess
import zlib
from pathlib import Path

SDK_SHA = "b19d6ba11f03795fb42c8d9301f9c39900c73150"
SDK_FILES = (
    "ci_core/__init__.py",
    "ci_core/gitcode_manual/__init__.py",
    "ci_core/gitcode_manual/aggregate.py",
    "ci_core/gitcode_manual/capture.py",
    "ci_core/gitcode_manual/cli.py",
    "ci_core/gitcode_manual/common.py",
    "ci_core/gitcode_manual/dispatch.py",
)
ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / ".gitcode/workflow-templates/manual_sdk_bundle.b64"


def build(repo):
    env = {**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"}
    resolved = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", SDK_SHA + "^{commit}"], text=True, env=env
    ).strip()
    if resolved != SDK_SHA:
        raise ValueError("SDK_COMMIT_MISMATCH")
    files = {}
    for name in SDK_FILES:
        row = subprocess.check_output(
            ["git", "-C", str(repo), "ls-tree", SDK_SHA, "--", name], text=True, env=env
        ).strip()
        if not row.startswith("100644 blob ") or not row.endswith("\t" + name):
            raise ValueError("SDK_FILE_NOT_REGULAR_BLOB")
        data = subprocess.check_output(["git", "-C", str(repo), "show", SDK_SHA + ":" + name], env=env)
        files[name] = data.decode("utf-8")
    raw = json.dumps(files, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    compressed = zlib.compress(raw, 9)
    return base64.b64encode(compressed).decode("ascii") + "\n", raw, compressed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sdk-repo", required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    encoded, raw, compressed = build(args.sdk_repo)
    if args.check:
        if BUNDLE.read_text("ascii") != encoded:
            raise SystemExit("BUNDLE_REPRODUCIBILITY_MISMATCH")
    else:
        BUNDLE.write_text(encoded, encoding="ascii")
    print("sdk_sha=" + SDK_SHA)
    print("raw_sha256=" + hashlib.sha256(raw).hexdigest())
    print("compressed_sha256=" + hashlib.sha256(compressed).hexdigest())
    print("raw_bytes=" + str(len(raw)) + " compressed_bytes=" + str(len(compressed)))


if __name__ == "__main__":
    main()
