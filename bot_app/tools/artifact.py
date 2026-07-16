from __future__ import annotations

import asyncio
from typing import Any

from ..artifact_store import ArtifactFile, ArtifactRecord
from ..protocol import CommandResultPayload, EventPayload
from .base import (
    ConcreteTool,
    ToolDependencies,
    fail_result,
    image_result,
    object_schema,
    ok_result,
)


MAX_ARTIFACT_TEXT_CHARS = 50_000


def build_artifact_tools(dependencies: ToolDependencies) -> list[ConcreteTool]:
    async def list_artifacts(
        arguments: dict[str, Any], event: EventPayload
    ) -> CommandResultPayload:
        del event
        store = dependencies.artifact_store
        if store is None:
            return fail_result(
                module="tool.artifact",
                command="list_artifacts",
                data={"reason": "not_configured"},
                message="产物目录尚未配置。",
                extra_prompt="请改用对应的获取或生成工具。",
            )

        raw_limit = arguments.get("limit", 50)
        try:
            limit = max(1, min(100, int(raw_limit)))
        except (TypeError, ValueError):
            limit = 50
        kind = str(arguments.get("kind", "") or "").strip().lower()
        records = await asyncio.to_thread(store.list_active, limit=limit, kind=kind)
        artifacts = [_serialize_artifact(record) for record in records]
        return ok_result(
            module="tool.artifact",
            command="list_artifacts",
            data={
                "artifact_count": len(artifacts),
                "artifacts": artifacts,
                "catalog_text": _render_catalog(records),
                "only_unexpired": True,
            },
            message=(
                f"当前有 {len(artifacts)} 个未过期产物"
                if artifacts
                else "当前没有未过期产物"
            ),
            extra_prompt=(
                "请结合用户原话，自行比较标题、摘要、标签、日期和文件角色。"
                "找到语义相关的产物时调用 read_artifact 读取或发送；"
                "不要要求标签完全相同。没有合适项时再调用获取或生成工具。"
            ),
        )

    async def read_artifact(
        arguments: dict[str, Any], event: EventPayload
    ) -> CommandResultPayload:
        del event
        store = dependencies.artifact_store
        if store is None:
            return fail_result(
                module="tool.artifact",
                command="read_artifact",
                data={"reason": "not_configured"},
                message="产物目录尚未配置。",
                extra_prompt="请改用对应的获取或生成工具。",
            )

        artifact_id = str(arguments.get("artifact_id", "") or "").strip().lower()
        record = await asyncio.to_thread(store.get_active, artifact_id)
        if record is None:
            return fail_result(
                module="tool.artifact",
                command="read_artifact",
                data={"artifact_id": artifact_id, "reason": "not_found_or_expired"},
                message="未找到该产物，或产物已经过期。",
                extra_prompt="请重新查看 list_artifacts，再选择有效产物。",
            )

        requested_role = str(arguments.get("role", "") or "").strip().lower()
        artifact_file = _select_file(record, requested_role)
        if artifact_file is None:
            return fail_result(
                module="tool.artifact",
                command="read_artifact",
                data={
                    "artifact_id": artifact_id,
                    "requested_role": requested_role,
                    "available_roles": [item.role for item in record.files],
                },
                message="该产物没有指定角色的文件。",
                extra_prompt="请根据 available_roles 选择 report、image、data 等有效角色。",
            )

        data = {
            "artifact": _serialize_artifact(record),
            "selected_role": artifact_file.role,
            "selected_path": str(artifact_file.path),
            "reused": True,
        }
        if _is_image(artifact_file):
            return image_result(
                module="tool.artifact",
                command="read_artifact",
                image_path=str(artifact_file.path),
                data=data,
                message=f"已取用产物：{record.title}",
                extra_prompt=(
                    "这是未过期的已有图片产物，会随最终回复发送。"
                    "请直接基于它回复，不要再次调用生图或活动图片工具。"
                ),
            )

        if _is_readable_text(artifact_file):
            content = await asyncio.to_thread(
                artifact_file.path.read_text,
                encoding="utf-8",
                errors="ignore",
            )
            truncated = len(content) > MAX_ARTIFACT_TEXT_CHARS
            data.update(
                {
                    "content": content[:MAX_ARTIFACT_TEXT_CHARS],
                    "content_truncated": truncated,
                }
            )
            return ok_result(
                module="tool.artifact",
                command="read_artifact",
                data=data,
                message=f"已读取产物：{record.title}",
                extra_prompt=(
                    "请直接使用 content 中的已有信息回答。"
                    "除非内容明显不满足用户问题，否则不要重复获取同类资料。"
                ),
            )

        return ok_result(
            module="tool.artifact",
            command="read_artifact",
            data=data,
            message=f"已定位产物文件：{record.title}",
            extra_prompt="该文件不可作为文本读取，请依据文件角色和路径决定下一步。",
        )

    return [
        ConcreteTool(
            spec={
                "name": "list_artifacts",
                "description": (
                    "查看当前全部或指定类型的未过期 Agent 产物清单。"
                    "清单包含易读标题、摘要、标签、生成时间、过期时间和文件路径。"
                    "当用户请求的资料、活动信息、报告或图片可能已经获取/生成过时，"
                    "优先使用此工具查看，不要求标签完全匹配。"
                ),
                "parameters": object_schema(
                    {
                        "kind": {
                            "type": "string",
                            "description": "可选的产物类型；留空时返回所有有效产物。",
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 100,
                            "description": "最多返回多少项，默认 50。",
                        },
                    }
                ),
            },
            handler=list_artifacts,
        ),
        ConcreteTool(
            spec={
                "name": "read_artifact",
                "description": (
                    "按 list_artifacts 返回的 artifact_id 取用一个未过期产物。"
                    "文档会返回正文，图片会直接作为图片结果发送。"
                ),
                "parameters": object_schema(
                    {
                        "artifact_id": {
                            "type": "string",
                            "description": "产物清单中的 artifact_id。",
                            "minLength": 1,
                        },
                        "role": {
                            "type": "string",
                            "description": (
                                "可选文件角色，如 report、image、data。"
                                "留空时优先读取 report，其次取 image。"
                            ),
                        },
                    },
                    required=["artifact_id"],
                ),
            },
            handler=read_artifact,
        ),
    ]


