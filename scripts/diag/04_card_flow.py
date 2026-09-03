"""Пробник 04: АКТИВНО — DOM-структура карточек ленты + клик по карточке.

1. /lk/teacher/neworders: HTML-структура списка (классы карточек, data-атрибуты)
2. Клик по первой карточке → URL/модалка + полный capture сети вокруг клика
3. Логируем всё в logs/recon/04_*.
"""

from __future__ import annotations

import json
import sys
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9335"
FEED = "https://repetit.ru/lk/teacher/neworders"
OUT = "logs/recon/04_card"


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
                item = {
                    "ts": round(time.time(), 3),
                    "phase": PHASE[0],
                    "method": req.method,
                    "url": resp.url,
                    "status": resp.status,
                    "req_body": None,
                    "resp_body": None,
                }
                try:
                    pd = req.post_data
                    item["req_body"] = pd[:3000] if pd else None
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
        PHASE = ["load"]
        page.goto(FEED, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(3_000)

        # --- 1. DOM-структура: наружный HTML первой карточки + теги списка ---
        struct = page.evaluate(
            """() => {
                const main = document.querySelector('main') || document.body;
                // ищем повторяющиеся контейнеры карточек: у каждой есть «№ »
                const candidates = Array.from(main.querySelectorAll('*')).filter(el =>
                    el.children.length === 0 && el.innerText && el.innerText.includes('№')
                );
                const cardEls = new Set();
                for (const el of candidates) {
                    let p = el.parentElement;
                    while (p && p !== main) {
                        if (p.innerText && p.innerText.includes('₽ за 60 мин')) { cardEls.add(p); break; }
                        p = p.parentElement;
                    }
                }
                const first = cardEls.values().next().value;
                return {
                    cardCount: cardEls.size,
                    firstCardHtml: first ? first.outerHTML.slice(0, 4000) : null,
                    firstCardClasses: first ? first.className : null,
                };
            }"""
        )
        print(f"карточек: {struct['cardCount']}, класс первой: {struct['firstCardClasses']!r}")
        with open(f"{OUT}_first.html", "w", encoding="utf-8") as f:
            f.write(struct["firstCardHtml"] or "")

        # есть ли пагинация/«показать ещё»
        more = page.evaluate(
            """() => Array.from(document.querySelectorAll('button, a'))
                .filter(e => /ещё|показать|загрузить/i.test(e.innerText || ''))
                .map(e => ({tag: e.tagName, text: e.innerText.trim().slice(0, 50)}))"""
        )
        print(f"пагинация: {more}")

        # --- 2. КЛИК по первой карточке (открываем заявку №3970286-подобную) ---
        PHASE[0] = "click-card"
        clicked = page.evaluate(
            """() => {
                const all = Array.from(document.querySelectorAll('*')).filter(el =>
                    el.children.length === 0 && el.innerText && /^№ ?\\d+$/.test(el.innerText.trim()));
                if (!all.length) return null;
                const target = all[0].closest('a, button, [role=button], [class*=card], [class*=order]') || all[0];
                const r = target.getBoundingClientRect();
                return {tag: target.tagName, cls: String(target.className).slice(0, 100), x: r.x + r.width/2, y: r.y + r.height/2};
            }"""
        )
        if clicked:
            print(f"кликаю: tag={clicked['tag']} cls={clicked['cls']!r}")
            page.mouse.click(clicked["x"], clicked["y"])
            page.wait_for_timeout(5_000)

        PHASE[0] = "after"
        url2, title2 = page.url, page.title()
        print(f"\nпосле клика: URL={url2}\ntitle={title2}")
        body2 = page.evaluate("() => document.body.innerText")
        with open(f"{OUT}_after_body.txt", "w", encoding="utf-8") as f:
            f.write(body2)
        print("=== тело после клика [:1800] ===")
        print(body2[:1800])
        page.screenshot(path=f"{OUT}_after_screen.png", full_page=False)
        page.remove_listener("response", on_response)

    print("\n=== XHR/FETCH вокруг клика ===")
    for c in captured:
        p = urlparse(c["url"])
        size = len(json.dumps(c["resp_body"], ensure_ascii=False)) if c["resp_body"] else 0
        print(f"  [{c['phase']}] {c['status']} {c['method']} {p.path}  resp~{size}b")
        if c["req_body"]:
            print(f"      req: {c['req_body'][:300]}")

    with open(f"{OUT}_net.json", "w", encoding="utf-8") as f:
        json.dump(captured, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
