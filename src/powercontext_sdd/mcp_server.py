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

"""MCP 服务端：把上下文模块接进任意 AI 开发助手。

用标准库手写 JSON-RPC 2.0 over stdio，不装任何第三方包。
原因很实际：评测机上不保证有 pip 网络，而 MCP 客户端（Claude Code / Cursor /
Qoder / 其他 IDE 插件）只要求 stdio 上跑 newline-delimited JSON。

暴露的工具：

- ``resolve_context``   给一个开发任务，返回当前有效的上下文（Markdown，可直接进提示词）
- ``check_verification``查看「先前的验证」现在还作不数
- ``list_artifacts``    列出工程制品（规格 / 契约 / 决策 / 验证）
- ``export_handover``   导出一份可交接的上下文快照
- ``import_handover``   导入快照并回查哪些内容已经过期
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List

from powercontext_sdd.handover import export_snapshot, import_snapshot
from powercontext_sdd.model import ArtifactKind, Task
from powercontext_sdd.render import render_prompt_context, render_report
from powercontext_sdd.resolver import build_validity, detect_conflicts, evaluate_verification, resolve
from powercontext_sdd.store import ContextStore

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "sdd-context-manager", "version": "1.0.0"}

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "resolve_context",
        "description": (
            "根据一个开发任务，返回当前仍然有效的工程上下文：需要遵守的规格、接口契约、"
            "设计决策、相关代码，以及先前验证的当前效力。同时显式标出已经失效的决策。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "开发任务的自然语言描述"},
                "session_id": {"type": "string", "description": "会话 ID，用于跨轮次保持上下文"},
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "任务涉及的代码文件（相对工程根目录）",
                },
                "min_score": {"type": "number", "description": "相关性阈值，默认 0.10"},
                "format": {"type": "string", "enum": ["markdown", "report", "json"], "description": "输出格式"},
            },
            "required": ["task"],
        },
    },
    {
        "name": "check_verification",
        "description": "检查工程里的测试/验证记录在当前代码上是否仍然成立（valid / stale_code / stale_test / superseded_spec / orphan）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "verification_id": {"type": "string", "description": "指定某一条验证；省略则检查全部"},
            },
        },
    },
    {
        "name": "list_artifacts",
        "description": "列出工程里的规格、契约、决策与验证制品，并标注每个制品当前是否生效。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["spec", "contract", "decision", "verification"],
                    "description": "按类型过滤",
                },
            },
        },
    },
    {
        "name": "export_handover",
        "description": "为当前任务导出一份可交接的上下文快照（含制品版本与代码指纹），用于跨会话或换人接手。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "开发任务描述"},
                "session_id": {"type": "string"},
                "notes": {"type": "string", "description": "给接手人的补充说明"},
                "open_questions": {"type": "array", "items": {"type": "string"}},
                "decisions_made": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["task"],
        },
    },
    {
        "name": "import_handover",
        "description": "导入一份交接快照，逐条回查哪些制品已失效、版本已变、验证已不成立。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "snapshot": {"type": "object", "description": "export_handover 导出的快照对象"},
            },
            "required": ["snapshot"],
        },
    },
]


def _text_result(text: str, is_error: bool = False) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


class MCPServer:
    """一个最小但完整的 MCP stdio 服务端。"""

    def __init__(self, store: ContextStore) -> None:
        self.store = store

    # ------------------------------------------------------------------ 工具

    def _tool_resolve_context(self, args: Dict[str, Any]) -> Dict[str, Any]:
        task = Task.from_dict(args)
        bundle = resolve(
            self.store,
            task,
            min_score=float(args.get("min_score", 0.10)),
        )
        fmt = str(args.get("format", "markdown"))
        if fmt == "json":
            return _text_result(json.dumps(bundle.to_dict(), ensure_ascii=False, indent=2))
        if fmt == "report":
            return _text_result(render_report(bundle))
        return _text_result(render_prompt_context(bundle))

    def _tool_check_verification(self, args: Dict[str, Any]) -> Dict[str, Any]:
        validity = build_validity(self.store)
        wanted = str(args.get("verification_id", "") or "").strip()
        rows: List[str] = []
        for artifact in sorted(self.store.verifications, key=lambda a: a.id):
            if wanted and artifact.id != wanted:
                continue
            status = evaluate_verification(artifact, self.store, validity)
            rows.append(
                "[%s] %s %s — %s" % (
                    "有效" if status.valid else status.state,
                    artifact.id,
                    artifact.title,
                    status.detail or "仍然成立",
                )
            )
            if status.changed_files:
                rows.append("    受影响文件：%s" % ", ".join(status.changed_files))
        if not rows:
            return _text_result("没有找到匹配的验证记录。", is_error=False)
        return _text_result("\n".join(rows))

    def _tool_list_artifacts(self, args: Dict[str, Any]) -> Dict[str, Any]:
        validity = build_validity(self.store)
        conflicts = detect_conflicts(self.store, validity)
        shadowed = {loser.id: conflict for conflict in conflicts for loser in conflict.losers}
        wanted = str(args.get("kind", "") or "").strip().lower()
        rows: List[str] = []
        for artifact in sorted(self.store.artifacts.values(), key=lambda a: (a.kind.value, a.id)):
            if wanted and artifact.kind.value != wanted:
                continue
            state = validity.get(artifact.id)
            if state and state.effective:
                conflict = shadowed.get(artifact.id)
                if conflict is not None:
                    mark = "被覆盖"
                    tail = " → 与 %s 冲突，按「%s」裁决" % (conflict.key, conflict.winner.id)
                else:
                    mark = "生效"
                    tail = ""
            else:
                mark = "失效"
                tail = ""
                if state and state.replaced_by:
                    tail = " → 由 %s 取代" % state.replaced_by
            rows.append(
                "[%s][%s] %s %s — %s%s"
                % (artifact.kind.value, mark, artifact.id, artifact.title, artifact.summary, tail)
            )
        return _text_result("\n".join(rows) if rows else "工程里还没有制品。")

    def _tool_export_handover(self, args: Dict[str, Any]) -> Dict[str, Any]:
        task = Task.from_dict(args)
        bundle = resolve(self.store, task)
        snapshot = export_snapshot(
            bundle,
            self.store,
            notes=str(args.get("notes", "") or ""),
            open_questions=[str(x) for x in (args.get("open_questions") or [])],
            decisions_made=[str(x) for x in (args.get("decisions_made") or [])],
        )
        return _text_result(json.dumps(snapshot, ensure_ascii=False, indent=2))

    def _tool_import_handover(self, args: Dict[str, Any]) -> Dict[str, Any]:
        snapshot = args.get("snapshot")
        if not isinstance(snapshot, dict):
            return _text_result("参数 snapshot 必须是一个对象。", is_error=True)
        report = import_snapshot(snapshot, self.store)
        return _text_result(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))

    _DISPATCH = {
        "resolve_context": _tool_resolve_context,
        "check_verification": _tool_check_verification,
        "list_artifacts": _tool_list_artifacts,
        "export_handover": _tool_export_handover,
        "import_handover": _tool_import_handover,
    }

    # -------------------------------------------------------------- JSON-RPC

    def handle(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        method = str(request.get("method", ""))
        request_id = request.get("id")
        params = request.get("params") or {}

        if method.startswith("notifications/"):
            return None

        if method == "initialize":
            result: Dict[str, Any] = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            name = str(params.get("name", ""))
            arguments = params.get("arguments") or {}
            handler = self._DISPATCH.get(name)
            if handler is None:
                return self._error(request_id, -32601, "未知的工具: %s" % name)
            try:
                result = handler(self, arguments)
            except Exception as exc:  # noqa: BLE001 - MCP 协议要求把异常转成结果
                result = _text_result("工具执行失败: %s" % exc, is_error=True)
        else:
            return self._error(request_id, -32601, "不支持的方法: %s" % method)

        if request_id is None:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

    def serve(self, stdin=None, stdout=None) -> None:
        """主循环：读一行 JSON，写一行 JSON。"""
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                response: Optional[Dict[str, Any]] = self._error(None, -32700, "JSON 解析失败")
            else:
                response = self.handle(request)
            if response is not None:
                stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                stdout.flush()


def main(argv: List[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="SDD 上下文 MCP 服务端")
    parser.add_argument("--context", required=True, help="context.json 路径或所在目录")
    parser.add_argument("--code-root", default=None, help="代码根目录（默认取 context.json 里的配置）")
    args = parser.parse_args(argv)

    store = ContextStore.load(args.context, args.code_root)
    MCPServer(store).serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
