"""Пробник 06: АКТИВНО — «Начать чат с клиентом» на заявке: форма отклика.

1. Открываем заявку /lk/teacher/neworders/{id}
2. Кликаем «Начать чат с клиентом»
3. Дамп: DOM после клика, скриншот, сеть. НЕ отправляем — сначала смотрим форму.
"""

from __future__ import annotations

import json
import sys
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9335"
ORDER_URL = "https://repetit.ru/lk/teacher/neworders/3970286"
OUT = "logs/recon/06_respond"


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
        page.goto(ORDER_URL, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(4_000)

        # ищем кнопку «Начать чат с клиентом»
        btn = page.get_by_text("Начать чат с клиентом", exact=False).first
        cnt = page.get_by_text("Начать чат с клиентом", exact=False).count()
        print(f"кнопок с текстом: {cnt}")
        if cnt:
            btn.click(timeout=5_000)
            page.wait_for_timeout(5_000)

        url2 = page.url
        print(f"URL после клика: {url2}")
        body2 = page.evaluate("() => document.body.innerText")
        with open(f"{OUT}_body.txt", "w", encoding="utf-8") as f:
            f.write(body2)
        # хвост тела — там обычно форма/диалог
        print("=== тело после клика (последние 2500) ===")
        print(body2[-2500:])
        page.screenshot(path=f"{OUT}_screen.png", full_page=False)

        # все testid на странице (для конструирования селекторов)
        testids = page.evaluate(
            """() => Array.from(document.querySelectorAll('[data-testid]'))
                 .map(e => e.getAttribute('data-testid')).filter((v,i,a) => a.indexOf(v) === i)"""
        )
        print("\n=== data-testid на странице ===")
        print("\n".join(testids[:80]))
        page.remove_listener("response", on_response)

    print("\n=== XHR/FETCH ===")
    for c in captured:
        p = urlparse(c["url"])
        size = len(json.dumps(c["resp"], ensure_ascii=False)) if c["resp"] else 0
        print(f"  {c['status']} {c['method']} {p.path}  resp~{size}b")
        if c["req"]:
            print(f"      req: {c['req'][:400]}")
    with open(f"{OUT}_net.json", "w", encoding="utf-8") as f:
        json.dump(captured, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
