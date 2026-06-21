from __future__ import annotations

from pathlib import Path
from typing import Any

from ..protocol import CommandResultPayload
from .base import fail_result, ok_result


def build_view_command_guide_result(
    *,
    command_guide_dir: Path,
    arguments: dict[str, Any],
    module: str,
) -> CommandResultPayload:
    command_name = extract_guide_command(arguments)
    if not command_name:
        return fail_result(
            module=module,
            command="view_command_guide",
            data={
                "requested_command": "",
                "available_guides": list_command_guides(command_guide_dir),
                "guide_text": "",
            },
            message="缺少参数 command，请指定要查看的指令教程。",
            extra_prompt="请提示用户先给出 command 参数，例如 script_manual_query。",
        )

    guide_path = command_guide_dir / f"{command_name}.md"
    if not guide_path.exists():
        return fail_result(
            module=module,
            command="view_command_guide",
            data={
                "requested_command": command_name,
                "available_guides": list_command_guides(command_guide_dir),
                "guide_text": "",
            },
            message=f"未找到指令教程: {command_name}",
            extra_prompt="请根据 available_guides 给出最接近的可用指令建议。",
        )

    guide_text = guide_path.read_text(encoding="utf-8").strip()
    return ok_result(
        module=module,
        command="view_command_guide",
        data={
            "requested_command": command_name,
            "guide_file": str(guide_path),
            "guide_text": guide_text,
        },
        message="已读取指令教程",
        extra_prompt=(
            "你拿到了本地指令教程。"
            "如果还需要继续完成任务，可继续输出下一条 command_processor 指令。"
            "如果信息已足够，请直接输出最终答复。"
        ),
    )


def extract_guide_command(arguments: dict[str, Any]) -> str:
    for key in ("command", "command_name", "intent", "name", "topic"):
        value = str(arguments.get(key, "") or "").strip()
        if value:
            return value
    return ""


def list_command_guides(command_guide_dir: Path) -> list[str]:
    if not command_guide_dir.exists():
        return []
    return sorted(
        path.stem for path in command_guide_dir.glob("*.md") if path.is_file()
    )
