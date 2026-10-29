# MiniMax 开发记录（ORDER-FILTER-001）

> **生成方式**：本文件由真实 MiniMax 会话生成，命令如下（需要你自己的 MiniMax API Key，
> 即参赛获赠的 Token Plan）。`minimax_dev_session.py` 在没有 Key 时退出码 3，不产生、
> 也不允许伪造任何记录。
>
> ```bash
> export MINIMAX_API_KEY=<你的 Key>
> export MINIMAX_MODEL=MiniMax-Text-01          # 可选
> python minimax/scripts/ob_artifact_store.py init        # 本地/云端 seekdb 或 OceanBase
> python minimax/scripts/ob_artifact_store.py import \
>     --context examples/sdd-context-manager/sample-app/context.template.json \
>     --code-root examples/sdd-context-manager/sample-app
> python minimax/scripts/build_context_from_db.py \
>     --task "订单明细按小计筛选，边界阈值 0 返回全部" --out /tmp/ctx.json
> python minimax/scripts/minimax_dev_session.py --context /tmp/ctx.json \
>     --task-file minimax/TASK_ORDER_FILTER.md \
>     --target examples/sdd-context-manager/sample-app/app/order.py
> ```
>
> 运行后本文件会被真实会话内容**追加**（会话时间、模型、任务、提供的上下文条目、
> 模型输出的实现补丁），完整请求/响应同时存于 `minimax-dev-record.jsonl`。
> 模型输出补丁写入 `minimax/out/minimax_patch_order.py`；如需让提交 diff 与模型输出
> 逐字对应，`reset_sample.sh before` 后把补丁追加进 `app/order.py` 再提交。

## 记录应包含的内容（核对要点）

1. **本次任务**：ORDER-FILTER-001 的任务描述（要改什么 / 必须保持的行为）；
2. **提供的上下文**：`build_context_from_db.py` 从 seekdb/OceanBase 检索出的制品清单
   （SPEC-001 / SPEC-101 / ADR-101 / VERIFY-101 / app/order.py / app/pricing.py /
   tests/test_order_filter.py 等，与 `VERIFICATION.md` §2 一致）；
3. **代码修改过程**：模型对 `Order.expensive_items` 的实现输出；
4. **与提交差异的对应**：`CODE_DIFF.md` §3 的 +11 行即该实现。
