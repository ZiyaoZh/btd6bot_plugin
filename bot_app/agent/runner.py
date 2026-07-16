from __future__ import annotations

from typing import Callable

from ..context_manager import ContextManager
from ..harness.context import ContextAssembler
from ..harness.image_policy import ImageGenerationPolicy
from ..harness.policies import CompositePolicy, GroundingPolicy
from ..harness.runtime import AgentHarness
from ..harness.tool_executor import ToolExecutor
from ..harness.types import AgentOutcome, ModelTurnHandler, RunRequest
from ..llm_client import LLMClient
from ..logging_utils import log_flow
from ..memory import GroupChatMemory
from ..model.chat_completions import ChatCompletionsModel
from ..model.types import ModelGateway
from ..protocol import EventPayload
from ..settings import AppSettings
from ..tools.base import ToolDependencies
from ..tools.knowledge_service import build_kb_query_plan
from .prompt_manager import PromptManager
from .tool_registry import ToolRegistry
from .types import AgentResponse, ToolCallRecord


class Agent:
    """Public facade for the Model + Harness composition.

    New application code should defer conversation commit until delivery succeeds
    by calling ``run(..., commit=False)`` followed by ``commit_turn``.
    """

    def __init__(
        self,
        llm: LLMClient,
        memory: GroupChatMemory,
        settings: AppSettings,
        tool_registry: ToolRegistry,
        tool_dependencies: ToolDependencies | None = None,
        prompt_manager: PromptManager | None = None,
        context_manager: ContextManager | None = None,
        *,
        model: ModelGateway | None = None,
        tool_executor: ToolExecutor | None = None,
        grounding_policy: GroundingPolicy | None = None,
        image_policy: ImageGenerationPolicy | None = None,
        harness: AgentHarness | None = None,
    ) -> None:
        self._grounding = grounding_policy or GroundingPolicy(
            query_classifier=_build_knowledge_classifier(tool_dependencies)
        )
        run_policy = CompositePolicy(
            self._grounding,
            image_policy or ImageGenerationPolicy(),
        )
        manager = context_manager or ContextManager(llm=llm, settings=settings)
        context = ContextAssembler(
            memory=memory,
            context_manager=manager,
            settings=settings,
            tool_dependencies=tool_dependencies,
        )
        self._harness = harness or AgentHarness(
            model=model or ChatCompletionsModel(llm),
            context=context,
            tools=tool_executor
            or ToolExecutor(
                tool_registry,
                timeout_seconds=settings.tool_timeout_seconds,
                max_result_chars=settings.max_tool_result_chars,
            ),
            prompts=prompt_manager
            or PromptManager(
                settings,
                artifact_store=(
                    tool_dependencies.artifact_store
                    if tool_dependencies is not None
                    else None
                ),
            ),
            policy=run_policy,
            max_tool_rounds=settings.max_tool_rounds,
            max_reply_chars=settings.max_reply_chars,
        )

    async def run(
        self,
        event: EventPayload,
        trace_id: str = "",
        *,
        commit: bool = True,
        on_model_turn: ModelTurnHandler | None = None,
    ) -> AgentResponse:
        request = RunRequest(
            event=event,
            trace_id=trace_id,
            on_model_turn=on_model_turn,
        )
        outcome = await self._harness.run(request)
        self._log_knowledge_summary(event, outcome, trace_id=trace_id)
        if commit and outcome.should_send:
            await self._harness.commit(request, outcome)
        return _to_agent_response(outcome)

    async def commit_turn(
        self,
        event: EventPayload,
        response: AgentResponse,
        *,
        trace_id: str = "",
    ) -> None:
        await self._harness.commit(
            RunRequest(event=event, trace_id=trace_id),
            AgentOutcome(
                text=response.text,
                response_type=response.response_type,
                image_path=response.image_path,
                token_usage=response.token_usage,
                should_send=response.should_send,
                tool_calls=response.tool_calls,
                terminal_reason=response.terminal_reason,
            ),
        )

    def reset_user_context(self, group_id: str, user_id: str) -> None:
        self._harness.reset(group_id, user_id)

    def _log_knowledge_summary(
        self, event: EventPayload, outcome: AgentOutcome, *, trace_id: str
    ) -> None:
        summary = self._grounding.build_summary(
            event=event,
            records=outcome.tool_calls,
            final_text=outcome.text,
        )
        if trace_id and summary is not None:
            log_flow(stage="agent.knowledge_summary", trace_id=trace_id, **summary)

    def _build_knowledge_summary(
        self,
        *,
        event: EventPayload,
        planning_result: object | None = None,
        tool_records: list[ToolCallRecord],
        final_text: str,
    ) -> dict[str, object] | None:
        del planning_result
        return self._grounding.build_summary(
            event=event,
            records=tool_records,
            final_text=final_text,
        )

    def _is_game_knowledge_query(self, text: str) -> bool:
        return self._grounding.is_knowledge_query(text)


def _to_agent_response(outcome: AgentOutcome) -> AgentResponse:
    return AgentResponse(
        text=outcome.text,
        response_type=outcome.response_type,
        image_path=outcome.image_path,
        token_usage=outcome.token_usage,
        should_send=outcome.should_send,
        tool_calls=outcome.tool_calls,
        terminal_reason=outcome.terminal_reason,
    )


def _build_knowledge_classifier(
    dependencies: ToolDependencies | None,
) -> Callable[[str], bool] | None:
    if dependencies is None:
        return None

    def _classify(text: str) -> bool:
        plan = build_kb_query_plan(text, kb_root=dependencies.kb_root)
        return bool(plan.entity_terms or plan.mechanic_terms or plan.upgrade_terms)

    return _classify


# Historical public import retained without keeping the old implementation.
AgentRunner = Agent
