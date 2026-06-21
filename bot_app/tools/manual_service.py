from __future__ import annotations

from pathlib import Path
from typing import Any

from ..protocol import CommandResultPayload, EventPayload
from .base import fail_result, ok_result


def build_script_manual_query_result(
    *,
    manual_file: Path,
    manual_text_cache: str,
    event: EventPayload,
    arguments: dict[str, Any],
    module: str,
) -> tuple[CommandResultPayload, str]:
    query = str(arguments.get("query", "") or "").strip() or event["text"]
    manual_text = _load_manual_text(manual_file, manual_text_cache)

    if not manual_text:
        return (
            fail_result(
                module=module,
                command="script_manual_query",
                data={
                    "query": query,
                    "manual_file": str(manual_file),
                    "manual_full_text": "",
                },
                message="未找到说明书内容，请先补充说明书文件。",
                extra_prompt="直接说明说明书缺失，并引导管理员补充 BOT_MANUAL_FILE 指向的文档。",
            ),
            manual_text,
        )

    return (
        ok_result(
            module=module,
            command="script_manual_query",
            data={
                "query": query,
                "manual_file": str(manual_file),
                "manual_full_text": manual_text,
            },
            message="说明书检索完成",
            extra_prompt=(
                "你现在要扮演程序说明书问答助手。"
                "请优先依据 command_result.data.manual_full_text 回答用户问题。"
                "如果说明书仍不足以回答，请明确说明“说明书未覆盖该细节”，并给出可执行下一步。"
                "不要凭空编造。"
            ),
        ),
        manual_text,
    )


def _load_manual_text(manual_file: Path, manual_text_cache: str) -> str:
    if manual_text_cache:
        return manual_text_cache

    if not manual_file.exists():
        return ""

    try:
        return manual_file.read_text(encoding="utf-8", errors="ignore").strip()
    except Exception:
        return ""
