from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from ncatbot.event.qq import GroupMessageEvent

from .group_context import (
    build_message_record_metadata,
    serialize_message_record_metadata,
)
from .media_store import CachedImage
from .protocol import ImagePayload


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

                CREATE TABLE IF NOT EXISTS message_media (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_row_id INTEGER NOT NULL,
                    segment_index INTEGER NOT NULL,
                    media_type TEXT NOT NULL DEFAULT 'image',
                    source_url TEXT NOT NULL DEFAULT '',
                    source_file_id TEXT NOT NULL DEFAULT '',
                    source_file_name TEXT NOT NULL DEFAULT '',
                    source_bytes INTEGER,
                    sub_type INTEGER,
                    mime_type TEXT NOT NULL DEFAULT '',
                    width INTEGER,
                    height INTEGER,
                    sha256 TEXT NOT NULL DEFAULT '',
                    cache_path TEXT NOT NULL DEFAULT '',
                    processing_status TEXT NOT NULL DEFAULT 'metadata_only',
                    expires_at TEXT NOT NULL DEFAULT '',
                    visual_summary TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(message_row_id) REFERENCES messages(id) ON DELETE CASCADE,
                    UNIQUE(message_row_id, segment_index)
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

                CREATE INDEX IF NOT EXISTS idx_message_media_message_row
                ON message_media(message_row_id, segment_index);

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
        images: list[ImagePayload] | None = None,
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

            message_row_id = self._find_message_row_id(
                cur,
                message_id=message_id,
                group_id=group_id,
                user_id=user_id,
                created_at=now,
            )
            if message_row_id is not None:
                self._upsert_message_media(
                    cur,
                    message_row_id=message_row_id,
                    images=images or [],
                    now=now,
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

    @staticmethod
    def _find_message_row_id(
        cur: sqlite3.Cursor,
        *,
        message_id: str,
        group_id: str,
        user_id: str,
        created_at: str,
    ) -> int | None:
        if message_id:
            row = cur.execute(
                "SELECT id FROM messages WHERE message_id = ? AND group_id = ?",
                (message_id, group_id),
            ).fetchone()
        else:
            row = cur.execute(
                """
                SELECT id FROM messages
                WHERE group_id = ? AND user_id = ? AND created_at = ?
                ORDER BY id DESC LIMIT 1
                """,
                (group_id, user_id, created_at),
            ).fetchone()
        return int(row["id"]) if row is not None else None

    @staticmethod
    def _upsert_message_media(
        cur: sqlite3.Cursor,
        *,
        message_row_id: int,
        images: list[ImagePayload],
        now: str,
    ) -> None:
        for image in images:
            cur.execute(
                """
                INSERT INTO message_media (
                    message_row_id, segment_index, source_url, source_file_id,
                    source_file_name, source_bytes, sub_type, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_row_id, segment_index) DO UPDATE SET
                    source_url = excluded.source_url,
                    source_file_id = excluded.source_file_id,
                    source_file_name = excluded.source_file_name,
                    source_bytes = excluded.source_bytes,
                    sub_type = excluded.sub_type,
                    updated_at = excluded.updated_at
                """,
                (
                    message_row_id,
                    int(image.get("segment_index", 0)),
                    str(image.get("url", "") or ""),
                    str(image.get("file_id", "") or ""),
                    str(image.get("file_name", "") or ""),
                    image.get("file_size"),
                    image.get("sub_type"),
                    now,
                    now,
                ),
            )

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

    def update_message_media_cache(
        self,
        *,
        group_id: str,
        message_id: str,
        result: CachedImage,
    ) -> None:
        if not message_id:
            return
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                """
                UPDATE message_media
                SET mime_type = ?, source_bytes = COALESCE(?, source_bytes),
                    width = ?, height = ?, sha256 = ?, cache_path = ?,
                    processing_status = ?, expires_at = ?, updated_at = ?
                WHERE message_row_id = (
                    SELECT id FROM messages WHERE group_id = ? AND message_id = ?
                ) AND segment_index = ?
                """,
                (
                    result.mime_type,
                    result.source_bytes,
                    result.width,
                    result.height,
                    result.sha256,
                    result.cache_path,
                    result.status,
                    result.expires_at,
                    now,
                    str(group_id),
                    str(message_id),
                    result.segment_index,
                ),
            )
            self._conn.commit()

    def update_message_media_summary(
        self,
        *,
        group_id: str,
        message_id: str,
        segment_index: int,
        visual_summary: str,
    ) -> None:
        summary = str(visual_summary or "").strip()
        if not message_id or not summary:
            return
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                """
                UPDATE message_media
                SET visual_summary = ?, updated_at = ?
                WHERE message_row_id = (
                    SELECT id FROM messages WHERE group_id = ? AND message_id = ?
                ) AND segment_index = ?
                """,
                (summary, now, str(group_id), str(message_id), int(segment_index)),
            )
            self._conn.commit()

    def get_recent_group_messages(
        self, group_id: str, limit: int
    ) -> list[dict[str, Any]]:
        fetch_limit = max(1, int(limit))

        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT
                    t.id,
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
                        m.id,
                        m.message_id,
                        user_id,
                        raw_message,
                        normalized_text,
                        mention_relation,
                        is_text_meaningful,
                        message_type,
                        sender_nickname,
                        sender_card,
                        created_at
                    FROM messages AS m
                    WHERE m.group_id = ? AND (
                        m.is_text_meaningful = 1 OR EXISTS (
                            SELECT 1 FROM message_media AS mm
                            WHERE mm.message_row_id = m.id
                        )
                    )
                    ORDER BY m.created_at DESC
                    LIMIT ?
                ) AS t
                ORDER BY t.created_at ASC
                """,
                (str(group_id), fetch_limit),
            )
            rows = cur.fetchall()

            message_row_ids = [int(row["id"]) for row in rows]
            media_by_message: dict[int, list[dict[str, Any]]] = {
                row_id: [] for row_id in message_row_ids
            }
            if message_row_ids:
                placeholders = ",".join("?" for _ in message_row_ids)
                media_rows = cur.execute(
                    f"""
                    SELECT message_row_id, segment_index, media_type, source_url,
                           source_file_id, source_file_name, source_bytes, sub_type,
                           mime_type, width, height, sha256, cache_path,
                           processing_status, expires_at, visual_summary
                    FROM message_media
                    WHERE message_row_id IN ({placeholders})
                    ORDER BY message_row_id, segment_index
                    """,
                    message_row_ids,
                ).fetchall()
                for media in media_rows:
                    media_by_message[int(media["message_row_id"])].append(
                        {
                            key: media[key]
                            for key in media.keys()  # noqa: SIM118 - sqlite3.Row values iterate
                            if key != "message_row_id"
                        }
                    )

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
                    "images": media_by_message.get(int(row["id"]), []),
                }
            )
        return result
