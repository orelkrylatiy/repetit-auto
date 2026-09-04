# SPEC.md — repetit-auto, Контур A

Статус: **текущая спецификация реализованного MVP**.

- Факты площадки, подтверждённые живой разведкой: `RECON.md`.
- Правила для дальнейшей разработки: `../AGENTS.md`.
- Исторические материалы `profi-agent`: `reference/` и не являются контрактом этого проекта.

Если этот документ расходится с кодом, это дефект документации или реализации,
который нужно исправить. Здесь не описываются «будущие» сущности, которых в коде нет.

## 1. Назначение

Контур A автоматически обрабатывает первый экран новых заявок repetit.ru:

```text
feed reload
  → пассивный network capture
  → Order
  → hard filters
  → LLM triage
  → post-check текста
  → проверка существующего чата
  → human UI input
  → Send
  → DOM confirmation
  → SQLite audit
```

Отклик = **первое сообщение в чат заявки**.

В текущий MVP не входят:

- ответы клиентам после первого сообщения (Контур Б);
- глубокий скролл feed дальше первого batch деталей;
- мультиаккаунтность;
- автоматический логин;
- «Обменяться контактами»;
- оплата, изменение фильтра заявок, отказ от заявки;
- browser E2E в CI.

## 2. Процесс и Chrome

Единственный runtime-процесс — `python -m repetit run`.

Chrome используется через CDP, по умолчанию `127.0.0.1:9335`. Если Chrome не
найден и `REPETIT_CHROME_NO_LAUNCH != 1`, `BrowserManager` запускает его с
профилем `data/chrome-profiles/main`. Сессия repetit.ru живёт в этом профиле.

Автологина нет. Редирект на `/lk/loginwithshortcode` означает `AUTH_REQUIRED`.

### Ownership вкладок

Воркер не должен присваивать или закрывать вкладки владельца.

Постоянная feed-вкладка воркера имеет URL:

```text
https://repetit.ru/lk/teacher/neworders#repetit-worker
```

Fragment не отправляется HTTP-серверу и нужен только для ownership после CDP
reconnect. Обычная ручная `/neworders` без marker не считается вкладкой воркера.

`Responder` создаёт новую chat-вкладку на одну заявку и в `finally` закрывает
только её.

## 3. Состояния BrowserManager

Поддерживаются только три публичных состояния:

| Состояние | Значение |
|---|---|
| `READY` | CDP доступен, рабочая page находится на feed URL |
| `AUTH_REQUIRED` | рабочая page находится на login URL |
| `BROWSER_OFFLINE` | Chrome/CDP/page недоступны или page не удалось восстановить |

`ensure_ready()` при необходимости восстанавливает собственную feed-вкладку.
Произвольный URL не считается `READY`.

## 4. Рабочие часы и singleton

До любого обращения к браузеру `run_cycle()` проверяет `in_work_hours()`.
Дефолт: `8,23`, то есть `[08:00, 23:00)` локального времени.

- `0,24` — явный 24/7 режим;
- некорректный `REPETIT_WORK_HOURS` fail-safe возвращается к `8,23`;
- ночью feed не reload'ится и chat-вкладки не открываются.

`run` и `once` используют один `data/worker.lock`. Два процесса, способных
отправлять сообщения через один Chrome/SQLite, параллельно не работают.

## 5. Feed capture

Основная страница: `/lk/teacher/neworders`.

`FeedCapture` ставит response listener **до** `page.reload()` и принимает только
HTTPS-ответы host `repetit.ru` или его subdomain с точным method/path:

- `POST /lk/api/teacher/searchOrders` → список ID;
- `GET /lk/api/teacher/orders?ids=...` → batch деталей.

Runtime не вызывает API repetit.ru напрямую.

### Правила capture

- пустой `searchOrders=[]` — нормальный пустой feed;
- непустой список ID без batch деталей — `FeedError`, цикл пропускается;
- ID нормализуются в `int`; нечисловой ID — `FeedError`;
- несколько одинаковых `searchOrders` допустимы;
- несколько разных `searchOrders` в одном capture window → `FEED_AMBIGUOUS`, ничего не угадываем;
- несколько batch-ответов объединяются по `order.id`;
- порядок `orders` следует порядку ID из `searchOrders` для тех ID, по которым есть детали;
- `401/403` → auth/fail-closed;
- `429` → feed cooldown 30 минут;
- `5xx` → feed cooldown 10 минут.

Открытие карточки `/neworders/{id}` ставит серверный `viewed`, поэтому runtime
не открывает карточку для триажа. Используются batch-details.

