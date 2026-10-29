# 代码与测试：修改前后快照与差异

## 1. 开发任务
ORDER-FILTER-001：`Order` 增加 `expensive_items(min_subtotal_cents)` 明细筛选。
完整要求见 `minimax/TASK_ORDER_FILTER.md`；**实现由 MiniMax Code（MiniMax-M3.1-Flash）完成**，
开发记录见 `minimax/records/minimax-dev-record.md`。

## 2. 修改前后快照
- 修改前：`minimax/snapshots/order_before.py`（与基线 `ab7d16a` 的 `app/order.py` 一致）
- 修改后：`minimax/snapshots/order_after.py`（提交状态 = MiniMax Code 会话的最终产出）
- 重现红→绿：`python minimax/scripts/reset_sample.sh before|after`

## 3. 代码差异（app/order.py，+11 行，与开发记录中模型输出一致）
```diff
@@ -29,3 +29,14 @@
         if not is_discount_eligible(subtotal) or not self.discount_rate:
             return subtotal
         return apply_discount(subtotal, self.discount_rate)
+
+    def expensive_items(self, min_subtotal_cents: int):
+        """筛选小计不低于 ``min_subtotal_cents`` 的商品明细，按小计降序。
+
+        小计 = 数量 × 单价（分）。同小计的明细保持输入顺序（稳定排序）；
+        ``min_subtotal_cents <= 0`` 时视为 0，返回全部明细（同样按小计降序）。
+        """
+        threshold = max(int(min_subtotal_cents), 0)
+        picked = [item for item in self.items if item[0] * item[1] >= threshold]
+        picked.sort(key=lambda item: item[0] * item[1], reverse=True)
+        return picked
```

## 4. 新增功能测试
`examples/sdd-context-manager/sample-app/tests/test_order_filter.py`（4 例，含边界 0 / 负数）：

```python
"""订单明细筛选的目标功能测试（开发任务 ORDER-FILTER-001）。

验证样例中"要改什么"：Order 增加 ``expensive_items`` 筛选方法——
返回小计不低于给定金额的商品明细，按小计降序排列。

"哪些原有行为必须保持"：订单小计、折扣判定与总价计算的行为不得变化，
见 test_order.py / test_pricing.py 的既有断言。
"""

import unittest

from app.order import Order


class OrderExpensiveItemsTest(unittest.TestCase):
    def test_filter_by_min_subtotal(self):
        # 小计：(2,500)=1000、(1,300)=300、(3,200)=600；阈值 600 → 保留 1000/600
        order = Order("O-F1", [(2, 500), (1, 300), (3, 200)])
        self.assertEqual(order.expensive_items(600), [(2, 500), (3, 200)])

    def test_sorted_by_subtotal_desc(self):
        # 输入乱序；(4,250) 与 (2,500) 小计同为 1000，同小计保持输入顺序
        order = Order("O-F2", [(1, 300), (4, 250), (2, 500)])
        self.assertEqual(order.expensive_items(600), [(4, 250), (2, 500)])

    def test_none_match_returns_empty(self):
        order = Order("O-F3", [(1, 100), (2, 50)])
        self.assertEqual(order.expensive_items(1000), [])

    def test_boundary_zero_returns_all_sorted(self):
        # 边界输入：阈值 <= 0 返回全部明细（同样按小计降序）
        order = Order("O-F4", [(1, 300), (2, 500)])
        self.assertEqual(order.expensive_items(0), [(2, 500), (1, 300)])
        self.assertEqual(order.expensive_items(-1), [(2, 500), (1, 300)])


if __name__ == "__main__":
    unittest.main()
```

## 5. 配套材料变更（非业务代码）
- `sample-app/docs/requirements.md`：SPEC-101；`docs/decisions.md`：ADR-101
- `sample-app/context.template.json`：SPEC-101 / ADR-101 / VERIFY-101 制品条目
- `minimax/`：本题材料与脚本

## 6. 回归说明
业务代码仅 `app/order.py` 新增一个方法；回归 14 例与模块 69 例全部通过，见 `minimax/TEST_RESULTS.md`。
