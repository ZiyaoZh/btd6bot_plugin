from __future__ import annotations

import asyncio
import io
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

try:
    import ncatbot  # noqa: F401
except ModuleNotFoundError:
    ncatbot_module = types.ModuleType("ncatbot")
    event_module = types.ModuleType("ncatbot.event")
    qq_module = types.ModuleType("ncatbot.event.qq")
    types_module = types.ModuleType("ncatbot.types")
    qq_module.GroupMessageEvent = type("GroupMessageEvent", (), {})
    types_module.MessageArray = type("MessageArray", (), {})
    sys.modules.update(
        {
            "ncatbot": ncatbot_module,
            "ncatbot.event": event_module,
            "ncatbot.event.qq": qq_module,
            "ncatbot.types": types_module,
        }
    )

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from bot_app.application import BotApplication
from bot_app.db_manager import BotDatabase
from bot_app.media_store import ChatMediaStore
from bot_app.receiver import GroupMessageReceiver
from bot_app.settings import AppSettings, load_settings


class _Response:
    def __init__(self, content: bytes, *, url: str = "https://cdn.example/a.png"):
        self._content = content
        self.url = url
        self.history: list[_Response] = []
        self.headers = {"Content-Length": str(len(content))}

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int):
        for offset in range(0, len(self._content), chunk_size):
            yield self._content[offset : offset + chunk_size]

    def close(self) -> None:
        return None


class _Session:
    def __init__(self, content: bytes):
        self.content = content
        self.calls = 0

    def get(self, *args, **kwargs) -> _Response:
        self.calls += 1
        return _Response(self.content)


def _image_bytes(image_format: str = "PNG", *, size: tuple[int, int] = (8, 6)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, "red").save(output, format=image_format)
    return output.getvalue()


