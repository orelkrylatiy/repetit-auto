"""FeedCapture: reload ленты neworders → перехват searchOrders + батча деталей.

RECON §3: reload — штатная команда браузера; слушатель ставится ДО reload.
searchOrders даёт упорядоченный список ID (новые сверху), батч /orders?ids=
— детали первых ~21. Пассивное чтение, viewed не ставится.
"""

from __future__ import annotations

import json
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


def _set_cooldown(path, seconds: float) -> None:
    """ts-файл «не дёргать до»: читает run_cycle перед reload."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(time.time() + seconds), encoding="utf-8")
    except Exception:
        pass


def _parsed(url: str):
    try:
        return urlparse(url)
    except Exception:
        return None


def _is_repetit_url(url: str) -> bool:
    parsed = _parsed(url)
    if parsed is None or parsed.scheme != "https":
        return False
    host = parsed.hostname
    return host == "repetit.ru" or bool(host and host.endswith(".repetit.ru"))


def _path_of(url: str) -> str:
    parsed = _parsed(url)
    return parsed.path if parsed is not None else ""


def _is_search_orders(resp: Response) -> bool:
    try:
        req = resp.request
        return (
            req.method == "POST"
            and _is_repetit_url(resp.url)
            and _path_of(resp.url) == config.API_SEARCH_ORDERS_PATH
        )
    except Exception:
        return False


def _is_orders_batch(resp: Response) -> bool:
    try:
        req = resp.request
        return (
            req.method == "GET"
            and _is_repetit_url(resp.url)
            and _path_of(resp.url) == config.API_ORDERS_BATCH_PATH
        )
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
        bad_status: list[int] = []

        def on_response(resp: Response) -> None:
            try:
                if not (_is_search_orders(resp) or _is_orders_batch(resp)):
                    return
                if resp.status in (401, 403, 429) or resp.status >= 500:
                    bad_status.append(resp.status)
                    return
                if resp.status != 200:
                    return
                if _is_search_orders(resp):
                    ids_resp.append(resp.json())
                else:
                    batch_resp.append(resp.json())
            except Exception:
                pass

        self.page.on("response", on_response)
        try:
            self.page.reload(wait_until="domcontentloaded", timeout=45_000)
            if config.LOGIN_PATH in (self.page.url or ""):
                raise FeedAuthError(f"редирект на {self.page.url}")

            deadline = time.monotonic() + config.CAPTURE_WINDOW_S
            while time.monotonic() < deadline:
                # Для непустой ленты ждём и ID, и batch деталей. Если сайт уже
                # вернул канонический пустой список, detail batch не нужен и нет
                # смысла ждать весь CAPTURE_WINDOW_S; extra-window ниже всё равно
                # доберёт возможный повтор searchOrders для ambiguity check.
                empty_feed = bool(ids_resp) and ids_resp[-1] == []
                if ids_resp and (batch_resp or empty_feed):
                    break
                self.page.wait_for_timeout(200)
            self.page.wait_for_timeout(int(config.CAPTURE_EXTRA_S * 1000))
        finally:
            try:
                self.page.remove_listener("response", on_response)
            except Exception:
                pass

        # Анти-долбление: 401/403 = сессия/антибот, 429 = лимит площадки,
        # 5xx = площадка нестабильна. Во всех случаях не продолжаем угадывать.
        if 401 in bad_status or 403 in bad_status:
            raise FeedAuthError(f"лента ответила {bad_status} — стоп-пауза")
        if 429 in bad_status:
            _set_cooldown(config.FEED_COOLDOWN_FILE, 30 * 60)
            raise FeedError("лента ответила 429 — cooldown 30 мин")
        if any(s >= 500 for s in bad_status):
            _set_cooldown(config.FEED_COOLDOWN_FILE, 10 * 60)
            raise FeedError(f"лента ответила {bad_status} — cooldown 10 мин")

        if not ids_resp:
            raise FeedError(
                f"searchOrders не пойман за {config.CAPTURE_WINDOW_S} с (url: {self.page.url})"
            )

        # Несколько РАЗНЫХ searchOrders в одном capture-окне — не выбираем
        # произвольно «последний». SPEC требует fail-closed + диагностику.
        uniq = {json.dumps(x, ensure_ascii=False, sort_keys=True) for x in ids_resp}
        if len(uniq) > 1:
            self.last_diag = {
                "error": "FEED_AMBIGUOUS",
                "variants": len(uniq),
                "url": self.page.url,
            }
            log.warning("FEED_AMBIGUOUS: %d разных searchOrders — цикл пропущен", len(uniq))
            raise FeedError(f"FEED_AMBIGUOUS: {len(uniq)} разных searchOrders")

        raw_ids = ids_resp[-1]
        if not isinstance(raw_ids, list):
            raise FeedError(f"searchOrders вернул не список: {type(raw_ids)}")
        try:
            all_ids = [int(i) for i in raw_ids]
        except (TypeError, ValueError) as e:
            raise FeedError(f"searchOrders содержит нечисловой id: {e}") from e

        # Пустая лента — нормальный исход. Для непустой ленты отсутствие батча
        # деталей означает неполный capture, кандидатов по нему не обрабатываем.
        if all_ids and not batch_resp:
            raise FeedError("searchOrders пойман, но батч /orders?ids= не пойман")

        details: dict[int, dict] = {}
        for batch in batch_resp:
            if not isinstance(batch, list):
                continue
            for item in batch:
                if isinstance(item, dict) and item.get("id"):
                    try:
                        details[int(item["id"])] = item
                    except (TypeError, ValueError):
                        continue

        orders = [Order.from_api(details[i]) for i in all_ids if i in details]
        self.last_diag = {
            "ids_total": len(all_ids),
            "details": len(details),
            "orders": len(orders),
            "url": self.page.url,
        }
        log.info("capture: %s", self.last_diag)
        return orders, all_ids
