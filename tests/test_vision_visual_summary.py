from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from bot_app.protocol import EventPayload
from bot_app.settings import AppSettings
from bot_app.visual_summary import VisualSummaryService


def _settings(**overrides: object) -> AppSettings:
    values = {
        "env_file": ".env",
        "target_group_id": "1000",
        "target_group_ids": ("1000",),
        "api_key": "test",
        "vision_enabled": True,
        "vision_summary_enabled": True,
    }
    values.update(overrides)
    return AppSettings(**values)  # type: ignore[arg-type]


def _event(images: list[dict[str, object]]) -> EventPayload:
    return {
        "module": "test",
        "event_type": "group_message",
        "group_id": "1000",
        "user_id": "2000",
        "text": "看看图片",
        "bot_mentioned": True,
        "mentioned_qqs": [],
        "identity": {},
        "message_id": "message-1",
        "images": images,  # type: ignore[typeddict-item]
    }


class _FakeLLM:
    def __init__(self, response: str = "  一张 BTD6 地图截图。  ") -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    async def chat(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        return self.response


class VisualSummaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_generates_summary_and_persists_metadata(self) -> None:
        """VS-05: 有效缓存生成短摘要，并同步事件引用和媒体元数据。"""
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "image.png"
            image_path.write_bytes(b"private-image-body")
            image = {
                "segment_index": 2,
                "processing_status": "cached",
                "mime_type": "image/png",
                "cache_path": str(image_path),
            }
            event = _event([image])
            llm = _FakeLLM()
            database = Mock()
            service = VisualSummaryService(
                llm=llm,  # type: ignore[arg-type]
                settings=_settings(),
                database=database,
            )

            count = await service.enrich_event(event, trace_id="trace")

        self.assertEqual(count, 1)
        self.assertEqual(image["visual_summary"], "一张 BTD6 地图截图。")
        database.update_message_media_summary.assert_called_once_with(
            group_id="1000",
            message_id="message-1",
            segment_index=2,
            visual_summary="一张 BTD6 地图截图。",
        )
        request_text = str(llm.calls[0])
        self.assertIn("不可信内容", request_text)
        self.assertNotIn("private-image-body", request_text)
        self.assertEqual(llm.calls[0]["purpose"], "vision_summary")

    async def test_skips_existing_summary_missing_cache_and_budget_overflow(
        self,
    ) -> None:
        """VS-06: 已摘要、无效缓存和超预算图片不会产生额外模型调用。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.png"
            second = root / "second.png"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            event = _event(
                [
                    {
                        "processing_status": "cached",
                        "cache_path": str(first),
                        "visual_summary": "已有摘要",
                    },
                    {
                        "processing_status": "cached",
                        "cache_path": "/missing/image.png",
                    },
                    {
                        "processing_status": "cached",
                        "cache_path": str(first),
                    },
                    {
                        "processing_status": "cached",
                        "cache_path": str(second),
                    },
                ]
            )
            llm = _FakeLLM("生成摘要")
            service = VisualSummaryService(
                llm=llm,  # type: ignore[arg-type]
                settings=_settings(vision_summary_image_limit=1),
            )

            count = await service.enrich_event(event)

        self.assertEqual(count, 1)
        self.assertEqual(len(llm.calls), 1)
        self.assertNotIn("visual_summary", event["images"][3])

    async def test_disabled_summary_does_not_read_or_call_model(self) -> None:
        """VS-07: 摘要开关关闭时保持原事件，不读取缓存。"""
        llm = _FakeLLM()
        event = _event(
            [{"processing_status": "cached", "cache_path": "/private/missing.png"}]
        )
        service = VisualSummaryService(
            llm=llm,  # type: ignore[arg-type]
            settings=_settings(vision_summary_enabled=False),
        )

        count = await service.enrich_event(event)

        self.assertEqual(count, 0)
        self.assertEqual(llm.calls, [])

    async def test_model_failure_is_non_fatal(self) -> None:
        """VS-08: 摘要模型失败时保留原媒体引用并返回零。"""

        class FailingLLM(_FakeLLM):
            async def chat(self, **kwargs: object) -> str:
                raise RuntimeError("proxy unavailable")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.png"
            path.write_bytes(b"image")
            image = {
                "processing_status": "cached",
                "cache_path": str(path),
            }
            service = VisualSummaryService(
                llm=FailingLLM(),  # type: ignore[arg-type]
                settings=_settings(),
            )

            count = await service.enrich_event(_event([image]), trace_id="trace")

        self.assertEqual(count, 0)
        self.assertNotIn("visual_summary", image)


if __name__ == "__main__":
    unittest.main()
