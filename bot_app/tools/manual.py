from __future__ import annotations

from typing import Any

from ..protocol import CommandResultPayload, EventPayload
from .base import ConcreteTool, ToolRuntime, object_schema


def build_manual_tools(runtime: ToolRuntime) -> list[ConcreteTool]:
    async def script_manual_query(
        arguments: dict[str, Any], event: EventPayload
    ) -> CommandResultPayload:
        query = str(arguments.get("query", "") or "").strip() or event["text"]
        manual_text = _load_manual_text(runtime)

        if not manual_text:
            return {
                "module": "tool.manual",
                "ok": False,
                "command": "script_manual_query",
                "data": {
                    "query": query,
                    "manual_file": str(runtime.manual_file or ""),
                    "manual_full_text": "",
                },
                "message": "未找到说明书内容，请先补充说明书文件。",
                "extra_prompt": "直接说明说明书缺失，并引导管理员补充 BOT_MANUAL_FILE 指向的文档。",
                "response_type": "text",
                "image_path": "",
            }

        return {
            "module": "tool.manual",
            "ok": True,
            "command": "script_manual_query",
            "data": {
                "query": query,
                "manual_file": str(runtime.manual_file),
                "manual_full_text": manual_text,
            },
            "message": "说明书检索完成",
            "extra_prompt": (
                "你现在要扮演程序说明书问答助手。"
                "请优先依据 command_result.data.manual_full_text 回答用户问题。"
                "如果说明书仍不足以回答，请明确说明“说明书未覆盖该细节”，并给出可执行下一步。"
                "不要凭空编造。"
            ),
            "response_type": "text",
            "image_path": "",
        }

    return [
        ConcreteTool(
            spec={
                "name": "script_manual_query",
                "description": "检索脚本程序说明书，并基于说明书回答脚本启动、配置、刷收集、刷黑框等问题。",
                "parameters": object_schema(
                    {
                        "query": {
                            "type": "string",
                            "description": "用户关于脚本说明书的问题或关键词。",
                        }
                    },
                    required=["query"],
                ),
            },
            handler=script_manual_query,
        )
    ]


def _load_manual_text(runtime: ToolRuntime) -> str:
    if runtime.manual_text_cache:
        return runtime.manual_text_cache

    if runtime.manual_file is None or not runtime.manual_file.exists():
        return ""

    try:
        runtime.manual_text_cache = runtime.manual_file.read_text(
            encoding="utf-8", errors="ignore"
        ).strip()
    except Exception:
        runtime.manual_text_cache = ""
    return runtime.manual_text_cache
