"""SQLite: дедуп ленты (feed_seen) + аудит откликов (responses)."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS feed_seen (
    order_id     TEXT PRIMARY KEY,
    first_seen_at INTEGER NOT NULL,
    last_seen_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS responses (
    order_id    TEXT PRIMARY KEY,
    subject     TEXT,
    title       TEXT,
    decision    TEXT NOT NULL,      -- respond / skip / error / filtered
    reason      TEXT,
    text        TEXT,
    status      TEXT NOT NULL,      -- not_sent / sent / already / error
    error       TEXT,
    screenshot  TEXT,
    created_at  INTEGER NOT NULL,
    sent_at     INTEGER
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
        self.conn.commit()

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
        """Массовая регистрация ID ленты одним запросом (сотни за цикл)."""
        now = int(time.time())
        rows = [(str(i), now, now) for i in order_ids]
        self.conn.executemany(
            "INSERT INTO feed_seen (order_id, first_seen_at, last_seen_at) VALUES (?, ?, ?) "
            "ON CONFLICT(order_id) DO UPDATE SET last_seen_at = excluded.last_seen_at",
            rows,
        )
        self.conn.commit()
        return len(rows)

    # --- отклики ---

    def upsert_response(
        self,
        order_id: int | str,
        *,
        subject: str | None = None,
        title: str | None = None,
        decision: str = "respond",
        reason: str | None = None,
        text: str | None = None,
        status: str = "not_sent",
        error: str | None = None,
        screenshot: str | None = None,
        sent: bool = False,
    ) -> None:
        now = int(time.time())
        self.conn.execute(
            "INSERT INTO responses (order_id, subject, title, decision, reason, text, status, "
            " error, screenshot, created_at, sent_at) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(order_id) DO UPDATE SET "
            "subject=excluded.subject, title=excluded.title, decision=excluded.decision, "
            "reason=excluded.reason, text=excluded.text, status=excluded.status, "
            "error=excluded.error, screenshot=excluded.screenshot, "
            "sent_at=CASE WHEN excluded.status IN ('sent','unknown','already') "
            "THEN excluded.sent_at ELSE responses.sent_at END",
            (
                str(order_id), subject, title, decision, reason, text, status,
                error, screenshot, now, now if sent else None,
            ),
        )
        self.conn.commit()

    def get_response(self, order_id: int | str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM responses WHERE order_id = ?", (str(order_id),)
        ).fetchone()

    def sends_today(self) -> int:
        """Расход дневного лимита: sent (подтверждено) + unknown (Send был,
        подтверждения нет — fail-closed). already чат создала не мы — не расход."""

        import datetime as _dt

        midnight = int(
            _dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        )
        row = self.conn.execute(
            "SELECT COUNT(*) FROM responses WHERE status IN ('sent','unknown') AND sent_at >= ?",
            (midnight,),
        ).fetchone()
        return int(row[0])

    def stats(self) -> dict:
        row = self.conn.execute(
            "SELECT decision, COUNT(*) c FROM responses GROUP BY decision"
        ).fetchall()
        sent = self.conn.execute(
            "SELECT COUNT(*) FROM responses WHERE status = 'sent'"
        ).fetchone()[0]
        seen = self.conn.execute("SELECT COUNT(*) FROM feed_seen").fetchone()[0]
        return {"seen": seen, "sent": sent, "by_decision": {r["decision"]: r["c"] for r in row}}

    def list_recent(self, n: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT order_id, subject, decision, reason, status, sent_at, created_at "
            "FROM responses ORDER BY created_at DESC LIMIT ?",
            (n,),
        ).fetchall()
