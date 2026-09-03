"""Модели данных. Спека: «Спека — Контур A», разд. 12 (FeedSnapshot)."""

from __future__ import annotations

from dataclasses import dataclass

from repetit.models.snippet import OrderSnippet


@dataclass
class FeedSnapshot:
    """Canonical ответ BoSearchBoardItems, нормализованный."""

    snippets: list[OrderSnippet]
    total_count: int | None
    next_cursor: str | None
    server_ts: int | None
    raw: dict
