from __future__ import annotations

from repetit.storage.store import Store


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
