"""Модели данных. Спека: «Спека — Контур A», разд. 14 (FilterVerdict)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FilterVerdict:
    """Результат hard filter (спека разд. 14): PASS / SKIP + причина для лога."""

    passed: bool
    reason: str
