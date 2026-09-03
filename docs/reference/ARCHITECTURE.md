# ARCHITECTURE.md — полная Mermaid-диаграмма системы

Живой документ: обновлять вместе с кодом. Источник правды — код
(`src/profi/`) и `docs/SPEC.md`; эта схема — его наглядная проекция.

Легенда цветов (flowchart): 🟩 планировщики · 🟦 Chrome/CDP · 🟨 данные/БД ·
🟥 LLM · 🟪 платная отправка · ⬜ прочее. Все тайминги/лимиты — из
`src/profi/config.py`.

## 1. Общая картина: процессы и Chromium (CDP)

Один Chrome-профиль = один аккаунт = одна персона. Chrome живёт сам по себе
(в нём сессия Профи); все процессы подключаются к нему по CDP-порту.

```mermaid
flowchart TB
    subgraph SCHED["⏰ Планировщики (вне кода)"]
        CRON["VPS: cron */15<br/>rhythm_keeper.sh<br/>(человеческий ритм 01–14 МСК)"]
        ACRO["VPS: cron 18,48 * * * *<br/>profi-autopilot-accounts.sh<br/>по всем accounts/*.ready"]
        LLDD["Mac: launchd каждые 120 с<br/>com.profi.autopilot →<br/>scripts/autopilot_cron.sh"]
    end

    subgraph CHROME["🌐 Chrome (внешний процесс, живёт всегда)"]
        direction TB
        CDP["CDP-сервер 127.0.0.1:9333<br/>(--remote-debugging-port,<br/>--remote-allow-origins=*)"]
        TABF["вкладка ЛЕНТА<br/>n.php (заказы)"]
        TABO["вкладка ЗАКАЗ<br/>n.php?o=<id> (одна за раз)"]
        TABC["вкладка ЧАТЫ<br/>r.php (своя, закрывается)"]
        PROF["профиль Chrome<br/>data/browser-profiles/&lt;acc&gt;<br/>= куки/сессия аккаунта"]
    end

    subgraph W["⚙️ Воркер (main.py, nohup, цикл 90–120 с)"]
        WLOOP["run_loop → run_cycle"]
    end

    subgraph AP["🤖 Автопилот (main.py autopilot)"]
        APMAIN["run_autopilot"]
    end

    subgraph CH["💬 Chat-auto (main.py chat-auto)"]
        CHMAIN["run_chat_auto"]
    end

    CRON -->|"оживляет акки"| RUNACC["scripts/account/run_account.sh &lt;acc&gt;<br/>браузер+воркер если мертвы"]
    ACRO -->|"по .ready-флагу"| APMAIN
    LLDD --> APMAIN
    APMAIN -->|"перед отправкой: pkill воркера,<br/>потом перезапуск (сериализация!)"| WLOOP

    WLOOP <-->|"CDP: reload ленты, клики"| TABF
    WLOOP <-->|"CDP: клик карточки → popup"| TABO
    APMAIN <-->|"CDP: форма отклика"| TABO
    CHMAIN <-->|"CDP: чтение/ввод в чатах"| TABC
    WLOOP -.->|"connect_over_cdp"| CDP
    APMAIN -.-> CDP
    CHMAIN -.-> CDP
    RUNACC --> WLOOP
```

## 2. Контур A — воркер ленты (каждые 90–120 с)

Пассивный перехват: данные читаются из сетевых ответов страницы, а не из DOM.

