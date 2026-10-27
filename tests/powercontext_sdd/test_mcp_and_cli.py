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

"""MCP 服务端与 CLI 的协议级测试。"""

from __future__ import annotations

import io
import json
import unittest

from powercontext_sdd.mcp_server import MCPServer, TOOLS
from powercontext_sdd.model import Task
from tests.powercontext_sdd.helpers import ProjectTestCase


class MCPTest(ProjectTestCase):
    def _server(self):
        return MCPServer(self.store())

    def test_initialize(self):
        response = self._server().handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(response["id"], 1)
        self.assertIn("protocolVersion", response["result"])
        self.assertIn("serverInfo", response["result"])

    def test_tools_list(self):
        response = self._server().handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {tool["name"] for tool in response["result"]["tools"]}
        self.assertEqual(names, {tool["name"] for tool in TOOLS})
        self.assertIn("resolve_context", names)

    def test_resolve_context_tool(self):
        response = self._server().handle({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "resolve_context", "arguments": {"task": "核心常量取值"}},
        })
        text = response["result"]["content"][0]["text"]
        self.assertIn("当前有效的工程上下文", text)
        self.assertFalse(response["result"]["isError"])
        # 参数名是 task（不是 text），必须真的被读进来并参与相关性打分
        self.assertIn("SPEC-1", text)
        self.assertIn("核心值必须是 1", text)

    def test_resolve_context_json_format(self):
        response = self._server().handle({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "resolve_context", "arguments": {"task": "核心常量取值", "format": "json"}},
        })
        data = json.loads(response["result"]["content"][0]["text"])
        self.assertIn("applicable_specs", data)
        self.assertEqual([a["id"] for a in data["applicable_specs"]], ["SPEC-1"])

    def test_check_verification_tool(self):
        response = self._server().handle({
            "jsonrpc": "2.0", "id": 5, "method": "tools/call",
            "params": {"name": "check_verification", "arguments": {}},
        })
        text = response["result"]["content"][0]["text"]
        self.assertIn("VERIFY-1", text)

    def test_export_and_import_handover(self):
        server = self._server()
        export = server.handle({
            "jsonrpc": "2.0", "id": 6, "method": "tools/call",
            "params": {"name": "export_handover", "arguments": {"task": "核心常量取值"}},
        })
        snapshot = json.loads(export["result"]["content"][0]["text"])
        imported = server.handle({
            "jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {"name": "import_handover", "arguments": {"snapshot": snapshot}},
        })
        report = json.loads(imported["result"]["content"][0]["text"])
        self.assertTrue(report["clean"])

    def test_unknown_method_returns_error(self):
        response = self._server().handle({"jsonrpc": "2.0", "id": 8, "method": "nope"})
        self.assertEqual(response["error"]["code"], -32601)

    def test_notification_returns_nothing(self):
        self.assertIsNone(self._server().handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_stdio_loop(self):
        server = self._server()
        stdin = io.StringIO(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"
            + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        )
        stdout = io.StringIO()
        server.serve(stdin, stdout)
        lines = [line for line in stdout.getvalue().splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)
        self.assertIn("tools", json.loads(lines[0])["result"])


class CLITest(ProjectTestCase):
    def test_resolve_command(self):
        from powercontext_sdd.cli import main

        code = main(["resolve", "核心常量取值", "--context", str(self.context_path), "--format", "json"])
        self.assertEqual(code, 0)

    def test_verify_command(self):
        from powercontext_sdd.cli import main

        code = main(["verify", "--context", str(self.context_path)])
        self.assertEqual(code, 0)

    def test_report_command(self):
        from powercontext_sdd.cli import main

        code = main(["report", "--context", str(self.context_path)])
        self.assertEqual(code, 0)

    def test_handover_export_and_check(self):
        import tempfile
        from pathlib import Path

        from powercontext_sdd.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            out = str(Path(tmp) / "snap.json")
            self.assertEqual(
                main(["handover", "export", "--context", str(self.context_path),
                      "--task", "核心常量取值", "--out", out]),
                0,
            )
            self.assertEqual(
                main(["handover", "check", "--context", str(self.context_path), "--snapshot", out]),
                0,
            )

    def test_context_arg_accepts_a_directory(self):
        # --context 既可以是文件也可以是目录，所有子命令要一致
        from powercontext_sdd.cli import main

        self.assertEqual(main(["report", "--context", str(self.root)]), 0)
        self.assertEqual(main(["verify", "--context", str(self.root)]), 0)

    def test_baseline_accepts_a_directory(self):
        import tempfile
        from pathlib import Path

        from powercontext_sdd.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            out = str(Path(tmp) / "context.json")
            self.assertEqual(
                main(["baseline", "--context", str(self.root), "--out", out]),
                0,
            )
            import json

            data = json.loads(Path(out).read_text(encoding="utf-8"))
            verify = next(a for a in data["artifacts"] if a["id"] == "VERIFY-1")
            self.assertTrue(verify["recorded_files"])
            self.assertIn("app/helper.py", verify["recorded_files"])


if __name__ == "__main__":
    unittest.main()
