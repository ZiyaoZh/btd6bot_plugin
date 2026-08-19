from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path

from bot_app.context_contracts import (
    build_user_session_memory_context,
    serialize_compact_user_event,
)
from bot_app.model.multimodal import build_multimodal_user_content
from bot_app.protocol import EventPayload
from bot_app.settings import AppSettings


def _settings(**overrides: object) -> AppSettings:
    values = {
        "env_file": ".env",
        "target_group_id": "1000",
        "target_group_ids": ("1000",),
        "api_key": "test",
        "vision_enabled": True,
        "vision_current_image_limit": 4,
        "vision_history_image_limit": 2,
        "vision_total_image_limit": 4,
    }
    values.update(overrides)
    return AppSettings(**values)  # type: ignore[arg-type]


def _event(images: list[dict[str, object]]) -> EventPayload:
    return {
        "module": "test",
        "event_type": "group_message",
        "group_id": "1000",
        "user_id": "2000",
        "text": "上一张图是什么",
        "bot_mentioned": True,
        "mentioned_qqs": [],
        "identity": {},
        "message_id": "current",
        "images": images,  # type: ignore[typeddict-item]
    }


def _cached_image(path: Path, marker: bytes, sha256: str) -> dict[str, object]:
    path.write_bytes(marker)
    return {
        "segment_index": 0,
        "processing_status": "cached",
        "mime_type": "image/png",
        "sha256": sha256,
        "cache_path": str(path),
    }


class VisionSessionMemoryTests(unittest.TestCase):
    def test_memory_keeps_safe_media_references_without_source(self) -> None:
        """VS-01: 会话记忆只保留安全媒体引用。"""
        event = _event(
            [
                {
                    "segment_index": 2,
                    "url": "https://qq.example/private?sig=secret",
                    "file_id": "private-file-id",
                    "processing_status": "cached",
                    "mime_type": "image/png",
                    "sha256": "abc123",
                    "cache_path": "/cache/abc123.png",
                    "visual_summary": "一张猴子截图",
                }
            ]
        )

        serialized = serialize_compact_user_event(event)
        session = build_user_session_memory_context(
            [{"role": "user", "content": serialized}], recent_turn_limit=2
        )

        self.assertNotIn("sig=secret", serialized)
        self.assertNotIn("private-file-id", serialized)
        turn = session["recent_turns"][0]
        self.assertEqual(turn["message_id"], "current")
        self.assertEqual(turn["image_count"], 1)
        self.assertEqual(turn["images"][0]["sha256"], "abc123")
        self.assertEqual(turn["images"][0]["visual_summary"], "一张猴子截图")

    def test_session_image_survives_outside_recent_group_window(self) -> None:
        """VS-02: 群聊窗口无图时仍可从同一用户会话复用有效缓存。"""
        with tempfile.TemporaryDirectory() as directory:
            image = _cached_image(Path(directory) / "prior.png", b"prior", "prior")
            content = build_multimodal_user_content(
                event=_event([]),
                reply_context={
                    "recent_group_window": [],
                    "user_session_memory": {
                        "summary": {},
                        "recent_turns": [
                            {
                                "role": "user",
                                "turn_type": "user_event",
                                "text": "看图",
                                "images": [image],
                            }
                        ],
                    },
                },
                settings=_settings(),
            )

        self.assertIsInstance(content, list)
        parts = content if isinstance(content, list) else []
        self.assertEqual(sum(part.get("type") == "image_url" for part in parts), 1)
        labels = [part.get("text", "") for part in parts if part.get("type") == "text"]
        self.assertIn("\n[用户会话前第 1 条图片 1]", labels)

    def test_duplicate_across_group_and_session_is_submitted_once(self) -> None:
        """VS-03: 最近群聊与用户会话中的同图按哈希去重。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            group_image = _cached_image(root / "group.png", b"same", "same-hash")
            session_image = _cached_image(root / "session.png", b"same", "same-hash")
            content = build_multimodal_user_content(
                event=_event([]),
                reply_context={
                    "recent_group_window": [{"sequence": 3, "images": [group_image]}],
                    "user_session_memory": {
                        "recent_turns": [{"images": [session_image]}]
                    },
                },
                settings=_settings(),
            )

        parts = content if isinstance(content, list) else []
        self.assertEqual(sum(part.get("type") == "image_url" for part in parts), 1)

    def test_expired_cache_keeps_summary_without_image_body(self) -> None:
        """VS-04: 缓存失效时保留摘要文本且不生成图片内容段。"""
        reply_context = {
            "user_session_memory": {
                "recent_turns": [
                    {
                        "role": "user",
                        "turn_type": "user_event",
                        "text": "看图",
                        "image_count": 1,
                        "images": [
                            {
                                "processing_status": "cached",
                                "cache_path": "/expired/image.png",
                                "visual_summary": "一张过期的地图截图",
                            }
                        ],
                    }
                ]
            }
        }
        content = build_multimodal_user_content(
            event=_event([]), reply_context=reply_context, settings=_settings()
        )

        self.assertIsInstance(content, str)
        parsed = json.loads(content)
        turn = parsed["reply_context"]["user_session_memory"]["recent_turns"][0]
        self.assertEqual(turn["image_count"], 1)
        self.assertEqual(turn["images"][0]["visual_summary"], "一张过期的地图截图")
        self.assertNotIn("base64", content)
        self.assertNotIn(base64.b64encode(b"anything").decode(), content)


if __name__ == "__main__":
    unittest.main()
