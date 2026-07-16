from __future__ import annotations

from typing import Any

from ..protocol import CommandResultPayload, EventPayload
from .base import ConcreteTool, ToolDependencies, object_schema
from .web_search_service import build_search_web_result


def build_web_tools(dependencies: ToolDependencies) -> list[ConcreteTool]:
    del dependencies

    async def search_web(
        arguments: dict[str, Any], event: EventPayload
    ) -> CommandResultPayload:
        del event
        return build_search_web_result(arguments=arguments, module="tool.web")

    return [
        ConcreteTool(
            spec={
                "name": "search_web",
                "description": (
                    "联网搜索公开网页，作为其他工具无结果、失败、信息可能过时，或用户需要当前公开资料时的备用证据路径。"
                    "适合时效信息、公开资料查证、新闻/公告、攻略教程、产品/服务资料、一般知识补充等问题；"
                    "不得用于违法违规、规避监管、侵犯隐私、侵权盗版或危险行为的操作性指导。"
                ),
                "parameters": object_schema(
                    {
                        "query": {
                            "type": "string",
                            "description": "搜索关键词或用户原问题；需要时加入主题、时间、地区、来源类型、产品名、版本号等限定词。",
                        },
                        "top_n": {
                            "type": "integer",
                            "description": "返回网页候选数，默认 5，范围 1-8。",
                            "minimum": 1,
                            "maximum": 8,
                        },
                        "timeout_seconds": {
                            "type": "number",
                            "description": "请求超时时间，默认 6 秒，范围 2-10 秒。",
                            "minimum": 2,
                            "maximum": 10,
                        },
                    },
                    required=["query"],
                ),
            },
            handler=search_web,
        )
    ]
