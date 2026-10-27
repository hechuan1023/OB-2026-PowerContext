"""订单模块的单元测试。"""

import unittest

from app.order import Order


class OrderTest(unittest.TestCase):
    def test_subtotal(self):
        order = Order("O-1", [(2, 500), (1, 300)])
        self.assertEqual(order.subtotal_cents(), 1300)

    def test_total_rounds_to_cent(self):
        # 10001 分（小计已过 100 元门槛）打 85 折，订单总价应为 8501 分
        order = Order("O-2", [(1, 10001)], discount_rate=0.15)
        self.assertEqual(order.total_cents(), 8501)

    def test_no_discount_below_threshold(self):
        order = Order("O-3", [(1, 500)], discount_rate=0.15)
        self.assertEqual(order.total_cents(), 500)


if __name__ == "__main__":
    unittest.main()
