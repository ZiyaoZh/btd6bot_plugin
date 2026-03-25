from __future__ import annotations

from collections import defaultdict, deque
from typing import Deque


class GroupChatMemory:
    """Store per-(group, user) short conversation history in memory."""

    def __init__(self, history_turns: int) -> None:
        self._max_messages = max(2, history_turns * 2)
        self._memory: dict[tuple[str, str], Deque[dict[str, str]]] = defaultdict(
            lambda: deque(maxlen=self._max_messages)
        )

    def build_messages(
        self,
        system_prompt: str,
        group_id: str,
        user_id: str,
        user_text: str,
    ) -> list[dict[str, str]]:
        key = (group_id, user_id)
        history = list(self._memory[key])
        return [{"role": "system", "content": system_prompt}, *history, {"role": "user", "content": user_text}]

    def append_turn(self, group_id: str, user_id: str, user_text: str, assistant_text: str) -> None:
        key = (group_id, user_id)
        self._memory[key].append({"role": "user", "content": user_text})
        self._memory[key].append({"role": "assistant", "content": assistant_text})

    def reset(self, group_id: str, user_id: str) -> None:
        self._memory.pop((group_id, user_id), None)
