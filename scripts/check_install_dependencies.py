#!/usr/bin/env python3
# Copyright (c) 2026 Huawei Technologies Co., Ltd. All Rights Reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Post-install smoke check for the NumPy / PyArrow / datasets stack.

Run after ``python3 -m pip install -r requirements.txt``. PyArrow 26 requires
NumPy 2 at import time but does not declare it in resolver-visible metadata,
while ``triton-ascend==3.2.1`` pins NumPy 1.26.4. Importing the three packages
here turns that otherwise silent conflict into an actionable failure.
"""

from __future__ import annotations

import sys

MAX_PYARROW_EXCLUSIVE = 26


def release_tuple(version: str) -> tuple[int, ...]:
    """Return the leading numeric components of a version (``"26.0.1"`` -> ``(26, 0, 1)``)."""
    numbers: list[int] = []
    for part in version.split("+", 1)[0].split("."):
        digits = ""
        for char in part:
            if not char.isdigit():
                break
            digits += char
        if not digits:
            break
        numbers.append(int(digits))
    return tuple(numbers)


def is_supported_pyarrow(version: str) -> bool:
    release = release_tuple(version)
    return bool(release) and release < (MAX_PYARROW_EXCLUSIVE,)


def main() -> int:
    try:
        import datasets
        import numpy
        import pyarrow
    except ImportError as error:
        print(f"FAILED: cannot import the dataset stack: {error}", file=sys.stderr)
        return 1

    pyarrow_version = pyarrow.__version__
    if not is_supported_pyarrow(pyarrow_version):
        print(
            f"FAILED: pyarrow {pyarrow_version} requires NumPy >= 2, but the supported "
            "triton-ascend stack pins NumPy 1.26.4. Install pyarrow<26.",
            file=sys.stderr,
        )
        return 1

    print(f"numpy {numpy.__version__}, pyarrow {pyarrow_version}, datasets {datasets.__version__}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
