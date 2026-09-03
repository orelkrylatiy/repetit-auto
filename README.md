# repetit-agent — автономный воркер откликов на repetit.ru (Контур А)

Форк архитектуры [profi-agent]. Лента «Новые заявки» → hard-фильтры →
LLM-триаж (GLM-5.3-Flash) → кастомный честный текст → первое сообщение
в чат заявки. Человеческий ввод через CDP, никаких JS-инъекций действий.

- Разведка платформы (сеть+DOM+флоу): `docs/RECON.md`
- Спецификация: `docs/SPEC.md`

## Запуск (macOS)

```bash
cd ~/repetit-agent
uv venv && uv pip install -e .       # или: python3 -m venv .venv && .venv/bin/pip install -e .

# 1. Chrome с профилем и CDP (сессия живёт в профиле; поднять один раз)
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --user-data-dir="$HOME/repetit-agent/data/chrome-profiles/main" \
  --remote-debugging-port=9335 --remote-allow-origins='*' \
  --no-first-run --no-default-browser-check about:blank
# Если редиректит на /lk/loginwithshortcode — залогинься руками один раз.

# 2. Проверка LLM
.venv/bin/python -m repetit llm-check

# 3. Воркер
.venv/bin/python -m repetit run              # цикл 90–120 с, постоянно
.venv/bin/python -m repetit once --dry-run   # один цикл без отправок
.venv/bin/python -m repetit status           # сводка по БД
```

Если Chrome не запущен, воркер сам поднимет его с нашим профилем
(отключить: `REPETIT_CHROME_NO_LAUNCH=1`).

## .env

```
ZAI_API_KEY=...                 # ключ z.ai
GLM_BASE_URL=https://api.z.ai/api/coding/paas/v4   # coding-эндпоинт
LLM_MODEL=GLM-5.3-Flash
# REPETIT_CDP_PORT=9335
# REPETIT_DAILY_SEND_LIMIT=3    # 0 = без лимита
# REPETIT_MAX_PER_CYCLE=3
# REPETIT_WORK_HOURS=0,24       # окно отправок, часы локального времени
# REPETIT_MIN_CLIENT_RATE=0     # мин. бюджет клиента ₽/60мин
# REPETIT_SUBJECTS=информатик,программирован
```

## Гейты безопасности

- дневной лимит отправок + лимит на цикл + рабочие часы;
- textguard: контакты/ссылки в тексте запрещены (блокировка аккаунта
  по правилам площадки) — постчек каждого текста;
- «Обменяться контактами» (платная квота) — никогда автоматически;
- тексты честные, без выдуманного опыта (промпт-правила + персона);
- открытие карточки заявки = серверный факт `viewed` — триаж идёт по
  батч-данным ленты, открывается только выбранное.

## Данные и логи

- `data/repetit.db` — feed_seen (дедуп), responses (аудит: вердикт,
  текст, статус, скриншот);
- `logs/worker.log` — основной лог;
- `logs/respond/*.png` — скриншот каждого отправленного отклика.

## Структура

```
src/repetit/
  config.py            — настройки + env (REPETIT_*)
  browser/manager.py   — Chrome over CDP :9335, health-check, логин-стена
  integration/feed.py  — пассивный перехват searchOrders + батча деталей
  integration/triage.py— LLM-триаж + генерация текста
  integration/respond.py— отправка первого сообщения (human input)
  filters.py           — hard-фильтры до LLM
  storage/store.py     — SQLite
  llm/client.py        — мульти-провайдерный LLM (glm/openai/anthropic)
  utils/               — pacing, textguard, workhours
personas/maxim.md      — персона репетитора
scripts/diag/          — пробники разведки (02–07)
```

## Бэклог

- Контур Б: ответы клиентам в чатах (GET /api/chats/*, WS-транспорт).
- Глубина ленты > первых ~21 заявки (скролл).
- Ретраи LLM-ошибок с бэкоффом; алерты при AUTH_REQUIRED.

[profi-agent]: ../profi
