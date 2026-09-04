"""Responder: первый отклик = первое сообщение в чат по заявке (RECON §5).

Человеческий ввод (RULES §1): посимвольный тайп через playwright.type с
delay, клики по элементам. Никаких page.evaluate-действий. Отправка уходит
через WS площадки — воркеру транспорт безразличен, успех подтверждаем по DOM.

Статусы:
  sent          — текст появился в чате И композер очистился
  already       — чат уже существует с историей / наш текст уже там
  unknown       — клик Send был, подтверждения за окно нет; повтор запрещён
  auth_required — чат ушёл на логин; цикл должен остановить отправки
  retry         — до Send не дошло из-за временного/неясного состояния UI

«Обменяться контактами» (платная квота) — НЕ трогаем никогда.
"""

from __future__ import annotations

import logging
import time

from playwright.sync_api import BrowserContext, Page

from repetit import config
from repetit.utils.pacing import human_pause, type_human

log = logging.getLogger("repetit.respond")

_COMPOSER = '[data-testid="message-composer-input"]'
_SEND_BTN = '[data-testid="message-composer-send-button"]'
_CHATS_ORDER_PATH = "/api/teacher/chats/order"
# Живой факт 2026-09-04: свежий чат проверяется эндпоинтом ws.repetit.ru,
# пустой чат = HTTP 204 (тело отсутствует). /lk/api/teacher/chats/order
# площадка больше не дёргает — оставляем распознавание на всякий случай.
_WS_CHATS_PERSONAL = "ws.repetit.ru/api/chats/personal"
_CHAT_STATE_WAIT_S = 5.0


class RespondError(Exception):
    pass


class RespondAuthError(RespondError):
    pass


def _chat_has_history(payload) -> bool:
    """Проверка существующего чата (chats/personal | chats/order).

    Fail-closed: пустой payload {} — истории нет; неизвестная форма или
    несловарный JSON — считаем что история есть (лучше скип, чем дубль).
    """
    if not isinstance(payload, dict):
        return True
    if not payload:
        return False
    result = payload.get("result") or payload
    if result.get("lastMessage"):
        return True
    messages = result.get("messages")
    if isinstance(messages, list):
        return len(messages) > 0
    return True  # форма ответа незнакома — не рискуем


