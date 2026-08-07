from __future__ import annotations

from typing import Any

from ..image_request import activity_brief_selection_error, classify_image_request
from ..protocol import CommandResultPayload, EventPayload
from ..settings import ACTIVITY_BRIEF_EXECUTION_TIMEOUT_SECONDS
from .activity_service import (
    build_activity_brief_image_result,
    build_activity_detail_result,
    build_activity_leaderboard_result,
    build_collection_event_info_result,
)
from .base import ConcreteTool, ToolDependencies, fail_result, object_schema


def build_activity_tools(dependencies: ToolDependencies) -> list[ConcreteTool]:
    async def get_activity_detail(
        arguments: dict[str, Any], event: EventPayload
    ) -> CommandResultPayload:
        return await build_activity_detail_result(
            api_processor_root=dependencies.api_processor_root,
            arguments=arguments,
            module="tool.activity",
            subprocess_runner=dependencies.subprocess_runner,
            artifact_store=dependencies.artifact_store,
            artifact_ttl_seconds=dependencies.artifact_activity_ttl_seconds,
        )

    async def get_activity_leaderboard(
        arguments: dict[str, Any], event: EventPayload
    ) -> CommandResultPayload:
        return await build_activity_leaderboard_result(
            api_processor_root=dependencies.api_processor_root,
            arguments=arguments,
            module="tool.activity",
            subprocess_runner=dependencies.subprocess_runner,
            artifact_store=dependencies.artifact_store,
            artifact_ttl_seconds=dependencies.artifact_activity_ttl_seconds,
        )

    async def get_activity_brief_image(
        arguments: dict[str, Any], event: EventPayload
    ) -> CommandResultPayload:
        activity_type = str(arguments.get("activity_type", "") or "").strip().lower()
        difficulty = str(arguments.get("difficulty", "hard") or "hard").strip().lower()
        default_scope = "relevant" if activity_type == "all" else "current"
        event_scope = str(
            arguments.get("event_scope", default_scope) or default_scope
        ).strip().lower()
        scheduled_overview = (
            event.get("event_type") == "scheduled_daily_activity_brief"
            and activity_type == "all"
        )
        selection_error = ""
        if not scheduled_overview:
            selection_error = activity_brief_selection_error(
                classify_image_request(str(event.get("text", "") or "")),
                activity_type=activity_type,
                difficulty=difficulty,
                event_scope=event_scope,
            )
        if selection_error:
            return fail_result(
                module="tool.activity",
                command="get_activity_brief_image",
                data={
                    "activity_type": activity_type,
                    "difficulty": difficulty,
                    "event_scope": event_scope,
                    "reason": "activity_selection_mismatch",
                },
                message=selection_error,
                extra_prompt="请按用户要求选择单项活动或 all 总览、范围和征程难度后重试。",
            )
        return await build_activity_brief_image_result(
            api_processor_root=dependencies.api_processor_root,
            arguments=arguments,
            module="tool.activity",
            subprocess_runner=dependencies.subprocess_runner,
            artifact_store=dependencies.artifact_store,
            artifact_ttl_seconds=dependencies.artifact_activity_ttl_seconds,
        )

    async def get_collection_event_info(
        arguments: dict[str, Any], event: EventPayload
    ) -> CommandResultPayload:
        return await build_collection_event_info_result(
            api_processor_root=dependencies.api_processor_root,
            arguments=arguments,
            module="tool.activity",
            subprocess_runner=dependencies.subprocess_runner,
            artifact_store=dependencies.artifact_store,
            artifact_ttl_seconds=dependencies.artifact_activity_ttl_seconds,
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
                        },
                        "difficulty": {
                            "type": "string",
                            "enum": ["easy", "medium", "hard"],
                            "description": "征程难度，默认 hard；其他活动会忽略此参数。",
                        },
                        "event_scope": {
                            "type": "string",
                            "enum": ["current", "upcoming", "ended", "relevant", "latest"],
                            "description": "生命周期范围。current/upcoming/ended 为精确状态，relevant 依次选择进行中、下一期、最近结束；latest 仅用于旧调用兼容。详情默认 latest。",
                        },
                    },
                    required=["activity_type"],
                ),
            },
            handler=get_activity_detail,
        ),
        ConcreteTool(
            spec={
                "name": "get_activity_brief_image",
                "description": "使用 Ninja Kiwi 真实数据和地图资产生成确定性 BTD6 活动情报图。all 生成竞速、Boss、征程、每日挑战的简洁总览图，也支持四类活动的单项详图。事实型活动图必须使用本工具，而不是通用 AI 生图。",
                "parameters": object_schema(
                    {
                        "activity_type": {
                            "type": "string",
                            "enum": ["all", "race", "boss", "odyssey", "daily"],
                            "description": "活动类型；all 表示四类活动总览。",
                        },
                        "difficulty": {
                            "type": "string",
                            "enum": ["easy", "medium", "hard"],
                            "description": "征程难度，默认 hard；all 会将其用于征程摘要。",
                        },
                        "event_scope": {
                            "type": "string",
                            "enum": ["current", "upcoming", "ended", "relevant", "latest"],
                            "description": "current/upcoming/ended 为精确状态；relevant 依次选择进行中、下一期、最近结束；latest 仅兼容旧调用。all 总览默认 relevant，单项默认 current。",
                        },
                    },
                    required=["activity_type"],
                ),
            },
            handler=get_activity_brief_image,
            execution_timeout_seconds=ACTIVITY_BRIEF_EXECUTION_TIMEOUT_SECONDS,
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
