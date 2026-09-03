# AGENTS.md — repetit-agent (Контур А: автоотклики Репетитор.ру)

Автономный воркер откликов: лента «Новые заявки» → hard-фильтры →
LLM-триаж (GLM-5.3-Flash, coding-эндпоинт z.ai) → кастомный честный текст →
первое сообщение в чат заявки. Форк архитектуры profi-agent (~/profi).

Документы: разведка платформы `docs/RECON.md` (источник истины по URL/API/
testid), спека `docs/SPEC.md`. Референсы profi — `docs/reference/`.

## Обязательные правила (наследие profi RULES, действуют здесь)

1. **Человечность**: действия только через UI — настоящие клики,
   посимвольный ввод, паузы. Никаких `page.evaluate` для действий
   (чтение DOM/network — пассивно — разрешено).
2. **Честность текстов**: не выдумывать опыт/достижения. Нет опыта под
   заявку — скип или упор на смежное.
3. **Контакты в текстах запрещены** платформой (блокировка аккаунта):
   каждый текст через `textguard.has_contacts()`.
4. **«Обменяться контактами» — никогда автоматически** (платная квота).
   Автопополнение запрещено.
5. Гейты: дневной лимит отправок, лимит на цикл, рабочие часы.

## Запуск (macOS)

```bash
cd ~/repetit-agent
# Chrome с CDP (один раз; сессия живёт в профиле):
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --user-data-dir="$HOME/repetit-agent/data/chrome-profiles/main" \
  --remote-debugging-port=9335 --remote-allow-origins='*' \
  --no-first-run --no-default-browser-check about:blank

.venv/bin/python -m repetit run            # цикл 90–120 с
.venv/bin/python -m repetit once --dry-run # прогон без отправок
.venv/bin/python -m repetit status         # сводка
```

Не запущен Chrome → воркер поднимет его сам (профиль data/chrome-profiles/main,
CDP :9335). Нет сессии → AUTH_REQUIRED, ждёт ручного логина в этом Chrome.

## Ключевые env (.env; префикс REPETIT_)

- `ZAI_API_KEY`, `GLM_BASE_URL=https://api.z.ai/api/coding/paas/v4`,
  `LLM_MODEL=GLM-5.3-Flash` (обычный paas-эндпоинт отдаёт 1113 «no balance» —
  flash живёт на coding-тарифе)
- `REPETIT_DAILY_SEND_LIMIT` (дефолт 3), `REPETIT_MAX_PER_CYCLE` (3),
  `REPETIT_WORK_HOURS` (0,24), `REPETIT_CDP_PORT` (9335)

## Структура

```
src/repetit/           — пакет: main (CLI/цикл), config, filters,
                         browser/manager (CDP), integration/ (feed|triage|respond),
                         storage/store (SQLite), llm/client, utils/, models/
personas/maxim.md      — персона репетитора (только правда, без выдумок)
scripts/diag/          — пробники разведки 02–07 (read-only + живой тест)
data/repetit.db        — feed_seen + responses (аудит откликов)
logs/worker.log        — основной лог; logs/respond/*.png — скриншоты отправок
```

## Флоу отклика (проверено живыми отправками)

лента neworders (reload + перехват `searchOrders` + `orders?ids=`) →
новые ID → фильтры → LLM → `chatforteacher?orderId=…` → ввод в
`message-composer-input` → Send → подтверждение по DOM (текст в чате).

## Известные грабли

- Отправка сообщений идёт по WebSocket — XHR-признака успеха нет,
  подтверждение только по DOM.
- Открытие карточки заявки ставит серверный `viewed` — триаж только по
  батчу ленты.
- `uv pip install -e .` требует сети; offline: `PYTHONPATH=src .venv/bin/python -m repetit …`.
- Дневной лимит считается по `sent_at >= полуночи` (sent+already).

## Бэклог

- Контур Б: ответы в чатах (`GET /api/chats/*`).
- Глубина ленты > 21 (скролл), ретраи LLM, алерты.
