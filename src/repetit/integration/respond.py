"""Responder: первый отклик = первое сообщение в чат по заявке.

Человеческий ввод через Playwright UI. Никаких page.evaluate-действий.
Успех подтверждаем по DOM: наш текст появился И composer очистился.
"""

from __future__ import annotations

import logging
import time

from playwright.sync_api import BrowserContext, Page

from repetit import config
from repetit.integration.chat import is_chat_state_url
from repetit.utils.pacing import human_pause, type_human

log = logging.getLogger("repetit.respond")

_COMPOSER = '[data-testid="message-composer-input"]'
_SEND_BTN = '[data-testid="message-composer-send-button"]'


class RespondError(Exception):
    pass


class RespondAuthError(RespondError):
    pass


def _chat_has_history(payload) -> bool:
    """Проверка существующего чата.

    Fail-closed: пустой payload {} — истории нет; неизвестная форма или
    несловарный JSON — считаем что история есть (лучше скип, чем дубль).
    """
    if not isinstance(payload, dict):
        return True
    if not payload:
        return False
    result = payload.get("result") or payload
    if not isinstance(result, dict):
        return True
    if result.get("lastMessage"):
        return True
    messages = result.get("messages")
    if isinstance(messages, list):
        return len(messages) > 0
    return True


class Responder:
    def __init__(self, ctx: BrowserContext):
        self.ctx = ctx

    def send_first_message(self, order_id: int, chat_title: str, text: str) -> dict:
        """Отправить первое сообщение. Возвращает {status, detail, screenshot}."""
        page: Page = self.ctx.new_page()
        shot = None
        clicked = False
        try:
            chat_state: dict = {}

            def on_chat_api(resp) -> None:
                try:
                    if not is_chat_state_url(resp.url or "", resp.request.method, order_id):
                        return
                    if resp.status == 204:
                        chat_state["payload"] = {}
                        return
                    if resp.status != 200:
                        chat_state["error"] = f"HTTP {resp.status}"
                        return
                    payload = resp.json()
                    if not isinstance(payload, dict):
                        chat_state["error"] = f"невалидный payload: {type(payload).__name__}"
                        return
                    existing = chat_state.get("payload")
                    if existing not in (None, {}) and existing != payload:
                        chat_state["error"] = "несколько разных chat-state payload"
                        return
                    if payload:
                        chat_state["payload"] = payload
                    else:
                        chat_state.setdefault("payload", {})
                except Exception as e:
                    chat_state["error"] = (
                        f"не удалось прочитать chat-state: {type(e).__name__}: {e}"
                    )

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

            deadline = time.monotonic() + config.CHAT_STATE_WAIT_S
            while not ({"payload", "error"} & chat_state.keys()) and time.monotonic() < deadline:
                page.wait_for_timeout(100)
            if "error" in chat_state:
                raise RespondError(f"состояние чата не подтверждено: {chat_state['error']}")
            if "payload" not in chat_state:
                raise RespondError("chat-state response не пойман — состояние не подтверждено")

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

            value = (composer.input_value() or "").strip()
            if value != text.strip():
                raise RespondError(f"в поле не наш текст: {value[:60]!r}")
            self._screenshot(page, order_id, "filled")

            send_btn.click()
            clicked = True

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
        except Exception as e:
            shot = shot or self._try_screenshot(page, order_id)
            return {
                "status": "unknown" if clicked else "retry",
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
        return self._screenshot(page, order_id, "error")
