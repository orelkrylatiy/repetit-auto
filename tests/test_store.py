from __future__ import annotations

import time

from repetit.storage.store import Store


def test_register_seen_reports_new_then_known_and_batch_updates(tmp_path):
    store = Store(tmp_path / "repetit.db")
    try:
        assert store.register_seen(1) == "NEW"
        assert store.register_seen(1) == "KNOWN"
        assert store.register_seen_many([1, 2, 3]) == 3
        assert store.stats()["seen"] == 3
    finally:
        store.close()


def test_daily_limit_counts_sent_and_unknown_but_not_already(tmp_path):
    store = Store(tmp_path / "repetit.db")
    try:
        store.upsert_response(1, status="sent", sent=True)
        store.upsert_response(2, status="unknown", sent=True)
        store.upsert_response(3, status="already", sent=False)
        store.upsert_response(4, status="error", sent=False)
        assert store.sends_today() == 2
    finally:
        store.close()


def test_daily_limit_ignores_old_sent_rows(tmp_path):
    store = Store(tmp_path / "repetit.db")
    try:
        store.upsert_response(1, status="sent", sent=True)
        old = int(time.time()) - 3 * 24 * 60 * 60
        store.conn.execute("UPDATE responses SET sent_at = ? WHERE order_id = '1'", (old,))
        store.conn.commit()
        assert store.sends_today() == 0
    finally:
        store.close()


def test_pending_draft_survives_retry_update(tmp_path):
    store = Store(tmp_path / "repetit.db")
    try:
        store.upsert_response(
            42,
            subject="Информатика",
            decision="respond",
            reason="подходит",
            text="Готов обсудить задачу подробнее и подобрать формат занятий?",
            status="not_sent",
            error="temporary ui error",
        )
        row = store.get_response(42)
        assert row["decision"] == "respond"
        assert row["status"] == "not_sent"
        assert row["text"]
        assert row["sent_at"] is None
    finally:
        store.close()


def test_upsert_updates_status_without_duplicating_order(tmp_path):
    store = Store(tmp_path / "repetit.db")
    try:
        store.upsert_response(
            7,
            subject="Информатика",
            decision="respond",
            text="Черновик сообщения клиенту, который будет отправлен после проверки всех гейтов.",
            status="not_sent",
        )
        store.upsert_response(
            7,
            subject="Информатика",
            decision="respond",
            text="Черновик сообщения клиенту, который будет отправлен после проверки всех гейтов.",
            status="sent",
            sent=True,
        )
        rows = store.conn.execute("SELECT COUNT(*) FROM responses WHERE order_id = '7'").fetchone()[0]
        row = store.get_response(7)
        assert rows == 1
        assert row["status"] == "sent"
        assert row["sent_at"] is not None
    finally:
        store.close()


def test_stats_group_decisions_and_count_confirmed_sent(tmp_path):
    store = Store(tmp_path / "repetit.db")
    try:
        store.register_seen_many([1, 2, 3])
        store.upsert_response(1, decision="respond", status="sent", sent=True)
        store.upsert_response(2, decision="skip", status="not_sent")
        store.upsert_response(3, decision="filtered", status="not_sent")
        stats = store.stats()
        assert stats["seen"] == 3
        assert stats["sent"] == 1
        assert stats["by_decision"] == {"filtered": 1, "respond": 1, "skip": 1}
    finally:
        store.close()
