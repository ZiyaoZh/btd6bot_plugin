from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ModelToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    argument_error: str = ""


@dataclass(frozen=True)
class ModelTurn:
    content: str
    assistant_message: dict[str, Any]
    tool_calls: list[ModelToolCall] = field(default_factory=list)
    total_tokens: int = 0
    model: str = ""


class ModelGateway(Protocol):
    async def generate(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        trace_id: str,
    ) -> ModelTurn: ...