```mermaid
flowchart TB
    A0["ensure_ready(): вкладка ленты жива?<br/>закрыли → ищем/открываем новую"] -->|"BROWSER_OFFLINE /<br/>AUTH_REQUIRED"| WAIT["ждём 10 с / 30 с<br/>(логин — руками владельца)"]
    A0 -->|READY| A1["FeedCapture.reload_and_capture()"]
    A1 --> A2{"случайная пауза<br/>90–120 с<br/>(анти-ритм)"}
    A2 --> A3["reload(n.php) + слушатель response<br/>(вешается ДО reload)"]
    A3 --> A4["Фильтр: POST /graphql,<br/>операция BoSearchBoardItems<br/>(имя из query-текста после #prfrtkn)"]
    A4 --> A5{"за окно 8 с<br/>пойман?"}
    A5 -->|нет| FE["FeedCaptureError →<br/>диагностика в logs/feed_diag/"]
    A5 -->|"401/403"| FA["FeedAuthError →<br/>ПАУЗА 30–60 мин (антибот)"]
    A5 -->|да| A6["валидация: HTTP 200,<br/>data.boSearchBoardItems.items = массив"]
    A6 --> A7{"canonical-ответ?<br/>(cursor == null)"}
    A7 -->|"несколько с разным<br/>контентом"| FAM["FEED_AMBIGUOUS —<br/>не угадываем, диагностика"]
    A7 -->|да| A8["normalize → FeedSnapshot<br/>(только type==SNIPPET;<br/>STORIES/DIVIDER — мимо)"]

    A8 --> A9["Дедуп по SQLite feed_seen:<br/>register_feed_seen(id, last_update)"]
    A9 --> A10{"NEW / UPDATED<br/>или UNCHANGED?"}
    A10 -->|UNCHANGED| A2
    A10 -->|"NEW/UPDATED"| A11["hard_filter (filters.py)"]

    A11 --> A12{"PASS?"}
    A12 -->|"SKIP: вакансия в тексте,<br/>предмет не совпал,<br/>только очно"| A13["log SKIP → на паузу"]
    A12 -->|PASS| A14["candidates: create_candidate<br/>details_status=pending"]
    A14 --> A15["open_candidate(): клик по карточке<br/>→ новая вкладка n.php?o=<id>"]
    A15 --> A16["перехват BoOrderScreen +<br/>DOM-текст карточки (aria)"]
    A16 --> A17["extract_full_order →<br/>FullOrder в candidates.details_json<br/>details_status=ready"]
    A17 --> A18["вкладка закрывается"]
    A18 --> A2
```

## 3. Контур A — автопилот (каждые 2 мин): триаж → платная отправка

