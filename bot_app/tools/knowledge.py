from __future__ import annotations

from typing import Any

from ..protocol import CommandResultPayload, EventPayload
from .base import ConcreteTool, ToolDependencies, object_schema
from .knowledge_service import (
    build_read_game_knowledge_file_result,
    build_search_game_knowledge_result,
)


def build_knowledge_tools(dependencies: ToolDependencies) -> list[ConcreteTool]:
    async def search_game_knowledge(
        arguments: dict[str, Any], event: EventPayload
    ) -> CommandResultPayload:
        return build_search_game_knowledge_result(
            kb_root=dependencies.kb_root,
            event=event,
            arguments=arguments,
            module="tool.knowledge",
        )

    async def read_game_knowledge_file(
        arguments: dict[str, Any], event: EventPayload
    ) -> CommandResultPayload:
        arguments = dict(arguments)
        if not any(
            arguments.get(key)
            for key in ("query", "keywords", "max_chars", "start_line")
        ):
            arguments["start_line"] = 1
        arguments.setdefault(
            "max_chars", min(8000, dependencies.knowledge_max_read_chars)
        )
        return build_read_game_knowledge_file_result(
            kb_root=dependencies.kb_root,
            arguments=arguments,
            module="tool.knowledge",
            max_read_chars=dependencies.knowledge_max_read_chars,
        )

    return [
        ConcreteTool(
            spec={
                "name": "search_game_knowledge",
                "description": "检索 BTD6 游戏知识库，返回最相关的候选文件路径。query 优先传用户原始问题，服务端会自动清洗和扩展查询。",
                "parameters": object_schema(
                    {
                        "query": {
                            "type": "string",
                            "description": "用户原始 BTD6 游戏知识问题；不要自行压缩成少量关键词。",
                        },
                        "top_n": {
                            "type": "integer",
                            "description": "返回候选文件数，默认 5。",
                            "minimum": 1,
                            "maximum": 20,
                        },
                    },
                    required=["query"],
                ),
            },
            handler=search_game_knowledge,
        ),
        ConcreteTool(
            spec={
                "name": "read_game_knowledge_file",
                "description": (
                    "读取 search_game_knowledge 返回的知识库 Markdown 文件。"
                    "默认最多返回 8000 字符。提供 query/keywords 定位相关证据；"
                    "start_line 可从原文件指定行继续读取。必要时可显式提高 max_chars 获取完整材料。"
                ),
                "parameters": object_schema(
                    {
                        "file_path": {
                            "type": "string",
                            "description": "search_game_knowledge 返回的候选文件路径。",
                        },
                        "query": {
                            "type": "string",
                            "description": "用户原问题或用于长文提取的关键词。",
                        },
                        "keywords": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "用于长文提取的关键词列表。",
                        },
                        "max_chars": {
                            "type": "integer",
                            "description": (
                                "最多读取或提取的字符数，默认 8000，服务端上限 "
                                f"{dependencies.knowledge_max_read_chars}。"
                            ),
                            "minimum": 500,
                            "maximum": dependencies.knowledge_max_read_chars,
                        },
                        "start_line": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "从原始 Markdown 文件的指定行开始分页；优先于 query，使用返回的 next_line 继续。",
                        },
                    },
                    required=["file_path"],
                ),
            },
            handler=read_game_knowledge_file,
        ),
    ]
