from __future__ import annotations

import base64
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from bot_app.agent.tool_registry import ToolRegistry  # noqa: E402
from bot_app.image_prompting import MAX_IMAGE_PROMPT_CHARS  # noqa: E402
from bot_app.settings import load_settings  # noqa: E402
from bot_app.tools import ToolDependencies  # noqa: E402
from bot_app.tools.image_generation_service import (  # noqa: E402
    ImageGenerationError,
    generate_image_file,
)


PNG_BYTES = b"\x89PNG\r\n\x1a\nvalid-test-payload"


class _JsonResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 300

    def json(self) -> dict[str, Any]:
        return self._payload


class _DownloadResponse:
    status_code = 200
    ok = True

    def __enter__(self) -> _DownloadResponse:
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def iter_content(self, chunk_size: int) -> list[bytes]:
        del chunk_size
        return [PNG_BYTES[:10], PNG_BYTES[10:]]


def _dependencies(plugin_root: Path, *, configured: bool = True) -> ToolDependencies:
    return ToolDependencies.create(
        db=object(),
        plugin_root=plugin_root,
        manual_file="manuals/program_manual.md",
        image_gen_api_base_url=("https://images.example/v1" if configured else ""),
        image_gen_api_key=("secret-image-key" if configured else ""),
        image_gen_model=("private-image-model" if configured else ""),
        image_gen_request_timeout=30,
        image_gen_max_retries=2,
        image_gen_retry_delay_seconds=2,
        image_gen_use_proxy=False,
    )


def _event() -> dict[str, Any]:
    return {
        "module": "test",
        "event_type": "group_message",
        "group_id": "1000",
        "user_id": "2000",
        "text": "画一张日落",
        "bot_mentioned": True,
        "mentioned_qqs": [],
        "identity": {},
    }