```mermaid
flowchart TB
    B0["trigger: launchd/cron → main.py autopilot"] --> B1{"рабочие часы?<br/>config.WORK_HOURS<br/>(норма 8–23)"}
    B1 -->|нет| BX["молча выходим"]
    B1 -->|да| B2{"autopilot.lock<br/>свободен? (TTL 30 мин)"}
    B2 -->|занят| BX
    B2 -->|да| B3["кандидаты:<br/>details=ready AND send=not_sent<br/>AND draft=pending"]

    B3 --> B4["по каждому заказу — жёсткие гейты ДО LLM:"]
    B4 --> G1{"«ваканс» в details_json?<br/>(бейдж полной карточки,<br/>инцидент #92799459)"}
    G1 -->|да| SK["skipped + note"]
    G1 -->|нет| G2{"bid_price ><br/>MAX_RESPONSE_PRICE_RUB<br/>(500 ₽)?"}
    G2 -->|да| SK
    G2 -->|нет| G3{"competition_position<br/>> 20?"}
    G3 -->|да| SK
    G3 -->|нет| G4{"has_bid?<br/>(уже есть отклик)"}
    G4 -->|да| SK
    G4 -->|нет| B5

    B5["user_prompt = _llm_order_payload(d)<br/>(компактный слепок 12 полей,<br/>без price_hash и raw-мусора)"] --> B6["+ _recipient_hint(d)<br/>(«КОМУ ПИШЕМ»: родитель/<br/>ученик/неясно)"]
    B6 --> B7["LLM: TRIAGE_SYSTEM + _style_variation()<br/>(GLM-5.3-Flash → GLM-5.3;<br/>модель видит персона + стиль + адресат)"]

    B7 --> B8{"verdict из JSON<br/>(llm.json_reply)"}
    B8 -->|"skip / JSON не спасся<br/>после цепочки попыток"| SK2["skipped / draft=error"]
    B8 -->|send| B9{"постчек текста:<br/>has_contacts()?<br/>(анти-инъекция: ссылки,<br/>телефоны, t.me)"}
    B9 -->|да| SK3["skipped: INJECTION_GUARD"]
    B9 -->|нет| B10{"длина 100–500 симв.<br/>(режем по границе предложения)"}
    B10 -->|нет| SK
    B10 -->|да| B11

    B11["_worker_running()?<br/>pkill profi.main<br/>(сериализация с монитором)"] --> B12["run_respond(order_id, RATE, text, send=True)"]
    linkStyle 33 stroke:#c00,stroke-width:2px

    subgraph RESP["run_respond — платная отправка"]
        direction TB
        R1["open_respond_form:<br/>гейт «Вам подходит?» → «Да»<br/>→ тарифы → «Продолжить»<br/>или CTA «Написать клиенту»"] --> R2["fill_form:<br/>ставка тройным кликом (clear!)<br/>+ input_value == rate? –– иначе RespondError<br/>текст посимвольно 3–9 симв., паузы<br/>человеческий рандом (RULES §1)"]
        R2 --> R3["read_footer: «К оплате» N ₽"]
        R3 --> R4{"гейты денег:<br/>to_pay ≤ 500 ₽?<br/>sends_today &lt; 3 ?<br/>(sent+unknown считаются,<br/>fail/skipped — нет)"}
        R4 -->|нет| RC["отмена: вкладка закрывается,<br/>деньги не тратятся"]
        R4 -->|да| R5["click_send() →<br/>ждём редирект r.php?id=<order>"]
        R5 --> R6{"исход"}
        R6 -->|"redirect на чат"| RS["send_status=sent ✓"]
        R6 -->|"«Произошла ошибка»<br/>на странице (rpc 400)"| RF["send_status=fail<br/>(инцидент #92799459:)<br/>деньги НЕ списаны"]
        R6 -->|"не поняли исход"| RU["send_status=unknown<br/>(списание могло пройти,<br/>лимит дня съеден — честно)"]
    end

    B12 --> RESP
    RESP --> B13["_start_worker()<br/>(nohup uv run python -m profi.main)"]
    B13 --> B14["stats в autopilot.log:<br/>note + модель + цена + позиция"]
```

## 4. Контур B light — автоответы в чатах (ручной запуск)

```mermaid
flowchart TB
    C0["main.py chat-auto (руками;<br/>в планировщики НЕ вшит)"] --> C1{"autopilot.lock свободен?<br/>(общий с автопилотом)"}
    C1 -->|занят| CX["выходим"]
    C1 -->|да| C2["_chat_page(): своё CDP-подключение,<br/>своя вкладка r.php —<br/>лента воркера не трогается"]
    C2 --> C3["list_dialogs() по aria_snapshot:<br/>строка имени после абзаца-аватара;<br/>unread из цифры в строке"]
    C3 --> C4["цели: unread &gt; 0, максимум 2"]
    C4 --> C5["по каждому: open_dialog (order_id из URL),<br/>текст диалога (последние 4000 симв.)"]
    C5 --> C6{"наш ответ в chat_log<br/>моложе 30 мин?"}
    C6 -->|да| C7["пропуск (анти-спам)"]
    C6 -->|нет| C8["LLM: CHAT_SYSTEM + _style_variation()<br/>+ время + диалог → JSON {reply,<br/>needs_human, note}"]
    C8 --> C9{"needs_human?<br/>(торг, гарантии, жалобы,<br/>не-учёба)"}
    C9 -->|да| C10["chat_log: NEEDS_HUMAN —<br/>эскалация владельцу,<br/>ответ НЕ отправляется"]
    C9 -->|нет| C11{"постчеки: ≥10 симв.,<br/>has_contacts()?,<br/>≤800 (режем по предложению)"}
    C11 -->|нет| C12["скип"]
    C11 -->|да| C13["send_reply(): ввод посимвольно<br/>3–9 симв. + Enter; если осталось —<br/>кнопка «Отправить»"]
    C13 --> C14["chat_log(tutor) + скриншот<br/>logs/chats/auto_*.png"]
```

