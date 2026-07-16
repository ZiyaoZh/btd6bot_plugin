from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..protocol import CommandResultPayload
from .base import fail_result, image_result, ok_result
from .subprocess_runner import AsyncioSubprocessRunner, AsyncSubprocessRunner

MAX_BOT_BOX_COUNT = 10_000


async def build_monkey_box_opening_result(
    *,
    chat_monkey_root: Path,
    arguments: dict[str, Any],
    module: str,
    subprocess_runner: AsyncSubprocessRunner | None = None,
    output_dir: Path | None = None,
) -> CommandResultPayload:
    raw_box_type = (
        str(arguments.get("box_type", "") or arguments.get("box", "") or "")
        .strip()
        .lower()
    )
    box_alias_map = {
        "木头": "木头",
        "wood": "木头",
        "青铜": "青铜",
        "bronze": "青铜",
        "白银": "白银",
        "silver": "白银",
        "黄金": "黄金",
        "gold": "黄金",
        "钻石": "钻石",
        "diamond": "钻石",
    }
    box_type = box_alias_map.get(raw_box_type, "")
    if not box_type:
        return fail_result(
            module=module,
            command="simulate_monkey_box_opening",
            data={
                "box_type": raw_box_type,
                "box_count": arguments.get("box_count", ""),
            },
            message="box_type 仅支持 木头、青铜、白银、黄金、钻石。",
            extra_prompt="请提示用户给出合法箱子类型。",
        )

    raw_box_count = arguments.get("box_count", arguments.get("count", ""))
    try:
        box_count = int(raw_box_count)
    except (TypeError, ValueError):
        box_count = 0

    if box_count <= 0:
        return fail_result(
            module=module,
            command="simulate_monkey_box_opening",
            data={"box_type": box_type, "box_count": raw_box_count},
            message="box_count 必须是正整数。",
            extra_prompt="请提示用户提供正确的开箱数量。",
        )

    if box_count > MAX_BOT_BOX_COUNT:
        return fail_result(
            module=module,
            command="simulate_monkey_box_opening",
            data={"box_type": box_type, "box_count": box_count},
            message=f"box_count 不能大于 {MAX_BOT_BOX_COUNT}。",
            extra_prompt="请提示用户减少开箱数量后重试。",
        )

    run_result = await run_chat_monkey_simulator(
        chat_monkey_root=chat_monkey_root,
        box_type=box_type,
        box_count=box_count,
        subprocess_runner=subprocess_runner,
        output_dir=output_dir,
    )
    if not run_result.ok:
        return fail_result(
            module=module,
            command="simulate_monkey_box_opening",
            data={
                "box_type": box_type,
                "box_count": box_count,
                "report_path": "",
                "image_path": "",
            },
            message=run_result.error_message,
            extra_prompt="请说明开箱模拟失败并建议稍后重试。",
        )

    report_path = run_result.report_path
    image_path = run_result.image_path
    generated_files = [report_path]
    if image_path:
        generated_files.append(image_path)
    execution_data = {
        "fresh_run": True,
        "stored_as_artifact": False,
    }

    if image_path:
        return image_result(
            module=module,
            command="simulate_monkey_box_opening",
            image_path=image_path,
            data={
                "box_type": box_type,
                "box_count": box_count,
                "report_path": report_path,
                "generated_files": generated_files,
                "output_text": run_result.output_text,
                **execution_data,
            },
            message="开箱模拟完成，已生成统计与拼图",
            extra_prompt="只写一句适合作为开箱拼图 caption 的简短总结。外层系统会负责发送图片，不要说图片无法发送。",
        )

    return ok_result(
        module=module,
        command="simulate_monkey_box_opening",
        data={
            "box_type": box_type,
            "box_count": box_count,
            "report_path": report_path,
            "generated_files": generated_files,
            "output_text": run_result.output_text,
            **execution_data,
        },
        message="开箱模拟完成，已生成统计文档",
        extra_prompt="请基于输出文本简要总结本次开箱统计结果。",
    )


@dataclass(frozen=True)
class ChatMonkeyResult:
    ok: bool
    report_path: str
    image_path: str
    output_text: str
    error_message: str = ""


async def run_chat_monkey_simulator(
    *,
    chat_monkey_root: Path,
    box_type: str,
    box_count: int,
    subprocess_runner: AsyncSubprocessRunner | None = None,
    output_dir: Path | None = None,
) -> ChatMonkeyResult:
    main_path = chat_monkey_root / "main.py"
    if not main_path.exists():
        return ChatMonkeyResult(False, "", "", "", "未找到 ChatMonkeyTD/main.py。")

    resolved_output_dir = output_dir or (chat_monkey_root / "output")
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    _cleanup_old_outputs(resolved_output_dir)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = uuid4().hex[:8]
    report_path = (
        resolved_output_dir / f"bot_open_result_{stamp}_{suffix}.md"
    ).resolve()
    image_path = (
        resolved_output_dir / f"bot_open_result_{stamp}_{suffix}.png"
    ).resolve()

    cmd = [
        sys.executable,
        str(main_path.name),
        box_type,
        str(box_count),
        "--report",
        str(report_path),
    ]
    if box_count <= 50:
        cmd.extend(["--output", str(image_path)])

    runner = subprocess_runner or AsyncioSubprocessRunner()
    try:
        proc = await runner.run(
            cmd,
            cwd=str(chat_monkey_root),
            timeout=180,
        )
    except TimeoutError:
        return ChatMonkeyResult(False, "", "", "", "开箱模拟超时，请稍后重试。")
    except Exception as exc:
        return ChatMonkeyResult(
            False, "", "", "", f"执行开箱模拟失败: {type(exc).__name__}"
        )

    if proc.returncode != 0:
        err_text = (proc.stderr or proc.stdout or "").strip()
        err_text = err_text.splitlines()[-1] if err_text else "未知错误"
        return ChatMonkeyResult(False, "", "", "", f"开箱模拟执行失败: {err_text}")

    if not report_path.exists():
        return ChatMonkeyResult(False, "", "", "", "开箱模拟执行成功但未找到统计文档。")

    final_image_path = ""
    if box_count <= 50:
        if not image_path.exists():
            return ChatMonkeyResult(
                False, "", "", "", "开箱模拟执行成功但未找到拼图文件。"
            )
        final_image_path = str(image_path)

    output_text = "\n".join(
        [
            (proc.stdout or "").strip(),
            (proc.stderr or "").strip(),
        ]
    ).strip()

    return ChatMonkeyResult(True, str(report_path), final_image_path, output_text)


def _cleanup_old_outputs(output_dir: Path, max_age_seconds: float = 86_400) -> None:
    cutoff = time.time() - max(60.0, float(max_age_seconds))
    for path in output_dir.glob("bot_open_result_*"):
        try:
            if path.is_file() and path.stat().st_mtime <= cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            continue
