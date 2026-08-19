from __future__ import annotations

import json
import re
from typing import Any

from .context_contracts import RecentGroupMessageContext

_CQ_CODE_RE = re.compile(r"\[CQ:[^\]]+\]")
_WHITESPACE_RE = re.compile(r"\s+")
_CQ_AT_RE = re.compile(r"\[CQ:at,qq=(\d+)\]")


def exclude_message_id(
    rows: list[dict[str, Any]], message_id: str
) -> list[dict[str, Any]]:
    normalized_id = str(message_id or "")
    if not normalized_id:
        return rows
    return [
        row for row in rows if str(row.get("message_id", "") or "") != normalized_id
    ]


def clean_group_message_text(raw_text: str) -> str:
    text = str(raw_text or "")
    text = _CQ_CODE_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


def classify_mention_relation(
    raw_text: str,
    *,
    bot_qq: str = "",
    bot_name: str = "",
) -> str:
    text = str(raw_text or "")
    if bot_qq and f"[CQ:at,qq={bot_qq}]" in text:
        return "mention_bot"

    if bot_name:
        for at_sign in ("@", "＠"):
            if f"{at_sign}{bot_name}" in text:
                return "mention_bot"

    if _CQ_AT_RE.search(text):
        return "mention_user"

    return "none"


def build_recent_group_window(
    rows: list[dict[str, Any]],
    *,
    limit: int,
    bot_qq: str = "",
    bot_name: str = "",
) -> list[RecentGroupMessageContext]:
    window: list[RecentGroupMessageContext] = []

    for row in rows:
        images = list(row.get("images", []) or [])
        cleaned_text = str(row.get("normalized_text", "") or "").strip()
        if not cleaned_text:
            cleaned_text = clean_group_message_text(
                str(row.get("raw_message", "") or "")
            )
        if not cleaned_text and images:
            cleaned_text = f"[该消息含 {len(images)} 张图片]"
        if not cleaned_text:
            continue

        speaker = (
            str(row.get("sender_card", "") or "").strip()
            or str(row.get("sender_nickname", "") or "").strip()
            or str(row.get("user_id", "") or "").strip()
            or "unknown"
        )
        item: RecentGroupMessageContext = {
            "message_id": str(row.get("message_id", "") or ""),
            "speaker": speaker,
            "cleaned_text": cleaned_text,
            "created_at": str(row.get("created_at", "") or ""),
            "mention_relation": str(row.get("mention_relation", "") or "").strip()
            or classify_mention_relation(
                str(row.get("raw_message", "") or ""),
                bot_qq=bot_qq,
                bot_name=bot_name,
            ),
        }
        if images:
            item["image_count"] = len(images)
            item["images"] = [
                {
                    key: image[key]
                    for key in (
                        "segment_index",
                        "media_type",
                        "processing_status",
                        "mime_type",
                        "sha256",
                        "cache_path",
                        "visual_summary",
                        "expires_at",
                    )
                    if image.get(key) not in (None, "")
                }
                for image in images
            ]
        window.append(item)

    trimmed_window = window[-max(1, int(limit)) :]
    for index, item in enumerate(trimmed_window, start=1):
        item["sequence"] = index
    return trimmed_window


def build_message_record_metadata(
    raw_message: str,
    *,
    bot_qq: str = "",
    bot_name: str = "",
) -> dict[str, Any]:
    normalized_text = clean_group_message_text(raw_message)
    mention_relation = classify_mention_relation(
        raw_message,
        bot_qq=bot_qq,
        bot_name=bot_name,
    )
    return {
        "normalized_text": normalized_text,
        "mention_relation": mention_relation,
        "is_text_meaningful": bool(normalized_text),
        "message_type": "text" if normalized_text else "non_text",
        "mentioned_qqs": _CQ_AT_RE.findall(str(raw_message or "")),
    }


def serialize_message_record_metadata(metadata: dict[str, Any]) -> str:
    return json.dumps(metadata, ensure_ascii=False)
