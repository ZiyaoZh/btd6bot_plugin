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
        return build_read_game_knowledge_file_result(
            kb_root=dependencies.kb_root,
            arguments=arguments,
            module="tool.knowledge",
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
                "description": "读取 search_game_knowledge 返回的知识库文件内容。长文档需要提供 query 或 keywords 以提取相关文本块。",
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
                            "description": "最多读取或提取的字符数，默认 8000。",
                            "minimum": 500,
                            "maximum": 20000,
                        },
                    },
                    required=["file_path"],
                ),
            },
            handler=read_game_knowledge_file,
        ),
    ]
