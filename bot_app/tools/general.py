from __future__ import annotations

from typing import Any

from ..protocol import CommandResultPayload, EventPayload
from .base import ConcreteTool, ToolRuntime, object_schema


def build_general_tools(runtime: ToolRuntime) -> list[ConcreteTool]:
    async def ignore_message(
        arguments: dict[str, Any], event: EventPayload
    ) -> CommandResultPayload:
        return {
            "module": "agent",
            "ok": True,
            "command": "ignore_message",
            "data": {"reason": str(arguments.get("reason", "") or "").strip()},
            "message": "忽略该群聊消息",
            "extra_prompt": "",
            "response_type": "none",
            "image_path": "",
        }

    async def list_features(
        arguments: dict[str, Any], event: EventPayload
    ) -> CommandResultPayload:
        features = [
            {
                "name": "list_features",
                "aliases": ["list", "help"],
                "description": "查看机器人当前支持的所有功能",
                "examples": ["list", "功能列表", "命令列表"],
            },
            {
                "name": "today_most_active",
                "aliases": ["今日活跃榜", "谁最活跃"],
                "description": "统计今天群里最活跃的成员",
                "arguments": {"top_n": "返回前 N 名，默认 3"},
                "examples": ["今天谁最活跃", "今日活跃榜前5"],
            },
            {
                "name": "summarize_recent_messages",
                "aliases": ["recent_chat_summary", "群聊总结", "总结最近消息"],
                "description": "总结最近的群聊消息",
                "arguments": {"top_n": "读取最近 N 条消息并总结，默认 30"},
                "examples": ["总结最近群聊", "群聊总结 最近50条"],
            },
            {
                "name": "script_manual_query",
                "aliases": [
                    "manual_query",
                    "说明书",
                    "文档查询",
                    "程序怎么用",
                    "脚本说明书",
                ],
                "description": "检索脚本程序说明书并基于说明书回答问题",
                "arguments": {"query": "问题关键词或完整问题"},
                "examples": ["这个脚本程序怎么启动", "刷收集怎么用"],
            },
            {
                "name": "search_game_knowledge",
                "aliases": ["game_kb_search", "btd6_kb_search", "检索游戏知识"],
                "description": "查看 BTD6 游戏知识",
                "arguments": {
                    "top_n": "返回前 N 个文件路径，默认 5",
                    "query": "检索问题，缺省时使用用户原话",
                },
                "examples": ["检索游戏知识 top 5", "查找猴子塔升级机制相关资料"],
            },
            {
                "name": "get_activity_detail",
                "aliases": ["activity_detail", "活动详情"],
                "description": "获取指定活动详情（detail 模式）",
                "arguments": {"activity_type": "race|boss|odyssey|daily"},
                "examples": ["获取竞速活动详情", "获取 boss 活动详情"],
            },
            {
                "name": "get_activity_leaderboard",
                "aliases": ["activity_leaderboard", "活动排行榜"],
                "description": "获取指定活动排行榜（leaderboard image 模式）",
                "arguments": {"leaderboard_type": "race|boss-standard|boss-elite"},
                "examples": ["获取竞速排行榜", "获取精英 boss 排行榜"],
            },
            {
                "name": "get_collection_event_info",
                "aliases": [
                    "collection_event",
                    "collection_schedule",
                    "收集活动",
                    "收集轮换",
                ],
                "description": "获取收集活动轮换信息并发送图片（collection-event 模式）",
                "arguments": {
                    "only_upcoming": "true|false，可选，true 表示仅当前和未来轮换"
                },
                "examples": [
                    "查询收集活动",
                    "看一下收集轮换图",
                    "获取 upcoming 收集活动",
                ],
            },
            {
                "name": "simulate_monkey_box_opening",
                "aliases": [
                    "open_monkey_box",
                    "game_box_opening",
                    "开箱模拟",
                    "收集开箱",
                ],
                "description": "模拟 BTD6 开箱并返回统计文档，箱子数量 <=50 时额外返回图片",
                "arguments": {
                    "box_type": "木头|青铜|白银|黄金|钻石",
                    "box_count": "正整数",
                },
                "examples": ["开 10 个钻石箱", "模拟青铜箱 30 个"],
            },
            {
                "name": "chat",
                "aliases": ["闲聊", "问答"],
                "description": "普通对话能力，由大模型直接回复",
            },
        ]
        return {
            "module": "tool.general",
            "ok": True,
            "command": "list_features",
            "data": {
                "feature_count": len(features),
                "features": features,
            },
            "message": "功能列表已整理完成",
            "extra_prompt": "",
            "response_type": "text",
            "image_path": "",
        }

    return [
        ConcreteTool(
            spec={
                "name": "ignore_message",
                "description": "忽略不需要机器人回复的群聊消息。用户明确要求闭嘴、别回复、不要说话时也使用此工具。",
                "parameters": object_schema(
                    {
                        "reason": {
                            "type": "string",
                            "description": "忽略原因，简短说明即可。",
                        }
                    }
                ),
            },
            handler=ignore_message,
        ),
        ConcreteTool(
            spec={
                "name": "list_features",
                "description": "查看机器人当前支持的所有功能。",
                "parameters": object_schema(),
            },
            handler=list_features,
        ),
    ]
