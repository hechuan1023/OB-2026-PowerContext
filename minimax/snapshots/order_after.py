"""订单：把商品明细算成订单总价。

订单总价 = 商品小计 → 判定是否满足折扣门槛 → 打折扣 → 得到最终金额。
"""

from .pricing import apply_discount, is_discount_eligible


class Order:
    """一个订单。

    :param order_id: 订单号
    :param items: ``[(数量, 单价（分）), ...]``
    :param discount_rate: 折扣率，0 表示不打折
    """

    def __init__(self, order_id: str, items, discount_rate: float = 0.0) -> None:
        self.order_id = order_id
        self.items = list(items)
        self.discount_rate = discount_rate

    def subtotal_cents(self) -> int:
        """商品小计（单位：分）。"""
        return sum(quantity * unit_price for quantity, unit_price in self.items)

    def total_cents(self) -> int:
        """订单最终金额（单位：分）。"""
        subtotal = self.subtotal_cents()
        if not is_discount_eligible(subtotal) or not self.discount_rate:
            return subtotal
        return apply_discount(subtotal, self.discount_rate)

    def expensive_items(self, min_subtotal_cents: int):
        """筛选小计不低于 ``min_subtotal_cents`` 的商品明细，按小计降序。

        小计 = 数量 × 单价（分）。同小计的明细保持输入顺序（稳定排序）；
        ``min_subtotal_cents <= 0`` 时视为 0，返回全部明细（同样按小计降序）。
        """
        threshold = max(int(min_subtotal_cents), 0)
        picked = [item for item in self.items if item[0] * item[1] >= threshold]
        picked.sort(key=lambda item: item[0] * item[1], reverse=True)
        return picked
