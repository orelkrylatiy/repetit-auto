"""CLI and orchestration for Repetit feed outreach and guarded chat replies."""

from __future__ import annotations

import argparse
import logging
import random
import sys
import time

from repetit import config
from repetit.browser import manager as bm
from repetit.fallback import fallback_reply
from repetit.filters import hard_filter
from repetit.integration.chat import ChatResponder
from repetit.integration.chat_triage import generate_chat_reply
from repetit.integration.feed import FeedAuthError, FeedCapture, FeedError
from repetit.integration.respond import Responder
from repetit.integration.triage import triage
from repetit.storage.store import Store
from repetit.utils.pacing import human_pause
from repetit.utils.workhours import in_work_hours

log = logging.getLogger("repetit.worker")

_CHAT_TERMINAL = {"sent", "unknown", "already_sent", "needs_human", "error", "stale"}


def _setup_logging() -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
        format=fmt,
        handlers=[
            logging.FileHandler(config.WORKER_LOG, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )


def _chat_title(order) -> str:
    name = (order.contact_name or "").strip()
    return f"№ {order.id}, {name}" if name else f"№ {order.id}"


def _cooldown_active(path) -> bool:
    try:
        return time.time() < float(path.read_text(encoding="utf-8").strip())
    except Exception:
        return False


def _set_cooldown(path, seconds: float) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(time.time() + seconds), encoding="utf-8")
    except Exception:
        pass


