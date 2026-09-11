from __future__ import annotations

import sqlite3

from repetit.storage.store import Store


def test_existing_database_gets_source_and_chat_title_columns(tmp_path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE feed_seen (
            order_id TEXT PRIMARY KEY,
            first_seen_at INTEGER NOT NULL,
            last_seen_at INTEGER NOT NULL
        );
        CREATE TABLE responses (
            order_id TEXT PRIMARY KEY,
            subject TEXT,
            title TEXT,
            decision TEXT NOT NULL,
            reason TEXT,
            text TEXT,
            status TEXT NOT NULL,
            error TEXT,
            screenshot TEXT,
            created_at INTEGER NOT NULL,
            sent_at INTEGER
        );
        """
    )
    conn.execute(
        "INSERT INTO responses(order_id, decision, status, created_at) VALUES ('1','respond','sent',1)"
    )
    conn.commit()
    conn.close()

    store = Store(path)
    try:
        columns = {
            row["name"] for row in store.conn.execute("PRAGMA table_info(responses)").fetchall()
        }
        assert {"source", "chat_title"} <= columns
        assert store.get_response(1)["status"] == "sent"
    finally:
        store.close()


def test_chat_candidates_only_include_worker_sent_or_unknown(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    try:
        store.upsert_response(1, decision="respond", status="sent", sent=True, chat_title="№ 1")
        store.upsert_response(2, decision="respond", status="unknown", sent=True, chat_title="№ 2")
        store.upsert_response(3, decision="respond", status="already", chat_title="№ 3")
        store.upsert_response(4, decision="skip", status="not_sent", chat_title="№ 4")
        ids = {row["order_id"] for row in store.list_chat_candidates(10, 14)}
        assert ids == {"1", "2"}
    finally:
        store.close()


def test_chat_check_rotation_moves_checked_order_behind_unchecked(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    try:
        store.upsert_response(1, decision="respond", status="sent", sent=True)
        store.upsert_response(2, decision="respond", status="sent", sent=True)
        before = [row["order_id"] for row in store.list_chat_candidates(10, 14)]
        store.mark_chat_checked(before[0])
        after = [row["order_id"] for row in store.list_chat_candidates(10, 14)]
        assert after[-1] == before[0]
    finally:
        store.close()


def test_same_incoming_message_has_single_durable_reply_row(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    try:
        store.upsert_chat_reply(
            10,
            "m-1",
            incoming_text="Когда можно?",
            decision="reply",
            text="Подскажите, какие дни удобны?",
            status="retry",
            error="temporary",
        )
        store.upsert_chat_reply(
            10,
            "m-1",
            incoming_text="Когда можно?",
            decision="reply",
            text="Подскажите, какие дни удобны?",
            status="sent",
            sent=True,
        )
        count = store.conn.execute(
            "SELECT COUNT(*) FROM chat_replies WHERE order_id='10' AND incoming_key='m-1'"
        ).fetchone()[0]
        row = store.get_chat_reply(10, "m-1")
        assert count == 1
        assert row["status"] == "sent"
        assert row["sent_at"] is not None
    finally:
        store.close()


def test_new_client_message_gets_independent_idempotency_key(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    try:
        store.upsert_chat_reply(
            10,
            "m-1",
            incoming_text="Первый вопрос",
            decision="needs_human",
            status="needs_human",
            error="manual",
        )
        assert store.get_chat_reply(10, "m-2") is None
    finally:
        store.close()
