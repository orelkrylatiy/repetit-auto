"""Hard-фильтры заявок repetit.ru до LLM (дешёвый отсев).

Философия как в profi: сомневаемся в поле — пропускаем дальше,
ложный SKIP дороже лишнего LLM-вызова.
"""

from __future__ import annotations

from repetit import config
from repetit.models.order import Order
from repetit.models.verdict import FilterVerdict


def hard_filter(order: Order) -> FilterVerdict:
    s = order.searchable.lower()

    if config.SUBJECT_KEYWORDS and not any(k.lower() in s for k in config.SUBJECT_KEYWORDS):
        return FilterVerdict(False, f"не наш предмет: {order.subject!r}")

    for p in config.SPECIAL_NEEDS_PATTERNS:
        if p in s:
            return FilterVerdict(False, f"особые потребности: {p}")

    for p in config.BARTER_PATTERNS:
        if p in s:
            return FilterVerdict(False, f"бартер/бесплатно: {p}")

    if config.MIN_CLIENT_RATE > 0:
        top = order.max_price or order.min_price
        if top and top < config.MIN_CLIENT_RATE:
            return FilterVerdict(False, f"бюджет {top} < {config.MIN_CLIENT_RATE} ₽/60мин")

    return FilterVerdict(True, "pass")
