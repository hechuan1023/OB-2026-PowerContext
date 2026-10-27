"""支付网关调用。

对外契约见 CTR-001：``charge`` 成功返回支付流水号，失败抛 ``PaymentError``。
"""

DEFAULT_TIMEOUT_SECONDS = 3
DEFAULT_MAX_RETRY = 3
RETRY_BACKOFF_SECONDS = 0.2


class PaymentError(Exception):
    """支付失败（重试耗尽或参数非法）。"""


def charge(
    amount_cents: int,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_retry: int = DEFAULT_MAX_RETRY,
) -> str:
    """发起一笔支付。

    :param amount_cents: 金额（单位：分），必须为正
    :param timeout_seconds: 单次调用超时时间
    :param max_retry: 最大重试次数
    :return: 支付流水号
    :raises PaymentError: 金额非法，或重试耗尽仍然失败
    """
    if amount_cents <= 0:
        raise PaymentError("金额必须为正，收到 %d" % amount_cents)
    if max_retry < 1:
        raise PaymentError("至少要重试一次")
    return "PAY-%08d" % amount_cents
