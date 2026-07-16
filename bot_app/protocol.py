from __future__ import annotations

from typing import Any, TypedDict


class EventPayload(TypedDict):
    module: str
    event_type: str
    group_id: str
    user_id: str
    text: str
    bot_mentioned: bool
    mentioned_qqs: list[str]
    identity: dict[str, Any]


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
    action: str  # start | stop | proactive_* | model_turns_* | none
    reason: str
