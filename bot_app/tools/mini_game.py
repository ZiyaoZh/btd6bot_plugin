from __future__ import annotations

from .base import ConcreteTool, ToolRuntime, command_tool, object_schema


def build_mini_game_tools(runtime: ToolRuntime) -> list[ConcreteTool]:
    return [
        command_tool(
            runtime=runtime,
            name="simulate_monkey_box_opening",
            description="模拟 BTD6 收集活动开箱并返回统计；箱子数量不超过 50 时额外生成图片。",
            parameters=object_schema(
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
        )
    ]
