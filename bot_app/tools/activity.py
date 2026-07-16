from __future__ import annotations

from typing import Any

from ..protocol import CommandResultPayload, EventPayload
from .activity_service import (
    build_activity_detail_result,
    build_activity_leaderboard_result,
    build_collection_event_info_result,
)
from .base import ConcreteTool, ToolDependencies, object_schema


def build_activity_tools(dependencies: ToolDependencies) -> list[ConcreteTool]:
    async def get_activity_detail(
        arguments: dict[str, Any], event: EventPayload
    ) -> CommandResultPayload:
        return await build_activity_detail_result(
            api_processor_root=dependencies.api_processor_root,
            arguments=arguments,
            module="tool.activity",
            subprocess_runner=dependencies.subprocess_runner,
        )

    async def get_activity_leaderboard(
        arguments: dict[str, Any], event: EventPayload
    ) -> CommandResultPayload:
        return await build_activity_leaderboard_result(
            api_processor_root=dependencies.api_processor_root,
            arguments=arguments,
            module="tool.activity",
            subprocess_runner=dependencies.subprocess_runner,
        )

    async def get_collection_event_info(
        arguments: dict[str, Any], event: EventPayload
    ) -> CommandResultPayload:
        return await build_collection_event_info_result(
            api_processor_root=dependencies.api_processor_root,
            arguments=arguments,
            module="tool.activity",
            subprocess_runner=dependencies.subprocess_runner,
        )

    return [
        ConcreteTool(
            spec={
                "name": "get_activity_detail",
                "description": "获取 BTD6 指定活动详情，支持竞速、Boss、征程、每日挑战。",
                "parameters": object_schema(
                    {
                        "activity_type": {
                            "type": "string",
                            "enum": ["race", "boss", "odyssey", "daily"],
                            "description": "活动类型。",
                        }
                    },
                    required=["activity_type"],
                ),
            },
            handler=get_activity_detail,
        ),
        ConcreteTool(
            spec={
                "name": "get_activity_leaderboard",
                "description": "获取 BTD6 活动排行榜图片，支持竞速、普通 Boss、精英 Boss。",
                "parameters": object_schema(
                    {
                        "leaderboard_type": {
                            "type": "string",
                            "enum": ["race", "boss-standard", "boss-elite"],
                            "description": "排行榜类型。",
                        }
                    },
                    required=["leaderboard_type"],
                ),
            },
            handler=get_activity_leaderboard,
        ),
        ConcreteTool(
            spec={
                "name": "get_collection_event_info",
                "description": "获取 BTD6 收集活动轮换信息并生成图片。",
                "parameters": object_schema(
                    {
                        "only_upcoming": {
                            "type": "boolean",
                            "description": "是否只查询当前和未来轮换。",
                        }
                    }
                ),
            },
            handler=get_collection_event_info,
        ),
    ]
