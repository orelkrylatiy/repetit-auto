"""FeedCapture: reload ленты neworders → перехват searchOrders + батча деталей.

RECON §3, §9: reload — штатная команда браузера; слушатель ставится ДО reload
(как в profi). searchOrders даёт упорядоченный список ID (новые сверху),
батч /orders?ids= — детали первых ~21. Пассивное чтение, viewed не ставится.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

from playwright.sync_api import Page, Response

from repetit import config
from repetit.models.order import Order

log = logging.getLogger("repetit.feed")


class FeedError(Exception):
    """Лента не поймана или невалидна."""


class FeedAuthError(FeedError):
    """Редирект на логин — сессии нет."""


def _path_of(url: str) -> str:
    return urlparse(url).path


def _is_search_orders(resp: Response) -> bool:
    try:
        req = resp.request
        return req.method == "POST" and _path_of(resp.url) == config.API_SEARCH_ORDERS_PATH
    except Exception:
        return False


def _is_orders_batch(resp: Response) -> bool:
    try:
        req = resp.request
        return req.method == "GET" and _path_of(resp.url) == config.API_ORDERS_BATCH_PATH
    except Exception:
        return False


class FeedCapture:
    def __init__(self, page: Page):
        self.page = page
        self.last_diag: dict = {}

    def reload_and_capture(self) -> tuple[list[Order], list[int]]:
        """Возвращает (заказы с деталями, все ID из searchOrders)."""
        ids_resp: list = []
        batch_resp: list = []

        def on_response(resp: Response) -> None:
            try:
                if _is_search_orders(resp):
                    ids_resp.append(resp.json())
                elif _is_orders_batch(resp):
                    batch_resp.append(resp.json())
            except Exception:
                pass

        self.page.on("response", on_response)
        try:
            self.page.reload(wait_until="domcontentloaded", timeout=45_000)
            if config.LOGIN_PATH in (self.page.url or ""):
                raise FeedAuthError(f"редирект на {self.page.url}")

            deadline = time.monotonic() + config.CAPTURE_WINDOW_S
            while time.monotonic() < deadline and not (ids_resp and batch_resp):
                self.page.wait_for_timeout(200)
            self.page.wait_for_timeout(int(config.CAPTURE_EXTRA_S * 1000))
        finally:
            try:
                self.page.remove_listener("response", on_response)
            except Exception:
                pass

        if not ids_resp:
            raise FeedError(
                f"searchOrders не пойман за {config.CAPTURE_WINDOW_S} с "
                f"(url: {self.page.url})"
            )
        all_ids = ids_resp[-1]
        if not isinstance(all_ids, list):
            raise FeedError(f"searchOrders вернул не список: {type(all_ids)}")

        batch = batch_resp[-1] if batch_resp else []
        if not isinstance(batch, list):
            batch = []
        details: dict[int, dict] = {}
        for item in batch:
            if isinstance(item, dict) and item.get("id"):
                details[int(item["id"])] = item

        orders = [Order.from_api(details[i]) for i in all_ids if i in details]
        self.last_diag = {
            "ids_total": len(all_ids),
            "details": len(details),
            "orders": len(orders),
            "url": self.page.url,
        }
        log.info("capture: %s", self.last_diag)
        return orders, [int(i) for i in all_ids]
