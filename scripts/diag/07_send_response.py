"""Пробник 07: РЕАЛЬНАЯ ОТПРАВКА отклика — первое сообщение в чат по заявке.

Заявка 3970286: информатика, ОГЭ, 9 класс, устранить пробелы, онлайн, Сб.
Текст честный, кастомный, без выдуманного опыта, без контактов (правила сервиса).
Логируем сеть вокруг отправки + состояние после.
"""

from __future__ import annotations

import json
import sys
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9335"
CHAT_URL = (
    "https://repetit.ru/lk/teacher/chatforteacher"
    "?orderId=3970286&chatTitle=%E2%84%96%203970286%2C%20%D0%A1%D0%B2%D0%B5%D1%82%D0%BB%D0%B0%D0%BD%D0%B0"
)
OUT = "logs/recon/07_send"

TEXT = (
    "Здравствуйте, Светлана! Меня зовут Максим, занимаюсь информатикой и программированием. "
    "Помогу девятикласснику закрыть пробелы и подготовиться к ОГЭ: разберём задания формата экзамена "
    "и темы, которые западают сильнее всего. Занимаюсь онлайн, суббота подходит. "
    "Предлагаю начать с вводного занятия — посмотрим текущий уровень и составим план. "
    "Напишите, если интересно — обсудим детали."
)


def main() -> int:
    captured: list[dict] = []
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(CDP, timeout=5_000)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        def on_response(resp) -> None:
            try:
                req = resp.request
                if req.resource_type not in ("xhr", "fetch"):
                    return
                item = {"ts": round(time.time(), 3), "method": req.method,
                        "url": resp.url, "status": resp.status, "req": None, "resp": None}
                try:
                    pd = req.post_data
                    item["req"] = pd[:4000] if pd else None
                except Exception:
                    pass
                ct = (resp.headers or {}).get("content-type", "")
                if "json" in ct:
                    try:
                        item["resp"] = resp.json()
                    except Exception:
                        item["resp"] = "<unparseable>"
                captured.append(item)
            except Exception:
                pass

        page.on("response", on_response)
        page.goto(CHAT_URL, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(4_000)

        composer = page.locator('[data-testid="message-composer-input"]')
        send_btn = page.locator('[data-testid="message-composer-send-button"]')
        print(f"composer: {composer.count()}, send: {send_btn.count()}")
        if not composer.count():
            print("нет композера — стоп")
            return 1

        # человеческий ввод: клик, посимвольный тайп с паузами
        composer.click()
        page.wait_for_timeout(400)
        composer.type(TEXT, delay=45)
        page.wait_for_timeout(600)
        page.screenshot(path=f"{OUT}_filled.png", full_page=False)

        send_btn.click()
        page.wait_for_timeout(5_000)

        body = page.evaluate("() => document.body.innerText")
        with open(f"{OUT}_after_body.txt", "w", encoding="utf-8") as f:
            f.write(body)
        page.screenshot(path=f"{OUT}_after.png", full_page=False)
        print("=== тело после отправки (хвост 900) ===")
        print(body[-900:])
        page.remove_listener("response", on_response)

    print("\n=== XHR/FETCH (только POST + chats) ===")
    for c in captured:
        p = urlparse(c["url"])
        if c["method"] == "POST" or "chat" in p.path.lower():
            size = len(json.dumps(c["resp"], ensure_ascii=False)) if c["resp"] else 0
            print(f"  {c['status']} {c['method']} {p.path}  resp~{size}b")
            if c["req"]:
                print(f"      req: {c['req'][:500]}")
    with open(f"{OUT}_net.json", "w", encoding="utf-8") as f:
        json.dump(captured, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
