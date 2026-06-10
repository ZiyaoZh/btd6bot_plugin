from __future__ import annotations

from typing import Any

from ..protocol import CommandResultPayload, EventPayload
from .base import ConcreteTool, ToolRuntime, object_schema


def build_social_tools(runtime: ToolRuntime) -> list[ConcreteTool]:
    async def today_most_active(
        arguments: dict[str, Any], event: EventPayload
    ) -> CommandResultPayload:
        top_n_raw = arguments.get("top_n", 3)
        try:
            top_n = int(top_n_raw)
        except (TypeError, ValueError):
            top_n = 3

        if runtime.db is None:
            return {
                "module": "tool.social",
                "ok": False,
                "command": "today_most_active",
                "data": {"top_n": top_n, "rows": []},
                "message": "数据库未初始化，无法统计今日活跃榜。",
                "extra_prompt": "请说明活跃榜暂时不可用，并建议稍后重试。",
                "response_type": "text",
                "image_path": "",
            }

        rows = runtime.db.get_today_most_active(event["group_id"], top_n=top_n)
        if not rows:
            return {
                "module": "tool.social",
                "ok": True,
                "command": "today_most_active",
                "data": {"top_n": top_n, "rows": []},
                "message": "今天还没有可统计的群聊消息。",
                "extra_prompt": "",
                "response_type": "text",
                "image_path": "",
            }

        return {
            "module": "tool.social",
            "ok": True,
            "command": "today_most_active",
            "data": {"top_n": top_n, "rows": rows},
            "message": "统计完成",
            "extra_prompt": "",
            "response_type": "text",
            "image_path": "",
        }

    async def summarize_recent_messages(
        arguments: dict[str, Any], event: EventPayload
    ) -> CommandResultPayload:
        top_n_raw = arguments.get("top_n", 30)
        try:
            top_n = int(top_n_raw)
        except (TypeError, ValueError):
            top_n = 30
        top_n = max(5, min(top_n, 200))

        if runtime.db is None:
            return {
                "module": "tool.social",
                "ok": False,
                "command": "summarize_recent_messages",
                "data": {"top_n": top_n, "message_count": 0, "messages": []},
                "message": "数据库未初始化，无法读取最近群聊消息。",
                "extra_prompt": "请说明群聊总结暂时不可用，并建议稍后重试。",
                "response_type": "text",
                "image_path": "",
            }

        rows = runtime.db.get_recent_group_messages(event["group_id"], limit=top_n)
        if not rows:
            return {
                "module": "tool.social",
                "ok": True,
                "command": "summarize_recent_messages",
                "data": {"top_n": top_n, "message_count": 0, "messages": []},
                "message": "最近没有可用于总结的群聊消息。",
                "extra_prompt": "直接说明最近缺少可总结的消息。",
                "response_type": "text",
                "image_path": "",
            }

        compact_messages = []
        for row in rows:
            display_name = (
                str(row.get("sender_card", "") or "").strip()
                or str(row.get("sender_nickname", "") or "").strip()
            )
            compact_messages.append(
                {
                    "display_name": display_name,
                    "text": str(row.get("raw_message", "") or "").strip(),
                    "created_at": str(row.get("created_at", "") or ""),
                }
            )

        return {
            "module": "tool.social",
            "ok": True,
            "command": "summarize_recent_messages",
            "data": {
                "top_n": top_n,
                "message_count": len(compact_messages),
                "messages": compact_messages,
            },
            "message": "最近群聊消息已提取完成",
            "extra_prompt": (
                "请基于 messages 生成群聊总结。"
                "总结重点包括: 主要话题、共识/分歧、待办事项。"
                "不要逐条复述，不要编造消息里不存在的内容。"
            ),
            "response_type": "text",
            "image_path": "",
        }

    return [
        ConcreteTool(
            spec={
                "name": "today_most_active",
                "description": "统计今天群里最活跃的成员。",
                "parameters": object_schema(
                    {
                        "top_n": {
                            "type": "integer",
                            "description": "返回前 N 名，默认 3。",
                            "minimum": 1,
                            "maximum": 20,
                        }
                    }
                ),
            },
            handler=today_most_active,
        ),
        ConcreteTool(
            spec={
                "name": "summarize_recent_messages",
                "description": "读取最近群聊消息并总结主要话题、共识分歧和待办事项。",
                "parameters": object_schema(
                    {
                        "top_n": {
                            "type": "integer",
                            "description": "读取最近 N 条消息，默认 30，范围 5 到 200。",
                            "minimum": 5,
                            "maximum": 200,
                        }
                    }
                ),
            },
            handler=summarize_recent_messages,
        ),
    ]
