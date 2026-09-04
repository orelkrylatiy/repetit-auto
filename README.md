# repetit-auto — автономный воркер откликов repetit.ru

Контур A автоматизирует первый отклик на новые заявки:

`лента → hard-фильтры → LLM-триаж → честный кастомный текст → первое сообщение в чат`

Архитектура унаследована от `profi-agent`, но интеграция и safety-гейты адаптированы под repetit.ru. Воркер работает с реальным Chrome через CDP, читает сетевые ответы страницы пассивно и выполняет действия только через UI.

Документы:

- `docs/RECON.md` — подтверждённые факты о repetit.ru: URL, API, DOM и ограничения;
- `docs/SPEC.md` — текущее поведение кода и инварианты Контур A;
- `AGENTS.md` — короткие правила для дальнейшей разработки агентом.

## Быстрый запуск

Требуется Python 3.11+ и Chrome.

```bash
cd ~/repetit-auto
uv sync
cp .env.example .env
# заполнить ZAI_API_KEY

uv run repetit llm-check
uv run repetit once --dry-run
uv run repetit status
uv run repetit run
```

По умолчанию воркер подключается к CDP `127.0.0.1:9335`. Если Chrome на этом порту не запущен, воркер сам запускает Chrome с профилем `data/chrome-profiles/main`. Автозапуск можно отключить через `REPETIT_CHROME_NO_LAUNCH=1`.

Для ручного запуска Chrome на macOS:

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --user-data-dir="$PWD/data/chrome-profiles/main" \
  --remote-debugging-port=9335 \
  --remote-allow-origins='*' \
  --no-first-run --no-default-browser-check about:blank
```

Если repetit.ru переводит на `/lk/loginwithshortcode`, войти нужно руками в этом Chrome. Воркер сам логин и shortcode не автоматизирует.

## Основные настройки

Смотри `.env.example`. Ключевые значения:

```dotenv
ZAI_API_KEY=...
GLM_BASE_URL=https://api.z.ai/api/coding/paas/v4
LLM_MODEL=GLM-5.3-Flash

REPETIT_DAILY_SEND_LIMIT=3
REPETIT_MAX_PER_CYCLE=3
REPETIT_WORK_HOURS=8,23
REPETIT_MIN_CLIENT_RATE=0
REPETIT_SUBJECTS=информатик,программирован
```

`REPETIT_WORK_HOURS` — полуинтервал `[start,end)` в локальном времени. `8,23` означает 08:00–22:59. Круглосуточный режим включается только явно через `0,24`; некорректное значение откатывается к безопасному `8,23`.

## Как работает цикл

1. `BrowserManager` держит отдельную feed-вкладку воркера `/lk/teacher/neworders#repetit-worker`. Fragment нужен только для ownership: обычные вкладки владельца воркер не присваивает и не закрывает.
2. После reload `FeedCapture` пассивно ловит `POST /lk/api/teacher/searchOrders` и `GET /lk/api/teacher/orders?ids=...` только с домена repetit.ru.
3. По батч-деталям строятся `Order`; карточки заявок для триажа не открываются, поэтому `viewed` не ставится.
4. `hard_filter()` дешёво отсеивает неподходящий предмет, особые потребности, бартер и слишком низкий известный бюджет.
5. LLM решает `respond/skip` и генерирует текст. Клиентские поля считаются недоверенными данными, а не инструкциями. После генерации обязательны длина и `textguard`.
6. Перед Send воркер открывает отдельную chat-вкладку и ждёт успешный `GET /api/teacher/chats/order`. Если история уже есть или состояние чата не подтверждено, новое первое сообщение не отправляется.
7. Текст вводится человеческим UI-input. `sent` подтверждается только двумя признаками одновременно: наш текст появился в чате и composer очистился.
8. Chat-вкладка, созданная `Responder`, закрывается. Постоянная feed-вкладка и ручные вкладки владельца остаются.

## Safety semantics

- `sent` — отправка подтверждена DOM;
- `already` — в чате уже есть история; повтор запрещён, дневной лимит не расходуется;
- `unknown` — Send был нажат, но полного подтверждения нет; повтор запрещён и дневной лимит расходуется;
- `retry`/`auth_required` до Send — draft сохраняется как `not_sent`, повтор возможен позже;
- `run` и `once` используют один `data/worker.lock`, поэтому два отправляющих процесса параллельно не стартуют;
- «Обменяться контактами», оплата, фильтр заявок, отказ от заявки и логин не автоматизируются;
- ссылки, телефоны, email и мессенджеры в сообщении блокируются `textguard`;
- `purpose/information` и остальные поля клиента не могут переопределять system prompt.

## Данные и наблюдаемость

- `data/repetit.db` — `feed_seen` и `responses`;
- `logs/worker.log` — основной журнал;
- `logs/respond/*.png` — диагностические скриншоты вокруг отправки;
- `logs/` и `data/` исключены из git.

`status` показывает количество увиденных заявок, подтверждённых отправок, решения и последние строки аудита.

## Тесты и CI

Локально:

```bash
uv run ruff check .
uv run pytest -q
```

GitHub Actions выполняет те же проверки на каждом PR и push в `main`. Unit-тесты не требуют живого repetit.ru, Chrome или LLM API: network/browser контракты моделируются локальными fake-объектами и monkeypatch.

Живой canary после значимых изменений браузерного флоу всё равно нужен отдельно: сначала `once --dry-run`, затем одна контролируемая отправка на подходящей заявке и проверка, что повторный запуск не создаёт дубль.

## Диагностические скрипты

`scripts/diag/` содержит только безопасные наблюдательные пробы:

- `01_open_lk.py` — CDP + состояние сессии;
- `02_nav_map.py` — карта навигации и сетевых запросов home;
- `03_feed_explore.py` — пассивный capture feed.

Все три создают собственную временную вкладку и не трогают открытые владельцем. `03` может сохранять сырые данные заявок в `logs/recon/`; каталог gitignored.

Исторические пробы открытия карточки, формы и реальной отправки удалены после фиксации результатов в `RECON.md`, чтобы их нельзя было случайно запустить с side effect.

## Структура

```text
src/repetit/
  main.py                 CLI и цикл
  config.py               env и safety defaults
  browser/manager.py      Chrome/CDP, ownership feed-вкладки
  integration/feed.py     пассивный capture ленты
  integration/triage.py   LLM-триаж и постгейты текста
  integration/respond.py  первое сообщение и подтверждение Send
  filters.py              hard-фильтры
  storage/store.py        SQLite
  llm/client.py           glm/openai/anthropic transport
  models/                 Order, FilterVerdict
  utils/                  pacing, textguard, workhours
personas/maxim.md
scripts/diag/              безопасная диагностика 01–03
tests/                     regression/unit tests
```

## Не входит в текущий MVP

- Контур Б: ответы на входящие сообщения клиента;
- обработка деталей глубже первого feed-batch (~21 заявка) через скролл;
- мультиаккаунтность;
- автоматические действия с платными контактами;
- автоматический логин;
- полноценный browser E2E в CI.
