from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

try:
    import ncatbot  # noqa: F401
except ModuleNotFoundError:
    ncatbot_module = types.ModuleType("ncatbot")
    event_module = types.ModuleType("ncatbot.event")
    qq_module = types.ModuleType("ncatbot.event.qq")
    qq_module.GroupMessageEvent = type("GroupMessageEvent", (), {})
    sys.modules.update(
        {
            "ncatbot": ncatbot_module,
            "ncatbot.event": event_module,
            "ncatbot.event.qq": qq_module,
        }
    )

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from bot_app.db_manager import BotDatabase
from bot_app.group_context import build_recent_group_window, exclude_message_id
from bot_app.receiver import (
    GroupMessageReceiver,
    extract_image_payloads,
    has_message_content,
    redact_event_payload_for_log,
)
from bot_app.settings import AppSettings


class _MessageArray:
    def __init__(self, segments: list[dict]) -> None:
        self._segments = segments

    def to_list(self) -> list[dict]:
        return self._segments


class VisionEventContractTests(unittest.TestCase):
    def test_extracts_ordered_image_metadata_from_message_array(self) -> None:
        """VE-01: 图片元数据按原始消息段位置提取，不解析 CQ 字符串。"""
        message = _MessageArray(
            [
                {"type": "text", "data": {"text": "先看这张"}},
                {
                    "type": "image",
                    "data": {
                        "url": "https://cdn.example/one.png?token=secret",
                        "file": "one-file-id",
                        "file_name": "one.png",
                        "file_size": "1234",
                        "sub_type": "0",
                    },
                },
                {"type": "at", "data": {"qq": "999"}},
                {
                    "type": "image",
                    "data": {
                        "url": "https://cdn.example/sticker.gif",
                        "file_id": "sticker-file-id",
                        "filename": "sticker.gif",
                        "size": 5678,
                        "sub_type": 1,
                    },
                },
            ]
        )

        self.assertEqual(
            extract_image_payloads(message),
            [
                {
                    "segment_index": 1,
                    "url": "https://cdn.example/one.png?token=secret",
                    "file_id": "one-file-id",
                    "file_name": "one.png",
                    "file_size": 1234,
                    "sub_type": 0,
                },
                {
                    "segment_index": 3,
                    "url": "https://cdn.example/sticker.gif",
                    "file_id": "sticker-file-id",
                    "file_name": "sticker.gif",
                    "file_size": 5678,
                    "sub_type": 1,
                },
            ],
        )

    def test_build_event_adds_message_identity_and_images(self) -> None:
        """VE-02: 接收事件契约包含 message_id 和结构化图片列表。"""
        receiver = GroupMessageReceiver(
            AppSettings(
                env_file=".env",
                target_group_id="1000",
                target_group_ids=("1000",),
                api_key="test-key",
                system_prompt="test",
            )
        )
        msg = SimpleNamespace(
            group_id=1000,
            user_id=2000,
            message_id=3000,
            raw_message="[CQ:at,qq=999] 看图 [CQ:image,file=one.png]",
            message=_MessageArray([{"type": "image", "data": {"file": "one.png"}}]),
            sender=SimpleNamespace(
                nickname="tester", card="", role="member", title="", level=""
            ),
        )

        event = receiver.build_event_payload(msg, "看图")

        self.assertEqual(event["message_id"], "3000")
        self.assertEqual(event["images"], [{"segment_index": 0, "file_id": "one.png"}])
        self.assertEqual(event["text"], "看图")

    def test_missing_or_invalid_message_has_no_images(self) -> None:
        """VE-03: 缺失或不可序列化的 MessageArray 安全降级为空列表。"""

        class _InvalidMessage:
            def to_list(self):
                raise ValueError("invalid")

        self.assertEqual(extract_image_payloads(None), [])
        self.assertEqual(extract_image_payloads(_InvalidMessage()), [])
        self.assertEqual(
            extract_image_payloads(
                [{"type": "image", "data": {"file_size": "unknown"}}]
            ),
            [{"segment_index": 0}],
        )

    def test_event_log_payload_redacts_image_sources(self) -> None:
        """VE-04: 结构化事件日志不暴露签名 URL 或文件标识。"""
        event = {
            "module": "receiver",
            "event_type": "group_message",
            "group_id": "1000",
            "user_id": "2000",
            "message_id": "3000",
            "text": "看图",
            "bot_mentioned": True,
            "mentioned_qqs": [],
            "identity": {},
            "images": [
                {
                    "segment_index": 2,
                    "url": "https://cdn.example/a.png?token=secret",
                    "file_id": "sensitive-file-id",
                    "file_name": "private.png",
                    "file_size": 1234,
                    "sub_type": 1,
                }
            ],
        }

        logged = redact_event_payload_for_log(event)

        self.assertEqual(logged["image_count"], 1)
        self.assertEqual(
            logged["images"],
            [{"segment_index": 2, "file_size": 1234, "sub_type": 1}],
        )
        self.assertNotIn("secret", str(logged))
        self.assertNotIn("sensitive-file-id", str(logged))

    def test_pure_image_counts_as_message_content(self) -> None:
        """VM-01: 纯图片消息不再被空文本检查拒绝。"""
        image_message = _MessageArray(
            [{"type": "image", "data": {"file": "only-image.png"}}]
        )

        self.assertTrue(has_message_content("", image_message))
        self.assertTrue(has_message_content("说明", None))
        self.assertFalse(has_message_content("", None))

    def test_media_metadata_is_idempotent_and_included_in_recent_messages(self) -> None:
        """VM-02: 图片元数据幂等入库，纯图片进入最近群聊查询。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            database = BotDatabase(str(Path(temp_dir) / "bot.db"))
            msg = SimpleNamespace(
                group_id="1000",
                user_id="2000",
                message_id="image-message-1",
                raw_message="[CQ:image,file=one.png]",
                message=_MessageArray(
                    [
                        {
                            "type": "image",
                            "data": {
                                "url": "https://cdn.example/one.png?token=secret",
                                "file": "one.png",
                                "file_size": "1234",
                                "sub_type": "1",
                            },
                        }
                    ]
                ),
                sender=SimpleNamespace(
                    nickname="tester",
                    card="",
                    role="member",
                    title="",
                    level="",
                ),
            )
            images = extract_image_payloads(msg.message)

            database.record_group_message(msg, images=images)
            database.record_group_message(msg, images=images)
            rows = database.get_recent_group_messages("1000", 10)

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["message_id"], "image-message-1")
            self.assertEqual(len(rows[0]["images"]), 1)
            self.assertEqual(rows[0]["images"][0]["segment_index"], 0)
            self.assertEqual(rows[0]["images"][0]["source_bytes"], 1234)
            self.assertEqual(rows[0]["images"][0]["processing_status"], "metadata_only")
            self.assertEqual(
                build_recent_group_window(rows, limit=10)[0]["cleaned_text"],
                "[该消息含 1 张图片]",
            )
            database._conn.close()

    def test_database_initializes_message_media_schema(self) -> None:
        """VM-03: 既有数据库初始化时安全增加媒体表及索引。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            database = BotDatabase(str(Path(temp_dir) / "bot.db"))
            columns = {
                row[1]
                for row in database._conn.execute(
                    "PRAGMA table_info(message_media)"
                ).fetchall()
            }
            indexes = {
                row[1]
                for row in database._conn.execute(
                    "PRAGMA index_list(message_media)"
                ).fetchall()
            }

            self.assertTrue(
                {
                    "message_row_id",
                    "segment_index",
                    "source_url",
                    "sha256",
                    "cache_path",
                    "processing_status",
                    "visual_summary",
                }.issubset(columns)
            )
            self.assertIn("idx_message_media_message_row", indexes)
            database._conn.close()

    def test_current_message_is_excluded_from_recent_context(self) -> None:
        """VM-04: 当前消息不会作为最近历史再次提交。"""
        rows = [
            {"message_id": "older", "normalized_text": "上一条"},
            {"message_id": "current", "normalized_text": "当前图片"},
        ]

        self.assertEqual(
            exclude_message_id(rows, "current"),
            [{"message_id": "older", "normalized_text": "上一条"}],
        )


if __name__ == "__main__":
    unittest.main()
