"""Диагностика 03: пассивный capture ленты «Новые заявки».

Открывает `/lk/teacher/neworders` в собственной временной вкладке, собирает
XHR/fetch и DOM для диагностики контрактов feed. Карточки заявок и чаты не
открывает, поэтому `viewed` не ставит и сообщений не отправляет.

Сырые ответы могут содержать данные заявок. Артефакты пишутся только в
`logs/recon/`, который исключён из git.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9335"
FEED = "https://repetit.ru/lk/teacher/neworders"
OUT_DIR = Path("logs/recon")
OUT_PREFIX = OUT_DIR / "03_feed"


def main() -> int:
    captured: list[dict] = []
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(CDP, timeout=5_000)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        try:
            def on_response(resp) -> None:
                try:
                    req = resp.request
                    if req.resource_type not in ("xhr", "fetch"):
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
                        post_data = req.post_data
                        item["req_body"] = post_data[:2000] if post_data else None
                    except Exception:
                        pass
                    content_type = (resp.headers or {}).get("content-type", "")
                    if "json" in content_type:
                        try:
                            item["resp_body"] = resp.json()
                        except Exception:
                            item["resp_body"] = "<unparseable>"
                    captured.append(item)
                except Exception:
                    pass

            page.on("response", on_response)
            page.goto(FEED, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(5_000)

            url, title = page.url, page.title()
            print(f"URL: {url}\ntitle: {title}\n")

            body_text = page.locator("body").inner_text()
            Path(f"{OUT_PREFIX}_body.txt").write_text(body_text, encoding="utf-8")
            print("=== body[:1500] ===")
            print(body_text[:1500])

            page.screenshot(path=f"{OUT_PREFIX}_screen.png", full_page=False)
            page.remove_listener("response", on_response)
        finally:
            page.close(run_before_unload=False)

    print("\n=== XHR/FETCH (кратко) ===")
    for item in captured:
        parsed = urlparse(item["url"])
        size = len(json.dumps(item["resp_body"], ensure_ascii=False)) if item["resp_body"] else 0
        print(f"  {item['status']} {item['method']} {parsed.path}  resp~{size}b")
        if item["req_body"]:
            print(f"      req: {item['req_body'][:200]}")

    net_path = Path(f"{OUT_PREFIX}_net.json")
    net_path.write_text(json.dumps(captured, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nсохранено: {net_path}, {OUT_PREFIX}_body.txt, {OUT_PREFIX}_screen.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
