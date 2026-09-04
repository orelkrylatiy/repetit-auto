"""Диагностика 02: безопасная карта навигации ЛК и сетевых запросов home.

Пассивно читает ссылки/кнопки и XHR/fetch после загрузки главной ЛК. Работает
в собственной временной вкладке, не использует вкладки владельца. Артефакты
пишутся только в gitignored `logs/recon/`.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9335"
HOME = "https://repetit.ru/lk/teacher/home"
OUT = Path("logs/recon/02_home_events.json")


def main() -> int:
    events: list[dict] = []
    OUT.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(CDP, timeout=5_000)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        try:
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

            # Пассивное чтение DOM для диагностики структуры навигации.
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
        finally:
            page.close(run_before_unload=False)

    api = [e for e in events if e["type"] in ("xhr", "fetch") or "/api/" in e["url"]]
    by_path: Counter = Counter()
    for event in api:
        parsed = urlparse(event["url"])
        by_path[f"{event['method']} {parsed.path}"] += 1

    print("=== ССЫЛКИ /lk ===")
    for link in links:
        print(f"  {link['text']!r:40} -> {link['href']}")
    print("\n=== КНОПКИ (первые 40) ===")
    for button in btns[:40]:
        print(f"  {button['text']!r}")
    print("\n=== XHR/FETCH за загрузку home ===")
    for key, count in sorted(by_path.items()):
        print(f"  {count:3}x {key}")

    OUT.write_text(
        json.dumps({"events": events, "links": links, "buttons": btns}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"\nсохранено: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
