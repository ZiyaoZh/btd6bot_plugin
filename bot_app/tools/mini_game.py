from __future__ import annotations

from typing import Any

from ..protocol import CommandResultPayload, EventPayload
from .base import ConcreteTool, ToolDependencies, object_schema
from .mini_game_service import build_monkey_box_opening_result


def build_mini_game_tools(dependencies: ToolDependencies) -> list[ConcreteTool]:
    async def simulate_monkey_box_opening(
        arguments: dict[str, Any], event: EventPayload
    ) -> CommandResultPayload:
        return await build_monkey_box_opening_result(
            chat_monkey_root=dependencies.chat_monkey_root,
            arguments=arguments,
            module="tool.mini_game",
            subprocess_runner=dependencies.subprocess_runner,
            output_dir=dependencies.plugin_root / "data" / "tool_outputs" / "mini_game",
        )

    return [
        ConcreteTool(
            spec={
                "name": "simulate_monkey_box_opening",
                "description": (
                    "实时模拟一次新的 BTD6 收集活动开箱并返回统计；"
                    "每次调用都会实际运行游戏工具，不复用产物清单。"
                    "箱子数量不超过 50 时额外生成图片。"
                ),
                "parameters": object_schema(
                    {
                        "box_type": {
                            "type": "string",
                            "enum": ["木头", "青铜", "白银", "黄金", "钻石"],
                            "description": "箱子类型。",
                        },
                        "box_count": {
                            "type": "integer",
                            "description": "开箱数量，必须为正整数。",
                            "minimum": 1,
                        },
                    },
                    required=["box_type", "box_count"],
                ),
            },
            handler=simulate_monkey_box_opening,
        )
    ]
