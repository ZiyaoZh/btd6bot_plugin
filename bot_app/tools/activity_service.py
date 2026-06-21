from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..protocol import CommandResultPayload
from .base import fail_result, image_result, ok_result
from .subprocess_runner import AsyncioSubprocessRunner, AsyncSubprocessRunner


async def build_activity_detail_result(
    *,
    api_processor_root: Path,
    arguments: dict[str, Any],
    module: str,
    subprocess_runner: AsyncSubprocessRunner | None = None,
) -> CommandResultPayload:
    raw_type = (
        str(
            arguments.get("activity_type", "") or arguments.get("detail_type", "") or ""
        )
        .strip()
        .lower()
    )
    alias_map = {
        "竞速": "race",
        "race": "race",
        "boss": "boss",
        "boss活动": "boss",
        "征程": "odyssey",
        "odyssey": "odyssey",
        "每日挑战": "daily",
        "daily": "daily",
    }
    detail_type = alias_map.get(raw_type, raw_type)
    if detail_type not in {"race", "boss", "odyssey", "daily"}:
        return fail_result(
            module=module,
            command="get_activity_detail",
            data={"mode": "detail", "activity_type": raw_type},
            message="activity_type 仅支持 race、boss、odyssey、daily。",
            extra_prompt="请提示用户给出合法活动类型。",
        )

    run_result = await run_btd6_api_cli(
        api_processor_root=api_processor_root,
        mode="detail",
        detail_type=detail_type,
        subprocess_runner=subprocess_runner,
    )
    if not run_result.ok:
        return fail_result(
            module=module,
            command="get_activity_detail",
            data={
                "mode": "detail",
                "activity_type": detail_type,
                "generated_files": [],
            },
            message=run_result.error_message,
            extra_prompt="请说明详情获取失败并建议稍后重试。",
        )

    return ok_result(
        module=module,
        command="get_activity_detail",
        data={
            "mode": "detail",
            "activity_type": detail_type,
            "generated_files": run_result.generated_files,
            "report": run_result.report,
        },
        message=f"{detail_type} 活动详情获取完成",
        extra_prompt="请优先基于 report 回答，并提炼关键规则和时间信息。",
    )


async def build_activity_leaderboard_result(
    *,
    api_processor_root: Path,
    arguments: dict[str, Any],
    module: str,
    subprocess_runner: AsyncSubprocessRunner | None = None,
) -> CommandResultPayload:
    raw_type = (
        str(
            arguments.get("leaderboard_type", "")
            or arguments.get("activity_type", "")
            or ""
        )
        .strip()
        .lower()
    )
    alias_map = {
        "竞速": "race",
        "race": "race",
        "boss": "boss-standard",
        "boss-standard": "boss-standard",
        "普通boss": "boss-standard",
        "boss-elite": "boss-elite",
        "精英boss": "boss-elite",
    }
    leaderboard_type = alias_map.get(raw_type, raw_type)
    if leaderboard_type not in {"race", "boss-standard", "boss-elite"}:
        return fail_result(
            module=module,
            command="get_activity_leaderboard",
            data={"mode": "leaderboard", "leaderboard_type": raw_type},
            message="leaderboard_type 仅支持 race、boss-standard、boss-elite。",
            extra_prompt="请提示用户给出合法排行榜类型。",
        )

    run_result = await run_btd6_api_cli(
        api_processor_root=api_processor_root,
        mode="leaderboard",
        leaderboard_type=leaderboard_type,
        leaderboard_format="image",
        subprocess_runner=subprocess_runner,
    )
    if not run_result.ok:
        return fail_result(
            module=module,
            command="get_activity_leaderboard",
            data={
                "mode": "leaderboard",
                "leaderboard_type": leaderboard_type,
                "generated_files": [],
            },
            message=run_result.error_message,
            extra_prompt="请说明排行榜获取失败并建议稍后重试。",
        )

    image_path = _first_existing_file(run_result.generated_files, ".png")
    if not image_path:
        return fail_result(
            module=module,
            command="get_activity_leaderboard",
            data={
                "mode": "leaderboard",
                "leaderboard_type": leaderboard_type,
                "generated_files": run_result.generated_files,
            },
            message="排行榜图片未生成成功。",
            extra_prompt="请说明未找到图片文件，并建议稍后重试。",
        )

    return image_result(
        module=module,
        command="get_activity_leaderboard",
        image_path=image_path,
        data={
            "mode": "leaderboard",
            "leaderboard_type": leaderboard_type,
            "generated_files": run_result.generated_files,
            "report": run_result.report,
        },
        message=f"{leaderboard_type} 排行榜图片已生成",
        extra_prompt="只写一句适合作为图片 caption 的说明。外层系统会负责发送图片，不要说图片无法发送。",
    )


