# Copyright (c) 2026 Huawei Technologies Co., Ltd. All Rights Reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""CPU tests for the post-install NumPy / PyArrow / datasets smoke check."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.tooling

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/check_install_dependencies.py"
REQUIREMENTS = ROOT / "requirements.txt"

_SPEC = importlib.util.spec_from_file_location("check_install_dependencies", SCRIPT)
check_install_dependencies = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_install_dependencies)


def _write_stack(site: Path, *, pyarrow_version: str | None = None, pyarrow_error: str | None = None) -> None:
    (site / "numpy.py").write_text('__version__ = "1.26.4"\n')
    (site / "datasets.py").write_text('__version__ = "4.7.0"\n')
    pyarrow = site / "pyarrow"
    pyarrow.mkdir()
    if pyarrow_error is not None:
        (pyarrow / "__init__.py").write_text(f"raise ImportError({pyarrow_error!r})\n")
    else:
        (pyarrow / "__init__.py").write_text(f'__version__ = "{pyarrow_version}"\n')


def _run(site: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=site,
        env=dict(os.environ, PYTHONPATH=str(site)),
        capture_output=True,
        text=True,
        check=False,
    )


def test_release_tuple_parses_leading_release_numbers():
    assert check_install_dependencies.release_tuple("25.0.1") == (25, 0, 1)
    assert check_install_dependencies.release_tuple("26.0.0.post1") == (26, 0, 0)
    assert check_install_dependencies.release_tuple("26.0.0+local") == (26, 0, 0)
    assert check_install_dependencies.release_tuple("") == ()


@pytest.mark.parametrize(
    ("version", "supported"),
    [("24.0.0", True), ("25.0.1", True), ("2.0.0", True), ("26.0.0", False), ("26.0.1", False)],
)
def test_is_supported_pyarrow_rejects_26_and_newer(version, supported):
    assert check_install_dependencies.is_supported_pyarrow(version) is supported


def test_smoke_check_accepts_pyarrow_below_26(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    _write_stack(site, pyarrow_version="25.0.1")

    result = _run(site)

    assert result.returncode == 0, result.stderr
    assert "numpy 1.26.4, pyarrow 25.0.1, datasets 4.7.0" in result.stdout


def test_smoke_check_rejects_pyarrow_26(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    _write_stack(site, pyarrow_version="26.0.1")

    result = _run(site)

    assert result.returncode != 0
    assert "pyarrow 26.0.1 requires NumPy >= 2" in result.stderr


def test_smoke_check_reports_import_failure(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    _write_stack(site, pyarrow_error="pyarrow requires NumPy 2.0 or newer, found 1.26.4")

    result = _run(site)

    assert result.returncode != 0
    assert "cannot import the dataset stack" in result.stderr
    assert "pyarrow requires NumPy 2.0 or newer" in result.stderr


def test_requirements_pin_supported_arrow_and_triton_stack():
    lines = [line.strip() for line in REQUIREMENTS.read_text().splitlines()]

    assert "pyarrow<26" in lines
    assert "triton-ascend==3.2.1" in lines