## 6. Order

`models/order.py` хранит только нужную проекцию API:

- id, subject/subject_id;
- purpose, information;
- min/max price;
- contact name;
- city/metro;
- lesson place;
- pupil category;
- date;
- subject additions/divisions;
- raw payload для диагностики.

`lessonPlace == 4` трактуется как online согласно живой разведке.

В LLM передаётся `triage_dict()`, а не весь `raw`.

## 7. Hard filters

`hard_filter()` выполняется до LLM. Философия: при отсутствии данных лучше
передать заявку в LLM, чем сделать ложный SKIP.

Текущие фильтры:

1. `REPETIT_SUBJECTS`: должен совпасть хотя бы один keyword в `Order.searchable`;
2. special-needs patterns из `config.SPECIAL_NEEDS_PATTERNS` → skip;
3. barter/free patterns → skip;
4. если `REPETIT_MIN_CLIENT_RATE > 0` и известная верхняя граница бюджета ниже
   порога → skip;
5. неизвестный бюджет сам по себе не является причиной skip.

Результат: `FilterVerdict(passed, reason)`.

## 8. LLM triage

`integration/triage.py` отправляет system prompt + JSON `Order.triage_dict()`.

### Trust boundary

Все поля заявки (`purpose`, `information`, имя, subject и т. д.) — **недоверенные
данные**, не инструкции. Команды, prompt injection и JSON-инструкции внутри
текста клиента игнорируются.

### Контракт ответа

Ожидается JSON-object:

```json
{"decision":"respond|skip","reason":"...","text":"..."}
```

- API/network exception из `llm.chat()` → `decision=llm_error`; main ставит LLM cooldown 30 минут и оставляет кандидата для будущего цикла;
- malformed JSON → локальный `decision=error`, не глобальный cooldown;
- валидный JSON не-object (`[]`, строка и т. п.) → `error`;
- неизвестный `decision` → `error`;
- `skip` всегда возвращает пустой `text`;
- `reason` обрезается до 500 символов.

### Post-check respond текста

После LLM:

- длина `100..600` символов;
- `textguard.has_contacts(text) == False`;
- длинное тире `—` детерминированно заменяется на обычный `-`;
- лёгкая финальная `)` может добавляться кодом примерно в 40% сообщений;
- prompt требует 3–6 предложений, честность, отсутствие выдуманного опыта и естественный вопрос клиенту.

Любое нарушение длины/textguard → `decision=error`, Send невозможен.

## 9. Проверка чата до Send

URL строится `config.chat_url(order_id, chat_title)`.

Перед вводом текста `Responder` пассивно ждёт:

```text
GET /api/teacher/chats/order?orderId=...
```

Разрешение на ввод появляется только если:

- ответ действительно пойман;
- HTTP status == 200;
- JSON читается;
- payload — dict;
- `lastMessage` отсутствует;
- `messages` пустой/отсутствует.

Если история есть → `already`, новое сообщение не вводится.
Если состояние нельзя подтвердить → `retry`, Send не выполняется.

Это независимая защита от дубля при потере/несогласованности локальной БД.

## 10. UI input и Send

Композер:

```text
[data-testid="message-composer-input"]
```

Send:

```text
[data-testid="message-composer-send-button"]
```

Действия выполняются через Playwright/CDP UI:

- locator click;
- keyboard typing с задержками/чанками;
- паузы;
- финальная проверка `composer.input_value() == text`.

JS `element.click()`, присвоение `input.value` и `dispatchEvent` для действий
runtime не используются.

Перед Send сохраняется screenshot `*_filled_*` при возможности.

## 11. Статусы отправки

`Responder.send_first_message()` возвращает:

| status | Семантика | Можно повторить? | Дневной лимит |
|---|---|---:|---:|
| `sent` | Send был, текст появился в DOM и composer очистился | нет | +1 |
| `already` | история чата уже есть / текст уже найден до Send | нет | 0 |
| `unknown` | Send был нажат, но оба DOM-признака не подтверждены | нет | +1 |
| `retry` | до Send произошёл временный/неясный browser/UI сбой | да | 0 |
| `auth_required` | до Send произошёл редирект на login | да после ручного логина | 0 |

Ключевой fail-closed принцип:

- **до Send** неизвестность = можно повторить позже;
- **после Send** неизвестность = возможная отправка, повтор запрещён.

`sent` требует двух признаков одновременно:

1. фрагмент нашего текста присутствует в DOM чата;
2. composer пуст.

## 12. SQLite

Файл: `data/repetit.db`, WAL mode.

