"""Конфигурация воркера Контур A.

Наружные настройки — .env в корне проекта (шаблон: .env.example), плюс
переопределение на запуск через переменные окружения (launchd, accounts/<acc>.env,
ручные прогоны). Приоритет: окружение процесса > .env > дефолт здесь.
Постоянные политики (гейты, ритм, URL) — литералами ниже, снаружи не меняются.
"""

from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]  # src/repetit/config.py → корень репо


def _load_env_file() -> dict[str, str]:
    """KEY=VALUE из корневого .env (тот же файл, что читает llm/client.py)."""
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
    """Приоритет: окружение процесса > .env > default. Пустая строка = не задано."""
    v = os.environ.get(name)
    if not v:
        v = _ENVFILE.get(name)
    return v if v else default


DATA_DIR = PROJECT_DIR / "data"
LOG_DIR = PROJECT_DIR / "logs"
DB_PATH = Path(_get("PROFI_DB", str(DATA_DIR / "profi.db")))

# --- Разделение нескольких акков на одной машине: свой лог и лок ---
# PROFI_LOG_TAG задаётся в accounts/<акк>.env; пустой = старые имена файлов.
LOG_TAG = _get("PROFI_LOG_TAG", "").strip()
WORKER_LOG = LOG_DIR / (f"worker-{LOG_TAG}.log" if LOG_TAG else "worker.log")
AUTOPILOT_LOG = LOG_DIR / (f"autopilot-{LOG_TAG}.log" if LOG_TAG else "autopilot.log")
AUTOPILOT_LOCK = DATA_DIR / (f"{LOG_TAG}.autopilot.lock" if LOG_TAG else "autopilot.lock")

# Production account workers на VPS маркируются PROFI_RHYTHM_TAG. Внешний
# run_account.sh и внутренний restart из autopilot обязаны использовать один
# и тот же lifetime-lock, иначе после первой платной отправки worker мог быть
# поднят уже без singleton-защиты.
RHYTHM_TAG = os.environ.get("PROFI_RHYTHM_TAG", "").strip()
WORKER_LOCK = DATA_DIR / (f"{RHYTHM_TAG}.worker.lock" if RHYTHM_TAG else "worker.lock")
if RHYTHM_TAG and shutil.which("flock") and not os.environ.get("PROFI_WORKER_START_CMD"):
    _project_q = shlex.quote(str(PROJECT_DIR))
    _lock_q = shlex.quote(str(WORKER_LOCK))
    _tag_q = shlex.quote(RHYTHM_TAG)
    _log_q = shlex.quote(str(WORKER_LOG))
    os.environ["PROFI_WORKER_START_CMD"] = (
        f"cd {_project_q} && nohup flock -w 15 {_lock_q} "
        f"env PROFI_RHYTHM_TAG={_tag_q} uv run python -m profi.main "
        f"--rhythm-tag {_tag_q} >> {_log_q} 2>&1 &"
    )

# Файл-сигнал «идёт платная отправка»: автопилот ставит его перед открытием
# формы отклика и снимает после. Воркер на это время пропускает цикл, а
# таб-гигиена не закрывает вкладки (pgrep/pkill на Windows недоступны —
# сериализация автопилота с воркером только кооперативная; инциденты
# #93438144/#93464149: гигиена воркера закрыла вкладку сразу после клика).
SEND_PAUSE_FILE = DATA_DIR / (f"{LOG_TAG}.send-pause" if LOG_TAG else "send-pause")

# Файл-пауза «LLM у провайдера на лимите» (429/1308/1310): автопилот пишет
# сюда ts сброса из текста ошибки, и до этого времени флоу откликов и чаты
# не запускаются вовсе (по образцу WORK_HOURS) — ноль холостых вызовов,
# кандидаты остаются в очереди.
LLM_COOLDOWN_FILE = DATA_DIR / (f"{LOG_TAG}.llm-cooldown" if LOG_TAG else "llm-cooldown")

# --- Персона (промпт) и фильтры: один аккаунт = одна персона ---
PERSONA = _get("PROFI_PERSONA", "info")
PERSONA_DIR = PROJECT_DIR / "personas"
SUBJECT_KEYWORDS = [
    s.strip()
    for s in _get("PROFI_SUBJECTS", "информатик,программирован,математик").split(",")
    if s.strip()
]

# --- Chrome (правило: один аккаунт = один user-data-dir, свой CDP-порт) ---
# 1 = Chrome сами не запускаем: нет CDP — BROWSER_OFFLINE и ждём (в Profi
# не заходим), браузер поднимает владелец. 0 = разрешён авто-запуск (VPS).
CHROME_NO_LAUNCH = _get("PROFI_CHROME_NO_LAUNCH", "0") == "1"
CHROME_PATH = _get(
    "PROFI_CHROME_PATH",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)
# Профили браузеров живут внутри проекта (data/browser-profiles/<акк>);
# относительный PROFI_CHROME_PROFILE из accounts/<acc>.env считается от
# PROJECT_DIR, чтобы env-файлы не зависели от расположения проекта.
_profile = _get("PROFI_CHROME_PROFILE")
USER_DATA_DIR = Path(_profile) if _profile else PROJECT_DIR / "data" / "chrome-profiles" / "main"
if not USER_DATA_DIR.is_absolute():
    USER_DATA_DIR = PROJECT_DIR / USER_DATA_DIR