class VisionMediaStoreTests(unittest.TestCase):
    @patch("bot_app.media_store.socket.getaddrinfo")
    def test_normalizes_deduplicates_and_updates_database(self, resolve) -> None:
        """VC-01: 图片归一化后按内容哈希去重，并完整回写媒体状态。"""
        resolve.return_value = [(None, None, None, None, ("93.184.216.34", 443))]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = BotDatabase(str(root / "bot.db"))
            session = _Session(_image_bytes("PNG"))
            store = ChatMediaStore(root / "cache", session=session)
            settings = AppSettings(
                env_file=".env",
                target_group_id="1000",
                target_group_ids=("1000",),
                api_key="test",
                vision_enabled=True,
            )
            receiver = GroupMessageReceiver(settings, db=database, media_store=store)
            message = SimpleNamespace(
                group_id="1000",
                user_id="2000",
                message_id="3000",
                raw_message="[CQ:image,file=a.png]",
                sender=SimpleNamespace(
                    nickname="", card="", role="", title="", level=""
                ),
                message=[
                    {
                        "type": "image",
                        "data": {"url": "https://cdn.example/a.png", "file": "a.png"},
                    },
                    {
                        "type": "image",
                        "data": {"url": "https://cdn.example/b.png", "file": "b.png"},
                    },
                ],
            )

            images = receiver.sync_message_to_db(message)
            metrics = receiver.cache_message_images(message, images)
            rows = database.get_recent_group_messages("1000", 1)[0]["images"]

            self.assertEqual(
                [row["processing_status"] for row in rows], ["cached", "cached"]
            )
            self.assertEqual(rows[0]["sha256"], rows[1]["sha256"])
            self.assertEqual(rows[0]["cache_path"], rows[1]["cache_path"])
            self.assertEqual(rows[0]["mime_type"], "image/jpeg")
            self.assertEqual((rows[0]["width"], rows[0]["height"]), (8, 6))
            self.assertEqual(len(list((root / "cache").rglob("*.jpg"))), 1)
            self.assertEqual(metrics["cached"], 2)
            self.assertEqual(metrics["rejected"], 0)
            self.assertEqual(metrics["cache_hits"], 1)
            self.assertGreater(metrics["total_bytes"], 0)
            database._conn.close()

    @patch("bot_app.media_store.socket.getaddrinfo")
    def test_animated_image_uses_representative_first_frame(self, resolve) -> None:
        """VC-02: 动图安全降级为首帧静态图片。"""
        resolve.return_value = [(None, None, None, None, ("93.184.216.34", 443))]
        output = io.BytesIO()
        frames = [Image.new("RGB", (5, 4), color) for color in ("red", "blue")]
        frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:])
        with tempfile.TemporaryDirectory() as directory:
            store = ChatMediaStore(directory, session=_Session(output.getvalue()))
            result = store.cache(
                {"segment_index": 2, "url": "https://cdn.example/a.gif"}
            )

            self.assertEqual(result.status, "cached")
            self.assertEqual((result.width, result.height), (5, 4))
            self.assertEqual(result.mime_type, "image/jpeg")

    def test_rejects_private_invalid_and_oversized_sources(self) -> None:
        """VC-03: 私网、损坏和超过字节上限的图片均安全拒绝。"""
        with tempfile.TemporaryDirectory() as directory:
            private_store = ChatMediaStore(directory, session=_Session(_image_bytes()))
            with patch("bot_app.media_store.socket.getaddrinfo") as resolve:
                resolve.return_value = [(None, None, None, None, ("127.0.0.1", 80))]
                private = private_store.cache({"url": "http://localhost/a.png"})
            with patch("bot_app.media_store.socket.getaddrinfo") as resolve:
                resolve.return_value = [
                    (None, None, None, None, ("93.184.216.34", 443))
                ]
                invalid = ChatMediaStore(
                    directory, session=_Session(b"not-image")
                ).cache({"url": "https://cdn.example/a.png"})
                oversized = ChatMediaStore(
                    directory, max_image_bytes=1024, session=_Session(b"x" * 1025)
                ).cache({"url": "https://cdn.example/a.png"})

            self.assertEqual(private.status, "rejected")
            self.assertEqual(invalid.status, "rejected")
            self.assertEqual(oversized.status, "rejected")
            self.assertFalse(list(Path(directory).rglob("*.png")))

    def test_cleanup_enforces_ttl_and_capacity(self) -> None:
        """VC-04: 缓存清理同时执行 TTL 和最旧文件优先的容量上限。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expired = root / "expired.jpg"
            older = root / "older.jpg"
            newer = root / "newer.jpg"
            for path in (expired, older, newer):
                path.write_bytes(b"x" * 700)
            now = time.time()
            os.utime(expired, (now - 20, now - 20))
            os.utime(older, (now - 2, now - 2))
            os.utime(newer, (now - 1, now - 1))

            ChatMediaStore(
                root, ttl_seconds=10, max_image_bytes=1024, capacity_bytes=1200
            ).cleanup()

            self.assertFalse(expired.exists())
            self.assertFalse(older.exists())
            self.assertTrue(newer.exists())

    def test_vision_configuration_is_disabled_by_default(self) -> None:
        """VC-05: 生产识图与缓存默认关闭，显式环境变量才启用。"""
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "BOT_TARGET_GROUP_ID=1000\nLLM_API_KEY=test\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                disabled = load_settings(str(env_file))
            env_file.write_text(
                "BOT_TARGET_GROUP_ID=1000\nBOT_TARGET_GROUP_IDS=1000,1001\n"
                "LLM_API_KEY=test\n"
                "LLM_VISION_ENABLED=true\nLLM_VISION_MAX_IMAGE_BYTES=2048\n"
                "LLM_VISION_IMAGE_DETAIL=low\nLLM_VISION_GROUP_IDS=1001\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                enabled = load_settings(str(env_file))

            self.assertFalse(disabled.vision_enabled)
            self.assertTrue(enabled.vision_enabled)
            self.assertEqual(enabled.vision_max_image_bytes, 2048)
            self.assertEqual(enabled.vision_image_detail, "low")
            self.assertFalse(enabled.is_vision_enabled_for_group("1000"))
            self.assertTrue(enabled.is_vision_enabled_for_group("1001"))

            env_file.write_text(
                "BOT_TARGET_GROUP_ID=1000\nLLM_API_KEY=test\n"
                "LLM_VISION_ENABLED=true\nLLM_VISION_GROUP_IDS=9999\n",
                encoding="utf-8",
            )
            with (
                patch.dict(os.environ, {}, clear=True),
                self.assertRaisesRegex(ValueError, "LLM_VISION_GROUP_IDS"),
            ):
                load_settings(str(env_file))

            env_file.write_text(
                "BOT_TARGET_GROUP_ID=1000\nLLM_API_KEY=test\n"
                "LLM_VISION_IMAGE_DETAIL=maximum\n",
                encoding="utf-8",
            )
            with (
                patch.dict(os.environ, {}, clear=True),
                self.assertRaisesRegex(ValueError, "LLM_VISION_IMAGE_DETAIL"),
            ):
                load_settings(str(env_file))

    def test_cache_failure_does_not_interrupt_message_processing(self) -> None:
        """VC-06: 缓存意外失败被降级，不阻断当前消息处理。"""

        class _Receiver:
            def cache_message_images(self, msg, images) -> None:
                raise RuntimeError("secret signed URL must not escape")

        application = object.__new__(BotApplication)
        application._receiver = _Receiver()
        message = SimpleNamespace(group_id="1000", message_id="3000")

        asyncio.run(
            application._cache_message_images(
                message,
                [{"segment_index": 0}],
                trace_id="trace",
            )
        )


if __name__ == "__main__":
    unittest.main()
