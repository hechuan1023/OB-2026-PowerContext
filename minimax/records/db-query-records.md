# seekdb / OceanBase 查询记录

数据库：OceanBase CE 4.4.2.1（docker `oceanbase/oceanbase-ce`，MySQL 协议）
接入：PyMySQL；库表：`powercontext_sdd.sdd_artifacts`

## 1. 资料入库（init + import）
```
OK: database powercontext_sdd / table sdd_artifacts ready
OK: imported 27 artifacts into sdd_artifacts
code           10
contract       1
decision       5
spec           6
verification   5
```

## 2. 任务检索（查询结果用于构造上下文）
```
OK: 13 artifacts from DB -> minimax\records\context_from_db.json
matched: SPEC-001, SPEC-003, SPEC-101, SPEC-004, ADR-004, ADR-101, VERIFY-101, VERIFY-004, tests/test_order_filter.py, tests/test_payment_retry_legacy.py, app/order.py, app/payment.py, app/pricing.py
```

匹配条目即 `VERIFICATION.md` §2 核对清单的来源；`app/order.py`、`app/pricing.py`
由命中条目的 scope 关联补全（SQL 为 OR 关联 id IN (...)）。

## 3. 规格更新并重新导入后，检索输出随内容变化
```
### 1. 更新 SPEC-101（模拟需求演进：同小计改按单价升序）
SPEC-101 body 已更新为 v2
OK: imported 27 artifacts into sdd_artifacts
--- 重新检索（应输出更新后正文）---
version: 2
1. 小计 = 数量 × 单价（分），保留小计不低于阈值的明细。
2. 结果按小计降序，同小计按单价升序。
3. 阈值 <= 0 视为 0，返回全部明细（同样降序）。
4. 不改变订单总价与折扣规则（SPEC-001）。

### 2. 还原 SPEC-101（提交状态与快照保持一致）
SPEC-101 已还原为 v1
OK: imported 27 artifacts into sdd_artifacts
还原后 version: 1 | 1. 小计 = 数量 × 单价（分），保留小计不低于阈值的明细。
2. 结果按小计降序，同小计保持输入顺序。
3. 阈值
```

## 4. 使用检索结果的任务上下文（节选）
```json
{
  "backend": "seekdb/oceanbase (pymysql, MySQL protocol)",
  "table": "sdd_artifacts",
  "task": "订单明细按小计筛选，边界阈值 0 返回全部",
  "keywords": [
    "订单明细按小计筛选",
    "边界阈值",
    "0",
    "返回全部"
  ],
  "matched_ids": [
    "SPEC-001",
    "SPEC-003",
    "SPEC-101",
    "SPEC-004",
    "ADR-004",
    "ADR-101",
    "VERIFY-101",
    "VERIFY-004",
    "tests/test_order_filter.py",
    "tests/test_payment_retry_legacy.py",
    "app/order.py",
    "app/payment.py",
    "app/pricing.py"
  ],
  "related_ids": [
    "app/order.py",
    "app/payment.py",
    "app/pricing.py"
  ]
}
```
