"""Verify an embedded pinned SDK subset and run its isolated CLI."""

import base64
import hashlib
import json
import os
import pathlib
import re
import sys
import tempfile
import time
import zlib

SDK_SHA = "__SDK_SHA__"
BUNDLE_SHA256 = "__BUNDLE_SHA256__"
RAW_SHA256 = "__RAW_SHA256__"
EXPECTED_FILES = frozenset(__EXPECTED_FILES__)
BUNDLE_B64 = __BUNDLE_B64__
MAX_COMPRESSED = 40 * 1024
MAX_RAW = 200 * 1024
STARTED = time.monotonic()
PHASE = "startup"


def fail(code):
    allowed = {
        "invalid_pin",
        "bundle_encoding_invalid",
        "bundle_digest_mismatch",
        "bundle_size_invalid",
        "bundle_format_invalid",
        "bundle_manifest_invalid",
        "bundle_extract_error",
        "sdk_entry_missing",
        "bootstrap_internal_error",
    }
    code = code if code in allowed else "bootstrap_internal_error"
    elapsed = max(0, int((time.monotonic() - STARTED) * 1000))
    print(
        "MANUAL_SDK_BOOTSTRAP_FAILED phase="
        + PHASE
        + " error="
        + code
        + " elapsed_ms="
        + str(elapsed)
        + " total_ms="
        + str(elapsed),
        file=sys.stderr,
    )
    raise SystemExit(1)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


def main():
    global PHASE
    if not re.fullmatch(r"[0-9a-f]{40}", SDK_SHA) or not all(
        re.fullmatch(r"[0-9a-f]{64}", value) for value in (BUNDLE_SHA256, RAW_SHA256)
    ):
        fail("invalid_pin")
    PHASE = "bundle"
    try:
        if len(BUNDLE_B64) > ((MAX_COMPRESSED + 2) // 3) * 4:
            fail("bundle_size_invalid")
        try:
            compressed = base64.b64decode(BUNDLE_B64, validate=True)
        except Exception:
            fail("bundle_encoding_invalid")
        if not compressed or len(compressed) > MAX_COMPRESSED:
            fail("bundle_size_invalid")
        if hashlib.sha256(compressed).hexdigest() != BUNDLE_SHA256:
            fail("bundle_digest_mismatch")
        try:
            dec = zlib.decompressobj()
            raw = dec.decompress(compressed, MAX_RAW + 1)
            if len(raw) > MAX_RAW or not dec.eof or dec.unused_data or dec.unconsumed_tail:
                fail("bundle_size_invalid")
        except zlib.error:
            fail("bundle_format_invalid")
        if hashlib.sha256(raw).hexdigest() != RAW_SHA256:
            fail("bundle_digest_mismatch")
        PHASE = "manifest"
        try:
            files = json.loads(raw.decode("ascii"), object_pairs_hook=unique_object)
        except Exception:
            fail("bundle_manifest_invalid")
        if (
            not isinstance(files, dict)
            or set(files) != EXPECTED_FILES
            or not all(isinstance(v, str) for v in files.values())
        ):
            fail("bundle_manifest_invalid")
        with tempfile.TemporaryDirectory(prefix="lite-gitcode-sdk-") as temporary:
            PHASE = "extract"
            root = pathlib.Path(temporary)
            for name in sorted(EXPECTED_FILES):
                target = root.joinpath(*name.split("/"))
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                try:
                    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
                    with os.fdopen(fd, "wb") as output:
                        output.write(files[name].encode("utf-8"))
                except Exception:
                    fail("bundle_extract_error")
            PHASE = "sdk_entry"
            if not (root / "ci_core" / "gitcode_manual" / "cli.py").is_file():
                fail("sdk_entry_missing")
            elapsed = max(0, int((time.monotonic() - STARTED) * 1000))
            if os.environ.get("LITE_GITCODE_BOOTSTRAP_DIAGNOSTIC_ONLY") == "1":
                print(
                    "MANUAL_SDK_BOOTSTRAP_OK phase=sdk_entry elapsed_ms=" + str(elapsed) + " total_ms=" + str(elapsed)
                )
                raise SystemExit(0)
            sys.path.insert(0, str(root))
            from ci_core.gitcode_manual.cli import main as sdk_main

            raise SystemExit(sdk_main())
    except SystemExit:
        raise
    except Exception:
        fail("bootstrap_internal_error")


if __name__ == "__main__":
    main()
