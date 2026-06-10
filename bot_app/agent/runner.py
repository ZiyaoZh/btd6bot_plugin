from __future__ import annotations

import json

from ..llm_client import LLMClient
from ..logging_utils import log_flow
from ..memory import GroupChatMemory
from ..protocol import CommandResultPayload, EventPayload
from ..settings import AppSettings
from ..context_manager import ContextManager
from .prompt_manager import PromptManager
from .tool_registry import ToolRegistry, parse_tool_arguments
from .types import AgentResponse, ToolCallRecord


class AgentRunner:
    def __init__(
        self,
        llm: LLMClient,
        memory: GroupChatMemory,
        settings: AppSettings,
        tool_registry: ToolRegistry,
        prompt_manager: PromptManager | None = None,
        context_manager: ContextManager | None = None,
    ) -> None:
        self._llm = llm
        self._memory = memory
        self._settings = settings
        self._tool_registry = tool_registry
        self._prompt_manager = prompt_manager or PromptManager(settings)
        self._context_manager = context_manager or ContextManager(
            llm=llm, settings=settings
        )

    async def run(self, event: EventPayload, trace_id: str = "") -> AgentResponse:
        await self._compact_if_needed(event, trace_id=trace_id)
        messages = self._memory.build_messages(
            system_prompt=self._prompt_manager.build_system_prompt(event),
            group_id=event["group_id"],
            user_id=event["user_id"],
            user_text=self._build_user_message(event),
        )
        tools = self._tool_registry.to_openai_tools()
        token_usage = 0
        tool_records: list[ToolCallRecord] = []
        last_command_result: CommandResultPayload | None = None

        for round_index in range(self._settings.max_tool_rounds + 1):
            response = await self._llm.chat_completion(
                messages=messages,
                tools=tools,
                trace_id=trace_id,
                purpose="agent_tool_calling",
            )
            token_usage += response.total_tokens
            assistant_message = response.assistant_message
            messages.append(assistant_message)

            if trace_id:
                log_flow(
                    stage="agent.llm_response",
                    trace_id=trace_id,
                    round=round_index + 1,
                    content=response.content,
                    tool_calls=[
                        tool_call.to_log_payload() for tool_call in response.tool_calls
                    ],
                    total_tokens=response.total_tokens,
                )

            if not response.tool_calls:
                text = self._normalize_plain_text(response.content)
                text = text or self._fallback_text(last_command_result)
                response_type = str(
                    (last_command_result or {}).get("response_type", "text") or "text"
                ).lower()
                image_path = str(
                    (last_command_result or {}).get("image_path", "") or ""
                )
                await self._record_turn(event, text, trace_id=trace_id)
                return AgentResponse(
                    text=text[: self._settings.max_reply_chars],
                    response_type=response_type,
                    image_path=image_path,
                    token_usage=token_usage,
                    should_send=bool(text.strip()) or response_type == "image",
                    tool_calls=tool_records,
                )

            for tool_call in response.tool_calls:
                arguments = parse_tool_arguments(tool_call.arguments)
                command_result = await self._tool_registry.execute(
                    tool_call.name, arguments, event
                )
                last_command_result = command_result
                tool_records.append(
                    ToolCallRecord(
                        name=tool_call.name,
                        arguments=arguments,
                        result=command_result,
                    )
                )
                if trace_id:
                    log_flow(
                        stage="agent.tool_result",
                        trace_id=trace_id,
                        tool_call_id=tool_call.id,
                        tool_name=tool_call.name,
                        arguments=arguments,
                        command_result=command_result,
                    )

                if command_result.get("command") == "ignore_message":
                    return AgentResponse(
                        text="",
                        response_type="none",
                        image_path="",
                        token_usage=token_usage,
                        should_send=False,
                        tool_calls=tool_records,
                    )

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_call.name,
                        "content": self._serialize_tool_result(command_result),
                    }
                )

        text = self._fallback_text(last_command_result)
        response_type = str(
            (last_command_result or {}).get("response_type", "text") or "text"
        ).lower()
        image_path = str((last_command_result or {}).get("image_path", "") or "")
        await self._record_turn(event, text, trace_id=trace_id)
        return AgentResponse(
            text=text[: self._settings.max_reply_chars],
            response_type=response_type,
            image_path=image_path,
            token_usage=token_usage,
            should_send=bool(text.strip()) or response_type == "image",
            tool_calls=tool_records,
        )

    def reset_user_context(self, group_id: str, user_id: str) -> None:
        self._memory.reset(group_id, user_id)

    def _build_user_message(self, event: EventPayload) -> str:
        return json.dumps(
            {
                "event": event,
                "instruction": "请处理这条群聊消息。需要事实信息时调用工具；无需回复时调用 ignore_message。",
            },
            ensure_ascii=False,
        )

    async def _record_turn(
        self, event: EventPayload, assistant_text: str, trace_id: str = ""
    ) -> None:
        self._memory.append_turn(
            group_id=event["group_id"],
            user_id=event["user_id"],
            user_text=self._build_user_message(event),
            assistant_text=(assistant_text or "").strip(),
        )
        await self._compact_if_needed(event, trace_id=trace_id)

    async def _compact_if_needed(self, event: EventPayload, trace_id: str = "") -> None:
        await self._context_manager.compact_if_needed(
            memory=self._memory,
            group_id=event["group_id"],
            user_id=event["user_id"],
            trace_id=trace_id,
        )

    def _fallback_text(self, command_result: CommandResultPayload | None) -> str:
        if not command_result:
            return "我暂时不知道怎么回答这个问题。"

        extra_prompt = str(command_result.get("extra_prompt", "") or "").strip()
        message = str(command_result.get("message", "") or "").strip()
        if extra_prompt:
            return message or "工具执行完成。"
        if command_result.get("ok", False):
            return message or "工具执行完成。"
        return message or "工具执行失败，请稍后重试。"

    def _serialize_tool_result(self, result: CommandResultPayload) -> str:
        return json.dumps(result, ensure_ascii=False, default=str)

    def _normalize_plain_text(self, text: str) -> str:
        cleaned = (text or "").strip()
        if not cleaned:
            return ""
        cleaned = cleaned.replace("```", "")
        cleaned = cleaned.replace("**", "")
        cleaned = cleaned.replace("__", "")
        cleaned = cleaned.replace("`", "")

        lines = []
        for raw_line in cleaned.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            while line.startswith("#"):
                line = line[1:].strip()
            lines.append(line)
        return "\n".join(lines)
