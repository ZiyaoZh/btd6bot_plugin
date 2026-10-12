from __future__ import annotations

from typing import Any, TypedDict


class ImagePayload(TypedDict, total=False):
    segment_index: int
    url: str
    file_id: str
    file_name: str
    file_size: int
    sub_type: int
    mime_type: str
    width: int
    height: int
    sha256: str
    cache_path: str
    processing_status: str
    expires_at: str


class _EventPayloadRequired(TypedDict):
    module: str
    event_type: str
    group_id: str
    user_id: str
    text: str
    bot_mentioned: bool
    mentioned_qqs: list[str]
    identity: dict[str, Any]


class EventPayload(_EventPayloadRequired, total=False):
    message_id: str
    reply_to_message_id: str
    images: list[ImagePayload]


class _CommandResultPayloadRequired(TypedDict):
    module: str
    ok: bool
    command: str
    data: dict[str, Any]
    message: str
    extra_prompt: str


class CommandResultPayload(_CommandResultPayloadRequired, total=False):
    response_type: str  # text | image | none
    image_path: str


class AdminControlPayload(TypedDict):
    module: str
    matched: bool
    authorized: bool
    action: str  # start | stop | proactive_* | model_turns_* | system_commands | none
    reason: str
