from __future__ import annotations

import json

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
        return "\n\n".join(
            [
                self._settings.system_prompt.strip(),
                (
                    "你正在群聊中工作。请根据当前消息决定直接回复、调用工具，或调用 ignore_message 忽略消息。"
                    "如果消息没有明确指向机器人、没有任务诉求、只是群友闲聊，应调用 ignore_message。"
                    "如果 event.bot_mentioned=true，不要忽略，除非用户明确要求你闭嘴、不要回复或停止说话。"
                    "需要 BTD6 活动、排行榜、知识库、脚本说明书、群聊总结、活跃榜、开箱模拟等信息时，必须调用工具获取事实。"
                    "不要编造工具结果里没有的信息；信息不足时只问一个关键澄清问题。"
                    "输出只使用纯文本，不要 Markdown、代码块、JSON、工具名或文件路径。"
                    "中文、自然、简洁，适合直接发到群里。"
                ),
                f"当前事件上下文: {event_context}",
            ]
        )
