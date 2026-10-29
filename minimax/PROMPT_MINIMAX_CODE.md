# 在 MiniMax Code 中执行 ORDER-FILTER-001（提示词，整段复制）

> 使用方式：把下面引号内的整段文字复制给 MiniMax Code。
> 工作目录：`E:\MINIOB\mini-ob\OB-2026-PowerContext\examples\sdd-context-manager\sample-app`

```
开发任务 ORDER-FILTER-001：给 app/order.py 的 Order 类增加一个方法：

    def expensive_items(self, min_subtotal_cents: int):
        """筛选小计不低于 min_subtotal_cents 的商品明细，按小计降序。"""

行为要求（来自规格 SPEC-101 与决策 ADR-101）：
1. 小计 = 数量 × 单价（分）；保留小计 >= min_subtotal_cents 的明细；
2. 返回列表按小计降序；同小计保持输入顺序（稳定排序）；
3. min_subtotal_cents <= 0 时视为 0，返回全部明细（同样按小计降序）；
4. 不修改输入的 items。

必须保持的原有行为（SPEC-001 / ADR-003）：
- subtotal_cents()、total_cents() 的结果不变（折扣门槛 10000 分、折扣四舍五入到分）；
- app/pricing.py、app/payment.py 不做任何改动；
- 既有测试 tests/test_order.py、tests/test_pricing.py、tests/test_payment.py 保持全绿。

验证：
- 目标测试 tests/test_order_filter.py（4 个用例，含阈值 0 与负数边界）修改前应失败、修改后应通过；
- 回归 python -m unittest discover -s tests 全部通过。

请只修改 app/order.py，实现 expensive_items 方法。
```
