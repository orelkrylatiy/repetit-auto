"""Responder: первый отклик = первое сообщение в чат по заявке (RECON §5).

Человеческий ввод (RULES §1): посимвольный тайп через playwright.type с
delay, клики по элементам. Никаких page.evaluate-действий. Отправка уходит
через WS площадки — воркеру транспорт безразличен, проверяем эффект в UI.
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


class RespondError(Exception):
    pass


class Responder:
    def __init__(self, ctx: BrowserContext):
        self.ctx = ctx

    def send_first_message(self, order_id: int, chat_title: str, text: str) -> dict:
        """Отправить первое сообщение. Возвращает {status, detail, screenshot}.

        status: sent | already | error
        """
        page: Page = self.ctx.new_page()
        shot = None
        try:
            url = config.chat_url(order_id, chat_title)
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            human_pause(1.2, 2.5)

            if config.LOGIN_PATH in (page.url or ""):
                raise RespondError("вылогинен при открытии чата")

            try:
                page.wait_for_selector(_COMPOSER, timeout=15_000)
            except Exception as e:
                raise RespondError(f"композер не появился: {e}") from e

            body_before = page.evaluate("() => document.body.innerText")
            if text[:80] in body_before:
                return {"status": "already", "detail": "текст уже в чате", "screenshot": None}

            composer = page.locator(_COMPOSER).first
            send_btn = page.locator(_SEND_BTN).first

            human_pause(0.8, 1.6)
            type_human(page, composer, text)
            human_pause(0.5, 1.2)

            send_btn.click()
            # эффект: сообщение появилось в теле чата
            ok = False
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                body = page.evaluate("() => document.body.innerText")
                if text[:80] in body:
                    ok = True
                    break
                page.wait_for_timeout(500)

            shot = self._screenshot(page, order_id)
            if not ok:
                return {
                    "status": "error",
                    "detail": "текст не появился в чате после Send",
                    "screenshot": shot,
                }
            log.info("отклик отправлен: заявка %s", order_id)
            return {"status": "sent", "detail": "ок", "screenshot": shot}
        except RespondError as e:
            shot = shot or self._try_screenshot(page, order_id)
            return {"status": "error", "detail": str(e), "screenshot": shot}
        except Exception as e:  # браузерные сбои не должны ронять цикл
            shot = shot or self._try_screenshot(page, order_id)
            return {"status": "error", "detail": f"{type(e).__name__}: {e}", "screenshot": shot}
        finally:
            try:
                page.close(run_before_unload=False)
            except Exception:
                pass

    @staticmethod
    def _screenshot(page: Page, order_id: int) -> str | None:
        try:
            config.RESPOND_SHOT_DIR.mkdir(parents=True, exist_ok=True)
            path = str(config.RESPOND_SHOT_DIR / f"{order_id}_{int(time.time())}.png")
            page.screenshot(path=path)
            return path
        except Exception:
            return None

    def _try_screenshot(self, page: Page, order_id: int) -> str | None:
        try:
            return self._screenshot(page, order_id)
        except Exception:
            return None
