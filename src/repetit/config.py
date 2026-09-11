"""Конфигурация воркера repetit.ru.

Наружные настройки — .env в корне (шаблон .env.example), префикс REPETIT_*.
Приоритет: окружение процесса > .env > дефолт здесь.
Постоянные политики (гейты, URL) — литералами.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]


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


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (_get(name, "1" if default else "0") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


# --- пути ---
DATA_DIR = PROJECT_DIR / "data"
LOG_DIR = PROJECT_DIR / "logs"
DB_PATH = Path(_get("REPETIT_DB", str(DATA_DIR / "repetit.db")))
LOG_TAG = (_get("REPETIT_LOG_TAG", "") or "").strip()
WORKER_LOG = LOG_DIR / (f"worker-{LOG_TAG}.log" if LOG_TAG else "worker.log")
RESPOND_SHOT_DIR = LOG_DIR / (f"respond-{LOG_TAG}" if LOG_TAG else "respond")
CHAT_SHOT_DIR = LOG_DIR / (f"chat-{LOG_TAG}" if LOG_TAG else "chat")

# --- Chrome: внешний процесс, свой профиль и CDP-порт ---
CHROME_NO_LAUNCH = _env_bool("REPETIT_CHROME_NO_LAUNCH", False)
CHROME_PATH = _get(
    "REPETIT_CHROME_PATH",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)
_profile = _get("REPETIT_CHROME_PROFILE")
USER_DATA_DIR = Path(_profile) if _profile else PROJECT_DIR / "data" / "chrome-profiles" / "main"
if not USER_DATA_DIR.is_absolute():
    USER_DATA_DIR = PROJECT_DIR / USER_DATA_DIR
CDP_PORT = int(_get("REPETIT_CDP_PORT", "9335") or "9335")

# --- URL площадки ---
BASE_URL = "https://repetit.ru"
FEED_URL = f"{BASE_URL}/lk/teacher/neworders"
LOGIN_PATH = "/lk/loginwithshortcode"


def chat_url(order_id: int | str, chat_title: str) -> str:
    """URL чата по заявке."""
    from urllib.parse import quote

    return f"{BASE_URL}/lk/teacher/chatforteacher?orderId={order_id}&chatTitle={quote(chat_title)}"


# --- API ленты ---
API_SEARCH_ORDERS_PATH = "/lk/api/teacher/searchOrders"
API_ORDERS_BATCH_PATH = "/lk/api/teacher/orders"

# --- ритм цикла ---
CYCLE_MIN_S = int(_get("REPETIT_CYCLE_MIN", "90") or "90")
CYCLE_MAX_S = int(_get("REPETIT_CYCLE_MAX", "120") or "120")
CAPTURE_WINDOW_S = 10.0
CAPTURE_EXTRA_S = 3.0
MAX_RESPONDS_PER_CYCLE = int(_get("REPETIT_MAX_PER_CYCLE", "3") or "3")
PAUSE_BETWEEN_SENDS_MIN_S = 20.0
PAUSE_BETWEEN_SENDS_MAX_S = 45.0

# --- hard-фильтры (до LLM) ---
SUBJECT_KEYWORDS = [
    s.strip()
    for s in (_get("REPETIT_SUBJECTS", "информатик,программирован") or "").split(",")
    if s.strip()
]
MIN_CLIENT_RATE = int(
    _get("REPETIT_MIN_CLIENT_RATE") or _get("REPETIT_MIN_CLIENT_PRICE") or "0"
)
SPECIAL_NEEDS_PATTERNS = [
    "сдвг",
    "adhd",
    "аутиз",
    "аутичн",
    "аутист",
    "зпр",
    "зпрр",
    "дислекси",
    "дисграфи",
    "овз",
    "дцп",
]
BARTER_PATTERNS = [
    "бартер",
    "обмен урок",
    "обмен услуг",
    "взаимозачёт",
    "взаимозачет",
    "бесплатн",
]
ONSITE_PATTERNS = [r"\bочн[а-яё]*"]
STOP_PATTERNS = [
    s.strip().lower()
    for s in (_get("REPETIT_STOP_PATTERNS", "c++,с++,олимпиад") or "").split(",")
    if s.strip()
]

# --- денежные/текстовые предохранители ---
DAILY_SEND_LIMIT = int(_get("REPETIT_DAILY_SEND_LIMIT", "0") or "0")
MIN_TEXT_LEN = 100
MAX_TEXT_LEN = 600

# --- fallback первого сообщения ---
# В отличие от chat-auto, fallback первого отклика безопасно включён по умолчанию:
# hard filters уже пройдены, а шаблон всё равно проходит length/textguard gate.
FALLBACK_ENABLED = _env_bool("REPETIT_FALLBACK_ENABLED", True)
_fallback_raw = (_get("REPETIT_FALLBACK_TEMPLATES", "") or "").strip()
# Для env-override шаблоны разделяются `||`. Пустое значение = встроенные шаблоны.
FALLBACK_TEMPLATES = tuple(x.strip() for x in _fallback_raw.split("||") if x.strip())

# --- Контур B: автоответы в уже начатых чатах ---
# По умолчанию выключен до live canary sender-схемы Repetit. `chats-once --dry-run`
# можно использовать для безопасной проверки без Send.
CHAT_AUTO_ENABLED = _env_bool("REPETIT_CHAT_AUTO", False)
CHAT_CHECK_EVERY_CYCLES = max(1, int(_get("REPETIT_CHAT_EVERY_CYCLES", "3") or "3"))
CHAT_MAX_PER_CYCLE = max(1, int(_get("REPETIT_CHAT_MAX_PER_CYCLE", "2") or "2"))
CHAT_CANDIDATE_SCAN_LIMIT = max(
    CHAT_MAX_PER_CYCLE,
    int(_get("REPETIT_CHAT_SCAN_LIMIT", "6") or "6"),
)
CHAT_MAX_ORDER_AGE_DAYS = max(1, int(_get("REPETIT_CHAT_MAX_ORDER_AGE_DAYS", "14") or "14"))
CHAT_MIN_TEXT_LEN = 10
CHAT_MAX_TEXT_LEN = 800
CHAT_STATE_WAIT_S = 5.0

# --- cooldown-файлы ---
LLM_COOLDOWN_FILE = DATA_DIR / (f"llm-cooldown-{LOG_TAG}" if LOG_TAG else "llm-cooldown")
FEED_COOLDOWN_FILE = DATA_DIR / (f"feed-cooldown-{LOG_TAG}" if LOG_TAG else "feed-cooldown")

DEFAULT_WORK_HOURS = (8, 23)


def _parse_work_hours(v: str | None) -> tuple[int, int]:
    """Разобрать `lo,hi` как полуинтервал часов [lo, hi).

    Некорректная настройка не должна случайно включать воркер 24/7, поэтому
    при любой ошибке возвращаем безопасный дефолт 08:00–23:00. Круглосуточный
    режим задаётся только явно как `0,24`.
    """
    if not v or "," not in v:
        return DEFAULT_WORK_HOURS
    lo_raw, _, hi_raw = v.partition(",")
    try:
        lo = int(lo_raw.strip())
        hi = int(hi_raw.strip())
    except ValueError:
        return DEFAULT_WORK_HOURS
    if not (0 <= lo < hi <= 24):
        return DEFAULT_WORK_HOURS
    return lo, hi


WORK_HOURS = _parse_work_hours(_get("REPETIT_WORK_HOURS", "8,23"))

# --- персона и LLM ---
PERSONA = _get("REPETIT_PERSONA", "maxim")
PERSONA_DIR = PROJECT_DIR / "personas"
LOG_LEVEL = _get("REPETIT_LOG_LEVEL", "INFO") or "INFO"
