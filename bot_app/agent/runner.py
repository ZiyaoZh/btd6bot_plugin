from __future__ import annotations

import json

from ..context_contracts import (
    build_current_event_context,
    serialize_compact_user_event,
)
from ..group_context import build_recent_group_window
from ..llm_client import LLMClient
from ..logging_utils import log_flow
from ..memory import GroupChatMemory
from ..protocol import CommandResultPayload, EventPayload
from ..settings import AppSettings
from ..context_manager import ContextManager
from ..tools.base import ToolRuntime
from .prompt_manager import PromptManager
from .tool_registry import ToolRegistry, parse_tool_arguments
from .types import (
    AgentResponse,
    PlanningResult,
    PlanningToolCall,
    ToolCallRecord,
)


class AgentRunner:
    def __init__(
        self,
        llm: LLMClient,
        memory: GroupChatMemory,
        settings: AppSettings,
        tool_registry: ToolRegistry,
        tool_runtime: ToolRuntime | None = None,
        prompt_manager: PromptManager | None = None,
        context_manager: ContextManager | None = None,
    ) -> None:
        self._llm = llm
        self._memory = memory
        self._settings = settings
        self._tool_registry = tool_registry
        self._tool_runtime = tool_runtime
        self._prompt_manager = prompt_manager or PromptManager(settings)
        self._context_manager = context_manager or ContextManager(
            llm=llm, settings=settings
        )

    async def run(self, event: EventPayload, trace_id: str = "") -> AgentResponse:
        await self._compact_if_needed(event, trace_id=trace_id)
        reply_context = self._build_reply_context(event)
        is_knowledge_query = self._is_game_knowledge_query(event["text"])
        token_usage = 0
        tool_records: list[ToolCallRecord] = []
        last_command_result: CommandResultPayload | None = None
        last_image_result: CommandResultPayload | None = None
        planning_result: PlanningResult | None = None

        for round_index in range(self._settings.max_tool_rounds + 1):
            planning_result, planning_tokens = await self._plan_next_step(
                event=event,
                reply_context=reply_context,
                tool_records=tool_records,
                trace_id=trace_id,
                round_index=round_index,
            )
            token_usage += planning_tokens
            planning_result = self._apply_knowledge_hard_rule(
                event=event,
                planning_result=planning_result,
                tool_records=tool_records,
                is_knowledge_query=is_knowledge_query,
            )

            if planning_result.decision == "ignore":
                if trace_id:
                    log_flow(
                        stage="agent.plan_result",
                        trace_id=trace_id,
                        round=round_index + 1,
                        decision=planning_result.decision,
                        reason=planning_result.reason,
                    )
                return AgentResponse(
                    text="",
                    response_type="none",
                    image_path="",
                    token_usage=token_usage,
                    should_send=False,
                    tool_calls=tool_records,
                )

            if planning_result.decision == "reply_direct":
                if trace_id:
                    log_flow(
                        stage="agent.plan_result",
                        trace_id=trace_id,
                        round=round_index + 1,
                        decision=planning_result.decision,
                        reason=planning_result.reason,
                        draft_reply=planning_result.draft_reply,
                    )
                text = self._normalize_plain_text(planning_result.draft_reply)
                text = text or self._fallback_text(last_command_result)
                self._log_knowledge_summary(
                    event=event,
                    trace_id=trace_id,
                    planning_result=planning_result,
                    tool_records=tool_records,
                    final_text=text,
                )
                await self._record_turn(event, text, trace_id=trace_id)
                return AgentResponse(
                    text=text[: self._settings.max_reply_chars],
                    response_type="text",
                    image_path="",
                    token_usage=token_usage,
                    should_send=bool(text.strip()),
                    tool_calls=tool_records,
                )

            if planning_result.decision == "final":
                if trace_id:
                    log_flow(
                        stage="agent.plan_result",
                        trace_id=trace_id,
                        round=round_index + 1,
                        decision=planning_result.decision,
                        reason=planning_result.reason,
                    )
                text, rendering_tokens = await self._render_final_response(
                    event=event,
                    reply_context=reply_context,
                    planning_result=planning_result,
                    tool_records=tool_records,
                    trace_id=trace_id,
                )
                token_usage += rendering_tokens
                text = text or self._fallback_text(last_command_result)
                response_type, image_path = self._response_metadata(
                    last_command_result=last_command_result,
                    last_image_result=last_image_result,
                )
                self._log_knowledge_summary(
                    event=event,
                    trace_id=trace_id,
                    planning_result=planning_result,
                    tool_records=tool_records,
                    final_text=text,
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

            if trace_id:
                log_flow(
                    stage="agent.plan_result",
                    trace_id=trace_id,
                    round=round_index + 1,
                    decision=planning_result.decision,
                    reason=planning_result.reason,
                    tool_calls=[
                        {"name": item.name, "arguments": item.arguments}
                        for item in planning_result.tool_calls
                    ],
                    reply_context=reply_context if round_index == 0 else None,
                )

            for planned_tool_call in planning_result.tool_calls:
                command_result = await self._tool_registry.execute(
                    planned_tool_call.name, planned_tool_call.arguments, event
                )
                last_command_result = command_result
                if self._is_image_result(command_result):
                    last_image_result = command_result
                tool_records.append(
                    ToolCallRecord(
                        name=planned_tool_call.name,
                        arguments=planned_tool_call.arguments,
                        result=command_result,
                    )
                )
                if trace_id:
                    log_flow(
                        stage="agent.tool_result",
                        trace_id=trace_id,
                        tool_name=planned_tool_call.name,
                        arguments=planned_tool_call.arguments,
                        command_result=command_result,
                    )

        text = self._fallback_text(last_command_result)
        response_type, image_path = self._response_metadata(
            last_command_result=last_command_result,
            last_image_result=last_image_result,
        )
        self._log_knowledge_summary(
            event=event,
            trace_id=trace_id,
            planning_result=planning_result,
            tool_records=tool_records,
            final_text=text,
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

    async def _plan_next_step(
        self,
        *,
        event: EventPayload,
        reply_context: dict[str, object],
        tool_records: list[ToolCallRecord],
        trace_id: str,
        round_index: int,
    ) -> tuple[PlanningResult, int]:
        planning_messages = [
            {
                "role": "system",
                "content": self._prompt_manager.build_planning_prompt(event),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": "agent_planning",
                        "reply_context": reply_context,
                        "tool_history": [
                            {
                                "name": record.name,
                                "arguments": record.arguments,
                                "result": record.result,
                            }
                            for record in tool_records
                        ],
                        "round": round_index + 1,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        response = await self._llm.chat_completion(
            messages=planning_messages,
            trace_id=trace_id,
            purpose="agent_planning",
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        return self._parse_planning_result(response.content), response.total_tokens

    def reset_user_context(self, group_id: str, user_id: str) -> None:
        self._memory.reset(group_id, user_id)

    def _build_user_message(self, event: EventPayload) -> str:
        return serialize_compact_user_event(event)

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

    def _response_metadata(
        self,
        *,
        last_command_result: CommandResultPayload | None,
        last_image_result: CommandResultPayload | None,
    ) -> tuple[str, str]:
        if last_image_result is not None:
            return "image", str(last_image_result.get("image_path", "") or "")
        response_type = str(
            (last_command_result or {}).get("response_type", "text") or "text"
        ).lower()
        image_path = str((last_command_result or {}).get("image_path", "") or "")
        return response_type, image_path

    def _is_image_result(self, command_result: CommandResultPayload) -> bool:
        return str(
            command_result.get("response_type", "") or ""
        ).lower() == "image" and bool(
            str(command_result.get("image_path", "") or "").strip()
        )

    def _serialize_tool_result(self, result: CommandResultPayload) -> str:
        return json.dumps(result, ensure_ascii=False, default=str)

    async def _render_final_response(
        self,
        *,
        event: EventPayload,
        reply_context: dict[str, object],
        planning_result: PlanningResult,
        tool_records: list[ToolCallRecord],
        trace_id: str,
    ) -> tuple[str, int]:
        aggregated_extra_prompt = "\n".join(
            str(record.result.get("extra_prompt", "") or "").strip()
            for record in tool_records
            if str(record.result.get("extra_prompt", "") or "").strip()
        ).strip()
        rendering_messages = [
            {
                "role": "system",
                "content": self._prompt_manager.build_rendering_prompt(event),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": "agent_rendering",
                        "reply_context": reply_context,
                        "planning_result": {
                            "decision": planning_result.decision,
                            "reason": planning_result.reason,
                            "draft_reply": planning_result.draft_reply,
                        },
                        "tool_results": [
                            {
                                "name": record.name,
                                "arguments": record.arguments,
                                "result": record.result,
                            }
                            for record in tool_records
                        ],
                        "extra_prompt": aggregated_extra_prompt,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        response = await self._llm.chat_completion(
            messages=rendering_messages,
            trace_id=trace_id,
            purpose="agent_rendering",
            temperature=0.4,
        )
        if trace_id:
            log_flow(
                stage="agent.render_result",
                trace_id=trace_id,
                content=response.content,
                total_tokens=response.total_tokens,
            )
        return self._normalize_plain_text(response.content), response.total_tokens

    def _build_reply_context(self, event: EventPayload) -> dict[str, object]:
        recent_group_window = self._load_recent_group_window(event)
        return self._memory.build_reply_context(
            current_event=build_current_event_context(event),
            group_id=event["group_id"],
            user_id=event["user_id"],
            recent_group_window=recent_group_window,
        )

    def _load_recent_group_window(self, event: EventPayload) -> list[dict[str, object]]:
        db = self._tool_runtime.db if self._tool_runtime is not None else None
        if db is None:
            return []

        rows = db.get_recent_group_messages(
            event["group_id"],
            limit=self._settings.recent_group_window_size,
        )
        return build_recent_group_window(
            rows,
            limit=self._settings.recent_group_window_size,
            bot_qq=self._settings.bot_qq,
            bot_name=self._settings.bot_name,
        )

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

    def _parse_planning_result(self, raw: str) -> PlanningResult:
        payload = self._safe_parse_json_object(raw)
        decision = str(payload.get("decision", "reply_direct") or "reply_direct").strip()
        if decision not in {"ignore", "reply_direct", "call_tools", "final"}:
            decision = "reply_direct"

        tool_calls: list[PlanningToolCall] = []
        raw_tool_calls = payload.get("tool_calls", [])
        if isinstance(raw_tool_calls, list):
            for item in raw_tool_calls:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "") or "").strip()
                arguments = item.get("arguments", {})
                if not name or not self._tool_registry.has_tool(name):
                    continue
                if not isinstance(arguments, dict):
                    arguments = parse_tool_arguments(str(arguments or "{}"))
                tool_calls.append(PlanningToolCall(name=name, arguments=arguments))

        if decision == "call_tools" and not tool_calls:
            decision = "final"

        return PlanningResult(
            decision=decision,
            reason=str(payload.get("reason", "") or "").strip(),
            draft_reply=str(payload.get("draft_reply", "") or "").strip(),
            tool_calls=tool_calls,
        )

    def _apply_knowledge_hard_rule(
        self,
        *,
        event: EventPayload,
        planning_result: PlanningResult,
        tool_records: list[ToolCallRecord],
        is_knowledge_query: bool,
    ) -> PlanningResult:
        if not is_knowledge_query:
            return planning_result

        search_records = [
            record for record in tool_records if record.name == "search_game_knowledge"
        ]
        read_records = [
            record for record in tool_records if record.name == "read_game_knowledge_file"
        ]
        last_search = search_records[-1].result if search_records else None
        last_search_data = (last_search or {}).get("data", {}) or {}
        matched_count = self._extract_int(last_search_data.get("matched_count"), default=0)
        matched_paths = self._extract_string_list(last_search_data.get("matched_paths"))

        if not search_records:
            return PlanningResult(
                decision="call_tools",
                reason="知识问题必须先检索知识库。",
                tool_calls=[
                    PlanningToolCall(
                        name="search_game_knowledge",
                        arguments={"query": event["text"]},
                    )
                ],
            )

        if matched_count > 0 and not read_records:
            return PlanningResult(
                decision="call_tools",
                reason="知识库已命中，必须先读取至少一个候选文件。",
                tool_calls=[
                    PlanningToolCall(
                        name="read_game_knowledge_file",
                        arguments={
                            "file_path": matched_paths[0],
                            "query": event["text"],
                        },
                    )
                ],
            )

        if matched_count <= 0 and planning_result.decision == "reply_direct":
            return PlanningResult(
                decision="final",
                reason="知识库未命中，需走统一未命中兜底。",
                draft_reply=planning_result.draft_reply,
                tool_calls=planning_result.tool_calls,
            )

        return planning_result

    def _is_game_knowledge_query(self, text: str) -> bool:
        normalized = str(text or "").lower()
        keywords = (
            "数据",
            "机制",
            "属性",
            "伤害",
            "攻速",
            "价格",
            "射程",
            "穿透",
            "升级",
            "路线",
            "ddt",
            "能不能",
            "抓",
            "野兽之主",
            "人鱼猴",
            "beast handler",
            "mermaid monkey",
        )
        return any(keyword in normalized for keyword in keywords)

    def _log_knowledge_summary(
        self,
        *,
        event: EventPayload,
        trace_id: str,
        planning_result: PlanningResult | None,
        tool_records: list[ToolCallRecord],
        final_text: str,
    ) -> None:
        summary = self._build_knowledge_summary(
            event=event,
            planning_result=planning_result,
            tool_records=tool_records,
            final_text=final_text,
        )
        if not trace_id or summary is None:
            return
        log_flow(stage="agent.knowledge_summary", trace_id=trace_id, **summary)

    def _build_knowledge_summary(
        self,
        *,
        event: EventPayload,
        planning_result: PlanningResult | None,
        tool_records: list[ToolCallRecord],
        final_text: str,
    ) -> dict[str, object] | None:
        search_records = [
            record for record in tool_records if record.name == "search_game_knowledge"
        ]
        read_records = [
            record for record in tool_records if record.name == "read_game_knowledge_file"
        ]
        planned_knowledge = bool(
            planning_result
            and any(
                item.name in {"search_game_knowledge", "read_game_knowledge_file"}
                for item in planning_result.tool_calls
            )
        )
        if not search_records and not read_records and not planned_knowledge:
            return None

        last_search = search_records[-1].result if search_records else None
        last_search_data = (last_search or {}).get("data", {}) or {}
        matched_count = self._extract_int(last_search_data.get("matched_count"), default=0)
        matched_paths = self._extract_string_list(last_search_data.get("matched_paths"))
        read_paths: list[str] = []
        successful_read_paths: list[str] = []
        total_content_chars = 0
        for record in read_records:
            data = record.result.get("data", {}) or {}
            file_path = str(data.get("file_path", "") or "").strip()
            if file_path:
                read_paths.append(file_path)
                if record.result.get("ok", False):
                    successful_read_paths.append(file_path)
            total_content_chars += self._extract_int(
                data.get("content_chars", data.get("chars")),
                default=0,
            )

        answer_grounded = bool(successful_read_paths)
        fallback_reason = ""
        if search_records:
            if not last_search or not last_search.get("ok", False):
                fallback_reason = "knowledge_search_failed"
            elif matched_count <= 0:
                fallback_reason = "knowledge_not_found"
            elif not read_records:
                fallback_reason = "search_hit_but_not_read"
            elif not successful_read_paths:
                fallback_reason = "knowledge_read_failed"
        elif read_records and not successful_read_paths:
            fallback_reason = "knowledge_read_failed"

        return {
            "event_text": event["text"],
            "knowledge_query": str(last_search_data.get("knowledge_query", "") or "").strip(),
            "knowledge_query_raw": str(
                last_search_data.get("knowledge_query_raw", "") or ""
            ).strip(),
            "matched_count": matched_count,
            "matched_paths": matched_paths,
            "read_count": len(read_paths),
            "read_paths": read_paths,
            "successful_read_count": len(successful_read_paths),
            "successful_read_paths": successful_read_paths,
            "answer_grounded": answer_grounded,
            "fallback_reason": fallback_reason,
            "final_response_has_text": bool((final_text or "").strip()),
            "final_response_preview": (final_text or "").strip()[:120],
            "content_chars": total_content_chars,
        }

    def _extract_int(self, value: object, *, default: int = 0) -> int:
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default

    def _extract_string_list(self, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        result: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text:
                result.append(text)
        return result

    def _safe_parse_json_object(self, raw: str) -> dict[str, object]:
        text = (raw or "").strip()
        if not text:
            return {}
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start == -1 or end == -1 or end <= start:
                return {}
            try:
                payload = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return {}
        return payload if isinstance(payload, dict) else {}
