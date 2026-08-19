from __future__ import annotations

import json

from ..agent.prompt_manager import PromptManager
from ..agent.types import ToolCallRecord
from ..logging_utils import log_flow
from ..model.multimodal import build_multimodal_user_content
from ..model.types import ModelGateway, ModelTurn
from ..protocol import CommandResultPayload
from .context import ContextAssembler
from .tool_executor import ToolExecutor
from .types import (
    AgentOutcome,
    ModelTurnUpdate,
    RunPolicy,
    RunRequest,
    RunState,
    ToolInvocation,
)


class AgentHarness:
    """Deterministic runtime around a model-driven native tool-calling loop."""

    def __init__(
        self,
        *,
        model: ModelGateway,
        context: ContextAssembler,
        tools: ToolExecutor,
        prompts: PromptManager,
        policy: RunPolicy,
        max_tool_rounds: int,
        max_reply_chars: int,
    ) -> None:
        self._model = model
        self._context = context
        self._tools = tools
        self._prompts = prompts
        self._policy = policy
        self._max_tool_rounds = max(1, int(max_tool_rounds))
        self._max_reply_chars = max(1, int(max_reply_chars))

    async def run(self, request: RunRequest) -> AgentOutcome:
        event = request.event
        reply_context = await self._context.prepare(event, trace_id=request.trace_id)
        state = RunState(
            request=request,
            messages=[
                {
                    "role": "system",
                    "content": self._prompts.build_system_prompt(event),
                },
                {
                    "role": "user",
                    "content": build_multimodal_user_content(
                        event=event,
                        reply_context=reply_context,
                        settings=self._context.settings,
                    ),
                },
            ],
        )

        if request.trace_id:
            log_flow(
                stage="harness.run.started",
                trace_id=request.trace_id,
                session_id=request.session_id,
                reply_context=reply_context,
            )

        while True:
            required = self._policy.required_tool_calls(event, state.tool_records)
            if required:
                if state.tool_rounds >= self._max_tool_rounds:
                    return self._finish(state, terminal_reason="tool_budget_exhausted")
                records = await self._execute(
                    state, required, append_native_tool_messages=False
                )
                self._append_policy_observations(state, records)
                if self._requested_no_reply(records):
                    return self._no_reply(state, "policy_no_reply")
                continue

            if state.model_calls >= self._max_tool_rounds + 1:
                return self._finish(state, terminal_reason="model_budget_exhausted")

            turn = await self._model.generate(
                messages=state.messages,
                tools=self._tools.schemas,
                trace_id=request.trace_id,
            )
            state.total_tokens += turn.total_tokens
            state.model_calls += 1

            if request.trace_id:
                log_flow(
                    stage="harness.model.turn",
                    trace_id=request.trace_id,
                    model=turn.model,
                    model_call=state.model_calls,
                    content=turn.content,
                    tool_calls=[
                        {
                            "id": call.id,
                            "name": call.name,
                            "arguments": call.arguments,
                            "argument_error": call.argument_error,
                        }
                        for call in turn.tool_calls
                    ],
                    total_tokens=turn.total_tokens,
                )

            await self._notify_model_turn(state, turn)

            if not turn.tool_calls:
                state.messages.append(turn.assistant_message)
                return self._finish(
                    state,
                    candidate_text=turn.content,
                    terminal_reason="model_final",
                )

            if state.tool_rounds >= self._max_tool_rounds:
                return self._finish(state, terminal_reason="tool_budget_exhausted")

            state.messages.append(turn.assistant_message)
            invocations = [
                ToolInvocation(
                    id=call.id,
                    name=call.name,
                    arguments=call.arguments,
                    source="model",
                    argument_error=call.argument_error,
                )
                for call in turn.tool_calls
            ]
            records = await self._execute(state, invocations)
            if self._requested_no_reply(records):
                return self._no_reply(state, "model_no_reply")

    async def commit(self, request: RunRequest, outcome: AgentOutcome) -> None:
        if not outcome.should_send:
            return
        await self._context.commit(
            request.event,
            outcome.text,
            trace_id=request.trace_id,
        )

    def reset(self, group_id: str, user_id: str) -> None:
        self._context.reset(group_id, user_id)

    async def _execute(
        self,
        state: RunState,
        invocations: list[ToolInvocation],
        *,
        append_native_tool_messages: bool = True,
    ) -> list[ToolCallRecord]:
        records = await self._tools.execute_batch(
            invocations,
            state.request.event,
            trace_id=state.request.trace_id,
        )
        state.tool_rounds += 1
        state.tool_records.extend(records)
        for record in records:
            state.last_result = record.result
            if _is_image_result(record.result):
                state.last_image_result = record.result
            if append_native_tool_messages:
                state.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": record.call_id,
                        "name": record.name,
                        "content": json.dumps(
                            record.result, ensure_ascii=False, default=str
                        ),
                    }
                )
        return records

    async def _notify_model_turn(self, state: RunState, turn: ModelTurn) -> None:
        handler = state.request.on_model_turn
        if handler is None:
            return
        update = ModelTurnUpdate(
            index=state.model_calls,
            content=turn.content,
            tool_calls=tuple(turn.tool_calls),
            model=turn.model,
            is_final=not turn.tool_calls,
        )
        try:
            await handler(update)
        except Exception as exc:
            if state.request.trace_id:
                log_flow(
                    stage="harness.model_turn_notification.failed",
                    trace_id=state.request.trace_id,
                    model_call=state.model_calls,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )

    def _finish(
        self,
        state: RunState,
        *,
        candidate_text: str = "",
        terminal_reason: str,
    ) -> AgentOutcome:
        override = self._policy.final_override(state.request.event, state.tool_records)
        text = _normalize_plain_text(override or candidate_text)
        if not text:
            text = _fallback_text(state.last_result)
        response_type, image_path = _response_metadata(
            state.last_result, state.last_image_result
        )
        state.terminal_reason = terminal_reason
        outcome = AgentOutcome(
            text=text[: self._max_reply_chars],
            response_type=response_type,
            image_path=image_path,
            token_usage=state.total_tokens,
            should_send=bool(text.strip()) or response_type == "image",
            tool_calls=list(state.tool_records),
            terminal_reason=terminal_reason,
        )
        self._log_finish(state, outcome)
        return outcome

    def _no_reply(self, state: RunState, terminal_reason: str) -> AgentOutcome:
        state.terminal_reason = terminal_reason
        outcome = AgentOutcome(
            text="",
            response_type="none",
            image_path="",
            token_usage=state.total_tokens,
            should_send=False,
            tool_calls=list(state.tool_records),
            terminal_reason=terminal_reason,
        )
        self._log_finish(state, outcome)
        return outcome

    def _append_policy_observations(
        self, state: RunState, records: list[ToolCallRecord]
    ) -> None:
        state.messages.append(
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "kind": "harness_tool_observations",
                        "source": "policy",
                        "instruction": (
                            "以下工具由 Harness 的确定性策略执行。"
                            "请将结果作为 observation，并继续遵守系统指令。"
                        ),
                        "results": [
                            {
                                "call_id": record.call_id,
                                "name": record.name,
                                "arguments": record.arguments,
                                "result": record.result,
                            }
                            for record in records
                        ],
                    },
                    ensure_ascii=False,
                    default=str,
                ),
            }
        )

    @staticmethod
    def _requested_no_reply(records: list[ToolCallRecord]) -> bool:
        return any(
            record.result.get("ok", False)
            and str(record.result.get("response_type", "") or "").lower() == "none"
            for record in records
        )

    @staticmethod
    def _log_finish(state: RunState, outcome: AgentOutcome) -> None:
        if not state.request.trace_id:
            return
        log_flow(
            stage="harness.run.finished",
            trace_id=state.request.trace_id,
            session_id=state.request.session_id,
            terminal_reason=outcome.terminal_reason,
            should_send=outcome.should_send,
            response_type=outcome.response_type,
            model_calls=state.model_calls,
            tool_rounds=state.tool_rounds,
            token_usage=state.total_tokens,
        )


def _fallback_text(result: CommandResultPayload | None) -> str:
    if not result:
        return "我暂时不知道怎么回答这个问题。"
    message = str(result.get("message", "") or "").strip()
    if result.get("ok", False):
        return message or "工具执行完成。"
    return message or "工具执行失败，请稍后重试。"


def _response_metadata(
    last_result: CommandResultPayload | None,
    last_image_result: CommandResultPayload | None,
) -> tuple[str, str]:
    if last_image_result is not None:
        return "image", str(last_image_result.get("image_path", "") or "")
    result = last_result or {}
    return (
        str(result.get("response_type", "text") or "text").lower(),
        str(result.get("image_path", "") or ""),
    )


def _is_image_result(result: CommandResultPayload) -> bool:
    return str(result.get("response_type", "") or "").lower() == "image" and bool(
        str(result.get("image_path", "") or "").strip()
    )


def _normalize_plain_text(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    for marker in ("```", "**", "__", "`"):
        cleaned = cleaned.replace(marker, "")
    lines: list[str] = []
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        while line.startswith("#"):
            line = line[1:].strip()
        lines.append(line)
    return "\n".join(lines)