### `feed_seen`

- `order_id` PK;
- `first_seen_at`;
- `last_seen_at`.

Используется как журнал наблюдения feed.

### `responses`

Одна строка на `order_id`:

- subject/title;
- `decision`: `respond | skip | filtered | error`;
- reason;
- generated text;
- `status`: `not_sent | sent | already | unknown | error`;
- error;
- screenshot;
- created_at;
- sent_at.

`sent_at` выставляется только для `sent/unknown`. `already` не создаёт timestamp
отправки и не расходует дневной лимит.

Дневной лимит `sends_today()` считает `status IN ('sent','unknown')` с локальной
полуночи.

## 13. Lifecycle строки response

Новая заявка может пройти один из путей:

```text
hard filter fail       → filtered/not_sent (terminal)
LLM skip               → skip/not_sent     (terminal)
LLM malformed output   → error/not_sent    (terminal для этого order)
LLM API failure        → запись не терминализируется; общий cooldown
respond + dry-run      → respond/not_sent  (pending draft)
respond + gate fail    → respond/not_sent  (pending draft)
pre-Send retry/auth    → respond/not_sent  (pending draft)
send confirmed         → respond/sent      (terminal)
existing chat          → respond/already   (terminal)
post-Send uncertain    → respond/unknown   (terminal, no retry)
```

Для совместимости старый `respond/error` считается pending и может быть
восстановлен новым циклом.

## 14. Лимиты цикла

Перед LLM main проверяет дневной лимит, чтобы не тратить LLM-квоту после его
исчерпания.

Настройки:

- `REPETIT_DAILY_SEND_LIMIT`, default 3, `0` = unlimited;
- `REPETIT_MAX_PER_CYCLE`, default 3;
- пауза между фактическими/возможными отправками 20–45 с;
- цикл feed 90–120 с.

`unknown` считается возможной отправкой и расходует лимит.

## 15. Textguard

Блокируются:

- `http://`, `https://`, `www.`;
- email;
- telegram/телеграм, WhatsApp/ватсап, Skype, Viber, VK, Instagram, Discord и др.;
- телефоноподобный цифровой прогон с 10+ цифрами.

Короткие числа, цены и диапазоны годов не должны давать ложный phone match.

## 16. Команды CLI

Реально реализованы:

```bash
python -m repetit run [--dry-run]
python -m repetit once [--dry-run]
python -m repetit llm-check
python -m repetit status
```

Отдельной команды `respond` в текущем MVP нет.

`once --dry-run` проходит feed/filters/LLM и сохраняет подходящий draft, но не
открывает Send flow.

## 17. Логи и screenshots

- `logs/worker.log` — состояния и результаты цикла;
- `logs/respond/` — screenshots Responder;
- `logs/recon/` — только ручная диагностика;
- `data/` и `logs/` gitignored.

Сбой screenshot не должен менять итог Send state.

## 18. Диагностика

В `scripts/diag/` разрешены только наблюдательные скрипты без отправок и без
открытия карточек заявок:

- `01_open_lk.py` — CDP/session;
- `02_nav_map.py` — home navigation/network inventory;
- `03_feed_explore.py` — feed capture.

Каждый создаёт собственную временную вкладку и закрывает только её.

Исторические side-effect probes `04–07` удалены: их результаты перенесены в
`RECON.md`, а production Send должен проверяться только контролируемым canary
через реальный runtime flow.

## 19. Тестирование

CI выполняет:

```bash
uv run ruff check .
uv run pytest -q
```

Unit/regression tests должны покрывать минимум:

- URL/worker-tab ownership;
- work-hours fail-safe;
- Order mapping;
- hard filters;
- feed response matching и failure modes;
- chat-history detection;
- prompt trust boundary;
- malformed LLM output;
- style/textguard;
- singleton lock;
- SQLite statuses и дневной лимит.

Unit tests не требуют живого Chrome/repetit.ru/LLM API.

После изменений browser/network selectors автоматические тесты не заменяют
живой canary:

1. `once --dry-run`;
2. одна контролируемая подходящая заявка;
3. проверить `sent` + screenshot + SQLite;
4. повторный запуск не должен отправить второе сообщение.

## 20. Критерии готовности текущего MVP

Контур A считается готовым к unattended работе одного аккаунта, когда:

- CI зелёный;
- Chrome/profile стабильно доступны;
- dry-run текущего UI проходит;
- один canary Send подтверждён;
- повтор canary не создаёт дубль;
- дневной лимит и рабочие часы настроены;
- `logs/worker.log` периодически просматривается владельцем.
