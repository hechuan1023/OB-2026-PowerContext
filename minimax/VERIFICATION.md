# 验证样例：ORDER-FILTER-001 订单明细按小计筛选

## 1. 任务描述

见 `minimax/TASK_ORDER_FILTER.md`。摘要：

- **要改什么**：`sample-app` 的 `Order` 增加 `expensive_items(min_subtotal_cents)`——
  按商品小计筛选明细、降序返回，边界阈值 `<=0` 返回全部；
- **哪些原有行为必须保持**：小计/折扣门槛/四舍五入到分（SPEC-001）不变，
  支付与定价行为不变，既有 10 个测试保持全绿。

## 2. 上下文中必须包含的来源（评审核对清单）

| 类别 | 来源 | 内容 |
|---|---|---|
| 规格 | `sample-app/docs/requirements.md` → SPEC-001 | 金额单位、折扣门槛、四舍五入到分（本任务不得破坏） |
| 规格 | `sample-app/docs/requirements.md` → SPEC-101 | 本次任务的明细筛选需求（要改什么） |
| 决策 | `sample-app/docs/decisions.md` → ADR-003、ADR-101 | 折扣计算位置；筛选排序与边界语义 |
| 代码 | `sample-app/app/order.py`（Order.expensive_items） | 本次修改目标文件/符号 |
| 代码 | `sample-app/app/pricing.py` | 修改涉及的依赖（禁止改动，回归覆盖） |
| 测试 | `sample-app/tests/test_order_filter.py`（VERIFY-101） | 目标功能测试（4 例，含边界 0 / 负数） |
| 测试 | `sample-app/tests/test_order.py`、`test_pricing.py` | 兼容性回归测试 |

上述条目均由 **seekdb/OceanBase 检索**得到（`ob_artifact_store.py query`），
再由 `build_context_from_db.py` 组装成上下文，来源字段逐条可见。

## 3. 初始数据

- 工程资料：`context.template.json` 的制品 + `app/`、`tests/` 源文件（共 23 条）；
- 状态：空（没有任何交接快照）；
- 代码状态：修改前（`minimax/snapshots/order_before.py`）。

## 4. 操作步骤与预期结果

```bash
# 0) 数据库初始化与资料入库（见 RUN.md §2-3）
python minimax/scripts/ob_artifact_store.py init
python minimax/scripts/ob_artifact_store.py import --context ... --code-root ...

# 1) 还原修改前 → 目标测试应当变红
python minimax/scripts/reset_sample.sh before
cd examples/sdd-context-manager/sample-app
python -m unittest tests.test_order_filter -v
#   预期：Ran 4 tests ... FAILED (errors=4)
#         AttributeError: 'Order' object has no attribute 'expensive_items'
#   （失败原因是目标行为未实现，不是缺依赖/环境错误）

# 2) 检索构建上下文
python minimax/scripts/build_context_from_db.py --task "订单明细按小计筛选" --out /tmp/ctx.json
#   预期：列出命中的 SPEC-101、SPEC-001、ADR-101、VERIFY-101、order.py、test_order_filter.py 等

# 3) MiniMax 完成修改（真实会话，见 RUN.md §6.1）
MINIMAX_API_KEY=... python minimax/scripts/minimax_dev_session.py --context /tmp/ctx.json \
  --task-file minimax/TASK_ORDER_FILTER.md --target .../app/order.py

# 4) 目标测试变绿 + 回归全绿
python -m unittest tests.test_order_filter -v     # 预期 OK（4 例）
python -m unittest discover -s tests -v           # 预期 OK（14 例）

# 5) 状态保存 → 重启 → 回查
python -m powercontext_sdd handover export --task "..." --out /tmp/snap.json ...
python -m powercontext_sdd handover check --snapshot /tmp/snap.json --format json
#   预期：输出快照中因代码变化而过期的验证项（stale_code 清单）
```

一键编排（等价于上面 0-5，输出汇总记录）：

```bash
python minimax/scripts/demo_feature_change.py               # 完整
python minimax/scripts/demo_feature_change.py --use-submitted  # 无 MiniMax Key 复核
```

## 5. 重置方法

```bash
python minimax/scripts/reset_sample.sh before|after   # 代码状态
rm -f minimax/records/context_from_db.json minimax/records/handover_snapshot.json  # 状态
# 数据库重置：
python - <<'EOF'
import os, pymysql
c = pymysql.connect(host=os.environ.get("OB_HOST","127.0.0.1"), port=int(os.environ.get("OB_PORT","2881")),
                    user=os.environ.get("OB_USER","root@sys"), password=os.environ.get("OB_PASSWORD","obsys123"),
                    autocommit=True)
with c.cursor() as cur: cur.execute("DROP DATABASE IF EXISTS powercontext_sdd")
EOF
```

## 6. 规格更新验证（上下文正确性）

1. 修改 `sample-app/docs/requirements.md` 中 SPEC-001 的正文（例如把
   "四舍五入到分"改为"向下取整到分"），同步更新 `context.template.json`；
2. 重新执行 `ob_artifact_store.py import`（覆盖入库）；
3. 重新 `build_context_from_db.py` 并 `resolve`——输出必须使用**更新后**的规格正文，
   而不是缓存的旧文案。演示输出见 `records/db-query-records.md`。
