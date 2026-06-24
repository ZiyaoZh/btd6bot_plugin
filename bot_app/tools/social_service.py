from __future__ import annotations

from typing import Any

from ..group_context import build_recent_group_window
from ..protocol import CommandResultPayload, EventPayload
from .base import fail_result, ok_result


def build_today_most_active_result(
    *,
    db: Any,
    event: EventPayload,
    arguments: dict[str, Any],
    module: str,
) -> CommandResultPayload:
    top_n_raw = arguments.get("top_n", 3)
    try:
        top_n = int(top_n_raw)
    except (TypeError, ValueError):
        top_n = 3

    if db is None:
        return fail_result(
            module=module,
            command="today_most_active",
            data={"top_n": top_n, "rows": []},
            message="数据库未初始化，无法统计今日活跃榜。",
            extra_prompt="请说明活跃榜暂时不可用，并建议稍后重试。",
        )

    rows = db.get_today_most_active(event["group_id"], top_n=top_n)
    if not rows:
        return ok_result(
            module=module,
            command="today_most_active",
            data={"top_n": top_n, "rows": []},
            message="今天还没有可统计的群聊消息。",
        )

    return ok_result(
        module=module,
        command="today_most_active",
        data={"top_n": top_n, "rows": rows},
        message="统计完成",
    )


def build_summarize_recent_messages_result(
    *,
    db: Any,
    event: EventPayload,
    arguments: dict[str, Any],
    module: str,
) -> CommandResultPayload:
    top_n_raw = arguments.get("top_n", 30)
    try:
        top_n = int(top_n_raw)
    except (TypeError, ValueError):
        top_n = 30
    top_n = max(5, min(top_n, 200))

    if db is None:
        return fail_result(
            module=module,
            command="summarize_recent_messages",
            data={"top_n": top_n, "message_count": 0, "messages": []},
            message="数据库未初始化，无法读取最近群聊消息。",
            extra_prompt="请说明群聊总结暂时不可用，并建议稍后重试。",
        )

    rows = db.get_recent_group_messages(event["group_id"], limit=top_n)
    if not rows:
        return ok_result(
            module=module,
            command="summarize_recent_messages",
            data={"top_n": top_n, "message_count": 0, "messages": []},
            message="最近没有可用于总结的群聊消息。",
            extra_prompt="直接说明最近缺少可总结的消息。",
        )

    compact_messages = []
    for item in build_recent_group_window(rows, limit=top_n):
        compact_messages.append(
            {
                "display_name": str(item.get("speaker", "") or ""),
                "text": str(item.get("cleaned_text", "") or ""),
                "created_at": str(item.get("created_at", "") or ""),
                "mention_relation": str(item.get("mention_relation", "") or ""),
            }
        )

    return ok_result(
        module=module,
        command="summarize_recent_messages",
        data={
            "top_n": top_n,
            "message_count": len(compact_messages),
            "messages": compact_messages,
        },
        message="最近群聊消息已提取完成",
        extra_prompt=(
            "请基于 messages 生成群聊总结。"
            "总结重点包括: 主要话题、共识/分歧、待办事项。"
            "不要逐条复述，不要编造消息里不存在的内容。"
        ),
    )
