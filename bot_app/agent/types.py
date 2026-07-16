from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TypedDict

from ..protocol import CommandResultPayload, EventPayload


ToolHandler = Callable[[dict[str, Any], EventPayload], Awaitable[CommandResultPayload]]


class ToolSpec(TypedDict):
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCallRecord:
    name: str
    arguments: dict[str, Any]
    result: CommandResultPayload
    call_id: str = ""
    source: str = "model"


@dataclass(frozen=True)
class AgentResponse:
    """Compatibility response type exposed by the historical AgentRunner API."""

    text: str = ""
    response_type: str = "text"
    image_path: str = ""
    token_usage: int = 0
    should_send: bool = True
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    terminal_reason: str = "final"
