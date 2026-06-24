from __future__ import annotations

import json

from .context_contracts import (
    UserSessionSummaryContext,
    build_user_session_memory_context,
    serialize_session_summary,
)
from .llm_client import LLMClient
from .logging_utils import log_flow
from .memory import GroupChatMemory
from .settings import AppSettings


class ContextManager:
    """Compact long per-user context using a dedicated summarization model."""

    def __init__(self, llm: LLMClient, settings: AppSettings) -> None:
        self._llm = llm
        self._max_chars = max(1000, int(settings.context_compact_max_chars))
        self._keep_recent_turns = max(1, int(settings.context_compact_keep_recent_turns))
        self._summary_max_chars = max(120, int(settings.context_compact_summary_max_chars))
        self._summary_model = (settings.context_compact_model or settings.models[0]).strip()
        self._summary_api_base_url = (settings.context_compact_api_base_url or settings.api_base_url).strip().rstrip("/")
        self._summary_api_key = (settings.context_compact_api_key or settings.api_key).strip()

    async def compact_if_needed(
        self,
        memory: GroupChatMemory,
        group_id: str,
        user_id: str,
        trace_id: str = "",
    ) -> bool:
        total_chars = memory.total_content_length(group_id, user_id)
        if total_chars <= self._max_chars:
            return False

        history = memory.get_history(group_id, user_id)
        keep_messages = max(2, self._keep_recent_turns * 2)
        if len(history) <= keep_messages:
            return False

        old_history = history[:-keep_messages]
        recent_history = history[-keep_messages:]
        summary = await self._summarize_old_history(old_history, trace_id=trace_id)
        if not summary:
            return False

        summary_message = {
            "role": "assistant",
            "content": serialize_session_summary(summary),
        }
        memory.replace_history(group_id, user_id, [summary_message, *recent_history])
        session_memory = build_user_session_memory_context(
            memory.get_history(group_id, user_id),
            recent_turn_limit=self._keep_recent_turns * 2,
        )

        if trace_id:
            log_flow(
                stage="context.compacted",
                trace_id=trace_id,
                group_id=group_id,
                user_id=user_id,
                original_chars=total_chars,
                keep_recent_turns=self._keep_recent_turns,
                summary_chars=len(summary_message["content"]),
                compacted_chars=memory.total_content_length(group_id, user_id),
                summary_payload=summary,
                user_session_memory=session_memory,
            )
        return True

    async def _summarize_old_history(
        self, history: list[dict[str, str]], trace_id: str = ""
    ) -> UserSessionSummaryContext | None:
        messages = [
            {
                "role": "system",
                "content": (
                    "你是会话压缩器。请将给定历史会话压缩为稳定 JSON。"
                    "你必须只输出 JSON 对象，不要输出解释。"
                    "字段固定为：user_preferences, open_loops, established_facts, recent_topic, restrictions, raw_summary。"
                    "其中前四个列表字段只保留明确出现或可高置信提取的信息，不要猜测。"
                    "recent_topic 用一句中文概括持续话题。"
                    "raw_summary 用一段简短中文保留总体摘要。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": "compress_history",
                        "summary_requirements": {
                            "output": "json_object",
                            "avoid_guessing": True,
                            "keep_high_confidence_only": True,
                        },
                        "history": history,
                    },
                    ensure_ascii=False,
                ),
            },
        ]

        summary = await self._llm.chat_with_override(
            messages=messages,
            trace_id=trace_id,
            purpose="context_compaction",
            model=self._summary_model,
            api_base_url=self._summary_api_base_url,
            api_key=self._summary_api_key,
            temperature=0.2,
        )
        parsed = self._parse_structured_summary(summary)
        if parsed is None:
            fallback_text = (summary or "").strip()[: self._summary_max_chars]
            if not fallback_text:
                return None
            return {
                "user_preferences": [],
                "open_loops": [],
                "established_facts": [],
                "recent_topic": fallback_text,
                "restrictions": [],
                "raw_summary": fallback_text,
            }
        if len(parsed["raw_summary"]) > self._summary_max_chars:
            parsed["raw_summary"] = parsed["raw_summary"][: self._summary_max_chars]
        return parsed

    def _parse_structured_summary(
        self, raw: str
    ) -> UserSessionSummaryContext | None:
        text = (raw or "").strip()
        if not text:
            return None

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start == -1 or end == -1 or end <= start:
                return None
            try:
                payload = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None

        if not isinstance(payload, dict):
            return None

        return {
            "user_preferences": self._normalize_string_list(
                payload.get("user_preferences", [])
            ),
            "open_loops": self._normalize_string_list(payload.get("open_loops", [])),
            "established_facts": self._normalize_string_list(
                payload.get("established_facts", [])
            ),
            "recent_topic": str(payload.get("recent_topic", "") or "").strip(),
            "restrictions": self._normalize_string_list(
                payload.get("restrictions", [])
            ),
            "raw_summary": str(payload.get("raw_summary", "") or "").strip(),
        }

    def _normalize_string_list(self, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        items: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text:
                items.append(text)
        return items
