# 开发任务 ORDER-FILTER-001：订单明细按小计筛选

## 要改什么

给示例工程 `sample-app` 的 `app/order.py` 中 `Order` 类增加一个方法：

```python
def expensive_items(self, min_subtotal_cents: int):
    """筛选小计不低于 min_subtotal_cents 的商品明细，按小计降序。"""
```

行为要求：

1. 小计 = 数量 × 单价（分）；保留小计 `>= min_subtotal_cents` 的明细；
2. 返回列表按小计**降序**；同小计保持输入顺序；
3. `min_subtotal_cents <= 0` 时视为 0，返回全部明细（同样按小计降序）；
4. 不修改输入的 `items`。

## 哪些原有行为必须保持

- `subtotal_cents()` / `total_cents()` 的计算结果不变（含折扣门槛 10000 分、
  折扣四舍五入到分——SPEC-001）；
- 支付、定价模块行为不变；
- 既有测试 `tests/test_order.py`、`tests/test_pricing.py`、`tests/test_payment.py`
  必须保持全绿。

## 验证方式

- 目标功能测试：`tests/test_order_filter.py`（4 个用例，含边界阈值 0 / 负数）；
- 回归测试：`python -m unittest discover -s tests`（14 个用例全绿）。
