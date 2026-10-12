from __future__ import annotations

import re
from typing import Any

from ..protocol import EventPayload
from .base import ConcreteTool, ToolDependencies, fail_result, object_schema, ok_result


def build_memory_tools(dependencies: ToolDependencies) -> list[ConcreteTool]:
    memory = dependencies.memory
    if memory is None or not memory.persistent:
        return []

    async def manage_memory(
        arguments: dict[str, Any], event: EventPayload
    ) -> dict[str, Any]:
        action = arguments.get("action", "search")
        group_id, user_id = event["group_id"], event["user_id"]
        if action == "search":
            return ok_result(
                module="tool.memory",
                command="manage_memory",
                data={
                    "memories": memory.recall(
                        group_id,
                        user_id,
                        str(arguments.get("query") or event["text"]),
                        explicit_search=True,
                    )
                },
            )
        scope = str(arguments.get("scope") or "user")
        key = str(arguments.get("key") or "").strip()
        text = str(event["text"])
        content = str(arguments.get("content") or "").strip()
        if scope == "group" and event.get("identity", {}).get("role") not in {
            "owner",
            "admin",
        }:
            return fail_result(
                module="tool.memory",
                command="manage_memory",
                message="只有本群管理员可以更改群共享记忆。",
            )
        if (
            not key
            or len(key) > 80
            or scope not in {"user", "group"}
            or action not in {"remember", "forget"}
        ):
            return fail_result(
                module="tool.memory",
                command="manage_memory",
                message="请指定有效的记忆 key、scope 和 action。",
            )
        if action == "remember" and (
            not content
            or len(content) > 600
            or content not in text
            or not re.search(
                r"记住|记一下|以后|偏好|习惯|喜欢|默认|群规|约定|接下来|计划|任务", text
            )
        ):
            return fail_result(
                module="tool.memory",
                command="manage_memory",
                message="只能记录当前用户明确表达的偏好、约定或任务，content 必须引用当前消息的原文。",
            )
        if action == "forget" and not re.search(
            r"忘记|删除|取消|清除|不再|完成|结束|更正", text
        ):
            return fail_result(
                module="tool.memory",
                command="manage_memory",
                message="删除记忆需要当前用户明确要求忘记、取消、更正或完成该事项。",
            )
        kind = str(arguments.get("kind") or "preference")
        if kind not in {"preference", "task", "rule"}:
            return fail_result(
                module="tool.memory",
                command="manage_memory",
                message="记忆类型必须是 preference、task 或 rule。",
            )
        try:
            ttl_days = max(
                1, min(365, int(arguments.get("ttl_days", 7 if kind == "task" else 90)))
            )
        except (TypeError, ValueError):
            return fail_result(
                module="tool.memory",
                command="manage_memory",
                message="ttl_days 必须为有效天数。",
            )
        memory.queue_update(
            group_id,
            user_id,
            {
                "action": action,
                "scope": scope,
                "key": key,
                "kind": kind,
                "content": content,
                "ttl_days": ttl_days,
            },
        )
        return ok_result(
            module="tool.memory",
            command="manage_memory",
            data={
                "pending_delivery": True,
                "action": action,
                "scope": scope,
                "key": key,
            },
            message="记忆变更已准备，回复成功发送后提交。",
        )

    return [
        ConcreteTool(
            spec={
                "name": "manage_memory",
                "description": "按需查找本群本用户的历史或显式记忆。用户明确要求记住偏好/约定/任务时 remember，完成或取消时 forget；相同 key 更新旧值。content 必须逐字引用当前用户消息，不能保存助手推测或游戏数值。群共享记忆仅管理员可更改。写入在回复成功发送后生效。",
                "parameters": object_schema(
                    {
                        "action": {
                            "type": "string",
                            "enum": ["search", "remember", "forget"],
                        },
                        "query": {"type": "string"},
                        "scope": {"type": "string", "enum": ["user", "group"]},
                        "key": {
                            "type": "string",
                            "description": "稳定的事项名，例如 reply_style；修改同一事项复用 key。",
                        },
                        "kind": {
                            "type": "string",
                            "enum": ["preference", "task", "rule"],
                        },
                        "content": {
                            "type": "string",
                            "description": "当前用户消息中的原文，最多 600 字符。",
                        },
                        "ttl_days": {"type": "integer", "minimum": 1, "maximum": 365},
                    },
                    required=["action"],
                ),
            },
            handler=manage_memory,
        )
    ]
