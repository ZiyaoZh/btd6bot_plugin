from __future__ import annotations

from .base import ConcreteTool, ToolRuntime, command_tool, object_schema


def build_activity_tools(runtime: ToolRuntime) -> list[ConcreteTool]:
    return [
        command_tool(
            runtime=runtime,
            name="get_activity_detail",
            description="获取 BTD6 指定活动详情，支持竞速、Boss、征程、每日挑战。",
            parameters=object_schema(
                {
                    "activity_type": {
                        "type": "string",
                        "enum": ["race", "boss", "odyssey", "daily"],
                        "description": "活动类型。",
                    }
                },
                required=["activity_type"],
            ),
        ),
        command_tool(
            runtime=runtime,
            name="get_activity_leaderboard",
            description="获取 BTD6 活动排行榜图片，支持竞速、普通 Boss、精英 Boss。",
            parameters=object_schema(
                {
                    "leaderboard_type": {
                        "type": "string",
                        "enum": ["race", "boss-standard", "boss-elite"],
                        "description": "排行榜类型。",
                    }
                },
                required=["leaderboard_type"],
            ),
        ),
        command_tool(
            runtime=runtime,
            name="get_collection_event_info",
            description="获取 BTD6 收集活动轮换信息并生成图片。",
            parameters=object_schema(
                {
                    "only_upcoming": {
                        "type": "boolean",
                        "description": "是否只查询当前和未来轮换。",
                    }
                }
            ),
        ),
    ]
