"""Пробник 02: карта навигации ЛК + инвентарь сетевых запросов home.

Пассивное чтение: goto + listener на responses + чтение DOM-ссылок.
Никаких кликов/действий.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9335"
HOME = "https://repetit.ru/lk/teacher/home"


def main() -> int:
    events: list[dict] = []

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(CDP, timeout=5_000)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        def on_response(resp) -> None:
            try:
                req = resp.request
                events.append(
                    {
                        "method": req.method,
                        "url": resp.url[:300],
                        "status": resp.status,
                        "type": req.resource_type,
                    }
                )
            except Exception:
                pass

        page.on("response", on_response)
        page.goto(HOME, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(4_000)

        # все ссылки и кнопки навигации (пассивное чтение DOM)
        links = page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href]')).map(a => ({
                   href: a.getAttribute('href'),
                   text: (a.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 60)
               })).filter(x => x.href && x.href.includes('/lk'))"""
        )
        btns = page.evaluate(
            """() => Array.from(document.querySelectorAll('button, [role=button]')).map(b => ({
                   text: (b.innerText || b.getAttribute('aria-label') || '')
                       .replace(/\\s+/g, ' ').trim().slice(0, 60)
               })).filter(x => x.text)"""
        )

        page.remove_listener("response", on_response)

    # сводка по API/XHR/fetch — группируем по path
    api = [e for e in events if e["type"] in ("xhr", "fetch") or "/api/" in e["url"]]
    by_path: Counter = Counter()
    for e in api:
        p = urlparse(e["url"])
        by_path[f"{e['method']} {p.path}"] += 1

    print("=== ССЫЛКИ /lk ===")
    for l in links:
        print(f"  {l['text']!r:40} -> {l['href']}")
    print("\n=== КНОПКИ (первые 40) ===")
    for b in btns[:40]:
        print(f"  {b['text']!r}")
    print("\n=== XHR/FETCH за загрузку home ===")
    for k, v in sorted(by_path.items()):
        print(f"  {v:3}x {k}")

    with open("logs/recon/02_home_events.json", "w", encoding="utf-8") as f:
        json.dump({"events": events, "links": links, "buttons": btns}, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
