"""CLI и цикл воркера Контур A (repetit.ru).

Цикл: reload ленты (пассивный перехват searchOrders + батча деталей) →
новые ID → hard-фильтры → LLM-триаж (glm-5.3-flash) → отправка первого
сообщения в чат заявки (человеческий ввод) → аудит в SQLite + логи.

Команды:
  run        — цикл; --dry-run — без отправок
  once       — один цикл; --dry-run — без отправок
  llm-check  — проверка LLM
  status     — сводка по БД
"""

from __future__ import annotations

import argparse
import logging
import random
import sys
import time

from repetit import config
from repetit.browser import manager as bm
from repetit.filters import hard_filter
from repetit.integration.feed import FeedAuthError, FeedCapture, FeedError
from repetit.integration.respond import Responder
from repetit.integration.triage import triage
from repetit.storage.store import Store
from repetit.utils.pacing import human_pause
from repetit.utils.workhours import in_work_hours

log = logging.getLogger("repetit.worker")


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
    """Singleton для ЛЮБОГО режима, который может отправлять сообщения.

    И `run`, и `once` используют один Chrome/SQLite. Без общего lock ручной
    `once` параллельно постоянному worker может отправить дубль.
    """
    import fcntl

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock_file = open(config.DATA_DIR / "worker.lock", "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        return None
    return lock_file


def _gates_ok(store: Store) -> tuple[bool, str]:
    """Денежные/временные предохранители перед отправкой."""
    if not in_work_hours():
        return False, f"вне рабочих часов {config.WORK_HOURS}"
    if config.DAILY_SEND_LIMIT and store.sends_today() >= config.DAILY_SEND_LIMIT:
        return False, f"дневной лимит {config.DAILY_SEND_LIMIT} исчерпан"
    return True, "ok"


def run_cycle(mgr: bm.BrowserManager, store: Store, dry_run: bool = False) -> dict:
    """Один цикл воркера. Возвращает сводку."""
    summary = {"new": 0, "responded": 0, "skipped": 0, "errors": 0}

    # Вне рабочих часов браузер не трогаем вообще (SPEC §6.2).
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

    # Не закрываем «лишние» вкладки эвристикой. После рестарта нельзя надёжно
    # отличить старую вкладку воркера от вкладки, которую владелец открыл руками.
    # Responder всегда закрывает только созданную им страницу в собственном finally.

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
        # Гейты ДО триажа: лимит исчерпан — LLM-квоту не тратим (SPEC §12.1).
        if config.DAILY_SEND_LIMIT and store.sends_today() >= config.DAILY_SEND_LIMIT:
            log.info("дневной лимит %s исчерпан — триаж не ведём", config.DAILY_SEND_LIMIT)
            break

        row = store.get_response(order.id)
        # Pending: новая строка, not_sent или legacy respond/error из старой
        # версии (pre-Send browser/UI error ошибочно был терминальным).
        pending = row is None or (
            row["decision"] == "respond" and row["status"] in ("not_sent", "error")
        )
        if not pending:
            continue
        summary["new"] += 1

        if row is not None and row["text"]:
            tri = {"decision": "respond", "reason": row["reason"] or "", "text": row["text"]}
        elif _cooldown_active(config.LLM_COOLDOWN_FILE):
            continue
        else:
            verdict = hard_filter(order)
            if not verdict.passed:
                store.upsert_response(
                    order.id,
                    subject=order.subject,
                    title=order.title,
                    decision="filtered",
                    reason=verdict.reason,
                    status="not_sent",
                )
                log.info("заявка %s отфильтрована: %s", order.id, verdict.reason)
                summary["skipped"] += 1
                continue

            tri = triage(order)
            if tri["decision"] == "skip":
                store.upsert_response(
                    order.id,
                    subject=order.subject,
                    title=order.title,
                    decision="skip",
                    reason=tri["reason"],
                    status="not_sent",
                )
                log.info("заявка %s: LLM skip — %s", order.id, tri["reason"])
                summary["skipped"] += 1
                continue
            if tri["decision"] == "llm_error":
                log.warning("заявка %s: LLM сбой — %s", order.id, tri["reason"])
                _set_cooldown(config.LLM_COOLDOWN_FILE, 30 * 60)
                summary["errors"] += 1
                break
            if tri["decision"] == "error":
                # Детерминированный брак ответа LLM: терминально, чтобы не
                # крутить тот же плохой результат бесконечно.
                store.upsert_response(
                    order.id,
                    subject=order.subject,
                    title=order.title,
                    decision="error",
                    reason=tri["reason"],
                    status="not_sent",
                )
                log.warning("заявка %s: брак триажа (терминально) — %s", order.id, tri["reason"])
                summary["errors"] += 1
                continue

        gates, why = _gates_ok(store)
        if not gates:
            store.upsert_response(
                order.id,
                subject=order.subject,
                title=order.title,
                decision="respond",
                reason=tri["reason"],
                text=tri["text"],
                status="not_sent",
                error=why,
            )
            log.info("заявка %s: гейт — %s (текст сохранён, не отправлен)", order.id, why)
            summary["skipped"] += 1
            continue

        if dry_run:
            store.upsert_response(
                order.id,
                subject=order.subject,
                title=order.title,
                decision="respond",
                reason=tri["reason"],
                text=tri["text"],
                status="not_sent",
                error="dry-run",
            )
            log.info("заявка %s: DRY-RUN, отправка не выполнена", order.id)
            continue

        result = Responder(mgr.context()).send_first_message(
            order.id, _chat_title(order), tri["text"]
        )
        status = result["status"]

        # До Send не дошли: сохраняем готовый draft как pending и прекращаем
        # текущую серию, чтобы не повторять один и тот же сбой на других заявках.
        if status in ("auth_required", "retry"):
            store.upsert_response(
                order.id,
                subject=order.subject,
                title=order.title,
                decision="respond",
                reason=tri["reason"],
                text=tri["text"],
                status="not_sent",
                error=result.get("detail"),
                screenshot=result.get("screenshot"),
            )
            summary["errors"] += 1
            if status == "auth_required":
                log.warning("заявка %s: AUTH_REQUIRED при отправке — цикл остановлен", order.id)
            else:
                log.warning(
                    "заявка %s: pre-Send сбой, повторим позже — %s",
                    order.id,
                    result.get("detail"),
                )
            break

        store.upsert_response(
            order.id,
            subject=order.subject,
            title=order.title,
            decision="respond",
            reason=tri["reason"],
            text=tri["text"],
            status=status,
            error=result.get("detail") if status == "unknown" else None,
            screenshot=result.get("screenshot"),
            sent=status in ("sent", "unknown"),
        )
        if status == "sent":
            log.info("заявка %s: ОТПРАВЛЕН отклик", order.id)
            summary["responded"] += 1
            human_pause(config.PAUSE_BETWEEN_SENDS_MIN_S, config.PAUSE_BETWEEN_SENDS_MAX_S)
        elif status == "already":
            summary["responded"] += 1
        elif status == "unknown":
            summary["responded"] += 1
            human_pause(config.PAUSE_BETWEEN_SENDS_MIN_S, config.PAUSE_BETWEEN_SENDS_MAX_S)
        else:
            # Неизвестный статус от интеграции — fail-closed: дальше не идём.
            log.error("заявка %s: неизвестный статус отправки: %s", order.id, status)
            summary["errors"] += 1
            break

        if summary["responded"] >= config.MAX_RESPONDS_PER_CYCLE:
            log.info("достигнут MAX_RESPONDS_PER_CYCLE=%s", config.MAX_RESPONDS_PER_CYCLE)
            break

    log.info("цикл: %s", summary)
    return summary


def cmd_run(args) -> int:
    _setup_logging()
    lock_file = _acquire_worker_lock()
    if lock_file is None:
        log.error("воркер уже запущен (worker.lock занят) — выходим")
        return 1

    log.info("=== repetit-worker старт (dry_run=%s) ===", args.dry_run)
    mgr = bm.BrowserManager()
    store = Store(config.DB_PATH)
    try:
        state = mgr.start()
        log.info("стартовое состояние: %s", state)
        while True:
            try:
                run_cycle(mgr, store, dry_run=args.dry_run)
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
        log.error("воркер уже запущен (worker.lock занят) — once не запускаем")
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
        for r in store.list_recent(15):
            ts = time.strftime("%m-%d %H:%M", time.localtime(r["created_at"]))
            print(
                f"  {ts} №{r['order_id']} [{r['decision']}/{r['status']}] "
                f"{(r['subject'] or '')[:30]} | {(r['reason'] or '')[:60]}"
            )
    finally:
        store.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="repetit", description="воркер откликов repetit.ru")
    sub = parser.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run", help="цикл воркера (постоянно)")
    p_run.add_argument("--dry-run", action="store_true", help="не отправлять")
    p_run.set_defaults(func=cmd_run)

    p_once = sub.add_parser("once", help="один цикл")
    p_once.add_argument("--dry-run", action="store_true", help="не отправлять")
    p_once.set_defaults(func=cmd_once)

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
