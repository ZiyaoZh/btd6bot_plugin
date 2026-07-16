from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from ..llm_client import LLMClient
from .types import ModelToolCall, ModelTurn


class ChatCompletionsModel:
    """Translate the OpenAI-compatible wire format into harness model turns."""

    def __init__(self, client: LLMClient) -> None:
        self._client = client

    async def generate(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        trace_id: str,
    ) -> ModelTurn:
        response = await self._client.chat_completion(
            messages=deepcopy(messages),
            tools=tools,
            tool_choice="auto",
            trace_id=trace_id,
            purpose="agent_turn",
            temperature=0.3,
        )
        calls: list[ModelToolCall] = []
        for call in response.tool_calls:
            arguments, argument_error = _parse_arguments(call.arguments)
            calls.append(
                ModelToolCall(
                    id=call.id,
                    name=call.name,
                    arguments=arguments,
                    argument_error=argument_error,
                )
            )
        return ModelTurn(
            content=response.content,
            assistant_message=response.assistant_message,
            tool_calls=calls,
            total_tokens=response.total_tokens,
            model=response.model,
        )


def _parse_arguments(raw: str) -> tuple[dict[str, Any], str]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        return {}, f"invalid JSON arguments: {exc.msg}"
    if not isinstance(value, dict):
        return {}, "tool arguments must be a JSON object"
    return value, ""
