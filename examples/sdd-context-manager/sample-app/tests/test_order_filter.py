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
