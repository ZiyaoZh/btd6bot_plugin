from __future__ import annotations

import json

from ..context_contracts import build_policy_hints_context
from ..protocol import EventPayload
from ..settings import AppSettings


class PromptManager:
    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings

    def build_system_prompt(self, event: EventPayload) -> str:
        event_context = json.dumps(
            {
                "event_type": event["event_type"],
                "group_id": event["group_id"],
                "user_id": event["user_id"],
                "bot_mentioned": event["bot_mentioned"],
                "identity": event["identity"],
            },
            ensure_ascii=False,
        )
        policy_hints = build_policy_hints_context()
        return "\n\n".join(
            [
                self._settings.system_prompt.strip(),
                (
                    "你正在群聊中工作。主链路已经拆成 planning 和 rendering 两阶段。"
                    "这里的系统提示词只负责稳定人格、通用边界和群聊场景约束，不直接决定工具选择细节。"
                    "总原则：能直接回答就直接回答；需要事实就依赖工具；信息不足时只问一个关键问题；不要编造。"
                ),
                f"策略提示: {json.dumps(policy_hints, ensure_ascii=False)}",
                f"当前事件上下文: {event_context}",
            ]
        )

    def build_planning_prompt(self, event: EventPayload) -> str:
        del event
        hints = (self._settings.router_tool_hints_prompt or "").strip()
        hint_block = ""
        if hints:
            hint_block = (
                "\n\n常见问题到工具类别/能力的映射参考如下，请在 decision=call_tools 时优先遵循：\n"
                f"{hints}"
            )
        return "\n\n".join(
            [
                self._settings.system_prompt.strip(),
                (
                    "你是群聊机器人决策器。你的任务只包括：是否忽略、是否直接回复、是否调用工具、"
                    "以及在拿到工具结果后是否已经足够进入最终表达。"
                    "你必须只输出 JSON，不要输出解释文本，不要输出 Markdown。"
                    "JSON 字段固定为：decision, reason, draft_reply, tool_calls。"
                    "decision 只能是 ignore、reply_direct、call_tools、final。"
                    "tool_calls 必须是数组，每项包含 name 和 arguments。"
                    "规则分层如下："
                    "A. ignore 规则：消息没有明确指向机器人、没有任务诉求、只是群友闲聊时，用 decision=ignore；"
                    "如果当前消息明确 @ 机器人，除非用户明确要求闭嘴/停止回复，否则不要 ignore。"
                    "B. 闲聊规则：简单寒暄、陪聊、轻问轻答且不需要事实检索时，用 decision=reply_direct，draft_reply 保持简短自然。"
                    "C. 工具规则：涉及 BTD6 活动、排行榜、知识库、脚本说明书、群聊总结、活跃榜、开箱模拟等事实信息时，用 decision=call_tools。"
                    "D. 完成规则：如果现有工具结果已经足够回答，就用 decision=final。"
                    "同一轮如果需要多个工具，可以一次返回多个 tool_calls。"
                    "只做决策，不负责最终文风润色。"
                )
                + hint_block,
            ]
        )

    def build_rendering_prompt(self, event: EventPayload) -> str:
        del event
        return "\n\n".join(
            [
                self._settings.system_prompt.strip(),
                (
                    "你是群聊机器人表达器。请根据给定的 planning_result、reply_context 和 tool_results 生成最终回复。"
                    "优先遵循 tool_results 中汇总的 extra_prompt。"
                    "表达规则分层如下："
                    "A. 闲聊型回答：保持短，一两句即可，自然像群友。"
                    "B. 工具事实回答：信息要完整，优先回答用户真正的问题，不要被“闲聊要短”规则压扁。"
                    "C. 失败兜底：如果工具失败，直接说明失败原因并给出一个可执行下一步。"
                    "不要编造工具结果中没有的信息。"
                    "输出只使用纯文本，不要 Markdown、代码块、JSON、工具名或文件路径。"
                    "中文、自然、简洁，适合直接发到群里。"
                ),
            ]
        )
