"""Пробник 05: АКТИВНО — найти кликабельного предка карточки и открыть заявку.

1. Предки order-list-item-info-subject-name: tag/role/testid/cursor
2. Клик по лучшему кандидату (Playwright locator — с actionability)
3. Наблюдение: URL, DOM, сеть 6 с
"""

from __future__ import annotations

import json
import sys
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9335"
FEED = "https://repetit.ru/lk/teacher/neworders"
OUT = "logs/recon/05_open"


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
                    item["req"] = pd[:3000] if pd else None
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
        page.goto(FEED, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(3_000)

        # --- 1. цепочка предков ---
        chain = page.evaluate(
            """() => {
                const el = document.querySelector('[data-testid=order-list-item-info-subject-name]');
                if (!el) return [];
                const out = [];
                let p = el;
                for (let i = 0; i < 8 && p; i++) {
                    const cs = getComputedStyle(p);
                    out.push({
                        depth: i, tag: p.tagName.toLowerCase(), role: p.getAttribute('role'),
                        testid: p.getAttribute('data-testid'), cls: String(p.className).slice(0, 80),
                        cursor: cs.cursor, clickable: typeof p.onclick === 'function' || !!p.getAttribute('onclick'),
                    });
                    p = p.parentElement;
                }
                return out;
            }"""
        )
        print("=== предки subject-name ===")
        for c in chain:
            print(f"  d{c['depth']} <{c['tag']}> role={c['role']} testid={c['testid']} cursor={c['cursor']} onclick={c['clickable']}")

        # --- 2. клик по контейнеру карточки (первый предок с cursor pointer) ---
        clicked_cls = page.evaluate(
            """() => {
                const el = document.querySelector('[data-testid=order-list-item-info-subject-name]');
                if (!el) return null;
                let p = el.parentElement;
                while (p) {
                    if (getComputedStyle(p).cursor === 'pointer') return p;
                    p = p.parentElement;
                }
                return null;
            }"""
        )
        print(f"\ncursor:pointer контейнер: {bool(clicked_cls)}")

        # кликаем по subject-name напрямую — RNW обычно вешает onPress на текст
        loc = page.locator('[data-testid="order-list-item-info-subject-name"]').first
        loc.click(timeout=5_000)
        page.wait_for_timeout(6_000)

        url2, title2 = page.url, page.title()
        print(f"\nпосле клика: URL={url2}\ntitle={title2}")
        body2 = page.evaluate("() => document.body.innerText")
        with open(f"{OUT}_after_body.txt", "w", encoding="utf-8") as f:
            f.write(body2)
        print("=== тело после клика [:2000] ===")
        print(body2[:2000])
        page.screenshot(path=f"{OUT}_after_screen.png", full_page=False)
        page.remove_listener("response", on_response)

    print("\n=== XHR/FETCH ===")
    for c in captured:
        p = urlparse(c["url"])
        size = len(json.dumps(c["resp"], ensure_ascii=False)) if c["resp"] else 0
        print(f"  {c['status']} {c['method']} {p.path}  resp~{size}b")
        if c["req"]:
            print(f"      req: {c['req'][:300]}")
    with open(f"{OUT}_net.json", "w", encoding="utf-8") as f:
        json.dump(captured, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
