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
    if not settings.vision_enabled or settings.vision_total_image_limit <= 0:
        return text

    selected: list[tuple[str, dict[str, Any]]] = []
    current = _available_images(list(event.get("images", []) or []))
    for index, image in enumerate(
        current[: settings.vision_current_image_limit], start=1
    ):
        selected.append((f"当前消息图片 {index}", image))

    remaining = settings.vision_total_image_limit - len(selected)
    history_limit = min(settings.vision_history_image_limit, max(0, remaining))
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
            selected.extend(history[:history_limit])

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
                    "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
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
