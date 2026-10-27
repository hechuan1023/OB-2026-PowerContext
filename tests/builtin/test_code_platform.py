# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Unsupported-platform failures precede native indexing side effects."""

import sys
import time
from pathlib import Path

import pytest

from powercontext.builtin.code.cache import GenerationCache, hash_file, read_private
from powercontext.builtin.code.capture import capture_repository
from powercontext.builtin.code.errors import CodeError
from powercontext.builtin.code.models import CodeLimits, CodeRepositoryConfig
from powercontext.builtin.code.seekdb import SeekDBGraphStore
from powercontext.builtin.code.telemetry import stage
from powercontext.builtin.code.worker import run


@pytest.mark.parametrize("operation", ["capture", "hash", "read", "publish", "pointer", "clear", "seekdb", "worker"])
def test_windows_native_operations_fail_before_writing_files(operation: str, tmp_path: Path, monkeypatch) -> None:
    original = tmp_path / "original"
    original.write_bytes(b"retained")
    cache = GenerationCache(tmp_path)
    store = SeekDBGraphStore(tmp_path / "database", {})
    config = CodeRepositoryConfig(root=tmp_path)
    deadline = time.monotonic() + 10
    operations = {
        "capture": lambda: capture_repository(config, CodeLimits(), deadline),
        "hash": lambda: hash_file(original, deadline),
        "read": lambda: read_private(original),
        "publish": lambda: cache.publish(tmp_path, {}, deadline, 1024),
        "pointer": lambda: cache.replace_json("current.json", {}),
        "clear": lambda: cache.clear(deadline),
        "seekdb": lambda: store.create(tmp_path, [], [], deadline),
        "worker": lambda: run(tmp_path / "absent-manifest.json"),
    }
    with monkeypatch.context() as patch:
        patch.setattr(sys, "platform", "win32")
        with pytest.raises(CodeError, match="unsupported_code_platform"):
            operations[operation]()
    assert original.read_bytes() == b"retained"
    assert list(tmp_path.iterdir()) == [original]


def test_windows_telemetry_keeps_outcome_without_posix_resource_metrics(monkeypatch) -> None:
    with monkeypatch.context() as patch:
        patch.setattr(sys, "platform", "win32")
        with stage("index") as attributes:
            attributes["files"] = 3
    assert attributes["files"] == 3
    assert attributes["outcome"] == "success"
    assert "seconds" in attributes
    assert "process_lifetime_max_rss_bytes" not in attributes
    assert "children_lifetime_max_rss_bytes" not in attributes
