# repetit-auto — autonomous Repetit.ru tutoring worker

The worker has two runtime contours:

```text
A. new orders
feed -> hard filters -> LLM or safe fallback -> first chat message

B. existing worker-owned chats
SQLite candidates -> strict chat-state inspection -> LLM reply -> UI Send
```

It uses the real Chrome session over CDP. Network responses are read passively;
actions are performed through the page UI.

## Setup

Requires Python 3.11+ and Chrome.

```bash
uv sync
cp .env.example .env
# set ZAI_API_KEY

uv run repetit llm-check
uv run repetit once --dry-run
uv run repetit status
uv run repetit run
```

Default CDP port is `9335`. If Chrome is absent and
`REPETIT_CHROME_NO_LAUNCH != 1`, `BrowserManager` starts it with the dedicated
profile under `data/chrome-profiles/main`. Login itself remains manual.

## Contour A: new orders

1. The worker keeps its own `/lk/teacher/neworders#repetit-worker` tab.
2. `FeedCapture` passively captures `searchOrders` and the order-detail batch.
3. Order cards are not opened for triage, avoiding the platform `viewed` side effect.
4. `hard_filter()` runs before any LLM/fallback decision.
5. When LLM works, normal `respond/skip` semantics remain unchanged.
6. When LLM/network is unavailable, in cooldown, returns malformed JSON, or returns
   an unusable reply, a deterministic prepared fallback may be used.
7. A normal LLM `skip` never becomes fallback.
8. LLM and fallback text both pass length/contact post-checks.
9. Before first Send the worker independently confirms there is no existing chat history.
10. `sent` requires both the message appearing in DOM and an empty composer.

### Fallback

Fallback is enabled by default:

```dotenv
REPETIT_FALLBACK_ENABLED=1
```

Built-in templates are safe generic informatics/programming messages. Optional
custom templates can be supplied with `||` as a separator:

```dotenv
REPETIT_FALLBACK_TEMPLATES=Здравствуйте! ...||Добрый день! ...
```

Selection is deterministic by `order_id`: retries and restarts do not randomly
change copy for the same order. `responses.source` records `llm|fallback|rules`.

The important behavior change is that an LLM outage no longer freezes acquisition:
new suitable orders can still receive a prepared first message.

## Contour B: existing-chat auto replies

Chat auto-reply is intentionally **off by default** until the current Repetit sender
schema has been confirmed with a live dry-run:

```dotenv
REPETIT_CHAT_AUTO=0
```

Safe canary:

```bash
uv run repetit chats-once --dry-run
```

Only chats whose first message was sent by this worker (`sent` or `unknown`) are
eligible. Rows recorded as `already` are excluded because they may be manual
conversations owned by the user.

For each candidate the worker:

1. opens that order's chat in its own temporary tab;
2. accepts chat-state only from exact HTTPS Repetit origins/paths;
3. requires a stable message id and an explicit client sender role;
4. treats `isOutgoing=false` alone as insufficient proof;
5. deduplicates by `(order_id, incoming_key)` in SQLite;
6. escalates price/payment/discount/contact/complaint/guarantee/onsite and unsupported
   subject cases to `needs_human` before LLM;
7. asks LLM for a short context-aware answer for ordinary messages;
8. re-opens/re-reads the chat before Send and requires the exact same incoming message
   to still be last;
9. refuses to type over a non-empty composer, preserving manual drafts;
10. confirms Send by both reply text in DOM and an empty composer.

If the client sends a new message between inspect and Send, or the user answers
manually, the prepared reply becomes `stale` and is not sent.

There is deliberately **no generic fallback inside an ongoing conversation**.
Contextual replies wait for LLM; only acquisition falls back to prepared copy.

After a successful dry-run confirms the live sender shape, enable the background path:

```dotenv
REPETIT_CHAT_AUTO=1
REPETIT_CHAT_EVERY_CYCLES=3
REPETIT_CHAT_MAX_PER_CYCLE=2
REPETIT_CHAT_SCAN_LIMIT=6
REPETIT_CHAT_MAX_ORDER_AGE_DAYS=14
```

## Safety semantics

First-message statuses:

- `sent` — DOM confirmed;
- `already` — history already exists; no new first message;
- `unknown` — Send was clicked but full confirmation was not obtained; never retry;
- pre-Send `retry/auth_required` — saved draft remains retryable.

Chat-reply statuses:

- `sent` / `already_sent` — terminal success;
- `unknown` — possible Send, terminal, never retry;
- `stale` — incoming message changed/manual reply happened, terminal for the old key;
- `needs_human` — human takeover for that incoming message;
- `retry` — pre-Send technical failure;
- `dry_run` — generated but not sent; may be reused if the same incoming message is still last.

Other invariants:

- client/order text is untrusted data, never prompt instructions;
- generated output cannot contain phones, email, URLs or messenger handles;
- the worker never automates contact exchange, payments, login or order rejection;
- `run`, `once` and `chats-once` share the same per-instance worker lock;
- malformed work-hour config falls back to `8,23`; 24/7 requires explicit `0,24`.

## SQLite

`data/repetit.db` contains:

- `feed_seen` — observed order ids;
- `responses` — first-outreach audit, including `source` and `chat_title`;
- `chat_checks` — scan rotation / last inspection error;
- `chat_replies` — durable per-incoming-message idempotency and reply status.

Existing databases are migrated in-place to add the new response columns. The DB
must not be deleted as a migration shortcut because it is part of duplicate protection.

## CLI

```text
repetit run [--dry-run]
repetit once [--dry-run]
repetit chats-once [--dry-run]
repetit llm-check
repetit status
```

`run --dry-run` also keeps chat-auto non-sending if chat auto is enabled.

## Tests / CI

```bash
uv run ruff check .
uv run pytest -q
```

Tests do not require live Repetit, Chrome or an LLM key. Browser/network behavior is
covered with parsers, fake objects and monkeypatches; live side effects remain outside CI.

After changes to chat/browser contracts, use a controlled live canary before enabling
unattended sending.

## Project docs

- `docs/RECON.md` — observed platform facts;
- `docs/SPEC.md` — runtime contract;
- `AGENTS.md` — safety rules for future changes;
- `docs/reference/HUMAN_STYLE.md` — copy style reference.
