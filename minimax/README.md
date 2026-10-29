# 一页技术说明：SDD 上下文管理器 × MiniMax × seekdb/OceanBase × PowerContext

> 赛题：MiniMax × OceanBase「规有所依——面向软件持续演进的 SDD 上下文管理系统」
> 分支：`feature/minimax-submission`（基于 `2026-competition-ecosystem` 后代提交 `ab7d16a` 开发）

## 1. 模块结构

```
src/powercontext_sdd/          SDD 上下文管理器（PowerContext 的一个模块）
  cli.py / mcp_server.py       调用入口：CLI 与 MCP stdio 服务端
  scan.py / resolver.py        制品扫描、任务→有效上下文解析、冲突裁决
  handover.py                  任务状态（交接快照）保存与重启后回查
  store.py / model.py          制品存储与数据模型
minimax/                       本题提交材料（本目录）
  scripts/ob_artifact_store.py seekdb/OceanBase 工程资料导入与检索
  scripts/build_context_from_db.py  用 DB 检索结果组装任务上下文
  scripts/minimax_dev_session.py     MiniMax 开发助手真实会话（生成开发记录）
  scripts/demo_feature_change.py     端到端验证编排（红→绿 + 状态复用）
  snapshots/                   修改前/后代码快照（可随时重现红→绿）
  records/                     技术使用记录（DB 查询、状态写入/复用、MiniMax 会话）
```

## 2. 上下文选择方式

1. 工程资料（规格 SPEC / 契约 CONTRACT / 决策 DECISION / 验证记录 VERIFICATION，
   以及代码、测试文件）**导入 seekdb/OceanBase 表 `sdd_artifacts`**；
2. 开发任务到来时，按任务关键词在库中**检索**（MySQL 协议，`LOCATE` 匹配标题/关键词/摘要）；
3. 检索结果组装成 `context.json`（含来源 `source` 字段），由 `python -m powercontext_sdd resolve`
   进一步裁剪：按 scope/关键词匹配、剔除 `superseded`/`deprecated` 制品、裁决冲突、
   标注每个验证记录当前是否仍成立（代码指纹比对）；
4. 输出的上下文明确列出：本次任务适用的规格条目、需要修改的代码文件/符号、
   验证功能与兼容性的测试、以及（通过 handover 快照）已完成/待办/已有验证结果。

## 3. 各项技术的分工

| 技术 | 承担的作用 | 证据 |
|---|---|---|
| **MiniMax** | 参与 ORDER-FILTER-001 的代码生成/修改（开发助手按上下文输出实现） | `records/minimax-dev-record.md`（由 RUN.md 的真实会话命令生成）、`minimax/out/minimax_patch_order.py` |
| **seekdb/OceanBase** | 存储工程资料与索引，检索结果直接用于构造任务上下文 | `records/db-query-records.md`（真实 SQL 与结果）、`build_context_from_db.py` |
| **PowerContext** | 保存任务进展与验证结果（handover 交接快照），模块重启后读取并用于回查 | `records/powercontext-state.md`（export/check 真实输出） |

## 4. 必做项自查表（对应题面 4.1）

| 验收项 | 自查 | 复核入口 |
|---|---|---|
| 能够运行 | ✅ 纯标准库 + PyMySQL；`RUN.md` 从干净环境到验证样例全步骤 | `minimax/RUN.md` |
| MiniMax 实际参与 | ✅ 开发记录含任务、上下文、模型输出，对应提交 diff 的 `expensive_items`（记录由真实会话命令生成，见记录文件头） | `records/minimax-dev-record.md` |
| 上下文正确 | ✅ 必含来源见 `VERIFICATION.md`；规格更新重导入后输出随检索变化 | `VERIFICATION.md` §3 |
| 数据库实际参与 | ✅ 制品入库、检索结果组装上下文，SQL 与结果存档 | `records/db-query-records.md` |
| 状态能够复用 | ✅ 空状态→export→重启→check 回查过期项 | `records/powercontext-state.md` |
| 代码修改有效 | ✅ 目标测试修改前 FAILED（AttributeError，非环境错误）→ 修改后 OK，回归 14/14 OK | `TEST_RESULTS.md` |

## 5. 材料索引

| 材料 | 文件 |
|---|---|
| 运行说明 | `minimax/RUN.md` |
| 验证样例 | `minimax/VERIFICATION.md` + `minimax/TASK_ORDER_FILTER.md` |
| 代码与测试 | `minimax/CODE_DIFF.md` + `minimax/snapshots/` + `examples/sdd-context-manager/sample-app/tests/test_order_filter.py` |
| 测试结果 | `minimax/TEST_RESULTS.md` + `minimax/records/verification-run.md` |
| 技术使用记录 | `minimax/records/`（db-query / powercontext-state / minimax-dev） |

已知限制见 `RUN.md` §6。
