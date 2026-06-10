from __future__ import annotations

from .base import ConcreteTool, ToolRuntime, command_tool, object_schema


def build_knowledge_tools(runtime: ToolRuntime) -> list[ConcreteTool]:
    return [
        command_tool(
            runtime=runtime,
            name="search_game_knowledge",
            description="检索 BTD6 游戏知识库，返回最相关的候选文件路径。通常应先调用本工具，再读取候选文件。",
            parameters=object_schema(
                {
                    "query": {
                        "type": "string",
                        "description": "要检索的 BTD6 游戏知识问题或关键词。",
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
        ),
        command_tool(
            runtime=runtime,
            name="read_game_knowledge_file",
            description="读取 search_game_knowledge 返回的知识库文件内容。长文档需要提供 query 或 keywords 以提取相关文本块。",
            parameters=object_schema(
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
        ),
    ]
