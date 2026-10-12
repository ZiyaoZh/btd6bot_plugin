from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, NotRequired, TypedDict

from .protocol import EventPayload


class CompactIdentityContext(TypedDict):
    nickname: str
    card: str
    role: str
    title: str
    level: str


class CurrentEventContext(TypedDict):
    event_type: str
    group_id: str
    user_id: str
    text: str
    bot_mentioned: bool
    mentioned_qqs: list[str]
    identity: CompactIdentityContext
    message_id: str
    image_count: int
    images: list[dict[str, Any]]
    reply_to_message_id: NotRequired[str]
    created_at: NotRequired[str]


class RecentGroupMessageContext(TypedDict, total=False):
    sequence: int
    message_id: str
    speaker: str
    cleaned_text: str
    created_at: str
    mention_relation: str
    image_count: int
    images: list[dict[str, Any]]


class UserSessionSummaryContext(TypedDict, total=False):
    user_preferences: list[str]
    open_loops: list[str]
    established_facts: list[str]
    recent_topic: str
    restrictions: list[str]
    raw_summary: str


class UserSessionTurnContext(TypedDict, total=False):
    role: str
    turn_type: str
    text: str
    message_id: str
    image_count: int
    images: list[dict[str, Any]]


class UserSessionMemoryContext(TypedDict):
    summary: UserSessionSummaryContext
    recent_turns: list[UserSessionTurnContext]


class PolicyHintsContext(TypedDict):
    ignore_rule: str
    mention_rule: str
    tool_rule: str
    style_rule: str


class ReplyContext(TypedDict):
    current_event: CurrentEventContext
    user_session_memory: UserSessionMemoryContext
    recent_group_window: list[RecentGroupMessageContext]
    policy_hints: PolicyHintsContext
    retrieved_memory: NotRequired[list[dict[str, Any]]]


def build_current_event_context(event: EventPayload) -> CurrentEventContext:
    images = _compact_image_references(event.get("images", []))
    context: CurrentEventContext = {
        "event_type": str(event.get("event_type", "") or ""),
        "group_id": str(event.get("group_id", "") or ""),
        "user_id": str(event.get("user_id", "") or ""),
        "text": str(event.get("text", "") or ""),
        "bot_mentioned": bool(event.get("bot_mentioned", False)),
        "mentioned_qqs": [
            str(item or "")
            for item in event.get("mentioned_qqs", [])
            if str(item or "").strip()
        ],
        "identity": _compact_identity(event.get("identity", {})),
        "message_id": str(event.get("message_id", "") or ""),
        "image_count": len(event.get("images", [])),
        "images": images,
    }
    if event.get("reply_to_message_id"):
        context["reply_to_message_id"] = event["reply_to_message_id"]
    return context


def serialize_compact_user_event(event: EventPayload) -> str:
    payload = build_current_event_context(event)
    payload["created_at"] = datetime.now(UTC).isoformat()
    return json.dumps(
        {
            "kind": "user_event",
            "event": payload,
        },
        ensure_ascii=False,
    )


def build_user_session_memory_context(
    history: list[dict[str, str]],
    recent_turn_limit: int,
) -> UserSessionMemoryContext:
    summary: UserSessionSummaryContext = {
        "user_preferences": [],
        "open_loops": [],
        "established_facts": [],
        "recent_topic": "",
        "restrictions": [],
        "raw_summary": "",
    }
    recent_turns: list[UserSessionTurnContext] = []

    for message in history:
        role = str(message.get("role", "assistant") or "assistant")
        content = str(message.get("content", "") or "")
        parsed = _parse_memory_message(role, content)
        if parsed["turn_type"] == "summary":
            summary.update(_extract_summary_fields(content, parsed["text"]))
            continue
        recent_turns.append(parsed)

    return {
        "summary": summary,
        "recent_turns": recent_turns[-max(1, int(recent_turn_limit)) :],
    }


def build_policy_hints_context() -> PolicyHintsContext:
    return {
        "ignore_rule": (
            "如果消息没有明确指向机器人、没有任务诉求、只是群友闲聊，应忽略消息。"
        ),
        "mention_rule": (
            "如果当前消息明确 @ 了机器人，除非用户明确要求停止回复，否则不要忽略。"
        ),
        "tool_rule": (
            "涉及 BTD6 活动、排行榜、知识库、脚本说明书、群聊总结、活跃榜或开箱模拟时，必须调用工具获取事实。"
        ),
        "style_rule": "输出只使用纯文本，中文、自然、简洁，适合直接发到群里。",
    }


