from __future__ import annotations

from typing import Any

from ..protocol import CommandResultPayload, EventPayload
from .base import ConcreteTool, ToolRuntime, object_schema
from .social_service import (
    build_summarize_recent_messages_result,
    build_today_most_active_result,
)


def build_social_tools(runtime: ToolRuntime) -> list[ConcreteTool]:
    async def today_most_active(
        arguments: dict[str, Any], event: EventPayload
    ) -> CommandResultPayload:
        return build_today_most_active_result(
            db=runtime.db,
            event=event,
            arguments=arguments,
            module="tool.social",
        )

    async def summarize_recent_messages(
        arguments: dict[str, Any], event: EventPayload
    ) -> CommandResultPayload:
        return build_summarize_recent_messages_result(
            db=runtime.db,
            event=event,
            arguments=arguments,
            module="tool.social",
        )

    return [
        ConcreteTool(
            spec={
                "name": "today_most_active",
                "description": "统计今天群里最活跃的成员。",
                "parameters": object_schema(
                    {
                        "top_n": {
                            "type": "integer",
                            "description": "返回前 N 名，默认 3。",
                            "minimum": 1,
                            "maximum": 20,
                        }
                    }
                ),
            },
            handler=today_most_active,
        ),
        ConcreteTool(
            spec={
                "name": "summarize_recent_messages",
                "description": "读取最近群聊消息并总结主要话题、共识分歧和待办事项。",
                "parameters": object_schema(
                    {
                        "top_n": {
                            "type": "integer",
                            "description": "读取最近 N 条消息，默认 30，范围 5 到 200。",
                            "minimum": 5,
                            "maximum": 200,
                        }
                    }
                ),
            },
            handler=summarize_recent_messages,
        ),
    ]