class ImageGenerationToolTests(unittest.IsolatedAsyncioTestCase):
    def test_default_run_timeout_covers_activity_brief_tool(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BOT_TARGET_GROUP_ID=1000",
                        "LLM_API_KEY=chat-key",
                        "BOT_RUN_TIMEOUT_SECONDS=120",
                    ]
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                settings = load_settings(str(env_file))

        self.assertGreaterEqual(settings.run_timeout_seconds, 240)

    def test_settings_load_private_image_generation_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BOT_TARGET_GROUP_ID=1000",
                        "LLM_API_KEY=chat-key",
                        "IMAGE_GEN_API_BASE_URL=https://images.example/v1/",
                        "IMAGE_GEN_API_KEY=secret-image-key",
                        "IMAGE_GEN_MODEL=private-image-model",
                        "IMAGE_GEN_REQUEST_TIMEOUT=90",
                        "IMAGE_GEN_MAX_RETRIES=3",
                        "IMAGE_GEN_RETRY_DELAY_SECONDS=1.5",
                        "IMAGE_GEN_USE_PROXY=false",
                        "IMAGE_GEN_MAX_BYTES=1048576",
                        "BOT_ARTIFACT_ROOT=data/custom_artifacts",
                        "BOT_ARTIFACT_ACTIVITY_TTL_SECONDS=300",
                        "BOT_ARTIFACT_IMAGE_TTL_SECONDS=7200",
                        "BOT_ARTIFACT_DEFAULT_TTL_SECONDS=1800",
                        "BOT_ARTIFACT_ARCHIVE_RETENTION_SECONDS=3600",
                    ]
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                settings = load_settings(str(env_file))

        self.assertEqual(
            settings.image_gen_api_base_url,
            "https://images.example/v1",
        )
        self.assertEqual(settings.image_gen_api_key, "secret-image-key")
        self.assertEqual(settings.image_gen_model, "private-image-model")
        self.assertEqual(settings.image_gen_request_timeout, 90)
        self.assertEqual(settings.image_gen_max_retries, 3)
        self.assertEqual(settings.image_gen_retry_delay_seconds, 1.5)
        self.assertFalse(settings.image_gen_use_proxy)
        self.assertEqual(settings.image_gen_max_bytes, 1048576)
        self.assertEqual(settings.artifact_root, "data/custom_artifacts")
        self.assertEqual(settings.artifact_activity_ttl_seconds, 300)
        self.assertEqual(settings.artifact_image_ttl_seconds, 7200)
        self.assertEqual(settings.artifact_default_ttl_seconds, 1800)
        self.assertEqual(settings.artifact_archive_retention_seconds, 3600)
        self.assertGreaterEqual(settings.run_timeout_seconds, 427.5)

    def test_agent_schema_exposes_only_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = ToolRegistry(_dependencies(Path(temp_dir)))

        tool = next(
            item
            for item in registry.to_openai_tools()
            if item["function"]["name"] == "generate_image"
        )
        parameters = tool["function"]["parameters"]

        self.assertEqual(set(parameters["properties"]), {"prompt"})
        self.assertEqual(parameters["required"], ["prompt"])
        self.assertFalse(parameters["additionalProperties"])
        self.assertEqual(
            parameters["properties"]["prompt"]["maxLength"],
            MAX_IMAGE_PROMPT_CHARS,
        )
        serialized = str(tool)
        self.assertNotIn("secret-image-key", serialized)
        self.assertNotIn("private-image-model", serialized)
        self.assertNotIn("images.example", serialized)
        self.assertEqual(
            registry.get_execution_timeout_seconds("generate_image"),
            106,
        )
        self.assertIsNone(registry.get_execution_timeout_seconds("list_features"))

    async def test_tool_uses_private_configuration_and_returns_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            generated = root / "data" / "generated_images" / "result.png"
            generated.parent.mkdir(parents=True)
            generated.write_bytes(PNG_BYTES)
            registry = ToolRegistry(_dependencies(root))

            with patch(
                "bot_app.tools.image.generate_image_file",
                return_value=generated,
            ) as generate:
                result = await registry.execute(
                    "generate_image",
                    {"prompt": "夕阳下的山谷，电影感构图"},
                    _event(),  # type: ignore[arg-type]
                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["response_type"], "image")
        normalized_path = Path(result["image_path"])
        self.assertEqual(normalized_path.name, "image.png")
        self.assertIn("artifacts", normalized_path.parts)
        self.assertIn("image_generated", normalized_path.parts)
        self.assertTrue(result["data"]["stored_as_artifact"])
        self.assertTrue(result["data"]["artifact_id"])
        self.assertNotIn("api_key", result["data"])
        self.assertNotIn("model", result["data"])
        self.assertEqual(generate.call_args.kwargs["api_key"], "secret-image-key")
        self.assertEqual(generate.call_args.kwargs["model"], "private-image-model")
        self.assertEqual(generate.call_args.kwargs["max_retries"], 2)
        self.assertEqual(generate.call_args.kwargs["retry_delay_seconds"], 2)
        self.assertFalse(generate.call_args.kwargs["use_proxy"])
        self.assertEqual(
            generate.call_args.kwargs["api_base_url"],
            "https://images.example/v1",
        )
        effective_prompt = generate.call_args.kwargs["prompt"]
        self.assertIn("[结构化生图任务 v2]", effective_prompt)
        self.assertIn("核心画面：夕阳下的山谷，电影感构图", effective_prompt)
        self.assertIn("画一张日落", effective_prompt)

    async def test_generated_image_is_listed_and_can_be_reused_by_agent_tool(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            generated = root / "generated.png"
            generated.write_bytes(PNG_BYTES)
            registry = ToolRegistry(_dependencies(root))

            with patch(
                "bot_app.tools.image.generate_image_file",
                return_value=generated,
            ) as generate:
                generated_result = await registry.execute(
                    "generate_image",
                    {"prompt": "夕阳下的山谷，电影感构图"},
                    _event(),  # type: ignore[arg-type]
                )
                catalog = await registry.execute(
                    "list_artifacts",
                    {},
                    _event(),  # type: ignore[arg-type]
                )
                artifact_id = catalog["data"]["artifacts"][0]["artifact_id"]
                reused = await registry.execute(
                    "read_artifact",
                    {"artifact_id": artifact_id, "role": "image"},
                    _event(),  # type: ignore[arg-type]
                )

        generate.assert_called_once()
        self.assertTrue(generated_result["data"]["stored_as_artifact"])
        self.assertEqual(catalog["data"]["artifact_count"], 1)
        self.assertEqual(catalog["data"]["artifacts"][0]["title"], "画一张日落")
        self.assertIn("生成", catalog["data"]["catalog_text"])
        self.assertTrue(reused["data"]["reused"])
        self.assertEqual(generated_result["image_path"], reused["image_path"])

    async def test_unconfigured_tool_fails_without_exposing_config_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = ToolRegistry(_dependencies(Path(temp_dir), configured=False))
            with patch("bot_app.tools.image.generate_image_file") as generate:
                result = await registry.execute(
                    "generate_image",
                    {"prompt": "一座雪山"},
                    _event(),  # type: ignore[arg-type]
                )

        self.assertFalse(result["ok"])
        self.assertEqual(result["data"], {"reason": "not_configured"})
        generate.assert_not_called()

    def test_service_posts_openai_compatible_payload_and_writes_base64_image(
        self,
    ) -> None:
        response = _JsonResponse(
            {
                "data": [
                    {"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}
                ]
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch(
                "bot_app.tools.image_generation_service.requests.post",
                return_value=response,
            ) as post:
                image_path = generate_image_file(
                    prompt="水彩风格的湖边小屋",
                    api_base_url="https://images.example/v1/",
                    api_key="secret-image-key",
                    model="private-image-model",
                    output_dir=Path(temp_dir),
                    timeout=45,
                    max_bytes=1024,
                )

            self.assertEqual(image_path.read_bytes(), PNG_BYTES)
            self.assertEqual(image_path.suffix, ".png")

        self.assertEqual(
            post.call_args.args[0],
            "https://images.example/v1/images/generations",
        )
        self.assertEqual(
            post.call_args.kwargs["json"],
            {
                "model": "private-image-model",
                "prompt": "水彩风格的湖边小屋",
                "n": 1,
            },
        )
        self.assertEqual(
            post.call_args.kwargs["headers"]["Authorization"],
            "Bearer secret-image-key",
        )

    def test_service_downloads_url_response(self) -> None:
        response = _JsonResponse(
            {"data": [{"url": "https://cdn.example/generated/image.png"}]}
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch(
                    "bot_app.tools.image_generation_service.requests.post",
                    return_value=response,
                ),
                patch(
                    "bot_app.tools.image_generation_service.requests.get",
                    return_value=_DownloadResponse(),
                ) as get,
            ):
                image_path = generate_image_file(
                    prompt="极简线稿头像",
                    api_base_url="https://images.example/v1/images/generations",
                    api_key="secret-image-key",
                    model="private-image-model",
                    output_dir=Path(temp_dir),
                    timeout=45,
                    max_bytes=1024,
                )

            self.assertEqual(image_path.read_bytes(), PNG_BYTES)

        get.assert_called_once_with(
            "https://cdn.example/generated/image.png",
            stream=True,
            timeout=45,
        )

    def test_service_retries_transient_failure_then_succeeds(self) -> None:
        failure = _JsonResponse({}, status_code=503)
        success = _JsonResponse(
            {
                "data": [
                    {"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}
                ]
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch(
                    "bot_app.tools.image_generation_service.requests.post",
                    side_effect=[failure, success],
                ) as post,
                patch("bot_app.tools.image_generation_service.time.sleep") as sleep,
            ):
                image_path = generate_image_file(
                    prompt="重试测试",
                    api_base_url="https://images.example/v1",
                    api_key="secret-image-key",
                    model="private-image-model",
                    output_dir=Path(temp_dir),
                    timeout=45,
                    max_bytes=1024,
                    max_retries=2,
                    retry_delay_seconds=1.5,
                )

            generated_bytes = image_path.read_bytes()

        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(1.5)
        self.assertEqual(generated_bytes, PNG_BYTES)

    def test_service_does_not_retry_authentication_failure(self) -> None:
        failure = _JsonResponse({}, status_code=401)
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch(
                    "bot_app.tools.image_generation_service.requests.post",
                    return_value=failure,
                ) as post,
                patch("bot_app.tools.image_generation_service.time.sleep") as sleep,
            ):
                with self.assertRaises(ImageGenerationError) as raised:
                    generate_image_file(
                        prompt="鉴权失败测试",
                        api_base_url="https://images.example/v1",
                        api_key="invalid-key",
                        model="private-image-model",
                        output_dir=Path(temp_dir),
                        timeout=45,
                        max_bytes=1024,
                        max_retries=2,
                        retry_delay_seconds=1.5,
                    )

        self.assertEqual(raised.exception.code, "authentication_failed")
        self.assertEqual(raised.exception.attempts, 1)
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(post.call_count, 1)
        sleep.assert_not_called()

    def test_service_can_bypass_environment_proxy(self) -> None:
        response = _JsonResponse(
            {
                "data": [
                    {"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}
                ]
            }
        )
        session = Mock()
        session.trust_env = True
        session.post.return_value = response
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch(
                "bot_app.tools.image_generation_service.requests.Session",
                return_value=session,
            ):
                image_path = generate_image_file(
                    prompt="代理绕过测试",
                    api_base_url="https://images.example/v1",
                    api_key="secret-image-key",
                    model="private-image-model",
                    output_dir=Path(temp_dir),
                    timeout=45,
                    max_bytes=1024,
                    max_retries=0,
                    use_proxy=False,
                )
                generated_bytes = image_path.read_bytes()

        self.assertFalse(session.trust_env)
        self.assertEqual(session.post.call_count, 1)
        self.assertEqual(generated_bytes, PNG_BYTES)
        session.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
