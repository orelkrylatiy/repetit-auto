"""Hard filters до LLM (спека разд. 14). Правила под цель проекта — в config.py.

Философия: сомневаемся в поле (None/нестандартная форма) — не режем заказ,
а пропускаем дальше: в milestone читателя это видно в логе, ложный SKIP
дороже лишнего LLM-вызова.
"""

from __future__ import annotations

import re

from repetit import config
from repetit.models import FilterVerdict, OrderSnippet

_NUM_RE = re.compile(r"\d[\d\s\u00a0]*")


def parse_price_max(price_raw: str | None) -> int | None:
    """«450–600 ₽» → 600; «до 2100 ₽» → 2100; «от 2000 ₽» → None (потолок неизвестен)."""
    if not price_raw:
        return None
    text = price_raw.lstrip()
    nums = [int(n.replace(" ", "").replace("\u00a0", "")) for n in _NUM_RE.findall(text)]
    if not nums:
        return None
    if text.startswith("от"):
        return None
    return max(nums)


def hard_filter(s: OrderSnippet) -> FilterVerdict:
    text = f"{s.title}\n{s.description}".lower()
    badges_text = " ".join(b.lower() for b in s.badges)

    if any(p in text or p in badges_text for p in config.VACANCY_PATTERNS):
        return FilterVerdict(False, "похоже на вакансию")

    if any(p in text or p in badges_text for p in config.BARTER_PATTERNS):
        return FilterVerdict(False, "бартер/без денег")

    if any(p in text or p in badges_text for p in config.SPECIAL_NEEDS_PATTERNS):
        return FilterVerdict(False, "особые потребности (СДВГ/аутизм и смежное)")

    if config.SUBJECT_KEYWORDS and not any(k in text for k in config.SUBJECT_KEYWORDS):
        return FilterVerdict(False, "предмет не совпал")

    if config.REMOTE_ONLY and s.geo_remote is not None and not str(s.geo_remote).strip():
        return FilterVerdict(False, "только очно (geo.remote пуст)")

    if config.MIN_RATE is not None:
        price_max = parse_price_max(s.price_raw)
        if price_max is not None and price_max < config.MIN_RATE:
            return FilterVerdict(False, f"бюджет {price_max} ₽ < порога {config.MIN_RATE} ₽")

    return FilterVerdict(True, "pass")
