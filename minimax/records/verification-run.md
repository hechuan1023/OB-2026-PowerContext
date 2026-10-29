# 验证运行记录（demo_feature_change.py）
时间：2026-10-30T00:02:44
模式：use-submitted

| 步骤 | 预期 | 实际 | 结果 |
|---|---|---|---|
| 步骤 1-2 目标测试（修改前） | 预期 FAILED | rc=1 | PASS |
| 步骤 3 DB 导入+检索+构建上下文 | 预期 OK | rc=0/0/0 | PASS |
| 步骤 4 代码修改 | 提交实现（--use-submitted/--offline） | - | PASS |
| 步骤 5a 目标测试（修改后） | 预期 OK | rc=0 | PASS |
| 步骤 5b 回归测试 | 预期 OK | rc=0 | PASS |
| 步骤 6 状态保存→重启→回查 | 预期 OK | rc=0/0 | PASS |

## 步骤 2 目标测试输出（修改前）

```
est_none_match_returns_empty)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "E:\MINIOB\mini-ob\OB-2026-PowerContext\examples\sdd-context-manager\sample-app\tests\test_order_filter.py", line 28, in test_none_match_returns_empty
    self.assertEqual(order.expensive_items(1000), [])
                     ^^^^^^^^^^^^^^^^^^^^^
AttributeError: 'Order' object has no attribute 'expensive_items'

======================================================================
ERROR: test_sorted_by_subtotal_desc (tests.test_order_filter.OrderExpensiveItemsTest.test_sorted_by_subtotal_desc)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "E:\MINIOB\mini-ob\OB-2026-PowerContext\examples\sdd-context-manager\sample-app\tests\test_order_filter.py", line 24, in test_sorted_by_subtotal_desc
    self.assertEqual(order.expensive_items(600), [(4, 250), (2, 500)])
                     ^^^^^^^^^^^^^^^^^^^^^
AttributeError: 'Order' object has no attribute 'expensive_items'

----------------------------------------------------------------------
Ran 4 tests in 0.003s

FAILED (errors=4)

```

## 步骤 5 目标测试输出（修改后）

```
test_boundary_zero_returns_all_sorted (tests.test_order_filter.OrderExpensiveItemsTest.test_boundary_zero_returns_all_sorted) ... ok
test_filter_by_min_subtotal (tests.test_order_filter.OrderExpensiveItemsTest.test_filter_by_min_subtotal) ... ok
test_none_match_returns_empty (tests.test_order_filter.OrderExpensiveItemsTest.test_none_match_returns_empty) ... ok
test_sorted_by_subtotal_desc (tests.test_order_filter.OrderExpensiveItemsTest.test_sorted_by_subtotal_desc) ... ok

----------------------------------------------------------------------
Ran 4 tests in 0.000s

OK

```

## 步骤 6 handover check 输出（重启后回查）

```
{
  "snapshot_id": "5fe3dea700ba3f16",
  "task": "订单明细按小计筛选（ORDER-FILTER-001）：Order.expensive_items(min_subtotal_cents)，边界阈值 0 返回全部",
  "total": 2,
  "still_valid": 2,
  "expired": [],
  "code_changed": [
    "app/order.py"
  ],
  "clean": false
}

```
