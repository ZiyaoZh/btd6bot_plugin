from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from ncatbot.event.qq import GroupMessageEvent

from .db_manager import BotDatabase
from .protocol import AdminControlPayload, EventPayload, ImagePayload
from .settings import AppSettings

PROACTIVE_CHAT_ENABLED_SETTING = "proactive_chat_enabled"


class GroupMessageReceiver:
    def __init__(self, settings: AppSettings, db: BotDatabase | None = None) -> None:
        self._settings = settings
        self._db = db
        self._volatile_proactive_chat_settings: dict[str, bool] = {}

    def set_proactive_chat_enabled(self, group_id: str, enabled: bool) -> None:
        if self._db is not None:
            self._db.set_group_setting(
                group_id,
                PROACTIVE_CHAT_ENABLED_SETTING,
                "true" if enabled else "false",
            )
            return
        self._volatile_proactive_chat_settings[str(group_id)] = bool(enabled)

    def is_proactive_chat_enabled(self, group_id: str) -> bool:
        if self._db is not None:
            return self._db.get_group_bool_setting(
                group_id,
                PROACTIVE_CHAT_ENABLED_SETTING,
                default=self._settings.proactive_chat_enabled,
            )
        return self._volatile_proactive_chat_settings.get(
            str(group_id), self._settings.proactive_chat_enabled
        )

    def should_accept(self, msg: GroupMessageEvent) -> bool:
        if str(msg.group_id) not in self._settings.target_group_ids:
            return False
        if self._settings.bot_qq and str(msg.user_id) == self._settings.bot_qq:
            return False
        proactive_enabled = self.is_proactive_chat_enabled(str(msg.group_id))
        bot_mentioned = self._contains_bot_mention(str(msg.raw_message or ""))
        return proactive_enabled or bot_mentioned

    def should_track_message(self, msg: GroupMessageEvent) -> bool:
        if str(msg.group_id) not in self._settings.target_group_ids:
            return False
        if self._settings.bot_qq and str(msg.user_id) == self._settings.bot_qq:
            return False
        return True

    def parse_admin_control(self, msg: GroupMessageEvent) -> AdminControlPayload:
        raw_text = (msg.raw_message or "").strip()
        action = self._parse_control_action(raw_text)
        if not action:
            return {
                "module": "receiver",
                "matched": False,
                "authorized": False,
                "action": "none",
                "reason": "not admin control command",
            }

        if not self._contains_bot_mention(raw_text):
            return {
                "module": "receiver",
                "matched": True,
                "authorized": False,
                "action": action,
                "reason": f"missing mention: @{self._settings.bot_name}",
            }

        if action != "system_commands" and not self._is_admin_sender(msg):
            return {
                "module": "receiver",
                "matched": True,
                "authorized": False,
                "action": action,
                "reason": "sender is not owner/admin",
            }

        return {
            "module": "receiver",
            "matched": True,
            "authorized": True,
            "action": action,
            "reason": "authorized",
        }

    def sync_message_to_db(self, msg: GroupMessageEvent) -> None:
        if self._db is None:
            return
        self._db.record_group_message(
            msg,
            bot_qq=self._settings.bot_qq,
            bot_name=self._settings.bot_name,
            images=extract_image_payloads(getattr(msg, "message", None)),
        )

    def normalize_text(self, raw_text: str) -> str:
        text = (raw_text or "").strip()
        if not text:
            return ""
        if self._settings.trigger_prefix:
            if not text.startswith(self._settings.trigger_prefix):
                return ""
            text = text[len(self._settings.trigger_prefix) :].strip()
        return text

    def build_event_payload(self, msg: GroupMessageEvent, text: str) -> EventPayload:
        sender = getattr(msg, "sender", None)
        raw_text = str(msg.raw_message or "")
        mentioned_qqs = self._extract_mentioned_qqs(raw_text)
        bot_mentioned = self._contains_bot_mention(raw_text)
        identity = {
            "nickname": str(getattr(sender, "nickname", "") or "").strip(),
            "card": str(getattr(sender, "card", "") or "").strip(),
            "role": str(getattr(sender, "role", "") or "").strip(),
            "title": str(getattr(sender, "title", "") or "").strip(),
            "level": str(getattr(sender, "level", "") or "").strip(),
        }
        return {
            "module": "receiver",
            "event_type": "group_message",
            "group_id": str(msg.group_id),
            "user_id": str(msg.user_id),
            "message_id": str(getattr(msg, "message_id", "") or ""),
            "text": text,
            "images": extract_image_payloads(getattr(msg, "message", None)),
            "bot_mentioned": bot_mentioned,
            "mentioned_qqs": mentioned_qqs,
            "identity": identity,
        }

    def _extract_mentioned_qqs(self, raw_text: str) -> list[str]:
        text = raw_text or ""
        return re.findall(r"\[CQ:at,qq=(\d+)\]", text)

    def _is_admin_sender(self, msg: GroupMessageEvent) -> bool:
        sender = getattr(msg, "sender", None)
        role = str(getattr(sender, "role", "") or "").lower()
        return role in {"owner", "admin"}

    def _contains_bot_mention(self, raw_text: str) -> bool:
        text = raw_text or ""
        if self._settings.bot_qq and f"[CQ:at,qq={self._settings.bot_qq}]" in text:
            return True

        name = self._settings.bot_name.strip()
        if not name:
            return False

        for at_sign in ("@", "＠"):
            if f"{at_sign}{name}" in text:
                return True
        return False

    def _parse_control_action(self, text: str) -> str:
        t = (text or "").strip()
        if not t:
            return ""

        # Keep admin control strict: only pure action words are accepted.
        t = re.sub(r"\[CQ:at,qq=\d+\]", "", t)
        t = re.sub(r"[@＠]\S+", "", t)
        t = re.sub(r"[\s,，。.!！?？;；:：'\"“”‘’()（）\-_/\\]+", "", t)

        if t == "启动":
            return "start"
        if t in {"终止", "停止", "关闭"}:
            return "stop"
        if t in {"开启主动接话", "打开主动接话"}:
            return "proactive_on"
        if t in {"关闭主动接话", "停止主动接话"}:
            return "proactive_off"
        if t in {"开启模型过程", "打开模型过程", "开启过程消息", "打开过程消息"}:
            return "model_turns_on"
        if t in {"关闭模型过程", "停止模型过程", "关闭过程消息", "停止过程消息"}:
            return "model_turns_off"
        if t in {"系统指令", "系统指令列表", "管理指令", "管理指令列表"}:
            return "system_commands"
        return ""


