# Copyright (c) 2026 Huawei Technologies Co., Ltd. All Rights Reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Load smoke definitions from the model repository's declarative catalog."""

import json
import re
from pathlib import Path

from tests.integration_tests import OverrideDefinitions

CATALOG_PATH = Path(__file__).resolve().parents[3] / ".ci/lite_actions/smoke_cases.json"
SELECTOR = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate catalog field: {key}")
        obj[key] = value
    return obj


def load_smoke_catalog(path=None):
    raw = Path(CATALOG_PATH if path is None else path).read_bytes()
    if len(raw) > 262144:
        raise ValueError("smoke catalog exceeds size limit")
    data = json.loads(raw, object_pairs_hook=_unique_object)
    if (
        not isinstance(data, dict)
        or set(data) != {"version", "default_profile", "defaults", "cases", "profiles"}
        or type(data["version"]) is not int
        or data["version"] != 1
    ):
        raise ValueError("invalid smoke catalog")
    defaults = data["defaults"]
    if not isinstance(defaults, dict) or set(defaults) != {"worker", "target_pool", "docker"}:
        raise ValueError("invalid smoke defaults")
    if any(not isinstance(v, str) or not v for v in defaults.values()) or defaults["worker"] not in {"blue", "yellow"}:
        raise ValueError("invalid smoke defaults")
    cases = data["cases"]
    if not isinstance(cases, list) or not 1 <= len(cases) <= 256:
        raise ValueError("invalid smoke case count")
    names = set()
    keys = {"test_id", "label", "kind", "worker", "nodes", "devices_per_node", "timeout", "env_vars"}
    for case in cases:
        if not isinstance(case, dict) or set(case) != keys:
            raise ValueError("invalid smoke case fields")
        name = case["test_id"]
        if not isinstance(name, str) or not SELECTOR.fullmatch(name) or name in names:
            raise ValueError("duplicate or invalid smoke test ID")
        names.add(name)
        if case["kind"] not in {"worker_probe", "npu_smoke"} or case["worker"] not in {"blue", "yellow"}:
            raise ValueError("invalid smoke case kind or worker")
        if not isinstance(case["label"], str) or not case["label"].strip() or len(case["label"]) > 115:
            raise ValueError("invalid smoke label")
        if type(case["nodes"]) is not int or type(case["devices_per_node"]) is not int:
            raise ValueError("invalid smoke topology")
        if case["kind"] == "worker_probe":
            if case["nodes"] != 0 or case["devices_per_node"] != 0 or case["env_vars"] != {}:
                raise ValueError("worker probes require zero devices and no environment")
        elif not 1 <= case["nodes"] <= 16 or not 1 <= case["devices_per_node"] <= 256:
            raise ValueError("invalid smoke topology")
        if type(case["timeout"]) is not int or not 1 <= case["timeout"] <= 14400:
            raise ValueError("invalid smoke timeout")
        if not isinstance(case["env_vars"], dict) or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in case["env_vars"].items()
        ):
            raise ValueError("invalid smoke environment")
    profiles = data["profiles"]
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("invalid smoke profiles")
    for name, items in profiles.items():
        if (
            not isinstance(name, str)
            or not SELECTOR.fullmatch(name)
            or not isinstance(items, list)
            or not 1 <= len(items) <= 256
        ):
            raise ValueError("invalid smoke profile")
        if any(not isinstance(item, str) or item not in names for item in items):
            raise ValueError("unknown smoke profile case")
        seen = set()
        for item in items:
            case = next(c for c in cases if c["test_id"] == item)
            if item in seen and case["kind"] != "npu_smoke":
                raise ValueError("duplicate non-smoke case")
            seen.add(item)
    if not isinstance(data["default_profile"], str) or data["default_profile"] not in profiles:
        raise ValueError("unknown default smoke profile")
    return data


def build_smoke_list() -> list[OverrideDefinitions]:
    data = load_smoke_catalog()
    return [
        OverrideDefinitions(
            test_name=case["test_id"],
            test_descr=case["label"],
            ci_kind=case["kind"],
            ci_overrides={
                "worker": case["worker"],
                **(
                    {"target_pool": data["defaults"]["target_pool"], "docker": data["defaults"]["docker"]}
                    if case["kind"] == "npu_smoke"
                    else {}
                ),
            },
            nnodes=case["nodes"],
            ngpu=case["devices_per_node"],
            timeout=case["timeout"],
            env_vars=case["env_vars"] or None,
            check_loss=False,
        )
        for case in data["cases"]
    ]
