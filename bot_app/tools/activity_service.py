from __future__ import annotations

import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..artifact_store import ArtifactRecord, ArtifactSource, ArtifactStore
from ..protocol import CommandResultPayload
from .base import fail_result, image_result, ok_result
from .subprocess_runner import AsyncioSubprocessRunner, AsyncSubprocessRunner


_ACTIVITY_LABELS = {
    "all": "全活动总览",
    "race": "竞速",
    "boss": "Boss",
    "odyssey": "征程",
    "daily": "每日挑战",
    "boss-standard": "普通 Boss",
    "boss-elite": "精英 Boss",
}
_ACTIVITY_TYPES = {"race", "boss", "odyssey", "daily"}
_BRIEF_ACTIVITY_TYPES = _ACTIVITY_TYPES | {"all"}
_ACTIVITY_TYPE_ALIASES = {
    "all": "all",
    "全部": "all",
    "全部活动": "all",
    "总览": "all",
    "活动总览": "all",
    "竞速": "race",
    "race": "race",
    "boss": "boss",
    "boss活动": "boss",
    "征程": "odyssey",
    "奥德赛": "odyssey",
    "odyssey": "odyssey",
    "每日挑战": "daily",
    "日挑": "daily",
    "daily": "daily",
}


async def build_activity_detail_result(
    *,
    api_processor_root: Path,
    arguments: dict[str, Any],
    module: str,
    subprocess_runner: AsyncSubprocessRunner | None = None,
    artifact_store: ArtifactStore | None = None,
    artifact_ttl_seconds: float = 600.0,
) -> CommandResultPayload:
    raw_type = (
        str(
            arguments.get("activity_type", "") or arguments.get("detail_type", "") or ""
        )
        .strip()
        .lower()
    )
    detail_type = _normalize_activity_type(raw_type)
    if detail_type not in _ACTIVITY_TYPES:
        return fail_result(
            module=module,
            command="get_activity_detail",
            data={"mode": "detail", "activity_type": raw_type},
            message="activity_type 仅支持 race、boss、odyssey、daily。",
            extra_prompt="请提示用户给出合法活动类型。",
        )

    difficulty = str(arguments.get("difficulty", "hard") or "hard").strip().lower()
    if difficulty not in {"easy", "medium", "hard"}:
        return fail_result(
            module=module,
            command="get_activity_detail",
            data={"mode": "detail", "activity_type": detail_type, "difficulty": difficulty},
            message="difficulty 仅支持 easy、medium、hard。",
            extra_prompt="请提示用户给出合法征程难度。",
        )
    event_scope = str(arguments.get("event_scope", "latest") or "latest").strip().lower()
    if event_scope not in {"current", "upcoming", "latest"}:
        return fail_result(
            module=module,
            command="get_activity_detail",
            data={"mode": "detail", "activity_type": detail_type, "event_scope": event_scope},
            message="event_scope 仅支持 current、upcoming、latest。",
            extra_prompt="请提示用户给出合法活动范围。",
        )

    scope_label = {"current": "当前", "upcoming": "下一期", "latest": "最新发布"}[event_scope]
    difficulty_label = {"easy": "简单", "medium": "中级", "hard": "困难"}[difficulty]
    tags = ["BTD6", "活动详情", _ACTIVITY_LABELS[detail_type], scope_label, "文档"]
    title_qualifier = scope_label
    if detail_type == "odyssey":
        title_qualifier += difficulty_label
        tags.append(difficulty_label)
    title = f"BTD6 {title_qualifier}{_ACTIVITY_LABELS[detail_type]}活动详情"

    run_result = await run_btd6_api_cli(
        api_processor_root=api_processor_root,
        mode="detail",
        detail_type=detail_type,
        difficulty=difficulty,
        event_scope=event_scope,
        subprocess_runner=subprocess_runner,
        report_output_dir=_activity_staging_dir(artifact_store),
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

    artifact = _store_activity_artifact(
        artifact_store,
        kind="activity.detail",
        title=title,
        summary=_catalog_summary(run_result.report),
        tags=tuple(tags),
        run_result=run_result,
        ttl_seconds=artifact_ttl_seconds,
        metadata={
            "mode": "detail",
            "activity_type": detail_type,
            "difficulty": difficulty,
            "event_scope": event_scope,
        },
    )
    data = {
        "mode": "detail",
        "activity_type": detail_type,
        "difficulty": difficulty,
        "event_scope": event_scope,
        "generated_files": run_result.generated_files,
        "json_path": _first_existing_file(run_result.generated_files, ".json"),
        "report": run_result.report,
        "stored_as_artifact": False,
    }
    if artifact is not None:
        data = _artifact_result_data(
            artifact,
            report=run_result.report,
            mode="detail",
            activity_type=detail_type,
            difficulty=difficulty,
            event_scope=event_scope,
        )
        data["json_path"] = str(artifact.file("data") or "")
    return ok_result(
        module=module,
        command="get_activity_detail",
        data=data,
        message=f"{detail_type} 活动详情获取完成",
        extra_prompt="请优先基于 report 回答，并提炼关键规则和时间信息。",
    )


async def build_activity_leaderboard_result(
    *,
    api_processor_root: Path,
    arguments: dict[str, Any],
    module: str,
    subprocess_runner: AsyncSubprocessRunner | None = None,
    artifact_store: ArtifactStore | None = None,
    artifact_ttl_seconds: float = 600.0,
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

    tags = (
        "BTD6",
        "活动排行榜",
        _ACTIVITY_LABELS[leaderboard_type],
        "图片",
    )

    run_result = await run_btd6_api_cli(
        api_processor_root=api_processor_root,
        mode="leaderboard",
        leaderboard_type=leaderboard_type,
        leaderboard_format="image",
        subprocess_runner=subprocess_runner,
        report_output_dir=_activity_staging_dir(artifact_store),
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
    artifact = _store_activity_artifact(
        artifact_store,
        kind="activity.leaderboard",
        title=f"BTD6 {_ACTIVITY_LABELS[leaderboard_type]}排行榜",
        summary=_catalog_summary(run_result.report),
        tags=tags,
        run_result=run_result,
        ttl_seconds=artifact_ttl_seconds,
        metadata={
            "mode": "leaderboard",
            "leaderboard_type": leaderboard_type,
        },
    )
    if artifact is not None:
        normalized_image_path = artifact.file("image")
        if normalized_image_path is not None:
            image_path = str(normalized_image_path)
        data = _artifact_result_data(
            artifact,
            report=run_result.report,
            mode="leaderboard",
            leaderboard_type=leaderboard_type,
        )
    else:
        data = {
            "mode": "leaderboard",
            "leaderboard_type": leaderboard_type,
            "generated_files": run_result.generated_files,
            "report": run_result.report,
            "stored_as_artifact": False,
        }
    return image_result(
        module=module,
        command="get_activity_leaderboard",
        image_path=image_path,
        data=data,
        message=f"{leaderboard_type} 排行榜图片已生成",
        extra_prompt="只写一句适合作为图片 caption 的说明。外层系统会负责发送图片，不要说图片无法发送。",
    )


async def build_activity_brief_image_result(
    *,
    api_processor_root: Path,
    arguments: dict[str, Any],
    module: str,
    subprocess_runner: AsyncSubprocessRunner | None = None,
    artifact_store: ArtifactStore | None = None,
    artifact_ttl_seconds: float = 600.0,
) -> CommandResultPayload:
    raw_type = str(arguments.get("activity_type", "") or "").strip().lower()
    activity_type = _normalize_activity_type(raw_type)
    if activity_type not in _BRIEF_ACTIVITY_TYPES:
        return fail_result(
            module=module,
            command="get_activity_brief_image",
            data={"activity_type": raw_type},
            message="activity_type 仅支持 all、race、boss、odyssey、daily。",
            extra_prompt="请提示用户给出合法活动类型。",
        )

    difficulty = str(arguments.get("difficulty", "hard") or "hard").strip().lower()
    if difficulty not in {"easy", "medium", "hard"}:
        return fail_result(
            module=module,
            command="get_activity_brief_image",
            data={"activity_type": activity_type, "difficulty": difficulty},
            message="difficulty 仅支持 easy、medium、hard。",
            extra_prompt="请提示用户给出合法征程难度。",
        )
    event_scope = str(arguments.get("event_scope", "current") or "current").strip().lower()
    if event_scope not in {"current", "upcoming", "latest"}:
        return fail_result(
            module=module,
            command="get_activity_brief_image",
            data={"activity_type": activity_type, "event_scope": event_scope},
            message="event_scope 仅支持 current、upcoming、latest。",
            extra_prompt="请提示用户给出合法活动范围。",
        )

    difficulty_label = {"easy": "简单", "medium": "中级", "hard": "困难"}[difficulty]
    scope_label = {"current": "当前", "upcoming": "下一期", "latest": "最新发布"}[event_scope]
    title_qualifier = scope_label
    if activity_type == "odyssey":
        title_qualifier += difficulty_label
    if activity_type == "all":
        title = f"BTD6 {title_qualifier}{_ACTIVITY_LABELS[activity_type]}情报图"
    else:
        title = f"BTD6 {title_qualifier}{_ACTIVITY_LABELS[activity_type]}活动情报图"
    tags = ["BTD6", "活动情报图", _ACTIVITY_LABELS[activity_type], scope_label, "图片"]
    if activity_type in {"all", "odyssey"}:
        tags.append(difficulty_label)

    run_result = await run_btd6_api_cli(
        api_processor_root=api_processor_root,
        mode="brief-image",
        activity_type=activity_type,
        difficulty=difficulty,
        event_scope=event_scope,
        subprocess_runner=subprocess_runner,
        report_output_dir=_activity_staging_dir(artifact_store),
    )
    if not run_result.ok:
        return fail_result(
            module=module,
            command="get_activity_brief_image",
            data={
                "mode": "brief-image",
                "activity_type": activity_type,
                "difficulty": difficulty,
                "event_scope": event_scope,
                "generated_files": [],
            },
            message=run_result.error_message,
            extra_prompt="请说明活动情报图获取失败并建议稍后重试。",
        )

    image_path = _first_existing_file(run_result.generated_files, ".png")
    if not image_path:
        return fail_result(
            module=module,
            command="get_activity_brief_image",
            data={
                "mode": "brief-image",
                "activity_type": activity_type,
                "difficulty": difficulty,
                "event_scope": event_scope,
                "generated_files": run_result.generated_files,
            },
            message="活动情报图未生成成功。",
            extra_prompt="请说明未找到图片文件，并建议稍后重试。",
        )
    json_path = _first_existing_file(run_result.generated_files, ".json")
    if not json_path:
        return fail_result(
            module=module,
            command="get_activity_brief_image",
            data={
                "mode": "brief-image",
                "activity_type": activity_type,
                "difficulty": difficulty,
                "event_scope": event_scope,
                "generated_files": run_result.generated_files,
            },
            message="活动情报图缺少对应的结构化快照。",
            extra_prompt="请说明活动快照未生成完整，并建议稍后重试。",
        )

    artifact = _store_activity_artifact(
        artifact_store,
        kind="activity.brief",
        title=title,
        summary=_catalog_summary(run_result.report),
        tags=tuple(tags),
        run_result=run_result,
        ttl_seconds=artifact_ttl_seconds,
        metadata={
            "mode": "brief-image",
            "activity_type": activity_type,
            "difficulty": difficulty,
            "event_scope": event_scope,
        },
    )
    if artifact is not None:
        normalized_image_path = artifact.file("image")
        if normalized_image_path is not None:
            image_path = str(normalized_image_path)
        data = _artifact_result_data(
            artifact,
            report=run_result.report,
            mode="brief-image",
            activity_type=activity_type,
            difficulty=difficulty,
            event_scope=event_scope,
        )
        data["json_path"] = str(artifact.file("data") or "")
    else:
        data = {
            "mode": "brief-image",
            "activity_type": activity_type,
            "difficulty": difficulty,
            "event_scope": event_scope,
            "generated_files": run_result.generated_files,
            "report": run_result.report,
            "json_path": json_path,
            "stored_as_artifact": False,
        }
    return image_result(
        module=module,
        command="get_activity_brief_image",
        image_path=image_path,
        data=data,
        message=f"{scope_label}{_ACTIVITY_LABELS[activity_type]}情报图已生成",
        extra_prompt="只写一句准确的图片说明。外层系统会发送图片，不要改写图中事实。",
    )


def _normalize_activity_type(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return _ACTIVITY_TYPE_ALIASES.get(normalized, normalized)


async def build_collection_event_info_result(
    *,
    api_processor_root: Path,
    arguments: dict[str, Any],
    module: str,
    subprocess_runner: AsyncSubprocessRunner | None = None,
    artifact_store: ArtifactStore | None = None,
    artifact_ttl_seconds: float = 600.0,
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

    tags = (
        "BTD6",
        "收集活动",
        "轮换",
        "图片",
        "仅当前和未来" if only_upcoming else "完整轮换",
    )

    run_result = await run_btd6_api_cli(
        api_processor_root=api_processor_root,
        mode="collection-event",
        collection_only_upcoming=only_upcoming,
        subprocess_runner=subprocess_runner,
        report_output_dir=_activity_staging_dir(artifact_store),
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

    artifact = _store_activity_artifact(
        artifact_store,
        kind="activity.collection",
        title=(
            "BTD6 收集活动轮换（仅当前和未来）"
            if only_upcoming
            else "BTD6 收集活动完整轮换"
        ),
        summary=_catalog_summary(run_result.report),
        tags=tags,
        run_result=run_result,
        ttl_seconds=artifact_ttl_seconds,
        metadata={
            "mode": "collection-event",
            "only_upcoming": only_upcoming,
        },
    )
    if artifact is not None:
        normalized_image_path = artifact.file("image")
        if normalized_image_path is not None:
            image_path = str(normalized_image_path)
        data = _artifact_result_data(
            artifact,
            report=run_result.report,
            mode="collection-event",
            only_upcoming=only_upcoming,
        )
        data["json_path"] = str(artifact.file("data") or "")
    else:
        data = {
            "mode": "collection-event",
            "only_upcoming": only_upcoming,
            "generated_files": run_result.generated_files,
            "json_path": json_path,
            "report": run_result.report,
            "stored_as_artifact": False,
        }
    return image_result(
        module=module,
        command="get_collection_event_info",
        image_path=image_path,
        data=data,
        message="收集活动轮换图片已生成",
        extra_prompt="只写一句适合作为图片 caption 的说明。外层系统会负责发送图片，不要说图片无法发送。",
    )


@dataclass(frozen=True)
class Btd6CliResult:
    ok: bool
    report: str
    generated_files: list[str]
    error_message: str = ""
    report_path: str = ""


async def run_btd6_api_cli(
    *,
    api_processor_root: Path,
    mode: str,
    detail_type: str = "",
    activity_type: str = "",
    difficulty: str = "hard",
    event_scope: str = "latest",
    leaderboard_type: str = "",
    leaderboard_format: str = "markdown",
    collection_only_upcoming: bool = False,
    subprocess_runner: AsyncSubprocessRunner | None = None,
    report_output_dir: Path | None = None,
) -> Btd6CliResult:
    cli_path = api_processor_root / "btd6_cli.py"
    if not cli_path.exists():
        return Btd6CliResult(False, "", [], "未找到 btd6_api_processor/btd6_cli.py。")

    (api_processor_root / "output").mkdir(parents=True, exist_ok=True)
    output_dir = report_output_dir or (api_processor_root / "output")
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = output_dir / f"bot_{mode}_{stamp}_{uuid4().hex[:8]}.md"

    cmd = [
        sys.executable,
        str(cli_path.name),
        "--mode",
        mode,
        "--output",
        _cli_output_argument(output_file, api_processor_root),
    ]
    if mode == "detail" and detail_type:
        cmd.extend(
            [
                "--detail-types",
                detail_type,
                "--difficulty",
                difficulty,
                "--event-scope",
                event_scope,
            ]
        )
    if mode == "brief-image":
        cmd.extend(
            [
                "--activity-type",
                activity_type or "race",
                "--difficulty",
                difficulty,
                "--event-scope",
                event_scope,
            ]
        )
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

    raw_report = output_file.read_text(encoding="utf-8", errors="ignore").strip()
    files = extract_generated_files(raw_report, api_processor_root=api_processor_root)
    report = _without_source_file_paths(raw_report)
    output_file.write_text(report, encoding="utf-8")
    return Btd6CliResult(True, report, files, report_path=str(output_file.resolve()))


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


def _without_source_file_paths(report: str) -> str:
    lines = [
        line
        for line in str(report or "").splitlines()
        if not line.strip().startswith(("# 文件:", "# JSON:", "# 图片:"))
    ]
    return "\n".join(lines).strip()


def _activity_staging_dir(artifact_store: ArtifactStore | None) -> Path | None:
    if artifact_store is None:
        return None
    return artifact_store.staging_dir / "activity"


def _cli_output_argument(output_file: Path, api_processor_root: Path) -> str:
    try:
        return str(output_file.relative_to(api_processor_root))
    except ValueError:
        return str(output_file.resolve())


def _store_activity_artifact(
    artifact_store: ArtifactStore | None,
    *,
    kind: str,
    title: str,
    summary: str,
    tags: tuple[str, ...],
    run_result: Btd6CliResult,
    ttl_seconds: float,
    metadata: dict[str, Any],
) -> ArtifactRecord | None:
    if artifact_store is None or not run_result.report_path:
        return None

    sources = [
        ArtifactSource(
            path=Path(run_result.report_path),
            role="report",
            media_type="text/markdown",
            remove_after_store=True,
        )
    ]
    image_path = _first_existing_file(run_result.generated_files, ".png")
    if image_path:
        sources.append(
            ArtifactSource(path=Path(image_path), role="image", media_type="image/png")
        )
    json_path = _first_existing_file(run_result.generated_files, ".json")
    if json_path:
        sources.append(
            ArtifactSource(
                path=Path(json_path),
                role="data",
                media_type="application/json",
            )
        )
    try:
        return artifact_store.register(
            kind=kind,
            title=title,
            summary=summary,
            tags=tags,
            files=sources,
            ttl_seconds=ttl_seconds,
            metadata=metadata,
        )
    except (OSError, sqlite3.Error, ValueError):
        return None


def _artifact_result_data(
    artifact: ArtifactRecord,
    *,
    report: str,
    **values: Any,
) -> dict[str, Any]:
    return {
        **values,
        "generated_files": [str(item.path) for item in artifact.files],
        "report": report,
        "stored_as_artifact": True,
        "artifact_id": artifact.artifact_id,
        "artifact_tags": list(artifact.tags),
        "artifact_expires_at": artifact.expires_at.isoformat(),
    }


def _catalog_summary(report: str, max_chars: int = 280) -> str:
    lines = [
        line.strip(" #-\t")
        for line in str(report or "").splitlines()
        if line.strip() and not line.strip().startswith("# 来源:")
    ]
    summary = "；".join(line for line in lines if line)
    if len(summary) <= max_chars:
        return summary
    return f"{summary[: max_chars - 1].rstrip()}…"
