from __future__ import annotations

from .command_processor import CommandProcessor
from .protocol import CommandResultPayload, EventPayload, IntentPayload


class ToolSelector:
    """Select user-facing tool type and load required command guides."""

    def __init__(self) -> None:
        self._tool_to_commands = {
            "feature_overview": ["list_features"],
            "activity_ranking": ["today_most_active"],
            "chat_summary": ["summarize_recent_messages"],
            "script_manual": ["script_manual_query"],
            "game_knowledge": ["search_game_knowledge", "read_game_knowledge_file"],
            "game_news": ["fetch_latest_game_news", "get_activity_detail", "get_activity_leaderboard"],
        }

        self._tool_aliases = {
            "feature_overview": "feature_overview",
            "list_features": "feature_overview",
            "机器人功能查看": "feature_overview",
            "activity_ranking": "activity_ranking",
            "today_most_active": "activity_ranking",
            "活跃度排行查看": "activity_ranking",
            "chat_summary": "chat_summary",
            "summarize_recent_messages": "chat_summary",
            "群聊总结": "chat_summary",
            "script_manual": "script_manual",
            "script_manual_query": "script_manual",
            "脚本说明书查看": "script_manual",
            "game_knowledge": "game_knowledge",
            "search_game_knowledge": "game_knowledge",
            "游戏知识查看": "game_knowledge",
            "game_news": "game_news",
            "fetch_latest_game_news": "game_news",
            "get_activity_detail": "game_news",
            "get_activity_leaderboard": "game_news",
            "游戏最新资讯获取": "game_news",
        }

    async def load_tool_guides(
        self,
        router_intent: IntentPayload,
        event: EventPayload,
        command_processor: CommandProcessor,
    ) -> CommandResultPayload:
        requested_tool = str(router_intent.get("intent", "") or "").strip()
        normalized_tool = self._normalize_tool_name(requested_tool)

        if normalized_tool not in self._tool_to_commands:
            return {
                "module": "tool_selector",
                "ok": False,
                "command": "load_tool_guides",
                "data": {
                    "requested_tool": requested_tool,
                    "normalized_tool": normalized_tool,
                    "available_tools": sorted(self._tool_to_commands.keys()),
                    "required_commands": [],
                    "guides": [],
                },
                "message": f"不支持的工具类型: {requested_tool}",
                "extra_prompt": "请在 available_tools 中选择最匹配的工具类型后重试。",
            }

        required_commands = self._tool_to_commands[normalized_tool]
        guides: list[dict] = []

        for command_name in required_commands:
            guide_intent: IntentPayload = {
                "module": "tool_selector",
                "target": "command_processor",
                "intent": "view_command_guide",
                "arguments": {"command": command_name},
                "reply": "",
                "reason": f"load guide for {normalized_tool}",
            }
            result = await command_processor.execute(guide_intent, event)
            guides.append(
                {
                    "command": command_name,
                    "ok": bool(result.get("ok", False)),
                    "message": str(result.get("message", "") or ""),
                    "guide_text": str(result.get("data", {}).get("guide_text", "") or ""),
                }
            )

        ok = all(item["ok"] for item in guides)
        return {
            "module": "tool_selector",
            "ok": ok,
            "command": "load_tool_guides",
            "data": {
                "requested_tool": requested_tool,
                "normalized_tool": normalized_tool,
                "required_commands": required_commands,
                "guides": guides,
            },
            "message": "工具技能已加载" if ok else "工具技能加载部分失败",
            "extra_prompt": (
                "你已拿到该工具对应的指令技能。"
                "请先根据 guides 选择下一条 command_processor 指令并补齐参数。"
                "若信息不足，继续调用指令；足够时再输出 final。"
            ),
        }

    def _normalize_tool_name(self, tool_name: str) -> str:
        key = (tool_name or "").strip()
        if not key:
            return ""
        return self._tool_aliases.get(key, key)
