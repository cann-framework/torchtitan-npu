# Copyright (c) 2026 Huawei Technologies Co., Ltd. All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Adapter for the shared model-owned smoke catalog."""

from tests.integration_tests.nightly_all_models_test.smoke_catalog import build_smoke_list, load_smoke_catalog


def build_test_list():
    tests = {test.test_name: test for test in build_smoke_list()}
    return [tests[name] for name in load_smoke_catalog()["profiles"]["a3_16p_smoke_tests"]]
