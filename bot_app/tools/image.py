from __future__ import annotations

import asyncio
from typing import Any

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


MAX_PROMPT_CHARS = 4000


def build_image_tools(dependencies: ToolDependencies) -> list[ConcreteTool]:
    async def generate_image(
        arguments: dict[str, Any], event: EventPayload
    ) -> CommandResultPayload:
        del event
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

        try:
            output_path = await asyncio.to_thread(
                generate_image_file,
                prompt=prompt,
                api_base_url=dependencies.image_gen_api_base_url,
                api_key=dependencies.image_gen_api_key,
                model=dependencies.image_gen_model,
                output_dir=dependencies.plugin_root / "data" / "generated_images",
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

        return image_result(
            module="tool.image",
            command="generate_image",
            image_path=str(output_path),
            data={"generated": True},
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
                    "根据用户要求生成一张图片。用户要求画图、生成图片、"
                    "设计视觉画面或制作插图时使用。"
                ),
                "parameters": object_schema(
                    {
                        "prompt": {
                            "type": "string",
                            "description": (
                                "完整、明确的生图提示词，描述主体、场景、"
                                "构图、风格和必要细节。"
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
