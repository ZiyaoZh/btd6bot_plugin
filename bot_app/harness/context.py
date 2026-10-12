from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from ..context_contracts import (
    build_current_event_context,
    serialize_compact_user_event,
)
from ..context_manager import ContextManager
from ..context_selection import evidence_excerpt, query_terms, relevance
from ..group_context import build_recent_group_window, exclude_message_id
from ..logging_utils import log_flow
from ..memory import GroupChatMemory
from ..protocol import EventPayload
from ..settings import AppSettings
from ..tools.base import ToolDependencies


class ContextAssembler:
    def __init__(
        self,
        *,
        memory: GroupChatMemory,
        context_manager: ContextManager,
        settings: AppSettings,
        tool_dependencies: ToolDependencies | None = None,
    ) -> None:
        self._memory = memory
        self._context_manager = context_manager
        self._settings = settings
        self._tool_dependencies = tool_dependencies

    @property
    def settings(self) -> AppSettings:
        return self._settings

    async def prepare(
        self, event: EventPayload, *, trace_id: str = ""
    ) -> dict[str, object]:
        self._memory.begin_turn(event["group_id"], event["user_id"])
        try:
            await self._compact(event, trace_id=trace_id)
        except Exception as exc:  # noqa: BLE001 - compaction must not prevent the main reply
            log_flow(
                "context.compaction_failed_before_reply",
                trace_id,
                error_type=type(exc).__name__,
            )
        context = self._memory.build_reply_context(
            current_event=build_current_event_context(event),
            group_id=event["group_id"],
            user_id=event["user_id"],
            recent_group_window=self._recent_group_window(event),
        )
        session_ids = {
            turn["message_id"]
            for turn in context["user_session_memory"]["recent_turns"]
            if turn.get("message_id")
        }
        context["recent_group_window"] = [
            row
            for row in context["recent_group_window"]
            if not row.get("message_id") or row["message_id"] not in session_ids
        ]
        context["retrieved_memory"] = self._memory.recall(
            event["group_id"], event["user_id"], event["text"]
        )
        return context

    async def commit(
        self, event: EventPayload, assistant_text: str, *, trace_id: str = ""
    ) -> None:
        self._memory.append_turn(
            group_id=event["group_id"],
            user_id=event["user_id"],
            user_text=serialize_compact_user_event(event),
            assistant_text=(assistant_text or "").strip(),
        )
        try:
            await self._compact(event, trace_id=trace_id)
        except Exception as exc:
            if trace_id:
                log_flow(
                    stage="context.compaction_failed_after_commit",
                    trace_id=trace_id,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )

    def reset(self, group_id: str, user_id: str) -> None:
        self._memory.reset(group_id, user_id)

    async def _compact(self, event: EventPayload, *, trace_id: str) -> None:
        await self._context_manager.compact_if_needed(
            memory=self._memory,
            group_id=event["group_id"],
            user_id=event["user_id"],
            trace_id=trace_id,
        )

    def _recent_group_window(self, event: EventPayload) -> list[dict[str, Any]]:
        db = self._tool_dependencies.db if self._tool_dependencies is not None else None
        if db is None:
            return []
        limit = self._settings.recent_group_window_size
        rows = db.get_recent_group_messages(event["group_id"], limit=limit * 3 + 1)
        current_message_id = str(event.get("message_id", "") or "")
        rows = exclude_message_id(rows, current_message_id)
        window = build_recent_group_window(
            rows,
            limit=limit * 3,
            bot_qq=self._settings.bot_qq,
            bot_name=self._settings.bot_name,
        )
        now = datetime.now(UTC)
        fresh = []
        for item in window:
            try:
                stamp = datetime.fromisoformat(str(item.get("created_at", "")))
                stamp = stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp
                if (
                    (now - stamp).total_seconds()
                    > self._settings.context_group_max_age_seconds
                    and item.get("message_id") != event.get("reply_to_message_id")
                ):
                    continue
            except ValueError:
                pass  # Legacy records without timestamps remain readable.
            fresh.append(item)
        terms = query_terms(event["text"])
        ranked = sorted(
            enumerate(fresh),
            key=lambda pair: (
                pair[1].get("message_id") == event.get("reply_to_message_id"),
                pair[0] >= len(fresh) - 2,
                relevance(pair[1]["cleaned_text"], terms),
                pair[0],
            ),
            reverse=True,
        )
        selected = []
        used = 0
        for index, item in ranked[:limit]:
            item["cleaned_text"] = evidence_excerpt(
                item["cleaned_text"], event["text"], 1000
            )[0]
            cost = len(json.dumps(item, ensure_ascii=False))
            if used + cost <= self._settings.context_group_max_chars:
                selected.append((index, item))
                used += cost
        return [item for _, item in sorted(selected)]
