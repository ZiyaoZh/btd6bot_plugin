from __future__ import annotations

import json
from typing import Any

from ..command_processor import CommandProcessor
from ..protocol import CommandResultPayload, EventPayload
from ..tools import (
    ConcreteTool,
    ToolRuntime,
    build_activity_tools,
    build_general_tools,
    build_knowledge_tools,
    build_manual_tools,
    build_mini_game_tools,
    build_social_tools,
)
from .types import ToolHandler, ToolSpec


class ToolRegistry:
    def __init__(self, command_processor: CommandProcessor) -> None:
        self._command_processor = command_processor
        self._runtime = ToolRuntime.from_command_processor(command_processor)
        self._specs: dict[str, ToolSpec] = {}
        self._handlers: dict[str, ToolHandler] = {}
        self._register_tools(self._build_tools())

    def to_openai_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": spec["name"],
                    "description": spec["description"],
                    "parameters": spec["parameters"],
                },
            }
            for spec in self._specs.values()
        ]

    def has_tool(self, name: str) -> bool:
        return name in self._handlers

    async def execute(
        self, name: str, arguments: dict[str, Any], event: EventPayload
    ) -> CommandResultPayload:
        handler = self._handlers.get(name)
        if handler is None:
            return {
                "module": "tool_registry",
                "ok": False,
                "command": name,
                "data": {"arguments": arguments},
                "message": f"未知工具: {name}",
                "extra_prompt": "请改用可用工具，或直接说明暂不支持该能力。",
                "response_type": "text",
                "image_path": "",
            }
        return await handler(arguments, event)

    def _register_tools(self, tools: list[ConcreteTool]) -> None:
        for tool in tools:
            self._register(tool.spec, tool.handler)

    def _register(self, spec: ToolSpec, handler: ToolHandler) -> None:
        name = spec["name"]
        if name in self._specs:
            raise ValueError(f"重复工具名: {name}")
        self._specs[name] = spec
        self._handlers[name] = handler

    def _build_tools(self) -> list[ConcreteTool]:
        return [
            *build_general_tools(self._runtime),
            *build_social_tools(self._runtime),
            *build_manual_tools(self._runtime),
            *build_knowledge_tools(self._runtime),
            *build_activity_tools(self._runtime),
            *build_mini_game_tools(self._runtime),
        ]


def parse_tool_arguments(raw_arguments: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
