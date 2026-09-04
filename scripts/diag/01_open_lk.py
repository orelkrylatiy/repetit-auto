"""Диагностика 01: безопасная проверка CDP и сессии repetit.ru.

Создаёт собственную временную вкладку, открывает главную ЛК, печатает URL/title
и небольшой фрагмент текста. Существующие вкладки владельца не использует и не
закрывает. Никаких заявок/чатов не открывает и ничего не отправляет.
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
        page = ctx.new_page()
        try:
            page.goto(LK_URL, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(2_000)

            url, title = page.url, page.title()
            text = page.locator("body").inner_text()[:800]
            logged_in = any(marker in text for marker in ("Выход", "Доступно контактов", "Заявки"))

            print(f"url: {url}")
            print(f"title: {title}")
            print(f"state: {'LOGGED_IN' if logged_in else 'AUTH_REQUIRED'}")
            print("--- body[:800] ---")
            print(text)
            return 0
        finally:
            page.close(run_before_unload=False)


if __name__ == "__main__":
    sys.exit(main())
