"""支付模块的单元测试。"""

import unittest

from app.payment import PaymentError, charge


class PaymentTest(unittest.TestCase):
    def test_charge_returns_serial(self):
        self.assertEqual(charge(1001), "PAY-00001001")

    def test_reject_non_positive_amount(self):
        with self.assertRaises(PaymentError):
            charge(0)

    def test_require_at_least_one_retry(self):
        with self.assertRaises(PaymentError):
            charge(100, max_retry=0)


if __name__ == "__main__":
    unittest.main()
