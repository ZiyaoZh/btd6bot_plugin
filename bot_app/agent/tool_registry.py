from __future__ import annotations

from typing import Any

from ..protocol import CommandResultPayload, EventPayload
from ..tools import (
    ConcreteTool,
    ToolDependencies,
    build_activity_tools,
    build_artifact_tools,
    build_general_tools,
    build_image_tools,
    build_knowledge_tools,
    build_manual_tools,
    build_mini_game_tools,
    build_social_tools,
    build_web_tools,
    fail_result,
    normalize_result,
)
from ..tools.session_memory import build_memory_tools
from .types import ToolHandler, ToolSpec


class ToolRegistry:
    def __init__(self, dependencies: ToolDependencies) -> None:
        self._dependencies = dependencies
        self._specs: dict[str, ToolSpec] = {}
        self._handlers: dict[str, ToolHandler] = {}
        self._execution_timeouts: dict[str, float] = {}
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

    def get_spec(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def get_execution_timeout_seconds(self, name: str) -> float | None:
        return self._execution_timeouts.get(name)

    async def execute(
        self, name: str, arguments: dict[str, Any], event: EventPayload
    ) -> CommandResultPayload:
        handler = self._handlers.get(name)
        if handler is None:
            return fail_result(
                module="tool_registry",
                command=name,
                data={"arguments": arguments},
                message=f"未知工具: {name}",
                extra_prompt="请改用可用工具，或直接说明暂不支持该能力。",
            )
        result = await handler(arguments, event)
        return normalize_result(result, default_module="tool", default_command=name)

    def _register_tools(self, tools: list[ConcreteTool]) -> None:
        for tool in tools:
            self._register(
                tool.spec,
                tool.handler,
                execution_timeout_seconds=tool.execution_timeout_seconds,
            )

    def _register(
        self,
        spec: ToolSpec,
        handler: ToolHandler,
        *,
        execution_timeout_seconds: float | None = None,
    ) -> None:
        name = spec["name"]
        if name in self._specs:
            raise ValueError(f"重复工具名: {name}")
        self._specs[name] = spec
        self._handlers[name] = handler
        if execution_timeout_seconds is not None:
            self._execution_timeouts[name] = max(
                0.1,
                float(execution_timeout_seconds),
            )

    def _build_tools(self) -> list[ConcreteTool]:
        return [
            *build_memory_tools(self._dependencies),
            *build_artifact_tools(self._dependencies),
            *build_general_tools(self._dependencies),
            *build_image_tools(self._dependencies),
            *build_social_tools(self._dependencies),
            *build_manual_tools(self._dependencies),
            *build_knowledge_tools(self._dependencies),
            *build_activity_tools(self._dependencies),
            *build_mini_game_tools(self._dependencies),
            *build_web_tools(self._dependencies),
        ]
