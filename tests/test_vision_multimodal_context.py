from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from bot_app.llm_client import _redact_multimodal_payload
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
        "message_id": "current",
        "images": images,  # type: ignore[typeddict-item]
    }


def _image(path: Path, marker: bytes) -> dict[str, object]:
    path.write_bytes(marker)
    return {
        "segment_index": 0,
        "processing_status": "cached",
        "mime_type": "image/png",
        "cache_path": str(path),
    }


class VisionMultimodalContextTests(unittest.TestCase):
    def test_disabled_vision_preserves_text_content(self) -> None:
        """VX-01: 视觉开关关闭时保持原有纯文本 payload。"""
        content = build_multimodal_user_content(
            event=_event([]),
            reply_context={"current_event": {"text": "hello"}},
            settings=_settings(vision_enabled=False),
        )

        self.assertIsInstance(content, str)
        self.assertIn('"task": "respond_to_event"', content)

    def test_current_and_history_images_follow_budget_and_labels(self) -> None:
        """VX-02: 当前与历史图片按预算进入有标签的多模态 content。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current = [
                _image(root / f"current-{i}.png", f"c{i}".encode()) for i in range(3)
            ]
            history = [
                _image(root / f"history-{i}.png", f"h{i}".encode()) for i in range(2)
            ]
            content = build_multimodal_user_content(
                event=_event(current),
                reply_context={
                    "recent_group_window": [
                        {"sequence": 7, "images": history},
                    ]
                },
                settings=_settings(
                    vision_current_image_limit=2,
                    vision_history_image_limit=2,
                    vision_total_image_limit=3,
                ),
            )

        self.assertIsInstance(content, list)
        parts = content if isinstance(content, list) else []
        self.assertEqual(sum(part.get("type") == "image_url" for part in parts), 3)
        labels = [part.get("text", "") for part in parts if part.get("type") == "text"]
        self.assertIn("\n[当前消息图片 1]", labels)
        self.assertIn("\n[当前消息图片 2]", labels)
        self.assertIn("\n[群聊历史第 7 条图片 1]", labels)

    def test_missing_cache_falls_back_to_text(self) -> None:
        """VX-03: 无效或过期缓存不会导致请求装配失败。"""
        content = build_multimodal_user_content(
            event=_event(
                [
                    {
                        "segment_index": 0,
                        "processing_status": "cached",
                        "cache_path": "/missing/image.png",
                    }
                ]
            ),
            reply_context={},
            settings=_settings(),
        )

        self.assertIsInstance(content, str)

    def test_request_log_redacts_image_body(self) -> None:
        """VX-04: 请求日志只保留图片摘要，不保留 Base64 正文。"""
        encoded = base64.b64encode(b"private-image").decode("ascii")
        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{encoded}"},
                        }
                    ],
                }
            ]
        }

        redacted = _redact_multimodal_payload(payload)
        serialized = str(redacted)
        self.assertNotIn(encoded, serialized)
        self.assertIn("image/png bytes=13 sha256=", serialized)
        self.assertIn(encoded, str(payload))


if __name__ == "__main__":
    unittest.main()
