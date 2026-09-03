"""Рабочие часы автономных контуров (RULES.md: 8–23 локального времени).

Единый гейт для автопилота, воркера ленты и чатов: вне окна браузер
не трогаем вообще — вкладки не открываются, лента не перезагружается,
чаты не читаются. 24/7-активность с одного IP = бот-сигнал (P0-B).
"""

from __future__ import annotations

from datetime import datetime

from repetit import config


def in_work_hours(now: datetime | None = None) -> bool:
    """True внутри config.WORK_HOURS (часы локального времени, [lo, hi))."""
    lo, hi = config.WORK_HOURS
    return lo <= (now or datetime.now()).hour < hi
