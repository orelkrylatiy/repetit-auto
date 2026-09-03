"""BrowserManager: Chrome + CDP + вкладка ленты + health-check (repetit.ru).

Chrome — внешний процесс со своим профилем; воркер подключается по CDP
и не убивает его при выходе (сессия живёт в профиле). Автологина нет:
нет сессии → AUTH_REQUIRED, ждём человека (RECON §7).
"""

from __future__ import annotations

import logging
import subprocess
import time
from urllib.parse import urlparse

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from repetit import config

log = logging.getLogger("repetit.browser")

READY = "READY"
BROWSER_OFFLINE = "BROWSER_OFFLINE"
AUTH_REQUIRED = "AUTH_REQUIRED"


def _host_ok(hostname: str | None) -> bool:
    return hostname is not None and (
        hostname == "repetit.ru" or hostname.endswith(".repetit.ru")
    )


def is_feed_url(url: str) -> bool:
    """Строго лента /lk/teacher/neworders (без query-карточек /neworders/{id})."""
    try:
        p = urlparse(url)
    except Exception:
        return False
    return _host_ok(p.hostname) and (
        p.path == "/lk/teacher/neworders" or p.path.startswith("/lk/teacher/neworders?")
    )


def is_login_url(url: str) -> bool:
    try:
        p = urlparse(url)
    except Exception:
        return False
    return _host_ok(p.hostname) and p.path.startswith(config.LOGIN_PATH)


class BrowserManager:
    def __init__(self) -> None:
        self._pw = None
        self.browser: Browser | None = None
        self.page: Page | None = None
        self._chrome_proc: subprocess.Popen | None = None
        self._login_hint_shown = False

    # --- lifecycle ---

    def start(self) -> str:
        self._pw = sync_playwright().start()
        self._pw.selectors.set_test_id_attribute("data-testid")
        self.browser = self._connect()
        if self.browser is None:
            if config.CHROME_NO_LAUNCH:
                log.info(
                    "Chrome по CDP :%s не найден, авто-запуск выключен — жду",
                    config.CDP_PORT,
                )
                return BROWSER_OFFLINE
            try:
                self.browser = self._launch_and_connect()
            except FileNotFoundError:
                log.error("Chrome не найден (%s) — проверь REPETIT_CHROME_PATH", config.CHROME_PATH)
                return BROWSER_OFFLINE
        if self.browser is None:
            return BROWSER_OFFLINE

        ctx = self._default_context()
        page = self._find_our_page(ctx)
        if page is None:
            page = ctx.new_page()
            try:
                page.goto(config.FEED_URL, wait_until="domcontentloaded", timeout=45_000)
            except Exception as e:
                log.warning("goto feed failed: %s", e)
        self.page = page
        return self.check_session()

    def shutdown(self) -> None:
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
        log.warning("CDP потерян — переподключаюсь")
        self.shutdown()
        try:
            return self.start()
        except Exception as e:
            log.warning("reconnect не удался: %s", e)
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

    def _connect(self) -> Browser | None:
        try:
            browser = self._pw.chromium.connect_over_cdp(
                f"http://127.0.0.1:{config.CDP_PORT}", timeout=3_000
            )
            log.info("подключён к Chrome по CDP :%s", config.CDP_PORT)
            return browser
        except Exception:
            return None

    def _launch_and_connect(self) -> Browser | None:
        config.USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        log.info("запускаю Chrome: %s, CDP :%s", config.USER_DATA_DIR, config.CDP_PORT)
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
                log.error("Chrome завершился сразу — профиль занят другим Chrome без CDP?")
                return None
            browser = self._connect()
            if browser is not None:
                return browser
            time.sleep(0.7)
        log.error("не смог подключиться к CDP :%s за 25 с", config.CDP_PORT)
        return None

    def context(self) -> BrowserContext:
        """Дефолтный контекст Chrome (реальный, с сессией)."""
        return self._default_context()

    def _default_context(self) -> BrowserContext:
        if self.browser.contexts:
            return self.browser.contexts[0]
        return self.browser.new_context()

    # --- наши вкладки ---

    def _find_our_page(self, ctx: BrowserContext) -> Page | None:
        for page in ctx.pages:
            try:
                if is_feed_url(page.url):
                    return page
            except Exception:
                continue
        return None

    # --- session health-check ---

    def check_session(self) -> str:
        page = self.page
        if not self._browser_connected() or page is None:
            return BROWSER_OFFLINE
        try:
            if page.is_closed():
                return BROWSER_OFFLINE
            url = page.url
        except Exception as e:
            log.warning("не смог прочитать вкладку: %s", e)
            return BROWSER_OFFLINE
        if is_login_url(url):
            if not self._login_hint_shown:
                log.info(
                    ">>> Сессии нет (%s). Залогинься в repetit.ru в Chrome — воркер подхватит сам.",
                    url,
                )
                self._login_hint_shown = True
            return AUTH_REQUIRED
        return READY

    def ensure_ready(self) -> str:
        if not self._browser_connected():
            return self.reconnect()
        page = self.page
        try:
            page_closed = page is None or page.is_closed()
        except Exception:
            return self.reconnect()
        if page_closed:
            try:
                ctx = self._default_context()
                page = self._find_our_page(ctx)
                if page is None:
                    page = ctx.new_page()
                    try:
                        page.goto(config.FEED_URL, wait_until="domcontentloaded", timeout=45_000)
                    except Exception as e:
                        log.warning("goto feed failed: %s", e)
                self.page = page
            except Exception:
                return self.reconnect()
        return self.check_session()
