"""Download and verify the fixed Lite Actions SDK archive, then enter its CLI."""

import hashlib
import io
import os
import pathlib
import re
import signal
import socket
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

SDK_SHA = "__SDK_SHA__"
ARCHIVE_SHA256 = "__SDK_ARCHIVE_SHA256__"
EXPECTED_FILES = frozenset(__EXPECTED_FILES__)
MAX_ARCHIVE = 4 * 1024 * 1024
MAX_EXTRACTED = 8 * 1024 * 1024


_PHASE_STARTED = time.monotonic()
_TOTAL_STARTED = _PHASE_STARTED
_DOWNLOAD_MS = 0
_PHASE = "startup"
_DIAGNOSTIC_CODES = frozenset(
    {
        "invalid_pin",
        "download_timeout",
        "http_error",
        "network_error",
        "download_error",
        "archive_too_large",
        "archive_digest_mismatch",
        "archive_format_invalid",
        "archive_manifest_invalid",
        "archive_member_unsafe",
        "archive_member_duplicate",
        "archive_member_unexpected",
        "archive_manifest_mismatch",
        "archive_extract_limit",
        "archive_extract_error",
        "sdk_entry_missing",
        "bootstrap_internal_error",
    }
)


def fail(error_code):
    code = error_code if error_code in _DIAGNOSTIC_CODES else "bootstrap_internal_error"
    elapsed_ms = max(0, int((time.monotonic() - _PHASE_STARTED) * 1000))
    print(
        "MANUAL_SDK_BOOTSTRAP_FAILED phase="
        + _PHASE
        + " error="
        + code
        + " elapsed_ms="
        + str(elapsed_ms)
        + " download_ms="
        + str(_DOWNLOAD_MS if _PHASE != "download" else elapsed_ms)
        + " total_ms="
        + str(max(0, int((time.monotonic() - _TOTAL_STARTED) * 1000))),
        file=sys.stderr,
    )
    raise SystemExit(1)


class DownloadDeadline(BaseException):
    pass


