from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from ncatbot.core import GroupMessage


class BotDatabase:
    """Lightweight SQLite storage for messages and user stats."""

    def __init__(self, db_file: str) -> None:
        self._db_path = Path(db_file)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    nickname TEXT DEFAULT '',
                    card TEXT DEFAULT '',
                    role TEXT DEFAULT '',
                    title TEXT DEFAULT '',
                    level TEXT DEFAULT '',
                    total_messages INTEGER NOT NULL DEFAULT 0,
                    last_message_at TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT,
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    raw_message TEXT NOT NULL DEFAULT '',
                    sender_nickname TEXT DEFAULT '',
                    sender_card TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    extra_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(message_id, group_id)
                );

                CREATE TABLE IF NOT EXISTS daily_user_stats (
                    day TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    message_count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(day, group_id, user_id)
                );

                CREATE INDEX IF NOT EXISTS idx_messages_group_created
                ON messages(group_id, created_at);

                CREATE INDEX IF NOT EXISTS idx_daily_group_count
                ON daily_user_stats(group_id, day, message_count DESC);
                """
            )
            self._conn.commit()

    def record_group_message(self, msg: GroupMessage) -> None:
        group_id = str(msg.group_id)
        user_id = str(msg.user_id)
        message_id = str(getattr(msg, "message_id", "") or "")
        raw_message = str(getattr(msg, "raw_message", "") or "")

        sender = getattr(msg, "sender", None)
        nickname = str(getattr(sender, "nickname", "") or "").strip()
        card = str(getattr(sender, "card", "") or "").strip()
        role = str(getattr(sender, "role", "") or "").strip()
        title = str(getattr(sender, "title", "") or "").strip()
        level = str(getattr(sender, "level", "") or "").strip()

        now = datetime.now(timezone.utc).isoformat()
        day = date.today().isoformat()

        with self._lock:
            cur = self._conn.cursor()

            cur.execute(
                """
                INSERT INTO users (
                    user_id, nickname, card, role, title, level, total_messages, last_message_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    nickname = excluded.nickname,
                    card = excluded.card,
                    role = excluded.role,
                    title = excluded.title,
                    level = excluded.level,
                    total_messages = users.total_messages + 1,
                    last_message_at = excluded.last_message_at
                """,
                (user_id, nickname, card, role, title, level, now),
            )

            cur.execute(
                """
                INSERT INTO messages (
                    message_id, group_id, user_id, raw_message, sender_nickname, sender_card, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id, group_id) DO NOTHING
                """,
                (
                    message_id if message_id else None,
                    group_id,
                    user_id,
                    raw_message,
                    nickname,
                    card,
                    now,
                ),
            )

            cur.execute(
                """
                INSERT INTO daily_user_stats (day, group_id, user_id, message_count)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(day, group_id, user_id) DO UPDATE SET
                    message_count = daily_user_stats.message_count + 1
                """,
                (day, group_id, user_id),
            )

            self._conn.commit()

    def get_today_most_active(self, group_id: str, top_n: int) -> list[dict[str, Any]]:
        day = date.today().isoformat()
        limit = max(1, int(top_n))

        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT
                    s.user_id,
                    s.message_count,
                    COALESCE(u.nickname, '') AS nickname,
                    COALESCE(u.card, '') AS card
                FROM daily_user_stats s
                LEFT JOIN users u ON u.user_id = s.user_id
                WHERE s.day = ? AND s.group_id = ?
                ORDER BY s.message_count DESC, s.user_id ASC
                LIMIT ?
                """,
                (day, str(group_id), limit),
            )
            rows = cur.fetchall()

        result: list[dict[str, Any]] = []
        for row in rows:
            result.append(
                {
                    "user_id": row["user_id"],
                    "nickname": row["nickname"],
                    "card": row["card"],
                    "message_count": row["message_count"],
                }
            )
        return result

    def get_recent_group_messages(self, group_id: str, limit: int) -> list[dict[str, Any]]:
        fetch_limit = max(1, int(limit))

        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT
                    t.user_id,
                    t.raw_message,
                    t.sender_nickname,
                    t.sender_card,
                    t.created_at
                FROM (
                    SELECT
                        user_id,
                        raw_message,
                        sender_nickname,
                        sender_card,
                        created_at
                    FROM messages
                    WHERE group_id = ? AND TRIM(raw_message) <> ''
                    ORDER BY created_at DESC
                    LIMIT ?
                ) AS t
                ORDER BY t.created_at ASC
                """,
                (str(group_id), fetch_limit),
            )
            rows = cur.fetchall()

        result: list[dict[str, Any]] = []
        for row in rows:
            result.append(
                {
                    "user_id": row["user_id"],
                    "raw_message": row["raw_message"],
                    "sender_nickname": row["sender_nickname"],
                    "sender_card": row["sender_card"],
                    "created_at": row["created_at"],
                }
            )
        return result