CDP_PORT = int(_get("PROFI_CDP_PORT", "9333"))

FEED_URL = "https://profi.ru/backoffice/n.php"
FEED_HOST = "profi.ru"
FEED_PATH = "/backoffice/n.php"

# --- Ритм цикла ---
# Рекон 2026-08-30: ~10 одинаковых запросов за 5 мин = мягкий 403.
# 45–60 с = ~6.7 запроса/5мин — ниже порога (владелец ускорил 2026-09-03;
# было 90–120). При 401/403 — пауза 30–60 мин (AUTH_COOLDOWN_S).
RELOAD_INTERVAL_MIN_S = int(_get("PROFI_RELOAD_MIN", "45"))
RELOAD_INTERVAL_MAX_S = int(_get("PROFI_RELOAD_MAX", "60"))
CAPTURE_WINDOW_S = 8.0  # сколько ждём первый BoSearchBoardItems после reload
CAPTURE_EXTRA_S = 3.0  # сколько ещё собираем повторы после первого пойманного
AUTH_WAIT_S = 30  # период проверки, пока ждём ручной логин
AUTH_COOLDOWN_S = 30 * 60  # пауза после 401/403

# --- Hard filters (до LLM). Настройки под цель: ЕГЭ/ОГЭ информатика, дистанционно ---
# Подстроки в title+description (без учёта регистра); переопределяется PROFI_SUBJECTS
MIN_RATE = (
    None  # 2026-08-31: фильтр по цене ВЫКЛЮЧЕН владельцем (вход на площадку, берём любые бюджеты)
)
VACANCY_PATTERNS = ["ваканс"]
# Особые потребности (СДВГ, аутизм и смежное) — не наш профиль (решение
# Макса 03.09). Стемы в нижнем регистре, матчатся по подстроке title+description.
SPECIAL_NEEDS_PATTERNS = [
    "сдвг",
    "adhd",
    "аутиз",
    "аутичн",
    "аутист",
    "зпр",  # ЗПР и ЗПРР
    "дислекси",
    "дисграфи",
    "овз",
    "дцп",
]
# Бартер/обмен/бесплатно — не монетизируются (владелец 03.09: «цель — бабки,
# а не что-то другое»). Без «без доплат»/«обмен» поодиночке — ложные SKIP дороже.
BARTER_PATTERNS = [
    "бартер",
    "обмен урок",
    "обмен услуг",
    "взаимозачёт",
    "взаимозачет",
    "бесплатн",
]
REMOTE_ONLY = True  # geo.remote пуст → только очно → skip

# --- Денежные предохранители (RULES.md §2; ревью P0-2) ---
MAX_RESPONSE_PRICE_RUB = int(_get("PROFI_MAX_RESPONSE_PRICE", "500"))  # 0 = без потолка
DAILY_SEND_LIMIT = int(_get("PROFI_DAILY_SEND_LIMIT", "0"))  # 0 = без дневного лимита
MAX_COMPETITION_POSITION = int(_get("PROFI_MAX_POSITION", "20"))  # 0 = не проверять позицию
RATE = 2000  # ставка ₽/час в форме отклика (RULES: менять здесь)

# --- Тариф отклика (адаптивность: акк может откликаться платно или через комиссию) ---
# PROFI_RESPOND_MODE: "pay" (платный отклик, дефолт) | "commission" (через комиссию Profi)
# Денежный режим обязан быть fail-closed: опечатка не должна молча превращаться
# в pay-flow (ветки кода исторически проверяют `mode != "commission"`).
RESPOND_MODE = _get("PROFI_RESPOND_MODE", "pay").strip().lower()
if RESPOND_MODE not in {"pay", "commission"}:
    raise RuntimeError(
        f"невалидный PROFI_RESPOND_MODE={RESPOND_MODE!r}; разрешены только 'pay' или 'commission'"
    )


# Рабочие часы автопилота (часы локального времени, платные отправки только
# внутри интервала). Дефолт — норма по RULES.md: 8–23. Через .env/окружение
# задаётся как "начало,конец": PROFI_WORK_HOURS=8,23 или 0,24 (24/7).
def _parse_work_hours(v: str | None) -> tuple[int, int]:
    if not v or "," not in v:
        return (8, 23)
    lo, _, hi = v.partition(",")
    try:
        return (max(0, int(lo.strip())), min(24, int(hi.strip())))
    except ValueError:
        return (8, 23)


WORK_HOURS = _parse_work_hours(_get("PROFI_WORK_HOURS"))

LOG_LEVEL = _get("PROFI_LOG_LEVEL", "INFO")

# --- Чаты в цикле воркера ---
# Каждый N-й цикл воркера (90–120 с) сам проверяет чаты: ≈ раз в 4.5–6 мин.
# run_chat_auto держит все гейты (autopilot.lock, ≤2 ответов за запуск,
# ≥30 мин на диалог, анти-инъекция). Launchd com.profi.chats остаётся
# запасным диспетчером на случай, когда воркер не запущен.
CHAT_CHECK_EVERY_CYCLES = int(_get("PROFI_CHAT_EVERY", "3"))

# --- Кандидаты и детали (спека §16, §19-22) ---
# v0.5: кандидат создаётся по PASS hard-фильтров, LLM-триаж подключается на M3
AUTO_CREATE_CANDIDATES = True
AUTO_LOAD_DETAILS = True
