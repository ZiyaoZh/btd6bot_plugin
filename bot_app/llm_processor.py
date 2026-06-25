"""Compatibility facade for the historical two-step LLM router.

The old intent_router -> tool_selector -> command_processor pipeline produced
its own planning/rendering log vocabulary and could diverge from AgentRunner.
Keep this import path for external callers, but route executable work through
AgentRunner only.
"""

from __future__ import annotations

from .agent.runner import AgentRunner
from .agent.tool_registry import ToolRegistry
from .agent.types import AgentResponse
from .context_manager import ContextManager
from .llm_client import LLMClient
from .logging_utils import log_flow
from .memory import GroupChatMemory
from .protocol import CommandResultPayload, EventPayload, IntentPayload
from .settings import AppSettings
from .tools.base import ToolRuntime


class LegacyLLMPipelineDisabled(RuntimeError):
    """Raised when code tries to execute the removed legacy LLM pipeline."""


class LLMMessageProcessor:
    """Backward-compatible wrapper around AgentRunner.

    Historical callers may still import ``LLMMessageProcessor``. They should now
    either call ``run()`` or construct/use ``AgentRunner`` directly. The legacy
    step-by-step methods are intentionally disabled so they cannot silently
    emit the old ``agent_tool_calling`` / ``agent.llm_response`` style flow.
    """

    def __init__(
        self,
        llm: LLMClient,
        memory: GroupChatMemory,
        settings: AppSettings,
        context_manager: ContextManager | None = None,
        *,
        tool_registry: ToolRegistry | None = None,
        tool_runtime: ToolRuntime | None = None,
        agent_runner: AgentRunner | None = None,
    ) -> None:
        self._llm = llm
        self._memory = memory
        self._settings = settings
        self._context_manager = context_manager
        self._agent_runner = agent_runner

        if self._agent_runner is None and tool_registry is not None:
            self._agent_runner = AgentRunner(
                llm=llm,
                memory=memory,
                settings=settings,
                tool_registry=tool_registry,
                tool_runtime=tool_runtime,
                context_manager=context_manager,
            )

    async def run(self, event: EventPayload, trace_id: str = "") -> AgentResponse:
        """Run the current AgentRunner pipeline from the legacy import path."""

        if self._agent_runner is None:
            self._raise_disabled(
                "LLMMessageProcessor.run requires agent_runner or tool_registry. "
                "Create AgentRunner with ToolRegistry/ToolRuntime and call it "
                "directly, or pass those objects into this compatibility wrapper.",
                trace_id=trace_id,
            )

        return await self._agent_runner.run(event, trace_id=trace_id)

    async def record_tool_turn(
        self, event: EventPayload, assistant_text: str, trace_id: str = ""
    ) -> None:
        self._memory.append_turn(
            group_id=event["group_id"],
            user_id=event["user_id"],
            user_text=str(event.get("text", "") or ""),
            assistant_text=(assistant_text or "").strip(),
        )
        manager = self._context_manager
        if manager is not None:
            await manager.compact_if_needed(
                memory=self._memory,
                group_id=event["group_id"],
                user_id=event["user_id"],
                trace_id=trace_id,
            )

    async def plan_intent(
        self, event: EventPayload, trace_id: str = ""
    ) -> IntentPayload:
        self._raise_disabled(
            "plan_intent was part of the removed legacy LLM pipeline.",
            trace_id=trace_id,
        )

    async def chat_reply(self, event: EventPayload, trace_id: str = "") -> str:
        self._raise_disabled(
            "chat_reply was part of the removed legacy LLM pipeline.",
            trace_id=trace_id,
        )

    async def render_command_result(
        self,
        event: EventPayload,
        intent: IntentPayload,
        command_result: CommandResultPayload,
        trace_id: str = "",
    ) -> str:
        self._raise_disabled(
            "render_command_result was part of the removed legacy LLM pipeline.",
            trace_id=trace_id,
        )

    async def decide_next_tool_step(
        self,
        event: EventPayload,
        tool_history: list[dict],
        trace_id: str = "",
    ) -> dict:
        self._raise_disabled(
            "decide_next_tool_step was part of the removed legacy LLM pipeline.",
            trace_id=trace_id,
        )

    def reset_user_context(self, group_id: str, user_id: str) -> None:
        self._memory.reset(group_id, user_id)

    def _raise_disabled(self, message: str, *, trace_id: str = "") -> None:
        if trace_id:
            log_flow(
                stage="legacy_pipeline.blocked",
                trace_id=trace_id,
                component="LLMMessageProcessor",
                message=message,
            )
        raise LegacyLLMPipelineDisabled(message)
