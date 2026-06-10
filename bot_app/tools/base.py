from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..command_processor import CommandProcessor
from ..protocol import CommandResultPayload, EventPayload, IntentPayload
from ..agent.types import ToolHandler, ToolSpec


@dataclass(frozen=True)
class ConcreteTool:
    spec: ToolSpec
    handler: ToolHandler


@dataclass
class ToolRuntime:
    command_processor: CommandProcessor
    db: Any = None
    manual_file: Path | None = None
    manual_text_cache: str = ""

    @classmethod
    def from_command_processor(cls, command_processor: CommandProcessor) -> ToolRuntime:
        return cls(
            command_processor=command_processor,
            db=getattr(command_processor, "_db", None),
            manual_file=getattr(command_processor, "_manual_file", None),
        )


def object_schema(
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


def command_tool(
    runtime: ToolRuntime,
    name: str,
    description: str,
    parameters: dict[str, Any],
) -> ConcreteTool:
    async def handler(
        arguments: dict[str, Any], event: EventPayload
    ) -> CommandResultPayload:
        intent: IntentPayload = {
            "module": "agent",
            "target": "command_processor",
            "intent": name,
            "arguments": arguments,
            "reply": "",
            "reason": "tool_call",
        }
        return await runtime.command_processor.execute(intent, event)

    return ConcreteTool(
        spec={
            "name": name,
            "description": description,
            "parameters": parameters,
        },
        handler=handler,
    )
