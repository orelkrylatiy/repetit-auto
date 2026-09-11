"""SQLite: дедуп ленты, аудит первых откликов и durable state автоответов."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS feed_seen (
    order_id      TEXT PRIMARY KEY,
    first_seen_at INTEGER NOT NULL,
    last_seen_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS responses (
    order_id    TEXT PRIMARY KEY,
    subject     TEXT,
    title       TEXT,
    decision    TEXT NOT NULL,
    reason      TEXT,
    text        TEXT,
    source      TEXT,
    chat_title  TEXT,
    status      TEXT NOT NULL,
    error       TEXT,
    screenshot  TEXT,
    created_at  INTEGER NOT NULL,
    sent_at     INTEGER
);

CREATE TABLE IF NOT EXISTS chat_checks (
    order_id    TEXT PRIMARY KEY,
    checked_at  INTEGER NOT NULL,
    last_error  TEXT
);

CREATE TABLE IF NOT EXISTS chat_replies (
    order_id       TEXT NOT NULL,
    incoming_key   TEXT NOT NULL,
    incoming_text  TEXT,
    decision       TEXT NOT NULL,
    text           TEXT,
    status         TEXT NOT NULL,
    error          TEXT,
    created_at     INTEGER NOT NULL,
    sent_at        INTEGER,
    PRIMARY KEY (order_id, incoming_key)
);
"""


