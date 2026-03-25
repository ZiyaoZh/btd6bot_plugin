from __future__ import annotations

from typing import Any, TypedDict

from typing_extensions import NotRequired


class EventPayload(TypedDict):
    module: str
    event_type: str
    group_id: str
    user_id: str
    text: str
    bot_mentioned: bool
    mentioned_qqs: list[str]
    identity: dict[str, Any]


class IntentPayload(TypedDict):
    module: str
    target: str  # chat | tool_selector | command_processor | noop
    intent: str
    arguments: dict[str, Any]
    reply: str
    reason: str


class CommandResultPayload(TypedDict):
    module: str
    ok: bool
    command: str
    data: dict[str, Any]
    message: str
    extra_prompt: str
    response_type: NotRequired[str]  # text | image
    image_path: NotRequired[str]


class AdminControlPayload(TypedDict):
    module: str
    matched: bool
    authorized: bool
    action: str  # start | stop | proactive_on | proactive_off | none
    reason: str
