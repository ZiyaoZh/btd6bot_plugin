from __future__ import annotations

from typing import Any

from ..protocol import CommandResultPayload, EventPayload
from .base import ConcreteTool, ToolRuntime, object_schema, ok_result
from .features import get_feature_catalog


def build_general_tools(runtime: ToolRuntime) -> list[ConcreteTool]:
    async def ignore_message(
        arguments: dict[str, Any], event: EventPayload
    ) -> CommandResultPayload:
        return ok_result(
            module="agent",
            command="ignore_message",
            data={"reason": str(arguments.get("reason", "") or "").strip()},
            message="忽略该群聊消息",
            response_type="none",
        )

    async def list_features(
        arguments: dict[str, Any], event: EventPayload
    ) -> CommandResultPayload:
        features = get_feature_catalog()
        return ok_result(
            module="tool.general",
            command="list_features",
            data={
                "feature_count": len(features),
                "features": features,
            },
            message="功能列表已整理完成",
        )

    return [
        ConcreteTool(
            spec={
                "name": "ignore_message",
                "description": "忽略不需要机器人回复的群聊消息。用户明确要求闭嘴、别回复、不要说话时也使用此工具。",
                "parameters": object_schema(
                    {
                        "reason": {
                            "type": "string",
                            "description": "忽略原因，简短说明即可。",
                        }
                    }
                ),
            },
            handler=ignore_message,
        ),
        ConcreteTool(
            spec={
                "name": "list_features",
                "description": "查看机器人当前支持的所有功能。",
                "parameters": object_schema(),
            },
            handler=list_features,
        ),
    ]
