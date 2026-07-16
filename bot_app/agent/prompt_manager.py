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
        tool_hints = (self._settings.router_tool_hints_prompt or "").strip()
        tool_hint_block = f"工具选择参考:\n{tool_hints}" if tool_hints else ""
        return "\n\n".join(
            item
            for item in [
                self._settings.system_prompt.strip(),
                (
                    "你正在群聊中工作，并通过系统提供的原生 tools 获取事实和执行能力。"
                    "需要事实、实时资料或程序能力时直接调用真实工具，不要在正文中伪造工具调用，也不要输出 JSON 决策协议。"
                    "工具返回后，根据 observation 判断是否继续调用工具；证据足够时直接输出最终纯文本。"
                    "普通群聊没有明确指向机器人或用户要求不要回复时，调用 ignore_message。"
                    "总原则：能可靠直接回答就回答；需要事实就依赖工具；信息不足时只问一个关键问题；不要编造。"
                    "BTD6 游戏知识必须先检索知识库，命中后读取候选文件；Harness 会确定性执行这一证据规则。"
                ),
                tool_hint_block,
                f"策略提示: {json.dumps(policy_hints, ensure_ascii=False)}",
                f"当前事件上下文: {event_context}",
            ]
            if item
        )
