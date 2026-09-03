"""Пробник: подключение к живому Chrome (CDP :9335) и открытие ЛК репетитора.

Аналог scripts/diag/ из profi. Chrome уже поднят с выделенным профилем,
воркер только подключается. Логина нет: если сессии нет — AUTH_REQUIRED,
ждём человека (см. RULES про логин-стену).
"""

from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9335"
LK_URL = "https://repetit.ru/lk/teacher/home"


def main() -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(CDP, timeout=5_000)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(LK_URL, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(2_000)

        url, title = page.url, page.title()
        # грубая детекция состояния: текст страницы скажет сам за себя
        text = page.evaluate("() => document.body ? document.body.innerText.slice(0, 800) : ''")
        logged_in = any(marker in text for marker in ("Выход", "Доступно контактов", "Заявки"))

        print(f"url: {url}")
        print(f"title: {title}")
        print(f"state: {'LOGGED_IN' if logged_in else 'AUTH_REQUIRED'}")
        print("--- body[:800] ---")
        print(text)
        return 0


if __name__ == "__main__":
    sys.exit(main())
