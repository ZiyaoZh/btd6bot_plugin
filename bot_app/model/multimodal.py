from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from ..protocol import EventPayload
from ..settings import AppSettings


def build_multimodal_user_content(
    *,
    event: EventPayload,
    reply_context: dict[str, object],
    settings: AppSettings,
) -> str | list[dict[str, Any]]:
    text = json.dumps(
        {"task": "respond_to_event", "reply_context": reply_context},
        ensure_ascii=False,
    )
    if (
        not settings.is_vision_enabled_for_group(str(event.get("group_id", "")))
        or settings.vision_total_image_limit <= 0
    ):
        return text

    selected: list[tuple[str, dict[str, Any]]] = []
    selected_keys: set[str] = set()
    current = _available_images(list(event.get("images", []) or []))
    for index, image in enumerate(
        current[: settings.vision_current_image_limit], start=1
    ):
        _append_unique(selected, selected_keys, f"当前消息图片 {index}", image)

    remaining = settings.vision_total_image_limit - len(selected)
    history_limit = min(settings.vision_history_image_limit, max(0, remaining))
    history_selected = 0
    if history_limit:
        recent = reply_context.get("recent_group_window", [])
        if isinstance(recent, list):
            history: list[tuple[str, dict[str, Any]]] = []
            for message in reversed(recent):
                if not isinstance(message, dict):
                    continue
                sequence = message.get("sequence", "?")
                for image_index, image in enumerate(
                    _available_images(list(message.get("images", []) or [])), start=1
                ):
                    history.append(
                        (f"群聊历史第 {sequence} 条图片 {image_index}", image)
                    )
            for label, image in history:
                if len(selected) >= settings.vision_total_image_limit:
                    break
                if history_selected >= history_limit:
                    break
                if _append_unique(selected, selected_keys, label, image):
                    history_selected += 1

    remaining = min(
        settings.vision_total_image_limit - len(selected),
        history_limit - history_selected,
    )
    if remaining:
        session = reply_context.get("user_session_memory", {})
        turns = session.get("recent_turns", []) if isinstance(session, dict) else []
        if isinstance(turns, list):
            for turn_index, turn in enumerate(reversed(turns), start=1):
                if remaining <= 0 or not isinstance(turn, dict):
                    break
                for image_index, image in enumerate(
                    _available_images(list(turn.get("images", []) or [])), start=1
                ):
                    if _append_unique(
                        selected,
                        selected_keys,
                        f"用户会话前第 {turn_index} 条图片 {image_index}",
                        image,
                    ):
                        remaining -= 1
                    if remaining <= 0:
                        break

    if not selected:
        return text

    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for label, image in selected:
        path = Path(str(image["cache_path"]))
        mime_type = str(image.get("mime_type", "") or "image/jpeg")
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        content.extend(
            [
                {"type": "text", "text": f"\n[{label}]"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{encoded}",
                        "detail": settings.vision_image_detail,
                    },
                },
            ]
        )
    return content


def _available_images(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    available: list[dict[str, Any]] = []
    for image in images:
        path = Path(str(image.get("cache_path", "") or ""))
        if image.get("processing_status") == "cached" and path.is_file():
            available.append(image)
    return available


def _append_unique(
    selected: list[tuple[str, dict[str, Any]]],
    selected_keys: set[str],
    label: str,
    image: dict[str, Any],
) -> bool:
    key = str(image.get("sha256", "") or image.get("cache_path", "") or "")
    if not key or key in selected_keys:
        return False
    selected_keys.add(key)
    selected.append((label, image))
    return True
