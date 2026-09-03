# RECON.md — разведка repetit.ru (ЛК репетитора), 2026-09-03

Всё проверено живьем на реальном аккаунте (Chrome CDP :9335, профиль
`data/chrome-profiles/main`). Скрипты разведки: `scripts/diag/02..07`.
Сырые дампы: `logs/recon/`.

## 1. Платформа и стек сайта

- ЛК `/lk/*` — SPA на **React Native Web** (классы `r-*`, emotion `css-*`).
- У всех значимых элементов есть **`data-testid`** — главный якорь локаторов.
- Данные ходят JSON XHR: `POST/GET /lk/api/*`, `GET/POST /api/*`.
- **Отправка сообщений чата идёт через WebSocket** (XHR-POST нет) — но воркеру
  это безразлично: он вводит текст в UI (человеческий инпут), транспорт не важен.

## 2. Карта страниц и URL

| Что | URL |
|---|---|
| Главная ЛК | `/lk/teacher/home` |
| Лента заявок («Новые заявки») | `/lk/teacher/neworders` |
| Карточка заявки | `/lk/teacher/neworders/{order_id}` |
| Чат по заявке (форма отклика) | `/lk/teacher/chatforteacher?orderId={id}&chatTitle={encoded}` |
| Логин-стена (нет сессии) | редирект на `/lk/loginwithshortcode` |

Меню слева — JS-навигация (не `<a href>`); «Заявки» = клик по тексту «Заявки».

## 3. Лента заявок: сеть

Загрузка `/lk/teacher/neworders`:

1. `POST /lk/api/teacher/searchOrders` — тело: фильтр (areaId=1,
   searchingSubjects[{subjectId:10,...}], lessonPlace, min/maxPrice, ...).
   Ответ: **плоский массив ID заявок** по убыванию новизны (сотни ID).
2. `GET /lk/api/teacher/orders?ids=X&ids=Y...` — **батч-детали первых ~21**
   заявок (то, что видно на экране). Ответ ~108KB, полные карточки.
3. `GET /lk/api/teacher/orders/{id}` — деталь одной заявки (~5.5KB) при
   открытии карточки.
4. Инфра: `GET /lk/api/userInfo`, `/lk/api/teacher/profile/status`,
   `/lk/api/notification`, `/lk/api/messages/unread/count`, `/v1/exps/`.

### Схема детали заявки (`orders/{id}` → `result`)

Ключевое для триажа:
- `contactName` («Светлана»), `pupilCategory.name` («школьники 9 класса»)
- `purpose` — машина-читаемая сводка: «Разделы/Цели/До экзамена/Категория»
- `information` — свободный текст клиента (полный)
- `subject.name`, `subjectDivisions[]`, `subjectAdditions[]` (ОГЭ, python, ...)
- `minPrice`/`maxPrice`, `lessonDuration`, `plannedLessonNumber`
- `lessonPlace` (int; флаги lessonPlaceRemote/Pupil/Teacher)
- `area` (город/регион), `homeMetroName`, `clientAddressStr`
- `status`/`statusObject` (1 = «Не обработана»), `viewed` (bool!)
- `orderDate` ISO, `id`
- `lessonPlace` — int-enum: **4 = «онлайн»** (живой факт E2E: у онлайн-заявки
  lessonPlace=4, при этом булевы `lessonPlaceRemote/Pupil/Teacher` все False —
  флагам доверять нельзя, только int-значению; см. models/order.py `is_remote`)

**`viewed: true` ставится сервером при открытии карточки** (аналог
UpdateOrderViewingEvent в profi). Загрузка ленты/батча viewed не меняла.

### Кнопка «Фильтр заявок» и чекбоксы фильтра

На ленте снизу кнопка «Фильтр заявок»; фильтр задаёт тело searchOrders
(предмет/цена/место). Аккаунт настроен: предмет id=10 (информатика), area 1.

## 4. DOM ленты

