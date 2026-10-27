"""扫描器：路径指错时必须报错，而不是默默生成一个空上下文。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sddctx.cli import main
from sddctx.scan import scan_repository


class ScanTest(unittest.TestCase):
    def test_missing_directory_raises(self):
        with self.assertRaises(FileNotFoundError):
            scan_repository("/definitely/not/here/repo")

    def test_file_instead_of_directory_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "a.txt"
            target.write_text("x", encoding="utf-8")
            with self.assertRaises(ValueError):
                scan_repository(str(target))

    def test_empty_directory_yields_no_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = scan_repository(tmp)
            self.assertEqual(result["artifacts"], [])
            self.assertEqual(result["stats"]["text_file_count"], 0)
            self.assertIn("scanned_root", result)

    def test_scan_command_warns_and_exits_nonzero_when_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "repo"
            empty.mkdir()
            out = Path(tmp) / "context.json"
            self.assertEqual(main(["scan", "--code-root", str(empty), "--out", str(out)]), 1)
            self.assertTrue(out.is_file())

    def test_scan_command_on_real_project_succeeds(self):
        project = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "sample-app"
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "context.json"
            self.assertEqual(main(["scan", "--code-root", str(project), "--out", str(out)]), 0)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertGreater(data["stats"]["text_file_count"], 0)
            self.assertGreater(len(data["artifacts"]), 0)
            # 扫描出来的一律是 draft，绝不能自动生效
            for artifact in data["artifacts"]:
                self.assertEqual(artifact["status"], "draft")


class TemplateTest(unittest.TestCase):
    def test_template_is_loadable(self):
        template = Path(__file__).resolve().parents[1] / "data" / "templates" / "context.template.json"
        self.assertTrue(template.is_file())
        data = json.loads(template.read_text(encoding="utf-8"))
        self.assertIn("artifacts", data)
        self.assertGreaterEqual(len(data["artifacts"]), 4)
        kinds = {a["kind"] for a in data["artifacts"]}
        self.assertEqual(kinds, {"spec", "contract", "decision", "verification"})

    def test_template_loads_as_a_store(self):
        from sddctx.store import ContextStore

        template = Path(__file__).resolve().parents[1] / "data" / "templates" / "context.template.json"
        store = ContextStore.load(str(template))
        self.assertEqual(len(store.artifacts), 4)


if __name__ == "__main__":
    unittest.main()
