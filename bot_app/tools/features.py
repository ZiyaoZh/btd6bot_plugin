from __future__ import annotations

from copy import deepcopy
from typing import Any

_FEATURE_CATALOG: list[dict[str, Any]] = [
    {
        "name": "list_features",
        "aliases": ["list", "help"],
        "description": "查看机器人当前支持的所有功能",
        "examples": ["list", "功能列表", "命令列表"],
    },
    {
        "name": "list_artifacts",
        "aliases": ["产物清单", "已有产物", "历史报告", "历史图片"],
        "description": "查看当前未过期的 Agent 产物及其标题、标签、日期和文件角色",
        "arguments": {"kind": "可选产物类型", "limit": "最多返回项数"},
        "examples": ["看看已有产物", "列出最近生成的图片"],
    },
    {
        "name": "read_artifact",
        "aliases": ["读取产物", "取用产物"],
        "description": "读取产物文档或直接发送已有图片",
        "arguments": {"artifact_id": "产物 ID", "role": "report|image|data"},
        "examples": ["读取这个活动报告", "发送这张已有图片"],
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
        "aliases": ["manual_query", "说明书", "文档查询", "程序怎么用", "脚本说明书"],
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
        "arguments": {
            "activity_type": "race|boss|odyssey|daily",
            "difficulty": "easy|medium|hard，征程默认 hard",
            "event_scope": "current|upcoming|latest，默认 latest",
        },
        "examples": ["获取竞速活动详情", "获取 boss 活动详情"],
    },
    {
        "name": "get_activity_brief_image",
        "aliases": ["activity_brief", "活动情报图", "竞速图", "征程图"],
        "description": "使用真实地图和结构化活动数据生成确定性竞速/征程情报图",
        "arguments": {
            "activity_type": "race|odyssey",
            "difficulty": "easy|medium|hard，征程默认 hard",
            "event_scope": "current|upcoming|latest，默认 current",
        },
        "examples": ["生成当前竞速情报图", "生成下一期困难征程图"],
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
        "aliases": ["collection_event", "collection_schedule", "收集活动", "收集轮换"],
        "description": "获取收集活动轮换信息并发送图片（collection-event 模式）",
        "arguments": {"only_upcoming": "true|false，可选，true 表示仅当前和未来轮换"},
        "examples": ["查询收集活动", "看一下收集轮换图", "获取 upcoming 收集活动"],
    },
    {
        "name": "search_web",
        "aliases": ["web_search", "联网搜索", "网页搜索"],
        "description": "联网搜索公开网页，作为其他工具无结果、失败、信息可能过时或需要当前公开资料时的备用证据路径",
        "arguments": {
            "query": "搜索关键词或完整问题",
            "top_n": "返回前 N 个网页候选，默认 5，范围 1-8",
        },
        "examples": ["联网搜索某产品最新公告", "网页搜索 BTD6 最新版本削弱"],
    },
    {
        "name": "generate_image",
        "aliases": ["画图", "生图", "生成图片", "制作插图"],
        "description": "根据文字提示生成并发送同人插画、壁纸、头像、场景画等创意图片；不用于事实型活动图",
        "arguments": {"prompt": "完整的图片描述"},
        "examples": ["画一张雨夜霓虹城市", "生成一张可爱的猴子头像"],
    },
    {
        "name": "simulate_monkey_box_opening",
        "aliases": ["open_monkey_box", "game_box_opening", "开箱模拟", "收集开箱"],
        "description": "实时运行一次新的 BTD6 开箱模拟并返回统计，不进入可复用产物清单；箱子数量 <=50 时额外返回图片",
        "arguments": {"box_type": "木头|青铜|白银|黄金|钻石", "box_count": "正整数"},
        "examples": ["开 10 个钻石箱", "模拟青铜箱 30 个"],
    },
    {
        "name": "chat",
        "aliases": ["闲聊", "问答"],
        "description": "普通对话能力，由大模型直接回复",
    },
]


def get_feature_catalog() -> list[dict[str, Any]]:
    return deepcopy(_FEATURE_CATALOG)
