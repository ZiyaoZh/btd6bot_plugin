"""SQLite session snapshots, bounded conversation recall and explicit memories."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from .context_selection import evidence_excerpt, query_terms, relevance


class SessionStore:
    def __init__(self, path: str, *, retention_days: int = 90) -> None:
        self.path = str(path)
        self.retention_days = max(1, retention_days)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS agent_sessions (
                    group_id TEXT NOT NULL, user_id TEXT NOT NULL,
                    history_json TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(group_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS agent_turns (
                    group_id TEXT NOT NULL, user_id TEXT NOT NULL,
                    message_id TEXT NOT NULL, user_json TEXT NOT NULL,
                    assistant_text TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(group_id, user_id, message_id)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_turns_scope_time
                    ON agent_turns(group_id, user_id, created_at);
                CREATE TABLE IF NOT EXISTS agent_memories (
                    group_id TEXT NOT NULL, user_id TEXT NOT NULL,
                    memory_key TEXT NOT NULL, kind TEXT NOT NULL,
                    content TEXT NOT NULL, source_message_id TEXT NOT NULL,
                    source_user_id TEXT NOT NULL, updated_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    PRIMARY KEY(group_id, user_id, memory_key)
                );
                CREATE TABLE IF NOT EXISTS agent_memory_sources (
                    group_id TEXT NOT NULL, user_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    PRIMARY KEY(group_id, user_id, message_id)
                );
            """)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def load(self, group_id: str, user_id: str) -> list[dict[str, str]]:
        cutoff = (datetime.now(UTC) - timedelta(days=self.retention_days)).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT history_json FROM agent_sessions WHERE group_id=? AND user_id=? AND updated_at>=?",
                (group_id, user_id, cutoff),
            ).fetchone()
        if row is None:
            return []
        try:
            history = json.loads(row[0])
        except (ValueError, TypeError):
            return []
        return (
            [
                {"role": m["role"], "content": m["content"]}
                for m in history
                if isinstance(m, dict)
                and m.get("role") in {"user", "assistant"}
                and isinstance(m.get("content"), str)
            ]
            if isinstance(history, list)
            else []
        )

    def save(
        self,
        group_id: str,
        user_id: str,
        history: list[dict[str, str]],
        *,
        turn: tuple[str, str] | None = None,
        updates: list[dict[str, Any]] | None = None,
    ) -> None:
        now = datetime.now(UTC)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO agent_sessions VALUES(?,?,?,?) ON CONFLICT(group_id,user_id) DO UPDATE SET history_json=excluded.history_json, updated_at=excluded.updated_at",
                (
                    group_id,
                    user_id,
                    json.dumps(history, ensure_ascii=False),
                    now.isoformat(),
                ),
            )
            if turn is not None:
                try:
                    event = json.loads(turn[0]).get("event", {})
                except (ValueError, AttributeError):
                    event = {}
                message_id = str(event.get("message_id") or uuid4().hex)
                conn.execute(
                    "INSERT OR IGNORE INTO agent_turns VALUES(?,?,?,?,?,?)",
                    (group_id, user_id, message_id, turn[0], turn[1], now.isoformat()),
                )
                cutoff = (now - timedelta(days=self.retention_days)).isoformat()
                conn.execute(
                    "DELETE FROM agent_turns WHERE group_id=? AND user_id=? AND (created_at<? OR message_id NOT IN (SELECT message_id FROM agent_turns WHERE group_id=? AND user_id=? ORDER BY created_at DESC LIMIT 400))",
                    (group_id, user_id, cutoff, group_id, user_id),
                )
                conn.execute(
                    "DELETE FROM agent_memory_sources WHERE group_id=? AND user_id=? AND message_id NOT IN (SELECT message_id FROM agent_turns WHERE group_id=? AND user_id=?)",
                    (group_id, user_id, group_id, user_id),
                )
                for update in updates or []:
                    conn.execute(
                        "INSERT OR IGNORE INTO agent_memory_sources VALUES(?,?,?)",
                        (group_id, user_id, message_id),
                    )
                    scope_user = "" if update["scope"] == "group" else user_id
                    if update["action"] == "forget":
                        conn.execute(
                            "DELETE FROM agent_memories WHERE group_id=? AND user_id=? AND memory_key=?",
                            (group_id, scope_user, update["key"]),
                        )
                    else:
                        conn.execute(
                            "INSERT INTO agent_memories VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(group_id,user_id,memory_key) DO UPDATE SET kind=excluded.kind,content=excluded.content,source_message_id=excluded.source_message_id,source_user_id=excluded.source_user_id,updated_at=excluded.updated_at,expires_at=excluded.expires_at",
                            (
                                group_id,
                                scope_user,
                                update["key"],
                                update["kind"],
                                update["content"],
                                message_id,
                                user_id,
                                now.isoformat(),
                                (now + timedelta(days=update["ttl_days"])).isoformat(),
                            ),
                        )
            conn.execute(
                "DELETE FROM agent_memories WHERE expires_at<=?", (now.isoformat(),)
            )

    def reset(self, group_id: str, user_id: str) -> None:
        with self._connect() as conn:
            for table in (
                "agent_sessions",
                "agent_turns",
                "agent_memories",
                "agent_memory_sources",
            ):
                conn.execute(
                    f"DELETE FROM {table} WHERE group_id=? AND user_id=?",
                    (group_id, user_id),
                )

    def recall(
        self,
        group_id: str,
        user_id: str,
        query: str,
        *,
        exclude_ids: set[str] | None = None,
        limit: int = 5,
        max_chars: int = 3000,
        include_preferences: bool = True,
    ) -> list[dict[str, Any]]:
        now = datetime.now(UTC)
        cutoff = (now - timedelta(days=self.retention_days)).isoformat()
        terms = query_terms(query)
        with self._connect() as conn:
            notes = conn.execute(
                "SELECT * FROM agent_memories WHERE group_id=? AND (user_id=? OR user_id='') AND expires_at>? ORDER BY updated_at DESC LIMIT 100",
                (group_id, user_id, now.isoformat()),
            ).fetchall()
            turns = conn.execute(
                "SELECT * FROM agent_turns WHERE group_id=? AND user_id=? AND created_at>=? ORDER BY created_at DESC LIMIT 200",
                (group_id, user_id, cutoff),
            ).fetchall()
            memory_sources = {
                row[0]
                for row in conn.execute(
                    "SELECT message_id FROM agent_memory_sources WHERE group_id=? AND user_id=?",
                    (group_id, user_id),
                )
            }
        candidates: list[tuple[float, dict[str, Any]]] = []
        for row in notes:
            score = relevance(f"{row['memory_key']} {row['content']}", terms)
            if score:
                score += 0.5  # Prefer explicit notes when equally relevant.
            elif include_preferences and row["kind"] in {"preference", "rule"}:
                score = 0.25  # Core preferences cannot displace a query match.
            if score:
                candidates.append(
                    (
                        score,
                        {
                            "kind": row["kind"],
                            "key": row["memory_key"],
                            "scope": "group" if not row["user_id"] else "user",
                            "text": row["content"],
                            "source_message_id": row["source_message_id"],
                            "source_user_id": row["source_user_id"],
                            "updated_at": row["updated_at"],
                            "expires_at": row["expires_at"],
                            "status": "explicit_user_statement",
                        },
                    )
                )
        for row in turns:
            if (
                row["message_id"] in (exclude_ids or set())
                or row["message_id"] in memory_sources
            ):
                continue
            try:
                event = json.loads(row["user_json"]).get("event", {})
                text = str(event.get("text", ""))
            except (ValueError, AttributeError):
                continue
            score = relevance(text, terms)
            if score < 4:
                continue
            # Recall user statements with provenance. An old assistant answer
            # must never be promoted to a confirmed game fact.
            candidates.append(
                (
                    score,
                    {
                        "kind": "conversation",
                        "text": evidence_excerpt(text, query, 650)[0],
                        "source_message_id": row["message_id"],
                        "created_at": row["created_at"],
                        "status": "historical_user_message",
                    },
                )
            )
        candidates.sort(key=lambda item: -item[0])
        selected: list[dict[str, Any]] = []
        used = 0
        for _, item in candidates:
            cost = len(json.dumps(item, ensure_ascii=False))
            if used + cost <= max_chars:
                selected.append(item)
                used += cost
            if len(selected) >= limit:
                break
        return selected