def extract_image_payloads(message: Any) -> list[ImagePayload]:
    """Extract ordered image metadata from a NcatBot MessageArray."""
    if message is None:
        return []

    to_list = getattr(message, "to_list", None)
    if callable(to_list):
        try:
            segments = to_list()
        except (AttributeError, TypeError, ValueError):
            return []
    elif isinstance(message, (list, tuple)):
        segments = message
    else:
        return []

    images: list[ImagePayload] = []
    for segment_index, segment in enumerate(segments):
        segment_type, data = _segment_type_and_data(segment)
        if segment_type != "image":
            continue

        image: ImagePayload = {"segment_index": segment_index}
        _set_text_field(image, "url", data.get("url"))
        _set_text_field(image, "file_id", data.get("file_id", data.get("file")))
        _set_text_field(
            image,
            "file_name",
            data.get("file_name", data.get("filename", data.get("name"))),
        )
        _set_int_field(image, "file_size", data.get("file_size", data.get("size")))
        _set_int_field(image, "sub_type", data.get("sub_type"))
        images.append(image)
    return images


def has_message_content(text: str, message: Any) -> bool:
    return bool(str(text or "").strip() or extract_image_payloads(message))


def redact_event_payload_for_log(event: EventPayload) -> dict[str, Any]:
    payload: dict[str, Any] = dict(event)
    images = event.get("images", [])
    payload["image_count"] = len(images)
    payload["images"] = [
        {
            key: image[key]
            for key in ("segment_index", "file_size", "sub_type")
            if key in image
        }
        for image in images
    ]
    return payload


def _segment_type_and_data(segment: Any) -> tuple[str, Mapping[str, Any]]:
    if isinstance(segment, Mapping):
        segment_type = str(segment.get("type", "") or "").lower()
        data = segment.get("data", {})
        return segment_type, data if isinstance(data, Mapping) else {}

    segment_type = str(getattr(segment, "type", "") or "").lower()
    if not segment_type:
        segment_type = type(segment).__name__.lower()
    data = getattr(segment, "data", None)
    if isinstance(data, Mapping):
        return segment_type, data
    return segment_type, getattr(segment, "__dict__", {})


def _set_text_field(image: ImagePayload, key: str, value: Any) -> None:
    normalized = str(value or "").strip()
    if normalized:
        image[key] = normalized  # type: ignore[literal-required]


def _set_int_field(image: ImagePayload, key: str, value: Any) -> None:
    if value in (None, ""):
        return
    try:
        image[key] = int(value)  # type: ignore[literal-required]
    except (TypeError, ValueError):
        return
