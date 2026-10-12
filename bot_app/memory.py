from __future__ import annotations

from collections import defaultdict
from typing import Any

from .context_contracts import (
    CurrentEventContext,
    PolicyHintsContext,
    RecentGroupMessageContext,
    ReplyContext,
    UserSessionMemoryContext,
    build_policy_hints_context,
    build_user_session_memory_context,
    serialize_reply_context,
)
from .session_store import SessionStore


class GroupChatMemory:
    """Store per-(group, user) short conversation history in memory."""

    def __init__(
        self, history_turns: int, *, store: SessionStore | None = None
    ) -> None:
        # Keep this parameter for compatibility; actual compaction is handled by ContextManager.
        self._history_turns = max(1, history_turns)
        self._memory: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
        self._store = store
        self._loaded: set[tuple[str, str]] = set()
        self._pending: dict[tuple[str, str], list[dict[str, Any]]] = {}

    @property
    def history_turns(self) -> int:
        return self._history_turns

    @property
    def persistent(self) -> bool:
        return self._store is not None

    def begin_turn(self, group_id: str, user_id: str) -> None:
        self._pending.pop((group_id, user_id), None)

    def queue_update(self, group_id: str, user_id: str, update: dict[str, Any]) -> None:
        self._pending.setdefault((group_id, user_id), []).append(dict(update))

    def recall(
        self, group_id: str, user_id: str, query: str, *, explicit_search: bool = False
    ) -> list[dict[str, Any]]:
        if self._store is None:
            return []
        context = self.build_user_session_memory_context(group_id, user_id)
        ids = {
            turn["message_id"]
            for turn in context["recent_turns"]
            if turn.get("message_id")
        }
        return self._store.recall(
            group_id,
            user_id,
            query,
            exclude_ids=set() if explicit_search else ids,
            include_preferences=not explicit_search,
        )

    def build_messages(
        self,
        system_prompt: str,
        group_id: str,
        user_id: str,
        reply_context: ReplyContext | None = None,
        user_text: str = "",
    ) -> list[dict[str, str]]:
        key = (group_id, user_id)
        history = self.get_history(*key)
        current_user_text = (
            serialize_reply_context(reply_context)
            if reply_context is not None
            else str(user_text or "")
        )
        return [
            {"role": "system", "content": system_prompt},
            *history,
            {"role": "user", "content": current_user_text},
        ]

    def append_turn(
        self, group_id: str, user_id: str, user_text: str, assistant_text: str
    ) -> None:
        key = (group_id, user_id)
        history = self.get_history(*key) + [
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": assistant_text},
        ]
        try:
            if self._store is not None:
                self._store.save(
                    *key,
                    history,
                    turn=(user_text, assistant_text),
                    updates=self._pending.get(key, []),
                )
        finally:
            # Delivery already succeeded. Preserve that conversation locally
            # even if persistence fails, and never replay uncommitted mutations.
            self._memory[key] = history
            self._pending.pop(key, None)

    def get_history(self, group_id: str, user_id: str) -> list[dict[str, str]]:
        key = (group_id, user_id)
        if key not in self._loaded:
            if self._store is not None:
                self._memory[key] = self._store.load(*key)
            self._loaded.add(key)
        return list(self._memory[key])

    def replace_history(
        self, group_id: str, user_id: str, messages: list[dict[str, str]]
    ) -> None:
        key = (group_id, user_id)
        history = [
            {
                "role": str(item.get("role", "assistant") or "assistant"),
                "content": str(item.get("content", "") or ""),
            }
            for item in messages
            if isinstance(item, dict)
        ]
        if self._store is not None:
            self._store.save(*key, history)
        self._memory[key] = history
        self._loaded.add(key)

    def total_content_length(self, group_id: str, user_id: str) -> int:
        return sum(
            len(str(message.get("content", "") or ""))
            for message in self.get_history(group_id, user_id)
        )

    def reset(self, group_id: str, user_id: str) -> None:
        if self._store is not None:
            self._store.reset(group_id, user_id)
        self._memory.pop((group_id, user_id), None)
        self._pending.pop((group_id, user_id), None)
        self._loaded.discard((group_id, user_id))

    def build_user_session_memory_context(
        self, group_id: str, user_id: str
    ) -> UserSessionMemoryContext:
        return build_user_session_memory_context(
            self.get_history(group_id, user_id),
            # Uncompacted turns remain visible until they have been summarized.
            # The final request budget can still select a shorter working set.
            recent_turn_limit=max(
                self._history_turns * 2, len(self.get_history(group_id, user_id))
            ),
        )

    def build_reply_context(
        self,
        *,
        current_event: CurrentEventContext,
        group_id: str,
        user_id: str,
        recent_group_window: list[RecentGroupMessageContext] | None = None,
        policy_hints: PolicyHintsContext | None = None,
    ) -> ReplyContext:
        return {
            "current_event": current_event,
            "user_session_memory": self.build_user_session_memory_context(
                group_id, user_id
            ),
            "recent_group_window": list(recent_group_window or []),
            "policy_hints": policy_hints or build_policy_hints_context(),
        }
