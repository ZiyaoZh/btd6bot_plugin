from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from ..agent.types import ToolCallRecord
from ..model.types import ModelToolCall
from ..protocol import CommandResultPayload, EventPayload


@dataclass(frozen=True)
class ModelTurnUpdate:
    """Provider-neutral, publicly displayable result of one model call."""

    index: int
    content: str
    tool_calls: tuple[ModelToolCall, ...] = ()
    model: str = ""
    is_final: bool = False


ModelTurnHandler = Callable[[ModelTurnUpdate], Awaitable[None]]


@dataclass(frozen=True)
class RunRequest:
    event: EventPayload
    trace_id: str = ""
    on_model_turn: ModelTurnHandler | None = field(
        default=None, repr=False, compare=False
    )

    @property
    def session_id(self) -> str:
        return f"{self.event['group_id']}:{self.event['user_id']}"


@dataclass(frozen=True)
class ToolInvocation:
    id: str
    name: str
    arguments: dict[str, Any]
    source: str = "model"
    argument_error: str = ""


@dataclass
class RunState:
    request: RunRequest
    messages: list[dict[str, Any]]
    tool_records: list[ToolCallRecord] = field(default_factory=list)
    total_tokens: int = 0
    model_calls: int = 0
    tool_rounds: int = 0
    last_result: CommandResultPayload | None = None
    last_image_result: CommandResultPayload | None = None
    terminal_reason: str = ""


@dataclass(frozen=True)
class AgentOutcome:
    text: str = ""
    response_type: str = "text"
    image_path: str = ""
    token_usage: int = 0
    should_send: bool = True
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    terminal_reason: str = "final"


class RunPolicy(Protocol):
    def required_tool_calls(
        self, event: EventPayload, records: list[ToolCallRecord]
    ) -> list[ToolInvocation]: ...

    def final_override(
        self, event: EventPayload, records: list[ToolCallRecord]
    ) -> str: ...
