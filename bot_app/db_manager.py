from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from ncatbot.event.qq import GroupMessageEvent

from .group_context import build_message_record_metadata, serialize_message_record_metadata


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
                    normalized_text TEXT NOT NULL DEFAULT '',
                    mention_relation TEXT NOT NULL DEFAULT 'none',
                    is_text_meaningful INTEGER NOT NULL DEFAULT 0,
                    message_type TEXT NOT NULL DEFAULT 'unknown',
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

                CREATE TABLE IF NOT EXISTS group_settings (
                    group_id TEXT NOT NULL,
                    setting_key TEXT NOT NULL,
                    setting_value TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(group_id, setting_key)
                );
                """
            )
            self._ensure_column(
                cur, "messages", "normalized_text", "TEXT NOT NULL DEFAULT ''"
            )
            self._ensure_column(
                cur, "messages", "mention_relation", "TEXT NOT NULL DEFAULT 'none'"
            )
            self._ensure_column(
                cur, "messages", "is_text_meaningful", "INTEGER NOT NULL DEFAULT 0"
            )
            self._ensure_column(
                cur, "messages", "message_type", "TEXT NOT NULL DEFAULT 'unknown'"
            )
            cur.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_group_created
                ON messages(group_id, created_at);

                CREATE INDEX IF NOT EXISTS idx_messages_group_meaningful_created
                ON messages(group_id, is_text_meaningful, created_at);

                CREATE INDEX IF NOT EXISTS idx_daily_group_count
                ON daily_user_stats(group_id, day, message_count DESC);
                """
            )
            self._conn.commit()

    def set_group_setting(self, group_id: str, key: str, value: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO group_settings (
                    group_id, setting_key, setting_value, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(group_id, setting_key) DO UPDATE SET
                    setting_value = excluded.setting_value,
                    updated_at = excluded.updated_at
                """,
                (str(group_id), str(key), str(value), now),
            )
            self._conn.commit()

    def get_group_bool_setting(
        self, group_id: str, key: str, *, default: bool = False
    ) -> bool:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT setting_value
                FROM group_settings
                WHERE group_id = ? AND setting_key = ?
                """,
                (str(group_id), str(key)),
            ).fetchone()
        if row is None:
            return bool(default)
        return str(row["setting_value"] or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    def _ensure_column(
        self, cur: sqlite3.Cursor, table: str, column: str, definition: str
    ) -> None:
        cur.execute(f"PRAGMA table_info({table})")
        existing_columns = {row[1] for row in cur.fetchall()}
        if column in existing_columns:
            return
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def record_group_message(
        self,
        msg: GroupMessageEvent,
        *,
        bot_qq: str = "",
        bot_name: str = "",
    ) -> None:
        group_id = str(msg.group_id)
        user_id = str(msg.user_id)
        message_id = str(getattr(msg, "message_id", "") or "")
        raw_message = str(getattr(msg, "raw_message", "") or "")
        metadata = build_message_record_metadata(
            raw_message,
            bot_qq=bot_qq,
            bot_name=bot_name,
        )

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
                    message_id, group_id, user_id, raw_message, normalized_text,
                    mention_relation, is_text_meaningful, message_type,
                    sender_nickname, sender_card, created_at, extra_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id, group_id) DO NOTHING
                """,
                (
                    message_id if message_id else None,
                    group_id,
                    user_id,
                    raw_message,
                    str(metadata.get("normalized_text", "") or ""),
                    str(metadata.get("mention_relation", "none") or "none"),
                    1 if bool(metadata.get("is_text_meaningful", False)) else 0,
                    str(metadata.get("message_type", "unknown") or "unknown"),
                    nickname,
                    card,
                    now,
                    serialize_message_record_metadata(metadata),
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
                    t.message_id,
                    t.user_id,
                    t.raw_message,
                    t.normalized_text,
                    t.mention_relation,
                    t.is_text_meaningful,
                    t.message_type,
                    t.sender_nickname,
                    t.sender_card,
                    t.created_at
                FROM (
                    SELECT
                        message_id,
                        user_id,
                        raw_message,
                        normalized_text,
                        mention_relation,
                        is_text_meaningful,
                        message_type,
                        sender_nickname,
                        sender_card,
                        created_at
                    FROM messages
                    WHERE group_id = ? AND is_text_meaningful = 1
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
                    "message_id": row["message_id"],
                    "user_id": row["user_id"],
                    "raw_message": row["raw_message"],
                    "normalized_text": row["normalized_text"],
                    "mention_relation": row["mention_relation"],
                    "is_text_meaningful": row["is_text_meaningful"],
                    "message_type": row["message_type"],
                    "sender_nickname": row["sender_nickname"],
                    "sender_card": row["sender_card"],
                    "created_at": row["created_at"],
                }
            )
        return result