def download(request):
    # Socket timeout is per operation, so enforce a separate wall-clock deadline.
    def deadline_handler(_signum, _frame):
        raise DownloadDeadline()

    previous = signal.signal(signal.SIGALRM, deadline_handler)
    signal.setitimer(signal.ITIMER_REAL, 65)
    try:
        for attempt in range(2):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return response.read(MAX_ARCHIVE + 1)
            except urllib.error.HTTPError as exc:
                code, retryable = "http_error", exc.code in (429, 500, 502, 503, 504)
            except TimeoutError:
                code, retryable = "download_timeout", True
            except urllib.error.URLError as exc:
                code = "download_timeout" if isinstance(exc.reason, (TimeoutError, socket.timeout)) else "network_error"
                retryable = isinstance(
                    exc.reason, (TimeoutError, socket.timeout, ConnectionResetError, ConnectionAbortedError)
                )
            except (ConnectionResetError, ConnectionAbortedError):
                code, retryable = "network_error", True
            except Exception:
                code, retryable = "download_error", False
            if not retryable or attempt == 1:
                fail(code)
    except DownloadDeadline:
        fail("download_timeout")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def main():
    if not re.fullmatch(r"[0-9a-f]{40}", SDK_SHA) or not re.fullmatch(r"[0-9a-f]{64}", ARCHIVE_SHA256):
        fail("invalid_pin")
    global _PHASE, _PHASE_STARTED, _DOWNLOAD_MS
    _PHASE = "download"
    _PHASE_STARTED = time.monotonic()
    url = "https://github.com/depeng1994/lite-actions/archive/" + SDK_SHA + ".tar.gz"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "lite-actions-manual-bootstrap"})
        archive = download(request)
        _DOWNLOAD_MS = max(0, int((time.monotonic() - _PHASE_STARTED) * 1000))
        _PHASE = "archive"
        _PHASE_STARTED = time.monotonic()
        if len(archive) > MAX_ARCHIVE:
            fail("archive_too_large")
        if hashlib.sha256(archive).hexdigest() != ARCHIVE_SHA256:
            fail("archive_digest_mismatch")
        with tempfile.TemporaryDirectory(prefix="lite-gitcode-sdk-") as temporary:
            root = pathlib.Path(temporary)
            prefix = "lite-actions-" + SDK_SHA + "/ci_core/"
            extracted = 0
            try:
                bundle = tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz")
                members = bundle.getmembers()
            except (tarfile.TarError, EOFError):
                fail("archive_format_invalid")
            except Exception:
                fail("bootstrap_internal_error")
            _PHASE = "manifest"
            _PHASE_STARTED = time.monotonic()
            with bundle:
                files = {}
                directories = set()
                seen = set()
                allowed_dirs = {
                    str(pathlib.PurePosixPath(name).parent)
                    for name in EXPECTED_FILES
                    if pathlib.PurePosixPath(name).parent.as_posix() != "."
                }
                allowed_dirs |= {
                    parent.as_posix()
                    for name in EXPECTED_FILES
                    for parent in pathlib.PurePosixPath(name).parents
                    if parent.as_posix() not in (".", "")
                }
                for member in members:
                    name = member.name
                    parts = pathlib.PurePosixPath(name).parts
                    if (
                        not parts
                        or name.startswith("/")
                        or ".." in parts
                        or member.issym()
                        or member.islnk()
                        or member.isdev()
                    ):
                        fail("archive_member_unsafe")
                    if name.rstrip("/") == prefix.rstrip("/"):
                        if "__CI_CORE_ROOT__" in seen:
                            fail("archive_member_duplicate")
                        seen.add("__CI_CORE_ROOT__")
                        continue
                    if not name.startswith(prefix):
                        continue
                    raw_relative = name[len(prefix) :].rstrip("/")
                    relative = pathlib.PurePosixPath(raw_relative)
                    normalized = relative.as_posix()
                    if (
                        not relative.parts
                        or normalized != raw_relative
                        or any(part in ("", ".", "..") for part in relative.parts)
                        or normalized in seen
                    ):
                        fail("archive_member_duplicate")
                    seen.add(normalized)
                    if member.isdir():
                        if normalized not in allowed_dirs:
                            fail("archive_manifest_invalid")
                        directories.add(normalized)
                        continue
                    if not member.isfile() or normalized not in EXPECTED_FILES or member.size < 0:
                        fail("archive_member_unexpected")
                    files[normalized] = member
                if set(files) != EXPECTED_FILES:
                    fail("archive_manifest_mismatch")
                for name in files:
                    parents = pathlib.PurePosixPath(name).parents
                    if any(parent.as_posix() in files for parent in parents):
                        fail("archive_manifest_invalid")
                if any(name in files for name in directories):
                    fail("archive_manifest_invalid")
                _PHASE = "extract"
                _PHASE_STARTED = time.monotonic()
                for name, member in files.items():
                    extracted += member.size
                    if extracted > MAX_EXTRACTED:
                        fail("archive_extract_limit")
                    target = root / "ci_core" / pathlib.PurePosixPath(name)
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    source = bundle.extractfile(member)
                    if source is None:
                        fail("archive_extract_error")
                    payload = source.read(member.size + 1)
                    if len(payload) != member.size:
                        fail("archive_extract_error")
                    descriptor = os.open(
                        target,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                        0o600,
                    )
                    with os.fdopen(descriptor, "wb") as output:
                        output.write(payload)
            _PHASE = "sdk_entry"
            _PHASE_STARTED = time.monotonic()
            if not (root / "ci_core" / "gitcode_manual" / "cli.py").is_file():
                fail("sdk_entry_missing")
            if os.environ.get("LITE_GITCODE_BOOTSTRAP_DIAGNOSTIC_ONLY") == "1":
                elapsed_ms = max(0, int((time.monotonic() - _PHASE_STARTED) * 1000))
                print(
                    "MANUAL_SDK_BOOTSTRAP_OK phase=sdk_entry elapsed_ms="
                    + str(elapsed_ms)
                    + " download_ms="
                    + str(_DOWNLOAD_MS)
                    + " total_ms="
                    + str(max(0, int((time.monotonic() - _TOTAL_STARTED) * 1000)))
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
