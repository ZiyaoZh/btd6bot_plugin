from __future__ import annotations

from typing import Any

from ..context_contracts import (
    build_current_event_context,
    serialize_compact_user_event,
)
from ..context_manager import ContextManager
from ..group_context import build_recent_group_window
from ..memory import GroupChatMemory
from ..logging_utils import log_flow
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

    async def prepare(
        self, event: EventPayload, *, trace_id: str = ""
    ) -> dict[str, object]:
        await self._compact(event, trace_id=trace_id)
        return self._memory.build_reply_context(
            current_event=build_current_event_context(event),
            group_id=event["group_id"],
            user_id=event["user_id"],
            recent_group_window=self._recent_group_window(event),
        )

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
        rows = db.get_recent_group_messages(
            event["group_id"], limit=self._settings.recent_group_window_size
        )
        return build_recent_group_window(
            rows,
            limit=self._settings.recent_group_window_size,
            bot_qq=self._settings.bot_qq,
            bot_name=self._settings.bot_name,
        )
