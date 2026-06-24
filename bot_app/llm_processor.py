"""Historical two-step LLM router.

The runtime entry points now use AgentRunner with native tool_calls. This file
is retained for legacy imports and comparison with the old command pipeline.
"""

from __future__ import annotations

import json

from .context_contracts import serialize_compact_user_event
from .context_manager import ContextManager
from .llm_client import LLMClient
from .logging_utils import log_flow
from .memory import GroupChatMemory
from .protocol import CommandResultPayload, EventPayload, IntentPayload
from .settings import AppSettings


class LLMMessageProcessor:
    def __init__(
        self,
        llm: LLMClient,
        memory: GroupChatMemory,
        settings: AppSettings,
        context_manager: ContextManager | None = None,
    ) -> None:
        self._llm = llm
        self._memory = memory
        self._settings = settings
        self._context_manager = context_manager or ContextManager(
            llm=llm, settings=settings
        )

    def _style_constraint(self) -> str:
        return (
            "输出要求: 只输出纯文本，不要使用任何 Markdown 格式。"
            "可以多用一些自然表情来增强语气。"
        )

    def _intent_router_prompt(self) -> str:
        base_prompt = (
            f"你是群聊机器人路由规划器。机器人的名字是 {self._settings.bot_name}。"
            "你的任务是把用户消息路由成 JSON 指令。"
            "你必须只输出 JSON，不要输出解释文本。"
            "字段固定: module,target,intent,arguments,reply,reason。"
            "target 只能是 chat、tool_selector、noop。"
            "当 target=tool_selector 时，intent 必须是以下工具类型之一:"
            "feature_overview(机器人功能查看)、activity_ranking(活跃度排行查看)、chat_summary(群聊总结)、script_manual(脚本说明书查看)、game_knowledge(游戏知识查看)、game_news(游戏活动详情/排行榜/收集轮换查询)、mini_game(收集开箱模拟)。"
            "你在路由阶段只选工具类型，不直接选具体 command_processor 指令。"
            "规则:"
            "1) 先看 event.bot_mentioned。若为 true，不得返回 noop。"
            "2) 用户明确要求闭嘴/别回复/不要说话 -> noop/noop。"
            "3) 明显是群友闲聊、无机器人指向且无任务诉求 -> noop/noop。"
            "4) 需要机器人直接自然聊天回复 -> chat/chat。"
            "5) 其余任务请求都走 tool_selector，并选择最匹配工具类型。"
        )

        hints = (self._settings.router_tool_hints_prompt or "").strip()
        if not hints:
            return base_prompt

        return (
            f"{base_prompt}\n\n"
            "以下是‘常见用户问题对应工具选择’参考，请优先遵循：\n"
            f"{hints}"
        )

    def _command_render_prompt(self) -> str:
        return (
            "你是群聊机器人文案器。"
            "任务: 根据 event + intent + command_result 生成最终回复。"
            "如果 command_result.ok=false，先说明失败原因，再给一个可执行下一步。"
            "如果 command_result.ok=true，优先使用 command_result.data 回答用户问题，而不是复述 JSON。"
            "若存在 command_result.extra_prompt，必须优先遵循该附加提示词。"
            "如信息不足，明确说明缺失点，不要编造。"
            "风格要求: 中文、自然、简洁、可读。"
            "输出要求: 只输出纯文本，不要使用任何 Markdown 格式。"
            "可以分点，但请使用表情或'-'，不要使用数字编号。"
            "可以用一些自然表情来增强语气。"
        )

    def _tool_planner_prompt(self) -> str:
        return (
            "你是任务编排器，需要在“继续调用工具/指令”与“直接给最终答复”之间做决策。"
            "你必须只输出 JSON，不要输出 Markdown。"
            "输出格式二选一:"
            'A) {"decision":"call_command","intent":{module,target,intent,arguments,reply,reason}}'
            'B) {"decision":"final","final_reply":"..."}'
            "其中 intent.target 只能是 command_processor 或 tool_selector。"
            "当 target=command_processor 时，intent 必须来自当前 tool_history 里最近一次 tool_selector.load_tool_guides 给出的 guides。"
            "若当前信息不足以回答用户，优先 call_command。"
            "当你判断“当前工具支持的指令都无法完成用户任务”时，应输出 target=tool_selector 并切换到新的工具类型，重新加载一套 skills。"
            "当 target=tool_selector 时，intent 必须是以下工具类型之一:"
            "feature_overview(机器人功能查看)、activity_ranking(活跃度排行查看)、chat_summary(群聊总结)、script_manual(脚本说明书查看)、game_knowledge(游戏知识查看)、game_news(游戏活动详情/排行榜/收集轮换查询)、mini_game(收集开箱模拟)。"
            "若已有信息足够回答用户，输出 final。"
            "输出final时注意:"
            "1) 抹除敏感信息如qq号、代码指令等。"
            "2) 风格要求: 中文、自然、简洁、可读。"
            "3) 只输出纯文本，不要使用任何 Markdown 格式。"
            "4) 可以分点，但请使用表情或'-'，不要使用数字编号。"
            "5) 可以用一些自然表情来增强语气。"
        )

    def _normalize_plain_text(self, text: str) -> str:
        cleaned = (text or "").strip()
        if not cleaned:
            return cleaned

        cleaned = cleaned.replace("```", "")
        cleaned = cleaned.replace("**", "")
        cleaned = cleaned.replace("__", "")
        cleaned = cleaned.replace("`", "")

        lines = []
        index = 1
        for raw_line in cleaned.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            while line.startswith("#"):
                line = line[1:].strip()

            if line.startswith("- ") or line.startswith("* "):
                line = f"{index}. {line[2:].strip()}"
                index += 1

            lines.append(line)

        if not lines:
            return ""
        return "\n".join(lines)

    def _build_user_turn_text(self, event: EventPayload) -> str:
        return serialize_compact_user_event(event)

    async def _build_memory_messages(
        self,
        system_prompt: str,
        event: EventPayload,
        user_payload: str,
        trace_id: str = "",
    ) -> list[dict[str, str]]:
        await self._context_manager.compact_if_needed(
            memory=self._memory,
            group_id=event["group_id"],
            user_id=event["user_id"],
            trace_id=trace_id,
        )
        return self._memory.build_messages(
            system_prompt=system_prompt,
            group_id=event["group_id"],
            user_id=event["user_id"],
            user_text=user_payload,
        )

    async def record_tool_turn(
        self, event: EventPayload, assistant_text: str, trace_id: str = ""
    ) -> None:
        user_text_for_memory = self._build_user_turn_text(event)
        self._memory.append_turn(
            group_id=event["group_id"],
            user_id=event["user_id"],
            user_text=user_text_for_memory,
            assistant_text=(assistant_text or "").strip(),
        )
        await self._context_manager.compact_if_needed(
            memory=self._memory,
            group_id=event["group_id"],
            user_id=event["user_id"],
            trace_id=trace_id,
        )

    async def plan_intent(
        self, event: EventPayload, trace_id: str = ""
    ) -> IntentPayload:
        planner_messages = await self._build_memory_messages(
            system_prompt=self._intent_router_prompt(),
            event=event,
            user_payload=json.dumps(
                {"stage": "intent_router", "event": event}, ensure_ascii=False
            ),
            trace_id=trace_id,
        )

        raw = await self._llm.chat(
            planner_messages, trace_id=trace_id, purpose="intent_planning"
        )
        parsed = self._safe_parse_intent(raw)
        if trace_id:
            log_flow(
                stage="llm_processor.intent_parsed",
                trace_id=trace_id,
                intent=parsed,
                raw=raw,
            )
        return parsed

    async def chat_reply(self, event: EventPayload, trace_id: str = "") -> str:
        user_text_for_llm = self._build_user_turn_text(event)
        messages = await self._build_memory_messages(
            system_prompt=f"{self._settings.system_prompt}\n\n{self._style_constraint()}",
            event=event,
            user_payload=user_text_for_llm,
            trace_id=trace_id,
        )
        answer = await self._llm.chat(messages, trace_id=trace_id, purpose="chat_reply")
        answer = self._normalize_plain_text(answer)
        answer = answer or "我暂时不知道怎么回答这个问题。🙂"
        answer = answer[: self._settings.max_reply_chars]
        self._memory.append_turn(
            group_id=event["group_id"],
            user_id=event["user_id"],
            user_text=user_text_for_llm,
            assistant_text=answer,
        )
        await self._context_manager.compact_if_needed(
            memory=self._memory,
            group_id=event["group_id"],
            user_id=event["user_id"],
            trace_id=trace_id,
        )
        if trace_id:
            log_flow(
                stage="llm_processor.chat_reply",
                trace_id=trace_id,
                answer=answer,
            )
        return answer

    async def render_command_result(
        self,
        event: EventPayload,
        intent: IntentPayload,
        command_result: CommandResultPayload,
        trace_id: str = "",
    ) -> str:
        system_prompt = (
            f"{self._command_render_prompt()}\n\n"
            f"附加提示: {str(command_result.get('extra_prompt', '') or '')}"
        )
        messages = await self._build_memory_messages(
            system_prompt=system_prompt,
            event=event,
            user_payload=json.dumps(
                {
                    "stage": "command_render",
                    "event": event,
                    "intent": intent,
                    "command_result": command_result,
                },
                ensure_ascii=False,
            ),
            trace_id=trace_id,
        )
        text = await self._llm.chat(
            messages, trace_id=trace_id, purpose="command_render"
        )
        text = self._normalize_plain_text(text)
        if not text:
            text = command_result.get("message", "指令执行完成。")
        if trace_id:
            log_flow(
                stage="llm_processor.command_render",
                trace_id=trace_id,
                intent=intent,
                command_result=command_result,
                text=text,
            )
        return text[: self._settings.max_reply_chars]

    async def decide_next_tool_step(
        self,
        event: EventPayload,
        tool_history: list[dict],
        trace_id: str = "",
    ) -> dict:
        messages = await self._build_memory_messages(
            system_prompt=self._tool_planner_prompt(),
            event=event,
            user_payload=json.dumps(
                {
                    "stage": "tool_planning",
                    "event": event,
                    "tool_history": tool_history,
                },
                ensure_ascii=False,
            ),
            trace_id=trace_id,
        )

        raw = await self._llm.chat(messages, trace_id=trace_id, purpose="tool_planning")
        parsed = self._safe_parse_json(raw)
        decision = str(parsed.get("decision", "")).strip()
        if decision not in {"call_command", "final"}:
            decision = "final"

        if decision == "final":
            final_reply = self._normalize_plain_text(
                str(parsed.get("final_reply", "")).strip()
            )
            return {"decision": "final", "final_reply": final_reply}

        intent = parsed.get("intent", {})
        if not isinstance(intent, dict):
            intent = {}

        target = str(
            intent.get("target", "command_processor") or "command_processor"
        ).strip()
        if target not in {"command_processor", "tool_selector"}:
            target = "command_processor"

        next_intent = {
            "module": "llm_processor",
            "target": target,
            "intent": str(intent.get("intent", "")).strip() or "list_features",
            "arguments": intent.get("arguments", {})
            if isinstance(intent.get("arguments", {}), dict)
            else {},
            "reply": str(intent.get("reply", "")).strip(),
            "reason": str(intent.get("reason", "")).strip() or "tool planning",
        }

        return {"decision": "call_command", "intent": next_intent}

    def _safe_parse_json(self, raw: str) -> dict:
        text = (raw or "").strip()
        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass

        payload = self._extract_json_object(text)
        return payload if isinstance(payload, dict) else {}

    def reset_user_context(self, group_id: str, user_id: str) -> None:
        self._memory.reset(group_id, user_id)

    def _safe_parse_intent(self, raw: str) -> IntentPayload:
        text = (raw or "").strip()
        payload: dict

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = self._extract_json_object(text)

        target = str(payload.get("target", "chat")).strip() or "chat"
        intent = str(payload.get("intent", "chat")).strip() or "chat"
        arguments = payload.get("arguments", {})
        if not isinstance(arguments, dict):
            arguments = {}

        reply = str(payload.get("reply", "")).strip()
        reason = str(payload.get("reason", "")).strip()

        aliases = {
            "ignore": "noop",
            "none": "noop",
            "do_nothing": "noop",
            "啥也不干": "noop",
        }
        target = aliases.get(target, target)

        if target not in {"chat", "tool_selector", "command_processor", "noop"}:
            target = "chat"
            intent = "chat"

        return {
            "module": "llm_processor",
            "target": target,
            "intent": intent,
            "arguments": arguments,
            "reply": reply,
            "reason": reason,
        }

    def _extract_json_object(self, text: str) -> dict:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {}

        snippet = text[start : end + 1]
        try:
            obj = json.loads(snippet)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            return {}
        return {}