def _serialize_artifact(record: ArtifactRecord) -> dict[str, Any]:
    return {
        "artifact_id": record.artifact_id,
        "title": record.title,
        "summary": record.summary,
        "kind": record.kind,
        "status": record.status,
        "tags": list(record.tags),
        "created_at": _display_datetime(record.created_at),
        "expires_at": _display_datetime(record.expires_at),
        "last_accessed_at": _display_datetime(record.last_accessed_at),
        "hit_count": record.hit_count,
        "files": [
            {
                "role": item.role,
                "path": str(item.path),
                "media_type": item.media_type,
                "size_bytes": item.size_bytes,
            }
            for item in record.files
        ],
    }


def _render_catalog(records: tuple[ArtifactRecord, ...]) -> str:
    if not records:
        return "当前没有未过期产物。"
    lines: list[str] = []
    for record in records:
        tags = "、".join(record.tags) or "无"
        files = "；".join(
            f"{item.role}={item.path}" for item in record.files
        )
        summary = record.summary or "无摘要"
        lines.append(
            f"[{record.artifact_id}] {record.title}\n"
            f"  类型: {record.kind}\n"
            f"  摘要: {summary}\n"
            f"  标签: {tags}\n"
            f"  生成: {_display_datetime(record.created_at)}\n"
            f"  过期: {_display_datetime(record.expires_at)}\n"
            f"  文件: {files}"
        )
    return "\n\n".join(lines)


def _select_file(record: ArtifactRecord, requested_role: str) -> ArtifactFile | None:
    if requested_role:
        return next(
            (item for item in record.files if item.role == requested_role),
            None,
        )
    for preferred_role in ("report", "image", "data"):
        selected = next(
            (item for item in record.files if item.role == preferred_role),
            None,
        )
        if selected is not None:
            return selected
    return record.files[0] if record.files else None


def _is_image(item: ArtifactFile) -> bool:
    return item.media_type.startswith("image/") or item.path.suffix.lower() in {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
    }


def _is_readable_text(item: ArtifactFile) -> bool:
    return (
        item.media_type.startswith("text/")
        or item.media_type == "application/json"
        or item.path.suffix.lower() in {".md", ".txt", ".json", ".csv"}
    )


def _display_datetime(value: Any) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S UTC")
