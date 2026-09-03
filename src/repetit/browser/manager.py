"""BrowserManager: жизненный цикл Chrome + CDP + feed page + health-check.

Спека: разд. 5 (startup lifecycle), 6 (BrowserManager), 7 (session health-check).
Chrome — внешний процесс, воркер только подключается по CDP и не убивает его
при выходе. Автологина нет: если сессии нет — AUTH_REQUIRED и ждём человека.
"""

from __future__ import annotations

import logging
import subprocess
import time
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from repetit import config

log = logging.getLogger("profi.browser")

# состояния воркера (спека разд. 7)
READY = "READY"
BROWSER_OFFLINE = "BROWSER_OFFLINE"
AUTH_REQUIRED = "AUTH_REQUIRED"
PROFI_UNAVAILABLE = "PROFI_UNAVAILABLE"


def is_order_tab(url: str) -> bool:
    """n.php?o=<id> — вкладка карточки заказа. Открывается ТОЛЬКО нашей
    автоматикой (popup или прямой URL в open_candidate) и закрывается там же;
    зависшая = утечка после падения/OOM-килла."""
    p = urlparse(url)
    return (
        p.hostname is not None
        and (p.hostname == config.FEED_HOST or p.hostname.endswith("." + config.FEED_HOST))
        and p.path == config.FEED_PATH
        and "o" in parse_qs(p.query)
    )


def is_feed_url(url: str) -> bool:
    """Feed page: host = profi.ru, path = /backoffice/n.php, без параметра o=<id>.

    o ищем parse_qs (точное имя параметра), а не подстрокой «o=»:
    ?logo=1 — тоже валидная лента, не AUTH_REQUIRED (ревью P3).
    """
    p = urlparse(url)
    if not (
        p.scheme in ("http", "https")
        and p.hostname is not None
        and (p.hostname == config.FEED_HOST or p.hostname.endswith("." + config.FEED_HOST))
        and p.path == config.FEED_PATH
    ):
        return False
    return "o" not in parse_qs(p.query)


