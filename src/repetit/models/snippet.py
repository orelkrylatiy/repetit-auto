"""Модели данных. Спека: «Спека — Контур A», разд. 12 (OrderSnippet)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OrderSnippet:
    """Нормализованный заказ из ленты (item.type == SNIPPET)."""

    id: str
    title: str
    description: str
    price_raw: str | None
    last_update: int | None

    score: float | None

    is_fresh: bool = False
    is_viewed: bool = False
    is_reposted: bool = False

    badges: list[str] = field(default_factory=list)
    client_name: str | None = None
    client_tags: list[str] = field(default_factory=list)
    schedule: str | None = None

    geo_remote: str | None = None
    geo_remote_suffix: str | None = None
    geo_local: str | None = None

    # берётся из DOM при необходимости; в чтении ленты не участвует
    order_href: str | None = None

    # исходный item как есть — ляжет в candidates.snippet_json
    raw: dict | None = None
