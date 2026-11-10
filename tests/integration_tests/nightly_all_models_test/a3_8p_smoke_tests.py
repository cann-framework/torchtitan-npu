# Copyright (c) 2026 Huawei Technologies Co., Ltd. All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Adapter for the shared model-owned smoke catalog."""

from tests.integration_tests.nightly_all_models_test.smoke_catalog import build_smoke_list


def build_test_list():
    return [test for test in build_smoke_list() if test.ci_kind == "npu_smoke" and test.nnodes == 1 and test.ngpu == 8]