Карточка: контейнер `data-testid="new-orders-list-item-container-{id}"`,
кликабельный `data-testid="new-orders-list-item-touchable"` (cursor:pointer,
onclick). Внутри testid-поля:
`order-list-item-info-subject-name`, `-subject-description`, `-date-and-id`
(`сегодня 19:32\n№ 3970286`), `-city-name`, `-price`, `lesson-place-info-remote`.

Пагинации/«показать ещё» нет — список подгружает детали батчами при скролле.

## 5. Флоу отклика (Контур А) — проверено живой отправкой ✅

1. Открыть карточку `/lk/teacher/neworders/{id}` (ставит viewed — ок после
   триажа; можно и сразу в чат, см. п.3 URL).
2. Кнопки карточки: **«Начать чат с клиентом»** (акцент) и «Отказаться от
   заявки».
3. Клик ведёт на `/lk/teacher/chatforteacher?orderId={id}&chatTitle=...`
   (прямой goto этого URL тоже работает — проверено).
4. Композер: `data-testid="message-composer-input"` (textarea) и
   `message-composer-send-button`. Ввод посимвольный + клик Send.
5. Сообщение уходит (WS), появляется в чате с таймстемпом; статус клиента
   «в сети». **Контакт при этом НЕ списывается.**
6. После первого сообщения платформа предлагает «Обменяться контактами» —
   это ОТДЕЛЬНОЕ платное действие (квота «Доступно контактов», сейчас 1).
   **В автопилот НЕ входит.**

### Запреты платформы (важно для textguard)

- «Пожалуйста, не передавайте контакты в тексте сообщений — это запрещено
  правилами сервиса и приведет к блокировке вашего аккаунта». Textguard:
  паттерны телефона/email/telegram/whatsapp/скайп и т.п.
- Первый ответ — кастомный честный текст под заявку (как RULES profi §2).

## 6. Детект «уже отвечал» / состояние чата

`GET /api/teacher/chats/order?orderId={id}` → `result`: chat id, status,
chatUsers, lastMessage, messages. Если чат есть — отклик уже сделан.
Для MVP достаточно своей SQLite (responded order_id), чат-API — для Контура Б.

## 7. Логин-стена / сессия

- Нет сессии: `/lk/*` → редирект `/lk/loginwithshortcode` (title «Вход в ЛК»).
- Детект готовности воркера: URL != loginwithshortcode и наличие маркеров
  («Выход», «Доступно контактов»).

## 8. Квоты и лимиты (наблюдения)

- «Доступно контактов: 1» — платная квота раскрытия контактов (не тратится
  откликом-сообщением).
- «Требования платформы: Выполнено 4 из 4» — анкета/верификация/карта/email
  (профиль статуса: `GET /lk/api/teacher/profile/status`).
- Rate-limit: не наблюдался; интервал цикла держим человеческий (90–120 с),
  как в profi RULES §3.

## 9. Решения по архитектуре воркера (из разведки)

- **Feed**: reload `/lk/teacher/neworders` + пассивный перехват
  `searchOrders` (ID) и `orders?ids=` (батч-детали) — окно capture как в profi.
- **Триаж**: по батч-деталям (новые ID минус seen). Ключевые поля: subject,
  purpose, information, price, place, pupilCategory.
- **Отклик**: goto `chatforteacher?orderId=...` → human-ввод в композер →
  Send. Дедуп — SQLite.
- **Гейты**: дневной лимит отправок; текст только через textguard (анти-контакт
  + честность); «Обменяться контактами» — никогда автоматически.
- **Логин-стена**: URL-детект → AUTH_REQUIRED, ждать человека.

## 10. Неясное / проверить позже

- Лимит длины первого сообщения (текст ~450 символов прошёл).
- Свежесть: сортировка searchOrders — по дате (первые = новые), но
  подтвердить формально.
- Скролл ленты для деталей глубже первых ~21 (MVP: хватает первых 21).
- Поведение при 0 новых заявок (пустой ответ) — обработать в коде.