class Store:
    def __init__(self, db_path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate_responses()
        self.conn.commit()

    def _migrate_responses(self) -> None:
        """Add columns introduced after the initial SQLite schema.

        Existing user databases are intentionally migrated in-place; deleting the
        DB would remove dedup/audit state and could lead to duplicate outreach.
        """
        columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(responses)").fetchall()
        }
        if "source" not in columns:
            self.conn.execute("ALTER TABLE responses ADD COLUMN source TEXT")
        if "chat_title" not in columns:
            self.conn.execute("ALTER TABLE responses ADD COLUMN chat_title TEXT")

    def close(self) -> None:
        self.conn.close()

    # --- дедуп ленты ---

    def register_seen(self, order_id: int | str) -> str:
        """NEW при первом появлении, иначе KNOWN."""
        oid = str(order_id)
        now = int(time.time())
        row = self.conn.execute(
            "SELECT 1 FROM feed_seen WHERE order_id = ?", (oid,)
        ).fetchone()
        if row is None:
            self.conn.execute(
                "INSERT INTO feed_seen (order_id, first_seen_at, last_seen_at) VALUES (?, ?, ?)",
                (oid, now, now),
            )
            self.conn.commit()
            return "NEW"
        self.conn.execute("UPDATE feed_seen SET last_seen_at = ? WHERE order_id = ?", (now, oid))
        self.conn.commit()
        return "KNOWN"

    def register_seen_many(self, order_ids: list[int | str]) -> int:
        """Массовая регистрация ID ленты одним запросом."""
        now = int(time.time())
        rows = [(str(i), now, now) for i in order_ids]
        self.conn.executemany(
            "INSERT INTO feed_seen (order_id, first_seen_at, last_seen_at) VALUES (?, ?, ?) "
            "ON CONFLICT(order_id) DO UPDATE SET last_seen_at = excluded.last_seen_at",
            rows,
        )
        self.conn.commit()
        return len(rows)

    # --- первые отклики ---

    def upsert_response(
        self,
        order_id: int | str,
        *,
        subject: str | None = None,
        title: str | None = None,
        decision: str = "respond",
        reason: str | None = None,
        text: str | None = None,
        source: str | None = None,
        chat_title: str | None = None,
        status: str = "not_sent",
        error: str | None = None,
        screenshot: str | None = None,
        sent: bool = False,
    ) -> None:
        now = int(time.time())
        self.conn.execute(
            "INSERT INTO responses "
            "(order_id, subject, title, decision, reason, text, source, chat_title, status, "
            " error, screenshot, created_at, sent_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(order_id) DO UPDATE SET "
            "subject=excluded.subject, title=excluded.title, decision=excluded.decision, "
            "reason=excluded.reason, text=excluded.text, "
            "source=COALESCE(excluded.source, responses.source), "
            "chat_title=COALESCE(excluded.chat_title, responses.chat_title), "
            "status=excluded.status, error=excluded.error, screenshot=excluded.screenshot, "
            "sent_at=CASE WHEN excluded.status IN ('sent','unknown') "
            "THEN excluded.sent_at ELSE responses.sent_at END",
            (
                str(order_id),
                subject,
                title,
                decision,
                reason,
                text,
                source,
                chat_title,
                status,
                error,
                screenshot,
                now,
                now if sent else None,
            ),
        )
        self.conn.commit()

    def get_response(self, order_id: int | str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM responses WHERE order_id = ?", (str(order_id),)
        ).fetchone()

    def sends_today(self) -> int:
        """Расход дневного лимита: sent + unknown. `already` лимит не расходует."""
        import datetime as _dt

        midnight = int(
            _dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        )
        row = self.conn.execute(
            "SELECT COUNT(*) FROM responses WHERE status IN ('sent','unknown') AND sent_at >= ?",
            (midnight,),
        ).fetchone()
        return int(row[0])

    # --- Контур B: durable chat state ---

    def list_chat_candidates(self, limit: int, max_age_days: int) -> list[sqlite3.Row]:
        """Chats eligible for auto-reply, oldest check first.

        Only chats initiated by this worker are eligible. `already` is excluded on
        purpose because it may represent a manual conversation owned by the user.
        """
        cutoff = int(time.time()) - int(max_age_days) * 24 * 60 * 60
        return self.conn.execute(
            "SELECT r.order_id, r.chat_title, r.sent_at, "
            "COALESCE(c.checked_at, 0) AS checked_at "
            "FROM responses r LEFT JOIN chat_checks c ON c.order_id = r.order_id "
            "WHERE r.decision='respond' AND r.status IN ('sent','unknown') "
            "AND COALESCE(r.sent_at, r.created_at) >= ? "
            "ORDER BY COALESCE(c.checked_at, 0) ASC, "
            "COALESCE(r.sent_at, r.created_at) DESC LIMIT ?",
            (cutoff, int(limit)),
        ).fetchall()

    def mark_chat_checked(self, order_id: int | str, error: str | None = None) -> None:
        now = int(time.time())
        self.conn.execute(
            "INSERT INTO chat_checks(order_id, checked_at, last_error) VALUES (?,?,?) "
            "ON CONFLICT(order_id) DO UPDATE SET checked_at=excluded.checked_at, "
            "last_error=excluded.last_error",
            (str(order_id), now, error),
        )
        self.conn.commit()

    def get_chat_reply(self, order_id: int | str, incoming_key: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM chat_replies WHERE order_id=? AND incoming_key=?",
            (str(order_id), str(incoming_key)),
        ).fetchone()

    def upsert_chat_reply(
        self,
        order_id: int | str,
        incoming_key: str,
        *,
        incoming_text: str | None,
        decision: str,
        text: str | None = None,
        status: str,
        error: str | None = None,
        sent: bool = False,
    ) -> None:
        now = int(time.time())
        self.conn.execute(
            "INSERT INTO chat_replies "
            "(order_id, incoming_key, incoming_text, decision, text, status, error, "
            " created_at, sent_at) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(order_id, incoming_key) DO UPDATE SET "
            "incoming_text=excluded.incoming_text, decision=excluded.decision, "
            "text=COALESCE(excluded.text, chat_replies.text), status=excluded.status, "
            "error=excluded.error, "
            "sent_at=CASE WHEN excluded.status IN ('sent','unknown','already_sent') "
            "THEN excluded.sent_at ELSE chat_replies.sent_at END",
            (
                str(order_id),
                str(incoming_key),
                incoming_text,
                decision,
                text,
                status,
                error,
                now,
                now if sent else None,
            ),
        )
        self.conn.commit()

    def chat_stats(self) -> dict:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS c FROM chat_replies GROUP BY status"
        ).fetchall()
        return {row["status"]: row["c"] for row in rows}

    # --- observability ---

    def stats(self) -> dict:
        row = self.conn.execute(
            "SELECT decision, COUNT(*) c FROM responses GROUP BY decision"
        ).fetchall()
        sent = self.conn.execute(
            "SELECT COUNT(*) FROM responses WHERE status = 'sent'"
        ).fetchone()[0]
        seen = self.conn.execute("SELECT COUNT(*) FROM feed_seen").fetchone()[0]
        return {
            "seen": seen,
            "sent": sent,
            "by_decision": {r["decision"]: r["c"] for r in row},
            "chat": self.chat_stats(),
        }

    def list_recent(self, n: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT order_id, subject, decision, reason, source, status, sent_at, created_at "
            "FROM responses ORDER BY created_at DESC LIMIT ?",
            (n,),
        ).fetchall()