async def build_collection_event_info_result(
    *,
    api_processor_root: Path,
    arguments: dict[str, Any],
    module: str,
    subprocess_runner: AsyncSubprocessRunner | None = None,
) -> CommandResultPayload:
    raw_only_upcoming = str(arguments.get("only_upcoming", "") or "").strip().lower()
    only_upcoming = raw_only_upcoming in {
        "1",
        "true",
        "yes",
        "on",
        "upcoming",
        "仅未来",
        "未来",
    }

    run_result = await run_btd6_api_cli(
        api_processor_root=api_processor_root,
        mode="collection-event",
        collection_only_upcoming=only_upcoming,
        subprocess_runner=subprocess_runner,
    )
    if not run_result.ok:
        return fail_result(
            module=module,
            command="get_collection_event_info",
            data={
                "mode": "collection-event",
                "only_upcoming": only_upcoming,
                "generated_files": [],
            },
            message=run_result.error_message,
            extra_prompt="请说明收集活动查询失败并建议稍后重试。",
        )

    image_path = _first_existing_file(run_result.generated_files, ".png")
    json_path = _first_existing_file(run_result.generated_files, ".json")
    if not image_path:
        return fail_result(
            module=module,
            command="get_collection_event_info",
            data={
                "mode": "collection-event",
                "only_upcoming": only_upcoming,
                "generated_files": run_result.generated_files,
                "report": run_result.report,
            },
            message="收集活动图片未生成成功。",
            extra_prompt="请说明未找到收集活动图片，并建议稍后重试。",
        )

    return image_result(
        module=module,
        command="get_collection_event_info",
        image_path=image_path,
        data={
            "mode": "collection-event",
            "only_upcoming": only_upcoming,
            "generated_files": run_result.generated_files,
            "json_path": json_path,
            "report": run_result.report,
        },
        message="收集活动轮换图片已生成",
        extra_prompt="只写一句适合作为图片 caption 的说明。外层系统会负责发送图片，不要说图片无法发送。",
    )


@dataclass(frozen=True)
class Btd6CliResult:
    ok: bool
    report: str
    generated_files: list[str]
    error_message: str = ""


async def run_btd6_api_cli(
    *,
    api_processor_root: Path,
    mode: str,
    detail_type: str = "",
    leaderboard_type: str = "",
    leaderboard_format: str = "markdown",
    collection_only_upcoming: bool = False,
    subprocess_runner: AsyncSubprocessRunner | None = None,
) -> Btd6CliResult:
    cli_path = api_processor_root / "btd6_cli.py"
    if not cli_path.exists():
        return Btd6CliResult(False, "", [], "未找到 btd6_api_processor/btd6_cli.py。")

    output_dir = api_processor_root / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = output_dir / f"bot_{mode}_{stamp}_{uuid4().hex[:8]}.md"

    cmd = [
        sys.executable,
        str(cli_path.name),
        "--mode",
        mode,
        "--output",
        str(output_file.relative_to(api_processor_root)),
    ]
    if mode == "detail" and detail_type:
        cmd.extend(["--detail-types", detail_type])
    if mode == "leaderboard":
        cmd.extend(
            [
                "--leaderboard-type",
                leaderboard_type or "race",
                "--leaderboard-format",
                leaderboard_format,
            ]
        )
    if mode == "collection-event" and collection_only_upcoming:
        cmd.append("--only-upcoming")

    env = os.environ.copy()
    nk_api_key = os.getenv("NK_API_KEY", "").strip()
    if nk_api_key:
        env["NK_API_KEY"] = nk_api_key

    runner = subprocess_runner or AsyncioSubprocessRunner()
    try:
        proc = await runner.run(
            cmd,
            cwd=str(api_processor_root),
            env=env,
            timeout=180,
        )
    except TimeoutError:
        return Btd6CliResult(False, "", [], "获取游戏资讯超时，请稍后重试。")
    except Exception as exc:
        return Btd6CliResult(
            False, "", [], f"执行游戏资讯工具失败: {type(exc).__name__}"
        )

    if proc.returncode != 0:
        err_text = (proc.stderr or proc.stdout or "").strip()
        err_text = err_text.splitlines()[-1] if err_text else "未知错误"
        return Btd6CliResult(False, "", [], f"游戏资讯工具执行失败: {err_text}")

    if not output_file.exists():
        return Btd6CliResult(False, "", [], "工具执行成功但未找到输出文件。")

    report = output_file.read_text(encoding="utf-8", errors="ignore").strip()
    files = extract_generated_files(report, api_processor_root=api_processor_root)
    return Btd6CliResult(True, report, files)


def extract_generated_files(report: str, *, api_processor_root: Path) -> list[str]:
    files: list[str] = []
    for line in (report or "").splitlines():
        text = line.strip()
        for prefix in ("# 文件:", "# JSON:", "# 图片:"):
            if text.startswith(prefix):
                raw = text.split(":", 1)[1].strip()
                if raw:
                    files.append(str((api_processor_root / raw).resolve()))
                break
        else:
            match = re.search(r"->\s*([\w./-]+\.(?:md|png|json))\s*$", text)
            if match:
                files.append(str((api_processor_root / match.group(1)).resolve()))

    deduped = []
    seen: set[str] = set()
    for path in files:
        if path in seen:
            continue
        seen.add(path)
        deduped.append(path)
    return deduped


def _first_existing_file(paths: list[str], suffix: str) -> str:
    for path in paths:
        if path.lower().endswith(suffix) and Path(path).exists():
            return path
    return ""
