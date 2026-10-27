"""定价模块的单元测试。"""

import unittest

from app.pricing import apply_discount, is_discount_eligible


class PricingTest(unittest.TestCase):
    def test_discount_rounds_to_cent(self):
        # 1001 分打 85 折 = 850.85 分，四舍五入应为 851 分
        self.assertEqual(apply_discount(1001, 0.15), 851)

    def test_discount_no_change(self):
        self.assertEqual(apply_discount(1000, 0.0), 1000)

    def test_eligibility_threshold(self):
        self.assertTrue(is_discount_eligible(10000))
        self.assertFalse(is_discount_eligible(9999))


if __name__ == "__main__":
    unittest.main()
