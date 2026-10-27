"""支付重试的早期用例，随 SPEC-002（v1）一起提交。

SPEC-002 后来被 SPEC-003 取代了，但这个用例文件还留在仓库里。
上下文模块应该把这条验证标成 ``superseded_spec``——它验证的是一份已经作废的要求。
"""

import unittest

from app.payment import DEFAULT_MAX_RETRY, DEFAULT_TIMEOUT_SECONDS


class LegacyRetryPolicyTest(unittest.TestCase):
    def test_fixed_timeout_and_retry(self):
        # 这正是 SPEC-002 v1 规定的固定策略
        self.assertEqual(DEFAULT_TIMEOUT_SECONDS, 3)
        self.assertEqual(DEFAULT_MAX_RETRY, 3)


if __name__ == "__main__":
    unittest.main()
