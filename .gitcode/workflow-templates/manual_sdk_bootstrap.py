"""Download and verify the fixed Lite Actions SDK archive, then enter its CLI."""

import hashlib
import io
import os
import pathlib
import re
import sys
import tarfile
import tempfile
import urllib.request

SDK_SHA = "__SDK_SHA__"
ARCHIVE_SHA256 = "__SDK_ARCHIVE_SHA256__"
EXPECTED_FILES = frozenset(__EXPECTED_FILES__)
MAX_ARCHIVE = 4 * 1024 * 1024
MAX_EXTRACTED = 8 * 1024 * 1024


def fail():
    print("MANUAL_SDK_BOOTSTRAP_FAILED", file=sys.stderr)
    raise SystemExit(1)


def main():
    if not re.fullmatch(r"[0-9a-f]{40}", SDK_SHA) or not re.fullmatch(
        r"[0-9a-f]{64}", ARCHIVE_SHA256
    ):
        fail()
    url = "https://github.com/depeng1994/lite-actions/archive/" + SDK_SHA + ".tar.gz"
    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "lite-actions-manual-bootstrap"}
        )
        with urllib.request.urlopen(request, timeout=25) as response:
            archive = response.read(MAX_ARCHIVE + 1)
        if len(archive) > MAX_ARCHIVE or hashlib.sha256(archive).hexdigest() != ARCHIVE_SHA256:
            fail()
        prefix = "lite-actions-" + SDK_SHA + "/ci_core/"
        with tempfile.TemporaryDirectory(prefix="lite-gitcode-sdk-") as temporary:
            root = pathlib.Path(temporary)
            prefix = "lite-actions-" + SDK_SHA + "/ci_core/"
            extracted = 0
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
                members = bundle.getmembers()
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
                        fail()
                    if name.rstrip("/") == prefix.rstrip("/"):
                        if "__CI_CORE_ROOT__" in seen:
                            fail()
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
                        fail()
                    seen.add(normalized)
                    if member.isdir():
                        if normalized not in allowed_dirs:
                            fail()
                        directories.add(normalized)
                        continue
                    if not member.isfile() or normalized not in EXPECTED_FILES or member.size < 0:
                        fail()
                    files[normalized] = member
                if set(files) != EXPECTED_FILES:
                    fail()
                for name in files:
                    parents = pathlib.PurePosixPath(name).parents
                    if any(parent.as_posix() in files for parent in parents):
                        fail()
                if any(name in files for name in directories):
                    fail()
                for name, member in files.items():
                    extracted += member.size
                    if extracted > MAX_EXTRACTED:
                        fail()
                    target = root / "ci_core" / pathlib.PurePosixPath(name)
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    source = bundle.extractfile(member)
                    if source is None:
                        fail()
                    payload = source.read(member.size + 1)
                    if len(payload) != member.size:
                        fail()
                    descriptor = os.open(
                        target,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                        0o600,
                    )
                    with os.fdopen(descriptor, "wb") as output:
                        output.write(payload)
            if not (root / "ci_core" / "gitcode_manual" / "cli.py").is_file():
                fail()
            sys.path.insert(0, str(root))
            from ci_core.gitcode_manual.cli import main as sdk_main

            raise SystemExit(sdk_main())
    except SystemExit:
        raise
    except Exception:
        fail()


main()
