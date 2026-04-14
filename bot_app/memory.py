from __future__ import annotations

from collections import defaultdict


class GroupChatMemory:
    """Store per-(group, user) short conversation history in memory."""

    def __init__(self, history_turns: int) -> None:
        # Keep this parameter for compatibility; actual compaction is handled by ContextManager.
        self._history_turns = max(1, history_turns)
        self._memory: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)

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

    def get_history(self, group_id: str, user_id: str) -> list[dict[str, str]]:
        return list(self._memory[(group_id, user_id)])

    def replace_history(self, group_id: str, user_id: str, messages: list[dict[str, str]]) -> None:
        key = (group_id, user_id)
        self._memory[key] = [
            {
                "role": str(item.get("role", "assistant") or "assistant"),
                "content": str(item.get("content", "") or ""),
            }
            for item in messages
            if isinstance(item, dict)
        ]

    def total_content_length(self, group_id: str, user_id: str) -> int:
        return sum(len(str(message.get("content", "") or "")) for message in self._memory[(group_id, user_id)])

    def reset(self, group_id: str, user_id: str) -> None:
        self._memory.pop((group_id, user_id), None)