class Responder:
    def __init__(self, ctx: BrowserContext):
        self.ctx = ctx

    def send_first_message(self, order_id: int, chat_title: str, text: str) -> dict:
        """Отправить первое сообщение. Возвращает {status, detail, screenshot}."""
        page: Page = self.ctx.new_page()
        shot = None
        try:
            # Пассивно слушаем чат-API ДО перехода. Нельзя отправлять первое
            # сообщение, пока не убедились, что истории действительно нет:
            # иначе потеря локальной БД может дать дубль.
            chat_state: dict = {}

            def on_chat_api(resp) -> None:
                try:
                    url = resp.url or ""
                    method = resp.request.method
                    is_legacy = method == "GET" and _CHATS_ORDER_PATH in url
                    is_personal = (
                        method == "GET"
                        and _WS_CHATS_PERSONAL in url
                        and f"orderId={order_id}" in url
                    )
                    if not (is_legacy or is_personal):
                        return
                    if is_personal and resp.status == 204:
                        chat_state["payload"] = {}  # пусто = чата с историей нет
                        return
                    if resp.status != 200:
                        chat_state["error"] = f"HTTP {resp.status}"
                        return
                    payload = resp.json()
                    if not isinstance(payload, dict):
                        chat_state["error"] = f"невалидный payload: {type(payload).__name__}"
                        return
                    chat_state["payload"] = payload
                except Exception as e:
                    chat_state["error"] = f"не удалось прочитать chat-state: {type(e).__name__}: {e}"

            url = config.chat_url(order_id, chat_title)
            page.on("response", on_chat_api)
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            human_pause(1.2, 2.5)

            if config.LOGIN_PATH in (page.url or ""):
                raise RespondAuthError("вылогинен при открытии чата")

            try:
                page.wait_for_selector(_COMPOSER, timeout=15_000)
            except Exception as e:
                if config.LOGIN_PATH in (page.url or ""):
                    raise RespondAuthError("вылогинен при ожидании композера") from e
                raise RespondError(f"композер не появился: {e}") from e

            # Явно ждём именно успешный ответ проверки существующего чата.
            # Composer сам по себе не доказывает, что история уже загружена.
            deadline = time.monotonic() + _CHAT_STATE_WAIT_S
            while not ({"payload", "error"} & chat_state.keys()) and time.monotonic() < deadline:
                page.wait_for_timeout(100)
            if "error" in chat_state:
                raise RespondError(f"состояние чата не подтверждено: {chat_state['error']}")
            if "payload" not in chat_state:
                raise RespondError("не пойман /api/teacher/chats/order — состояние чата не подтверждено")

            if _chat_has_history(chat_state["payload"]):
                return {
                    "status": "already",
                    "detail": "чат уже существует с историей — не дублируем",
                    "screenshot": self._screenshot(page, order_id, "already"),
                }

            body_before = page.locator("body").inner_text()
            if text[:80] in body_before:
                return {
                    "status": "already",
                    "detail": "текст уже в чате",
                    "screenshot": None,
                }

            composer = page.locator(_COMPOSER).first
            send_btn = page.locator(_SEND_BTN).first

            human_pause(0.8, 1.6)
            type_human(page, composer, text)
            human_pause(0.4, 0.9)

            # Финальная сверка поля: площадка могла порезать ввод. Обрезанный
            # или изменённый текст не отправляем.
            value = (composer.input_value() or "").strip()
            if value != text.strip():
                raise RespondError(f"в поле не наш текст: {value[:60]!r}")
            self._screenshot(page, order_id, "filled")

            send_btn.click()

            # Успех подтверждаем двумя независимыми DOM-признаками из SPEC:
            # сообщение появилось в чате И composer очистился. Иначе fail-closed.
            ok = False
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                body = page.locator("body").inner_text()
                try:
                    composer_empty = (composer.input_value() or "").strip() == ""
                except Exception:
                    composer_empty = False
                if text[:80] in body and composer_empty:
                    ok = True
                    break
                page.wait_for_timeout(500)

            shot = self._screenshot(page, order_id, "after")
            if ok:
                log.info("отклик отправлен: заявка %s", order_id)
                return {"status": "sent", "detail": "ок", "screenshot": shot}

            # Send был, подтверждения нет: считаем возможной отправкой. Повторная
            # попытка запрещена, дневной лимит расходуется.
            log.warning("заявка %s: нет полного DOM-подтверждения после Send — unknown", order_id)
            return {
                "status": "unknown",
                "detail": "за 15 с не подтверждены одновременно сообщение и пустой composer",
                "screenshot": shot,
            }
        except RespondAuthError as e:
            shot = shot or self._try_screenshot(page, order_id)
            return {"status": "auth_required", "detail": str(e), "screenshot": shot}
        except RespondError as e:
            shot = shot or self._try_screenshot(page, order_id)
            return {"status": "retry", "detail": str(e), "screenshot": shot}
        except Exception as e:  # браузерные сбои не должны ронять процесс
            shot = shot or self._try_screenshot(page, order_id)
            return {
                "status": "retry",
                "detail": f"{type(e).__name__}: {e}",
                "screenshot": shot,
            }
        finally:
            try:
                page.close(run_before_unload=False)
            except Exception:
                pass

    @staticmethod
    def _screenshot(page: Page, order_id: int, tag: str) -> str | None:
        try:
            config.RESPOND_SHOT_DIR.mkdir(parents=True, exist_ok=True)
            path = str(config.RESPOND_SHOT_DIR / f"{order_id}_{tag}_{int(time.time())}.png")
            page.screenshot(path=path)
            return path
        except Exception:
            return None

    def _try_screenshot(self, page: Page, order_id: int) -> str | None:
        try:
            return self._screenshot(page, order_id, "error")
        except Exception:
            return None