class BrowserManager:
    def __init__(self) -> None:
        self._pw = None
        self.browser: Browser | None = None
        self.page: Page | None = None
        self._chrome_proc: subprocess.Popen | None = None
        self._login_hint_shown = False

    # --- lifecycle ---

    def start(self) -> str:
        """INITIALIZING → CONNECT_CDP → FIND_FEED_PAGE → SESSION_CHECK. Возвращает READY/AUTH_REQUIRED/..."""
        self._pw = sync_playwright().start()
        # data-testid — родной атрибут Профи: включаем testid-движок локаторов
        self._pw.selectors.set_test_id_attribute("data-testid")
        self.browser = self._connect()
        if self.browser is None:
            if config.CHROME_NO_LAUNCH:
                # Chrome поднимает владелец (сессия/телеметрия в его руках):
                # сами не стартуем — OFFLINE, воркер молча ждёт (инцидент 03.09:
                # автозапуск схватил чужой профиль и упал)
                log.info(
                    "Chrome по CDP :%s не найден, авто-запуск выключен "
                    "(PROFI_CHROME_NO_LAUNCH=1) — жду, пока браузер поднимут",
                    config.CDP_PORT,
                )
                return BROWSER_OFFLINE
            self.browser = self._launch_and_connect()
        if self.browser is None:
            return BROWSER_OFFLINE

        ctx = self._default_context()
        page = self._find_feed_page(ctx)
        if page is None:
            page = ctx.new_page()
            try:
                page.goto(config.FEED_URL, wait_until="domcontentloaded", timeout=45_000)
            except Exception as e:
                log.warning("goto feed failed: %s", e)
        self.page = page
        return self.check_session()

    def shutdown(self) -> None:
        """Отключаемся от CDP; Chrome оставляем жить (сессия и телеметрия в нём)."""
        for closer in (self._close_browser, self._pw.stop if self._pw else None):
            try:
                if callable(closer):
                    closer()
            except Exception:
                pass
        self.browser = None
        self.page = None
        self._pw = None

    def reconnect(self) -> str:
        """Полностью пересоздать Playwright/CDP connection после падения Chrome.

        Старый Browser object после disconnect не оживает сам. Без reset worker
        мог бесконечно возвращать BROWSER_OFFLINE даже после того, как supervisor
        уже поднял новый Chrome на том же порту.
        """
        log.warning("CDP соединение потеряно — переподключаю BrowserManager")
        self.shutdown()
        try:
            return self.start()
        except Exception as e:
            log.warning("reconnect Chrome не удался: %s", e)
            self.shutdown()
            return BROWSER_OFFLINE

    def _close_browser(self) -> None:
        if self.browser is not None:
            self.browser.close()

    def _browser_connected(self) -> bool:
        if self.browser is None:
            return False
        try:
            return self.browser.is_connected()
        except Exception:
            return False

    # --- CDP ---

    def _cdp_url(self) -> str:
        return f"http://127.0.0.1:{config.CDP_PORT}"

    def _connect(self) -> Browser | None:
        """Chrome уже запущен с нашим CDP-портом?"""
        try:
            browser = self._pw.chromium.connect_over_cdp(self._cdp_url(), timeout=3_000)
            log.info("подключился к работающему Chrome по CDP :%s", config.CDP_PORT)
            return browser
        except Exception:
            return None

    def _launch_and_connect(self) -> Browser | None:
        config.USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        log.info(
            "запускаю Chrome: user-data-dir=%s, CDP :%s", config.USER_DATA_DIR, config.CDP_PORT
        )
        self._chrome_proc = subprocess.Popen(
            [
                config.CHROME_PATH,
                f"--user-data-dir={config.USER_DATA_DIR}",
                f"--remote-debugging-port={config.CDP_PORT}",
                "--remote-allow-origins=*",
                "--no-first-run",
                "--no-default-browser-check",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 25.0
        while time.monotonic() < deadline:
            if self._chrome_proc.poll() is not None:
                log.error(
                    "Chrome завершился сразу. Вероятно, профиль %s уже открыт другим Chrome "
                    "без CDP-порта — закрой это окно Chrome и запусти воркер снова.",
                    config.USER_DATA_DIR,
                )
                return None
            browser = self._connect()
            if browser is not None:
                return browser
            time.sleep(0.7)
        log.error("не смог подключиться к CDP :%s за 25 с", config.CDP_PORT)
        return None

    def context(self) -> BrowserContext:
        """Дефолтный контекст Chrome (реальный, с сессией) — публичный API."""
        return self._default_context()

    def _default_context(self) -> BrowserContext:
        if self.browser.contexts:
            return self.browser.contexts[0]
        return self.browser.new_context()

    # --- feed page discovery (спека разд. 6) ---

    def _find_feed_page(self, ctx: BrowserContext) -> Page | None:
        for page in ctx.pages:
            try:
                # вкладка с ?o= — открытый заказ, не лента (o= уже в is_feed_url)
                if is_feed_url(page.url):
                    log.info("нашёл вкладку ленты: %s", page.url)
                    return page
            except Exception:
                continue
        return None

    # --- session health-check (спека разд. 7) ---

    def check_session(self) -> str:
        """Дешёвая проверка без reload: страница жива и осталась на n.php.

        Окончательное подтверждение сессии делает FeedCapture
        (BoSearchBoardItems → 200 → data.boSearchBoardItems).
        """
        page = self.page
        if not self._browser_connected() or page is None:
            return BROWSER_OFFLINE
        try:
            if page.is_closed():
                return BROWSER_OFFLINE
            url = page.url
        except Exception as e:
            log.warning("не смог прочитать состояние вкладки: %s", e)
            return BROWSER_OFFLINE
        if not is_feed_url(url):
            # редирект на логин/авторизацию или уход со страницы ленты
            if not self._login_hint_shown:
                log.info("вкладка не на ленте (%s) — вероятно, нужна авторизация", url)
                log.info(
                    ">>> Залогинься в Профи.ру в открывшемся Chrome — воркер подхватит сессию сам."
                )
                self._login_hint_shown = True
            return AUTH_REQUIRED
        return READY

    def close_stray_tabs(self) -> list[str]:
        """Гигиена вкладок (анти-утечка памяти, VPS 1.6 ГБ).

        Наша автоматика держит РОВНО ОДНУ вкладку ленты; карточки заказов
        (n.php?o=) — всплывающие и закрываются сразу. Зависшие дубли ленты и
        карточки — от прошлых падений/OOM-киллов, их нельзя поймать в коде,
        поэтому подчищаем перед каждым циклом. Чужие вкладки (чаты r.php,
        newtab) НЕ трогаем: r.php бывает открыт chat-пробником прямо сейчас.
        """
        closed: list[str] = []
        # Кооперативная пауза: автопилот отправляет отклик — вкладки не трогаем,
        # иначе гигиена закроет вкладку заказа прямо после клика (инциденты
        # #93438144/#93464149). Протухший сигнал (>240 с) игнорируем.
        try:
            if (time.time() - config.SEND_PAUSE_FILE.stat().st_mtime) < 240:
                return closed
        except OSError:
            pass
        try:
            ctx = self._default_context()
        except Exception:
            return closed
        feed_kept = False
        for pg in list(ctx.pages):
            try:
                url = pg.url
                if is_feed_url(url):
                    if feed_kept:
                        closed.append(url)
                        pg.close(run_before_unload=False)
                    else:
                        feed_kept = True
                elif is_order_tab(url):
                    closed.append(url)
                    pg.close(run_before_unload=False)
            except Exception:
                continue
        if closed:
            log.warning(
                "гигиена вкладок: закрыто %d зависших: %s", len(closed), [u[:70] for u in closed]
            )
        return closed

    def ensure_ready(self) -> str:
        """Перед каждым циклом: reconnect при dead CDP, затем гигиена вкладок."""
        if not self._browser_connected():
            return self.reconnect()
        self.close_stray_tabs()
        page = self.page
        try:
            page_closed = page is None or page.is_closed()
        except Exception:
            return self.reconnect()
        if page_closed:
            try:
                ctx = self._default_context()
                page = self._find_feed_page(ctx)
                if page is None:
                    page = ctx.new_page()
                    try:
                        page.goto(config.FEED_URL, wait_until="domcontentloaded", timeout=45_000)
                    except Exception as e:
                        log.warning("goto feed failed: %s", e)
                self.page = page
            except Exception:
                return self.reconnect()
        state = self.check_session()
        if state == BROWSER_OFFLINE and not self._browser_connected():
            return self.reconnect()
        return state