def _acquire_worker_lock():
    """Singleton for every mode that can reach a Send action."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock_name = f"worker-{config.LOG_TAG}.lock" if config.LOG_TAG else "worker.lock"
    lock_file = open(config.DATA_DIR / lock_name, "w")
    try:
        try:
            import fcntl

            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:
            import msvcrt

            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    except (BlockingIOError, OSError):
        lock_file.close()
        return None
    return lock_file


def _gates_ok(store: Store) -> tuple[bool, str]:
    if not in_work_hours():
        return False, f"вне рабочих часов {config.WORK_HOURS}"
    if config.DAILY_SEND_LIMIT and store.sends_today() >= config.DAILY_SEND_LIMIT:
        return False, f"дневной лимит {config.DAILY_SEND_LIMIT} исчерпан"
    return True, "ok"


def _new_order_decision(order) -> dict:
    """Hard-filter -> LLM -> safe fallback when LLM is unavailable/unusable."""
    verdict = hard_filter(order)
    if not verdict.passed:
        return {
            "decision": "filtered",
            "reason": verdict.reason,
            "text": "",
            "source": "rules",
        }

    if _cooldown_active(config.LLM_COOLDOWN_FILE):
        return fallback_reply(order.id, reason="LLM cooldown активен: fallback")

    result = triage(order)
    if result["decision"] == "skip":
        result["source"] = "llm"
        return result
    if result["decision"] == "respond":
        result["source"] = "llm"
        return result
    if result["decision"] == "llm_error":
        _set_cooldown(config.LLM_COOLDOWN_FILE, 30 * 60)
        return fallback_reply(order.id, reason=f"LLM недоступна: {result['reason']}")
    if result["decision"] == "error":
        return fallback_reply(order.id, reason=f"LLM ответ непригоден: {result['reason']}")
    return fallback_reply(order.id, reason=f"неизвестный triage verdict: {result!r}")


def run_cycle(mgr: bm.BrowserManager, store: Store, dry_run: bool = False) -> dict:
    """One feed/outreach cycle."""
    summary = {"new": 0, "responded": 0, "skipped": 0, "errors": 0}

    if not in_work_hours():
        log.info("вне рабочих часов %s — спим", config.WORK_HOURS)
        return summary
    if _cooldown_active(config.FEED_COOLDOWN_FILE):
        log.info("feed-cooldown активен — ленту не дёргаем")
        return summary

    state = mgr.ensure_ready()
    if state == bm.AUTH_REQUIRED:
        log.warning("AUTH_REQUIRED — жду ручной логин")
        return summary
    if state != bm.READY:
        log.warning("браузер не готов: %s", state)
        return summary

    try:
        orders, all_ids = FeedCapture(mgr.page).reload_and_capture()
    except FeedAuthError:
        log.warning("лента ушла на логин/антибот — пауза 30 мин")
        _set_cooldown(config.FEED_COOLDOWN_FILE, 30 * 60)
        return summary
    except FeedError as e:
        log.error("лента не поймана: %s", e)
        return summary

    store.register_seen_many(all_ids)

    for order in orders:
        if config.DAILY_SEND_LIMIT and store.sends_today() >= config.DAILY_SEND_LIMIT:
            log.info("дневной лимит %s исчерпан — триаж не ведём", config.DAILY_SEND_LIMIT)
            break

        row = store.get_response(order.id)
        pending = row is None or (
            row["decision"] == "respond" and row["status"] in ("not_sent", "error")
        )
        if not pending:
            continue
        summary["new"] += 1
        chat_title = _chat_title(order)

        if row is not None and row["text"]:
            decision = {
                "decision": "respond",
                "reason": row["reason"] or "",
                "text": row["text"],
                "source": row["source"] or "saved",
            }
        else:
            decision = _new_order_decision(order)

        if decision["decision"] == "filtered":
            store.upsert_response(
                order.id,
                subject=order.subject,
                title=order.title,
                decision="filtered",
                reason=decision["reason"],
                source="rules",
                chat_title=chat_title,
                status="not_sent",
            )
            log.info("заявка %s отфильтрована: %s", order.id, decision["reason"])
            summary["skipped"] += 1
            continue

        if decision["decision"] == "skip":
            store.upsert_response(
                order.id,
                subject=order.subject,
                title=order.title,
                decision="skip",
                reason=decision["reason"],
                source=decision.get("source") or "llm",
                chat_title=chat_title,
                status="not_sent",
            )
            log.info("заявка %s: LLM skip — %s", order.id, decision["reason"])
            summary["skipped"] += 1
            continue

        if decision["decision"] != "respond" or not decision.get("text"):
            # If fallback itself is unavailable while LLM is down, keep the order
            # pending instead of terminalising it. A later cycle may recover.
            log.warning("заявка %s: нет безопасного текста — %s", order.id, decision["reason"])
            summary["errors"] += 1
            if _cooldown_active(config.LLM_COOLDOWN_FILE):
                break
            continue

        source = decision.get("source") or "llm"
        gates, why = _gates_ok(store)
        if not gates:
            store.upsert_response(
                order.id,
                subject=order.subject,
                title=order.title,
                decision="respond",
                reason=decision["reason"],
                text=decision["text"],
                source=source,
                chat_title=chat_title,
                status="not_sent",
                error=why,
            )
            log.info("заявка %s: гейт — %s (текст сохранён)", order.id, why)
            summary["skipped"] += 1
            continue

        if dry_run:
            store.upsert_response(
                order.id,
                subject=order.subject,
                title=order.title,
                decision="respond",
                reason=decision["reason"],
                text=decision["text"],
                source=source,
                chat_title=chat_title,
                status="not_sent",
                error="dry-run",
            )
            log.info("заявка %s: DRY-RUN source=%s", order.id, source)
            continue

        result = Responder(mgr.context()).send_first_message(
            order.id, chat_title, decision["text"]
        )
        status = result["status"]

        if status in ("auth_required", "retry"):
            store.upsert_response(
                order.id,
                subject=order.subject,
                title=order.title,
                decision="respond",
                reason=decision["reason"],
                text=decision["text"],
                source=source,
                chat_title=chat_title,
                status="not_sent",
                error=result.get("detail"),
                screenshot=result.get("screenshot"),
            )
            summary["errors"] += 1
            log.warning("заявка %s: pre-Send %s — цикл остановлен", order.id, status)
            break

        store.upsert_response(
            order.id,
            subject=order.subject,
            title=order.title,
            decision="respond",
            reason=decision["reason"],
            text=decision["text"],
            source=source,
            chat_title=chat_title,
            status=status,
            error=result.get("detail") if status == "unknown" else None,
            screenshot=result.get("screenshot"),
            sent=status in ("sent", "unknown"),
        )
        if status == "sent":
            log.info("заявка %s: ОТПРАВЛЕН отклик source=%s", order.id, source)
            summary["responded"] += 1
            human_pause(config.PAUSE_BETWEEN_SENDS_MIN_S, config.PAUSE_BETWEEN_SENDS_MAX_S)
        elif status == "already":
            summary["responded"] += 1
        elif status == "unknown":
            summary["responded"] += 1
            human_pause(config.PAUSE_BETWEEN_SENDS_MIN_S, config.PAUSE_BETWEEN_SENDS_MAX_S)
        else:
            log.error("заявка %s: неизвестный статус отправки: %s", order.id, status)
            summary["errors"] += 1
            break

        if summary["responded"] >= config.MAX_RESPONDS_PER_CYCLE:
            log.info("достигнут MAX_RESPONDS_PER_CYCLE=%s", config.MAX_RESPONDS_PER_CYCLE)
            break

    log.info("цикл: %s", summary)
    return summary


def run_chat_cycle(
    mgr: bm.BrowserManager,
    store: Store,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> dict:
    """Inspect worker-owned conversations and answer proven client-last messages."""
    summary = {"checked": 0, "targets": 0, "sent": 0, "human": 0, "errors": 0}
    if not in_work_hours():
        return summary
    if not force and not config.CHAT_AUTO_ENABLED:
        return summary
    if _cooldown_active(config.LLM_COOLDOWN_FILE):
        log.info("chat-auto: LLM cooldown — чаты ждут, acquisition использует fallback")
        return summary

    state = mgr.ensure_ready()
    if state != bm.READY:
        log.warning("chat-auto: браузер не READY: %s", state)
        return summary

    responder = ChatResponder(mgr.context())
    rows = store.list_chat_candidates(
        config.CHAT_CANDIDATE_SCAN_LIMIT,
        config.CHAT_MAX_ORDER_AGE_DAYS,
    )
    for row in rows:
        order_id = row["order_id"]
        title = row["chat_title"] or f"№ {order_id}"
        snapshot = responder.inspect(order_id, title)
        summary["checked"] += 1
        inspect_error = snapshot.detail if snapshot.state == "unsupported" else None
        store.mark_chat_checked(order_id, inspect_error)

        if snapshot.detail == "auth_required":
            summary["errors"] += 1
            break
        if snapshot.state != "client_last":
            continue
        incoming_key = snapshot.incoming_key or ""
        incoming_text = snapshot.incoming_text or ""
        if not incoming_key or not incoming_text:
            summary["errors"] += 1
            continue
        summary["targets"] += 1

        existing = store.get_chat_reply(order_id, incoming_key)
        if existing is not None and existing["status"] in _CHAT_TERMINAL:
            continue

        if existing is not None and existing["text"] and existing["status"] in {
            "retry",
            "dry_run",
        }:
            decision = {
                "decision": "reply",
                "reason": existing["error"] or "сохранённый draft",
                "text": existing["text"],
            }
        else:
            decision = generate_chat_reply(
                order_id=order_id,
                last_client_text=incoming_text,
                dialog_text=snapshot.dialog_text,
            )

        if decision["decision"] == "llm_error":
            store.upsert_chat_reply(
                order_id,
                incoming_key,
                incoming_text=incoming_text,
                decision="reply",
                status="retry",
                error=decision["reason"],
            )
            _set_cooldown(config.LLM_COOLDOWN_FILE, 30 * 60)
            summary["errors"] += 1
            break
        if decision["decision"] == "needs_human":
            store.upsert_chat_reply(
                order_id,
                incoming_key,
                incoming_text=incoming_text,
                decision="needs_human",
                status="needs_human",
                error=decision["reason"],
            )
            log.warning("chat %s: NEEDS_HUMAN — %s", order_id, decision["reason"])
            summary["human"] += 1
            continue
        if decision["decision"] != "reply" or not decision.get("text"):
            store.upsert_chat_reply(
                order_id,
                incoming_key,
                incoming_text=incoming_text,
                decision="error",
                status="error",
                error=decision["reason"],
            )
            summary["errors"] += 1
            continue

        reply_text = decision["text"]
        if dry_run:
            store.upsert_chat_reply(
                order_id,
                incoming_key,
                incoming_text=incoming_text,
                decision="reply",
                text=reply_text,
                status="dry_run",
                error="dry-run: send не выполнен",
            )
            log.info("chat %s: DRY-RUN reply=%r", order_id, reply_text[:120])
            continue

        result = responder.send_reply(order_id, title, incoming_key, reply_text)
        send_status = result["status"]
        if send_status == "auth_required":
            store.upsert_chat_reply(
                order_id,
                incoming_key,
                incoming_text=incoming_text,
                decision="reply",
                text=reply_text,
                status="retry",
                error=result.get("detail"),
            )
            summary["errors"] += 1
            break
        if send_status == "retry":
            store.upsert_chat_reply(
                order_id,
                incoming_key,
                incoming_text=incoming_text,
                decision="reply",
                text=reply_text,
                status="retry",
                error=result.get("detail"),
            )
            summary["errors"] += 1
            continue
        if send_status == "stale":
            store.upsert_chat_reply(
                order_id,
                incoming_key,
                incoming_text=incoming_text,
                decision="reply",
                text=reply_text,
                status="stale",
                error=result.get("detail"),
            )
            continue
        if send_status in ("sent", "unknown", "already_sent"):
            store.upsert_chat_reply(
                order_id,
                incoming_key,
                incoming_text=incoming_text,
                decision="reply",
                text=reply_text,
                status=send_status,
                error=result.get("detail") if send_status == "unknown" else None,
                sent=True,
            )
            summary["sent"] += 1
            human_pause(1.5, 3.5)
        else:
            summary["errors"] += 1
            log.error("chat %s: неизвестный send status %s", order_id, send_status)

        if summary["sent"] >= config.CHAT_MAX_PER_CYCLE:
            break

    log.info("chat-cycle: %s", summary)
    return summary


def cmd_run(args) -> int:
    _setup_logging()
    lock_file = _acquire_worker_lock()
    if lock_file is None:
        log.error("воркер уже запущен — выходим")
        return 1

    log.info(
        "=== repetit-worker старт (dry_run=%s chat_auto=%s) ===",
        args.dry_run,
        config.CHAT_AUTO_ENABLED,
    )
    mgr = bm.BrowserManager()
    store = Store(config.DB_PATH)
    cycles = 0
    try:
        state = mgr.start()
        log.info("стартовое состояние: %s", state)
        while True:
            try:
                run_cycle(mgr, store, dry_run=args.dry_run)
                cycles += 1
                if config.CHAT_AUTO_ENABLED and cycles % config.CHAT_CHECK_EVERY_CYCLES == 0:
                    run_chat_cycle(mgr, store, dry_run=args.dry_run)
            except KeyboardInterrupt:
                raise
            except Exception:
                log.exception("цикл упал — продолжаю")
            pause = random.uniform(config.CYCLE_MIN_S, config.CYCLE_MAX_S)
            log.info("сон %.0f с", pause)
            time.sleep(pause)
    except KeyboardInterrupt:
        log.info("останов по Ctrl+C")
    finally:
        store.close()
        mgr.shutdown()
        lock_file.close()
    return 0


def cmd_once(args) -> int:
    _setup_logging()
    lock_file = _acquire_worker_lock()
    if lock_file is None:
        log.error("воркер уже запущен — once не запускаем")
        return 1

    mgr = bm.BrowserManager()
    store = Store(config.DB_PATH)
    rc = 0
    try:
        state = mgr.start()
        log.info("состояние: %s", state)
        if state == bm.BROWSER_OFFLINE:
            rc = 1
        else:
            summary = run_cycle(mgr, store, dry_run=args.dry_run)
            print(f"итог цикла: {summary}")
    finally:
        store.close()
        mgr.shutdown()
        lock_file.close()
    return rc


def cmd_chats_once(args) -> int:
    _setup_logging()
    lock_file = _acquire_worker_lock()
    if lock_file is None:
        log.error("воркер уже запущен — chats-once не запускаем")
        return 1
    mgr = bm.BrowserManager()
    store = Store(config.DB_PATH)
    try:
        state = mgr.start()
        if state == bm.BROWSER_OFFLINE:
            return 1
        summary = run_chat_cycle(mgr, store, dry_run=args.dry_run, force=True)
        print(f"итог chat-cycle: {summary}")
        return 0
    finally:
        store.close()
        mgr.shutdown()
        lock_file.close()


def cmd_llm_check(args) -> int:
    from repetit.llm import client as llm

    print("LLM:", llm.status())
    try:
        ans = llm.chat("Ты тест-помощник.", "Ответь ровно одним словом: работает", max_tokens=500)
        print("ответ:", ans.strip()[:100])
        print("OK")
        return 0
    except Exception as e:
        print(f"СБОЙ: {e}")
        return 1


def cmd_status(args) -> int:
    store = Store(config.DB_PATH)
    try:
        print("БД:", config.DB_PATH)
        print("статистика:", store.stats())
        print(f"отправлено сегодня: {store.sends_today()} / лимит {config.DAILY_SEND_LIMIT}")
        print("\nпоследние:")
        for row in store.list_recent(15):
            ts = time.strftime("%m-%d %H:%M", time.localtime(row["created_at"]))
            source = row["source"] or "-"
            print(
                f"  {ts} №{row['order_id']} [{row['decision']}/{row['status']}/{source}] "
                f"{(row['subject'] or '')[:30]} | {(row['reason'] or '')[:60]}"
            )
    finally:
        store.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="repetit", description="воркер repetit.ru")
    sub = parser.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run", help="цикл воркера (постоянно)")
    p_run.add_argument("--dry-run", action="store_true", help="не отправлять")
    p_run.set_defaults(func=cmd_run)

    p_once = sub.add_parser("once", help="один feed/outreach цикл")
    p_once.add_argument("--dry-run", action="store_true", help="не отправлять")
    p_once.set_defaults(func=cmd_once)

    p_chats = sub.add_parser("chats-once", help="одна проверка worker-owned чатов")
    p_chats.add_argument("--dry-run", action="store_true", help="сгенерировать, но не отправлять")
    p_chats.set_defaults(func=cmd_chats_once)

    p_llm = sub.add_parser("llm-check", help="проверка LLM")
    p_llm.set_defaults(func=cmd_llm_check)

    p_st = sub.add_parser("status", help="сводка по БД")
    p_st.set_defaults(func=cmd_status)

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
