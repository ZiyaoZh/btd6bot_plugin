from __future__ import annotations

import re
from typing import Optional

from ncatbot.event.qq import GroupMessageEvent

from .db_manager import BotDatabase
from .protocol import AdminControlPayload, EventPayload
from .settings import AppSettings


class GroupMessageReceiver:
    def __init__(self, settings: AppSettings, db: Optional[BotDatabase] = None) -> None:
        self._settings = settings
        self._db = db
        self._proactive_chat_enabled = settings.proactive_chat_enabled

    def set_proactive_chat_enabled(self, enabled: bool) -> None:
        self._proactive_chat_enabled = bool(enabled)

    def is_proactive_chat_enabled(self) -> bool:
        return self._proactive_chat_enabled

    def should_accept(self, msg: GroupMessageEvent) -> bool:
        if str(msg.group_id) not in self._settings.target_group_ids:
            return False
        if self._settings.bot_qq and str(msg.user_id) == self._settings.bot_qq:
            return False
        if not self._proactive_chat_enabled and not self._contains_bot_mention(str(msg.raw_message or "")):
            return False
        return True

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

        if not self._is_admin_sender(msg):
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
        self._db.record_group_message(msg)

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
            "text": text,
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
        return ""