def serialize_reply_context(context: ReplyContext) -> str:
    return json.dumps(
        {
            "task": "process_group_message",
            "reply_context": context,
            "instruction": (
                "请根据 reply_context 处理当前群聊消息。需要事实信息时调用工具；"
                "无需回复时调用 ignore_message。"
            ),
        },
        ensure_ascii=False,
    )


def _compact_identity(identity: dict[str, Any]) -> CompactIdentityContext:
    return {
        "nickname": str(identity.get("nickname", "") or ""),
        "card": str(identity.get("card", "") or ""),
        "role": str(identity.get("role", "") or ""),
        "title": str(identity.get("title", "") or ""),
        "level": str(identity.get("level", "") or ""),
    }


def _parse_memory_message(role: str, content: str) -> UserSessionTurnContext:
    parsed_json = _try_parse_json_object(content)

    if role == "user":
        text = _extract_user_text(parsed_json, content)
        turn: UserSessionTurnContext = {
            "role": "user",
            "turn_type": "user_event",
            "text": text,
        }
        event = parsed_json.get("event", {}) if isinstance(parsed_json, dict) else {}
        if isinstance(event, dict):
            message_id = str(event.get("message_id", "") or "")
            images = _compact_image_references(event.get("images", []))
            image_count = max(_safe_int(event.get("image_count", 0)), len(images))
            if message_id:
                turn["message_id"] = message_id
            if image_count:
                turn["image_count"] = image_count
            if images:
                turn["images"] = images
        return turn

    if content.startswith("[历史会话摘要]"):
        return {
            "role": "assistant",
            "turn_type": "summary",
            "text": content.removeprefix("[历史会话摘要]").strip(),
        }

    if role == "assistant":
        parsed_summary = _try_parse_json_object(content)
        if (
            isinstance(parsed_summary, dict)
            and str(parsed_summary.get("kind", "") or "") == "session_summary"
        ):
            return {
                "role": "assistant",
                "turn_type": "summary",
                "text": str(parsed_summary.get("recent_topic", "") or "").strip(),
            }

    return {
        "role": "assistant",
        "turn_type": "assistant_reply",
        "text": str(content or "").strip(),
    }


def _extract_user_text(parsed_json: dict[str, Any] | None, content: str) -> str:
    if not isinstance(parsed_json, dict):
        return str(content or "").strip()

    if parsed_json.get("kind") == "user_event":
        event = parsed_json.get("event", {})
        if isinstance(event, dict):
            return str(event.get("text", "") or "").strip()

    event = parsed_json.get("event", {})
    if isinstance(event, dict):
        return str(event.get("text", "") or "").strip()

    return str(parsed_json.get("text", "") or content or "").strip()


def _try_parse_json_object(content: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def serialize_session_summary(summary: UserSessionSummaryContext) -> str:
    normalized = {
        "kind": "session_summary",
        "user_preferences": _normalize_string_list(summary.get("user_preferences", [])),
        "open_loops": _normalize_string_list(summary.get("open_loops", [])),
        "established_facts": _normalize_string_list(
            summary.get("established_facts", [])
        ),
        "recent_topic": str(summary.get("recent_topic", "") or "").strip(),
        "restrictions": _normalize_string_list(summary.get("restrictions", [])),
        "raw_summary": str(summary.get("raw_summary", "") or "").strip(),
    }
    return json.dumps(normalized, ensure_ascii=False)


def _extract_summary_fields(
    content: str, fallback_text: str
) -> UserSessionSummaryContext:
    parsed = _try_parse_json_object(content)
    if (
        not isinstance(parsed, dict)
        or str(parsed.get("kind", "") or "") != "session_summary"
    ):
        text = str(fallback_text or "").strip()
        return {
            "recent_topic": text,
            "raw_summary": text,
        }

    return {
        "user_preferences": _normalize_string_list(parsed.get("user_preferences", [])),
        "open_loops": _normalize_string_list(parsed.get("open_loops", [])),
        "established_facts": _normalize_string_list(
            parsed.get("established_facts", [])
        ),
        "recent_topic": str(parsed.get("recent_topic", "") or "").strip(),
        "restrictions": _normalize_string_list(parsed.get("restrictions", [])),
        "raw_summary": str(parsed.get("raw_summary", "") or "").strip(),
    }


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            result.append(text)
    return result


def _compact_image_references(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    allowed_keys = (
        "segment_index",
        "media_type",
        "processing_status",
        "mime_type",
        "sha256",
        "cache_path",
        "visual_summary",
        "expires_at",
    )
    references: list[dict[str, Any]] = []
    for image in value:
        if not isinstance(image, dict):
            continue
        reference = {
            key: image[key] for key in allowed_keys if image.get(key) not in (None, "")
        }
        if reference:
            references.append(reference)
    return references


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
