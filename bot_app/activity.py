from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date


class ActivityTracker:
    def __init__(self) -> None:
        self._daily_counter: dict[tuple[str, date], Counter[str]] = defaultdict(Counter)

    def record_message(self, group_id: str, user_id: str) -> None:
        key = (group_id, date.today())
        self._daily_counter[key][user_id] += 1

    def get_today_most_active(self, group_id: str, top_n: int = 3) -> list[dict[str, int | str]]:
        key = (group_id, date.today())
        result: list[dict[str, int | str]] = []
        for user_id, count in self._daily_counter[key].most_common(max(1, top_n)):
            result.append({"user_id": user_id, "message_count": count})
        return result
