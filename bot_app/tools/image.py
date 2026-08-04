from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from typing import Any

from ..artifact_store import ArtifactSource
from ..image_request import classify_image_request
from ..image_prompting import MAX_IMAGE_PROMPT_CHARS, build_image_generation_prompt
from ..protocol import CommandResultPayload, EventPayload
from ..settings import calculate_image_gen_execution_timeout
from .base import (
    ConcreteTool,
    ToolDependencies,
    fail_result,
    image_result,
    object_schema,
)
from .image_generation_service import ImageGenerationError, generate_image_file


MAX_PROMPT_CHARS = MAX_IMAGE_PROMPT_CHARS


def build_image_tools(dependencies: ToolDependencies) -> list[ConcreteTool]:
    async def generate_image(
        arguments: dict[str, Any], event: EventPayload
    ) -> CommandResultPayload:
        prompt = str(arguments.get("prompt", "") or "").strip()
        if not prompt:
            return fail_result(
                module="tool.image",
                command="generate_image",
                data={"reason": "missing_prompt"},
                message="缺少生图提示词。",
                extra_prompt=(
                    "请根据用户要求补全清晰的图片描述后重试 generate_image。"
                ),
            )
        if len(prompt) > MAX_PROMPT_CHARS:
            return fail_result(
                module="tool.image",
                command="generate_image",
                data={"reason": "prompt_too_long"},
                message=f"生图提示词不能超过 {MAX_PROMPT_CHARS} 个字符。",
                extra_prompt=(
                    "请压缩图片描述，保留主体、场景、构图、"
                    "风格等关键要求后重试。"
                ),
            )
        image_request = classify_image_request(
            "\n".join(
                (
                    str(event.get("text", "") or ""),
                    prompt,
                )
            )
        )
        if (
            image_request is not None
            and image_request.kind in {"infographic", "leaderboard", "collection"}
            and (image_request.activity_types or image_request.kind != "infographic")
        ):
            return fail_result(
                module="tool.image",
                command="generate_image",
                data={"reason": "use_activity_brief_tool"},
                message=(
                    "事实型 BTD6 活动图不能使用通用 AI 生图；活动情报图请使用 "
                    "get_activity_brief_image，排行榜和收集轮换请使用各自的专用工具。"
                ),
                extra_prompt="请调用活动情报图、排行榜或收集活动专用工具，不要用 AI 生图伪造事实。",
            )

        effective_prompt = build_image_generation_prompt(
            prompt,
            user_request=str(event.get("text", "") or ""),
        )
        artifact_tags = _image_artifact_tags(
            model=dependencies.image_gen_model,
        )

        if not all(
            (
                dependencies.image_gen_api_base_url,
                dependencies.image_gen_api_key,
                dependencies.image_gen_model,
            )
        ):
            return fail_result(
                module="tool.image",
                command="generate_image",
                data={"reason": "not_configured"},
                message="AI 生图工具尚未完成服务配置。",
                extra_prompt=(
                    "请告知用户生图服务暂不可用，"
                    "不要编造图片或暴露内部配置项。"
                ),
            )

        output_dir = dependencies.plugin_root / "data" / "generated_images"
        if dependencies.artifact_store is not None:
            output_dir = dependencies.artifact_store.staging_dir / "image-generation"
        try:
            output_path = await asyncio.to_thread(
                generate_image_file,
                prompt=effective_prompt,
                api_base_url=dependencies.image_gen_api_base_url,
                api_key=dependencies.image_gen_api_key,
                model=dependencies.image_gen_model,
                output_dir=output_dir,
                timeout=dependencies.image_gen_request_timeout,
                max_bytes=dependencies.image_gen_max_bytes,
                max_retries=dependencies.image_gen_max_retries,
                retry_delay_seconds=dependencies.image_gen_retry_delay_seconds,
                use_proxy=dependencies.image_gen_use_proxy,
            )
        except ImageGenerationError as exc:
            return fail_result(
                module="tool.image",
                command="generate_image",
                data={
                    "reason": exc.code,
                    "status_code": exc.status_code,
                    "attempts": exc.attempts,
                },
                message=str(exc),
                extra_prompt=(
                    "请简要说明生图失败并建议用户稍后重试；"
                    "不要编造已经生成了图片。"
                ),
            )
        except Exception:
            return fail_result(
                module="tool.image",
                command="generate_image",
                data={"reason": "unexpected_error"},
                message="AI 生图失败，请稍后重试。",
                extra_prompt=(
                    "请简要说明生图失败并建议用户稍后重试；"
                    "不要编造已经生成了图片。"
                ),
            )

        artifact = None
        if dependencies.artifact_store is not None:
            try:
                artifact = await asyncio.to_thread(
                    dependencies.artifact_store.register,
                    kind="image.generated",
                    title=_image_artifact_title(
                        str(event.get("text", "") or ""),
                        prompt,
                    ),
                    summary=_compact_artifact_summary(prompt),
                    tags=artifact_tags,
                    files=[
                        ArtifactSource(
                            path=output_path,
                            role="image",
                            remove_after_store=True,
                        )
                    ],
                    ttl_seconds=dependencies.artifact_image_ttl_seconds,
                    metadata={
                        "model": dependencies.image_gen_model,
                        "prompt_sha256": hashlib.sha256(
                            effective_prompt.encode("utf-8")
                        ).hexdigest(),
                    },
                )
            except (OSError, sqlite3.Error, ValueError):
                artifact = None

        result_data: dict[str, Any] = {
            "generated": True,
            "stored_as_artifact": artifact is not None,
        }
        if artifact is not None:
            normalized_image = artifact.file("image")
            if normalized_image is not None:
                output_path = normalized_image
            result_data.update(
                {
                    "artifact_id": artifact.artifact_id,
                    "artifact_tags": list(artifact.tags),
                    "artifact_expires_at": artifact.expires_at.isoformat(),
                }
            )
        return image_result(
            module="tool.image",
            command="generate_image",
            image_path=str(output_path),
            data=result_data,
            message="AI 图片已生成",
            extra_prompt=(
                "图片会随最终回复一起发送。"
                "请用一句简短自然的话回复，不要输出本地文件路径。"
            ),
        )

    return [
        ConcreteTool(
            spec={
                "name": "generate_image",
                "description": (
                    "根据用户要求生成并返回创意图片，适用于同人插画、壁纸、头像、"
                    "场景画等。四类事实型 BTD6 活动情报图必须使用 get_activity_brief_image，"
                    "排行榜和收集活动也必须使用各自的专用图片工具。"
                ),
                "parameters": object_schema(
                    {
                        "prompt": {
                            "type": "string",
                            "description": (
                                "完整、明确的创意图片描述，写清主体、动作、场景、构图和风格。"
                            ),
                            "minLength": 1,
                            "maxLength": MAX_PROMPT_CHARS,
                        }
                    },
                    required=["prompt"],
                ),
            },
            handler=generate_image,
            execution_timeout_seconds=calculate_image_gen_execution_timeout(
                dependencies.image_gen_request_timeout,
                dependencies.image_gen_max_retries,
                dependencies.image_gen_retry_delay_seconds,
            ),
        )
    ]
def _image_artifact_tags(*, model: str) -> tuple[str, ...]:
    tags = [
        "AI 生图",
        "图片",
    ]
    normalized_model = str(model or "").strip().lower()
    if normalized_model:
        tags.append(f"模型:{normalized_model}")
    return tuple(tags)


def _image_artifact_title(user_request: str, prompt: str) -> str:
    title = " ".join(str(user_request or "").split())
    if not title:
        title = " ".join(str(prompt or "").split())
    if not title:
        return "AI 生成图片"
    return title if len(title) <= 120 else f"{title[:119].rstrip()}…"


def _compact_artifact_summary(value: str, max_chars: int = 360) -> str:
    summary = " ".join(str(value or "").split())
    if len(summary) <= max_chars:
        return summary
    return f"{summary[: max_chars - 1].rstrip()}…"
