"""价格与折扣计算。

约定：所有金额一律以「分」为最小单位的整数表示，不使用浮点金额。
"""

CENTS_PER_UNIT = 100

#: 满 100 元（即 10000 分）才可享受折扣
DISCOUNT_THRESHOLD_CENTS = 10000


def is_discount_eligible(total_cents: int) -> bool:
    """订单总额是否达到折扣门槛。"""
    return total_cents >= DISCOUNT_THRESHOLD_CENTS


def apply_discount(total_cents: int, discount_rate: float) -> int:
    """按折扣率打折，返回打折后的金额（单位：分）。

    规则（见 SPEC-001）：折扣后金额四舍五入到分。
    """
    discounted = total_cents * (1 - discount_rate)
    return int(round(discounted))
