# 运行说明

从干净环境到验证样例的完整步骤。全程只需要 **Python 3.10+、PyMySQL、一个 seekdb/OceanBase 实例**
（Docker 一条命令起）；MiniMax 步骤需要参赛者自己的 API Key。

## 1. 依赖版本

| 组件 | 版本 | 说明 |
|---|---|---|
| Python | 3.10+（实测 3.11 / 3.14） | 标准库即可运行模块与测试 |
| PyMySQL | >=1.1,<2 | seekdb/OceanBase 的 MySQL 协议客户端（本仓库 pyproject.toml 已含） |
| seekdb 或 OceanBase | OceanBase CE 4.x（实测 docker `oceanbase/oceanbase-ce` slim） | MySQL 协议 2881 |
| MiniMax SDK/接口 | HTTP API（OpenAI 兼容 `/chat/completions`） | 仅生成开发记录时需要 |

```bash
pip install 'pymysql>=1.1,<2'
```

## 2. 启动数据库（Docker）

```bash
docker run -d --name ob-sdd -p 2881:2881 \
  -e MODE=slim -e OB_SYS_PASSWORD=obsys123 oceanbase/oceanbase-ce
# 等待约 60~120 秒初始化完成
```

连接参数用环境变量传递（本机端口不同时改 `OB_PORT`）：

```bash
export OB_HOST=127.0.0.1 OB_PORT=2881 OB_USER=root@sys
export OB_PASSWORD=obsys123 OB_DB=powercontext_sdd
```

## 3. 数据初始化（工程资料入库）

```bash
cd <仓库根目录>
python minimax/scripts/ob_artifact_store.py init
python minimax/scripts/ob_artifact_store.py import \
  --context examples/sdd-context-manager/sample-app/context.template.json \
  --code-root examples/sdd-context-manager/sample-app
python minimax/scripts/ob_artifact_store.py stats
```

## 4. 调用入口

| 入口 | 命令 | 输入 → 输出 |
|---|---|---|
| 任务上下文解析 | `PYTHONPATH=src python -m powercontext_sdd resolve --context <ctx.json> --code-root <工程> --task "<任务>"` | 任务描述 → 有效规格/代码/测试/验证状态 |
| 报告 | `python -m powercontext_sdd report --context <ctx.json> --code-root <工程>` | 全部制品及生效状态 |
| 状态交接 | `python -m powercontext_sdd handover export|check ...` | 任务状态快照（保存/重启后回查） |
| MCP 服务端 | `python -m powercontext_sdd mcp --context <ctx.json> --code-root <工程>` | MCP stdio（供 Agent 插件调用） |
| DB 检索 | `python minimax/scripts/ob_artifact_store.py query --task "<任务>" --json` | 任务关键词 → 命中制品 JSON |
| DB→上下文 | `python minimax/scripts/build_context_from_db.py --task "<任务>" --out ctx.json` | 检索结果 → 模块可加载的 context.json |
| MiniMax 开发会话 | `MINIMAX_API_KEY=... python minimax/scripts/minimax_dev_session.py ...` | 上下文+任务 → 模型补丁 + 开发记录 |

## 5. 测试命令

```bash
cd examples/sdd-context-manager/sample-app
python -m unittest tests.test_order_filter -v      # 目标功能测试（4 个）
python -m unittest discover -s tests -v            # 回归测试（14 个）

cd <仓库根目录>
PYTHONPATH=src python -m unittest discover -s tests/powercontext_sdd -v   # 模块单测（69 个）

python minimax/scripts/demo_feature_change.py --offline     # 端到端（无 DB/Key 的复核模式）
python minimax/scripts/demo_feature_change.py               # 端到端（完整：DB + MiniMax）
```

红→绿复核（任意时刻可重现）：

```bash
python minimax/scripts/reset_sample.sh before   # 或 demo 内置的 reset_sample()
cd examples/sdd-context-manager/sample-app && python -m unittest tests.test_order_filter -v   # 预期 FAILED
python minimax/scripts/reset_sample.sh after
cd examples/sdd-context-manager/sample-app && python -m unittest tests.test_order_filter -v   # 预期 OK
```

## 6. 已知限制

1. **MiniMax 开发记录需真实会话生成**：`minimax_dev_session.py` 不伪造记录，
   没有 `MINIMAX_API_KEY` 时退出码 3。评审核对的 `records/minimax-dev-record.md`
   由参赛者在自己的 Token Plan 下运行上述命令产生。
2. **数据库检索为关键词精确匹配**（`LOCATE`），未接向量检索；对本验证样例的
   中文关键词足够，超大语料建议叠加全文/向量索引。
3. `expensive_items` 假定数量、单价为非负整数（示例工程的既有约定）。
4. 状态复用只覆盖任务状态（交接快照），不恢复完整 Agent 会话（题面允许）。
