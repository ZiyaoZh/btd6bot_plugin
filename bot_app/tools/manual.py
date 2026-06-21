from __future__ import annotations

from typing import Any

from ..protocol import CommandResultPayload, EventPayload
from .base import ConcreteTool, ToolRuntime, object_schema
from .manual_service import build_script_manual_query_result


def build_manual_tools(runtime: ToolRuntime) -> list[ConcreteTool]:
    async def script_manual_query(
        arguments: dict[str, Any], event: EventPayload
    ) -> CommandResultPayload:
        result, manual_text = build_script_manual_query_result(
            manual_file=runtime.manual_file,
            manual_text_cache=runtime.manual_text_cache,
            event=event,
            arguments=arguments,
            module="tool.manual",
        )
        runtime.manual_text_cache = manual_text
        return result

    return [
        ConcreteTool(
            spec={
                "name": "script_manual_query",
                "description": "检索脚本程序说明书，并基于说明书回答脚本启动、配置、刷收集、刷黑框等问题。",
                "parameters": object_schema(
                    {
                        "query": {
                            "type": "string",
                            "description": "用户关于脚本说明书的问题或关键词。",
                        }
                    },
                    required=["query"],
                ),
            },
            handler=script_manual_query,
        )
    ]
