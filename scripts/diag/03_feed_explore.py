"""Пробник 03: АКТИВНО — клик «Заявки» в меню, полный capture сети + DOM ленты.

Логируем всё: URL, статусы, JSON-тела ответов (в файл), скриншот, текст DOM.
"""

from __future__ import annotations

import json
import sys
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9335"
HOME = "https://repetit.ru/lk/teacher/home"
OUT = "logs/recon/03_feed"


def main() -> int:
    captured: list[dict] = []

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(CDP, timeout=5_000)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        def on_response(resp) -> None:
            try:
                req = resp.request
                rt = req.resource_type
                if rt not in ("xhr", "fetch"):
                    return
                item = {
                    "ts": round(time.time(), 3),
                    "method": req.method,
                    "url": resp.url,
                    "status": resp.status,
                    "req_body": None,
                    "resp_body": None,
                }
                try:
                    pd = req.post_data
                    item["req_body"] = pd[:2000] if pd else None
                except Exception:
                    pass
                ct = (resp.headers or {}).get("content-type", "")
                if "json" in ct:
                    try:
                        item["resp_body"] = resp.json()
                    except Exception:
                        item["resp_body"] = "<unparseable>"
                captured.append(item)
            except Exception:
                pass

        page.on("response", on_response)
        page.goto(HOME, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(2_500)

        # АКТИВНО: клик по пункту меню «Заявки»
        try:
            menu_item = page.get_by_text("Заявки", exact=True).first
            menu_item.click(timeout=5_000)
        except Exception as e:
            print(f"клик «Заявки» не удался: {e}")
        page.wait_for_timeout(5_000)

        url, title = page.url, page.title()
        print(f"URL: {url}\ntitle: {title}\n")

        body_text = page.evaluate("() => document.body.innerText")
        with open(f"{OUT}_body.txt", "w", encoding="utf-8") as f:
            f.write(body_text)
        print("=== body[:1500] ===")
        print(body_text[:1500])

        page.screenshot(path=f"{OUT}_screen.png", full_page=False)
        page.remove_listener("response", on_response)

    # сводка
    print("\n=== XHR/FETCH (кратко) ===")
    for c in captured:
        p = urlparse(c["url"])
        size = len(json.dumps(c["resp_body"], ensure_ascii=False)) if c["resp_body"] else 0
        print(f"  {c['status']} {c['method']} {p.path}  resp~{size}b")
        if c["req_body"]:
            print(f"      req: {c['req_body'][:200]}")

    with open(f"{OUT}_net.json", "w", encoding="utf-8") as f:
        json.dump(captured, f, ensure_ascii=False, indent=1)
    print(f"\nсохранено: {OUT}_net.json, {OUT}_body.txt, {OUT}_screen.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
