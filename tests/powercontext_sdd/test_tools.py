# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""工具层：切词、路径通配、依赖闭包、模型解析。"""

from __future__ import annotations

import unittest

from powercontext_sdd.deps import dependency_closure
from powercontext_sdd.model import Artifact, ArtifactKind, Lifecycle, Task
from powercontext_sdd.textutil import glob_to_regex, overlap_ratio, path_matches, token_set
from tests.powercontext_sdd.helpers import ProjectTestCase


class TokenizeTest(unittest.TestCase):
    def test_mixed_language_tokens(self):
        tokens = token_set("修改 bplus_tree.cpp 的分裂逻辑")
        self.assertIn("bplus_tree", tokens)
        self.assertIn("修改", tokens)

    def test_cjk_bigrams(self):
        tokens = token_set("折扣")
        self.assertIn("折", tokens)
        self.assertIn("扣", tokens)

    def test_overlap_ratio(self):
        self.assertAlmostEqual(overlap_ratio({"a", "b"}, {"a", "b", "c"}), 1.0)
        self.assertAlmostEqual(overlap_ratio({"a"}, {"b"}), 0.0)


class GlobTest(unittest.TestCase):
    def test_star_does_not_cross_directory(self):
        self.assertTrue(path_matches("src/a.cpp", ["src/*"]))
        self.assertFalse(path_matches("src/sub/a.cpp", ["src/*"]))

    def test_double_star_crosses_directory(self):
        self.assertTrue(path_matches("src/sub/a.cpp", ["src/**"]))
        self.assertTrue(path_matches("src/a.cpp", ["src/**"]))

    def test_exact_path(self):
        self.assertTrue(path_matches("app/core.py", ["app/core.py"]))
        self.assertFalse(path_matches("app/core.py.bak", ["app/core.py"]))

    def test_regex_escapes_dots(self):
        regex = glob_to_regex("a.b")
        self.assertTrue(regex.match("a.b"))
        self.assertFalse(regex.match("axb"))


class ModelTest(unittest.TestCase):
    def test_from_dict_keeps_extra_fields(self):
        artifact = Artifact.from_dict({
            "id": "V1", "kind": "verification", "title": "t",
            "targets": ["app/a.py"], "test_path": "tests/t.py", "validates": ["S1"],
        })
        self.assertEqual(artifact.kind, ArtifactKind.VERIFICATION)
        self.assertEqual(artifact.targets, ["app/a.py"])
        self.assertEqual(artifact.validates, ["S1"])

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            Artifact.from_dict({"id": "X", "kind": "unknown"})

    def test_unknown_status_raises(self):
        with self.assertRaises(ValueError):
            Artifact.from_dict({"id": "X", "kind": "spec", "status": "wat"})

    def test_task_from_dict_accepts_message_alias(self):
        task = Task.from_dict({"message": "干活"})
        self.assertEqual(task.text, "干活")
        self.assertEqual(task.session_id, "default")

    def test_lifecycle_values(self):
        self.assertEqual(Lifecycle.parse("active"), Lifecycle.ACTIVE)
        self.assertEqual(Lifecycle.parse(" SUPERSEDED "), Lifecycle.SUPERSEDED)


class DependencyClosureTest(ProjectTestCase):
    def test_closure_follows_relative_import(self):
        closure = dependency_closure(self.root, ["app/helper.py"])
        self.assertIn("app/helper.py", closure)
        self.assertIn("app/core.py", closure)

    def test_closure_stops_at_leaves(self):
        closure = dependency_closure(self.root, ["app/core.py"])
        self.assertEqual(closure, ["app/core.py"])

    def test_closure_is_sorted_and_unique(self):
        closure = dependency_closure(self.root, ["app/helper.py", "app/core.py"])
        self.assertEqual(closure, sorted(set(closure)))


if __name__ == "__main__":
    unittest.main()
