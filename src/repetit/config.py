"""Конфигурация воркера Контур A (repetit.ru).

Наружные настройки — .env в корне (шаблон .env.example), префикс REPETIT_*.
Приоритет: окружение процесса > .env > дефолт здесь.
Постоянные политики (гейты, URL) — литералами.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]  # src/repetit/config.py → корень


def _load_env_file() -> dict[str, str]:
    env: dict[str, str] = {}
    path = PROJECT_DIR / ".env"
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip("'\"")
    return env


_ENVFILE = _load_env_file()


def _get(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    if not v:
        v = _ENVFILE.get(name)
    return v if v else default


# --- пути ---
DATA_DIR = PROJECT_DIR / "data"
LOG_DIR = PROJECT_DIR / "logs"
DB_PATH = Path(_get("REPETIT_DB", str(DATA_DIR / "repetit.db")))
WORKER_LOG = LOG_DIR / "worker.log"
RESPOND_SHOT_DIR = LOG_DIR / "respond"

# --- Chrome: внешний процесс, свой профиль и CDP-порт ---
CHROME_NO_LAUNCH = _get("REPETIT_CHROME_NO_LAUNCH", "0") == "1"
CHROME_PATH = _get(
    "REPETIT_CHROME_PATH",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)
_profile = _get("REPETIT_CHROME_PROFILE")
USER_DATA_DIR = Path(_profile) if _profile else PROJECT_DIR / "data" / "chrome-profiles" / "main"
if not USER_DATA_DIR.is_absolute():
    USER_DATA_DIR = PROJECT_DIR / USER_DATA_DIR
CDP_PORT = int(_get("REPETIT_CDP_PORT", "9335"))

# --- URL площадки ---
BASE_URL = "https://repetit.ru"
FEED_URL = f"{BASE_URL}/lk/teacher/neworders"
LOGIN_PATH = "/lk/loginwithshortcode"  # редирект сюда = сессии нет


def chat_url(order_id: int | str, chat_title: str) -> str:
    """URL чата-формы отклика по заявке (см. RECON §5)."""
    from urllib.parse import quote

    return f"{BASE_URL}/lk/teacher/chatforteacher?orderId={order_id}&chatTitle={quote(chat_title)}"


# --- API ленты (RECON §3) ---
API_SEARCH_ORDERS_PATH = "/lk/api/teacher/searchOrders"  # POST → [id, ...]
API_ORDERS_BATCH_PATH = "/lk/api/teacher/orders"  # GET ?ids= → [dict, ...]

# --- ритм цикла ---
CYCLE_MIN_S = int(_get("REPETIT_CYCLE_MIN", "90"))
CYCLE_MAX_S = int(_get("REPETIT_CYCLE_MAX", "120"))
CAPTURE_WINDOW_S = 10.0  # ждём первый ответ ленты после reload
CAPTURE_EXTRA_S = 3.0  # добираем повторы
AUTH_WAIT_S = 30  # период проверки при вылогине
MAX_RESPONDS_PER_CYCLE = int(_get("REPETIT_MAX_PER_CYCLE", "3"))
PAUSE_BETWEEN_SENDS_MIN_S = 20.0
PAUSE_BETWEEN_SENDS_MAX_S = 45.0

# --- hard-фильтры (до LLM) ---
SUBJECT_KEYWORDS = [
    s.strip()
    for s in _get("REPETIT_SUBJECTS", "информатик,программирован").split(",")
    if s.strip()
]
# Минимальный бюджет клиента ₽/60 мин; 0 = не фильтровать
# (алиас REPETIT_MIN_CLIENT_PRICE из SPEC тоже принимается)
MIN_CLIENT_RATE = int(_get("REPETIT_MIN_CLIENT_RATE") or _get("REPETIT_MIN_CLIENT_PRICE") or "0")
# Особые потребности — не наш профиль (как в profi, решение владельца)
SPECIAL_NEEDS_PATTERNS = [
    "сдвг", "adhd", "аутиз", "аутичн", "аутист", "зпр", "зпрр",
    "дислекси", "дисграфи", "овз", "дцп",
]
BARTER_PATTERNS = ["бартер", "обмен урок", "обмен услуг", "взаимозачёт", "взаимозачет", "бесплатн"]

# --- денежные предохранители ---
DAILY_SEND_LIMIT = int(_get("REPETIT_DAILY_SEND_LIMIT", "3"))  # 0 = без лимита
# Живой факт 2026-09-03: реальные отправки 497–576 символов приняты площадкой.
# Верхняя граница = проверенное + запас; промпт просит короче (3–6 предложений)
MIN_TEXT_LEN = 100
MAX_TEXT_LEN = 600

# --- cooldown-файлы (ts до которого не дёргаем) ---
LLM_COOLDOWN_FILE = DATA_DIR / "llm-cooldown"
FEED_COOLDOWN_FILE = DATA_DIR / "feed-cooldown"


def _parse_work_hours(v: str | None) -> tuple[int, int]:
    if not v or "," not in v:
        return (0, 24)
    lo, _, hi = v.partition(",")
    try:
        return (max(0, int(lo.strip())), min(24, int(hi.strip())))
    except ValueError:
        return (0, 24)


WORK_HOURS = _parse_work_hours(_get("REPETIT_WORK_HOURS", "8,23"))

# --- персона и LLM ---
PERSONA = _get("REPETIT_PERSONA", "maxim")
PERSONA_DIR = PROJECT_DIR / "personas"
LOG_LEVEL = _get("REPETIT_LOG_LEVEL", "INFO")