## 5. Данные и статусы (SQLite, data/&lt;acc&gt;.db)

```mermaid
flowchart LR
    subgraph DB["🟨 SQLite (одна на аккаунт)"]
        FS[("feed_seen<br/>order_id, last_update<br/>— дедуп ленты")]
        CAND[("candidates<br/>жизненный цикл заказа")]
        CHL[("chat_log<br/>история чатов")]
        VR[("v_responses<br/>вьюшка статистики")]
    end

    FS -->|"NEW → кандидат"| CAND
    CAND --> VR
    CHL -->|"last_chat_sent_at<br/>(лимит 30 мин)"| CHL

    subgraph LC["Жизненный цикл кандидата"]
        direction LR
        s1["details:<br/>pending → ready / error"] --> s2["draft:<br/>pending → generated / error / stale"]
        s2 --> s3["send:<br/>not_sent → sent | unknown | fail | skipped"]
    end
    CAND -.-> LC
```

## 6. Анти-детект и человечность (RULES.md)

```mermaid
flowchart TB
    subgraph HUMAN["🧠 «Человеческий слой» — на чём стоит система"]
        P1["паузы 90–120 с между циклами<br/>+ cooldown 30–60 мин на 401/403"]
        P2["клики: locator.click() =<br/>trusted CDP Input-события<br/>(isTrusted=true),<br/>НИКАКИХ page.evaluate"]
        P3["ввод: посимвольно чанками<br/>3–9 симв., случайные задержки;<br/>тройной клик = выделить всё"]
        P4["стиль текстов: _HUMAN_STYLE +<br/>_style_variation() — улыбка ~40%,<br/>дефис-тире ~50% (рандом от кода,<br/>иначе улыбка в каждом = шаблон)"]
        P5["анти-инъекция: текст клиента =<br/>ДАННЫЕ; has_contacts() постчек;<br/>needs_human на торг/жалобы"]
        P6["рабочие часы 8–23<br/>(ночные тесты — вручную)<br/>+ лимит 3 платных/день, ≤500 ₽"]
    end
```

## 7. Стилевая схема кода (src/profi/)

```mermaid
flowchart LR
    MAIN["main.py<br/>CLI + оркестрация"] --> CFG["config.py"]
    MAIN --> BR["browser/manager.py<br/>CDP-коннект, вкладка ленты"]
    MAIN --> INT["integration/"]
    INT --> F["feed.py<br/>BoSearchBoardItems"]
    INT --> O["orders.py<br/>BoOrderScreen → FullOrder"]
    INT --> RSP["respond.py<br/>форма отклика"]
    INT --> CHT["chat.py<br/>чаты"]
    MAIN --> LLM["llm/client.py<br/>glm/openai/anthropic"]
    MAIN --> ST["storage/store.py<br/>SQLite"]
    MAIN --> FIL["filters.py<br/>hard-фильтры"]
    MAIN --> UT["utils/<br/>pacing, textguard"]
    BR & INT --> PW["Playwright<br/>sync API"]
    F & O --> MDL["models/<br/>OrderSnippet,<br/>FeedSnapshot, FilterVerdict"]
    FIL --> MDL
    ST --> MDL
```

## 8. Известные инциденты, зашитые в код

| Дата | Инцидент | Защита теперь |
|---|---|---|
| 01.09 | GLM обрезал JSON по max_tokens | цепочка моделей с наращиванием токенов |
| 01.09 | PATH в launchd → exit 127 | экспорт PATH в autopilot_cron.sh |
| 02.09 23:00 | #92799459: бейдж «вакансия» не в сниппете + сайт подставил 2000 → «20002000», rpc 400 | гейт вакансии по details_json; тройной клик + сверка input_value; статус fail по маркеру ошибки |
| 02.09 | price_hash съедал лимит промпта | _llm_order_payload — компактный слепок |
| 02.09 | вопрос школьнику вместо родителя | _recipient_hint («КОМУ ПИШЕМ») |
