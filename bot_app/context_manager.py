from __future__ import annotations

import json
import time

from .context_budget import ContextBudget
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
        self._keep_recent_turns = max(
            1, int(settings.context_compact_keep_recent_turns)
        )
        self._summary_max_chars = max(
            120, int(settings.context_compact_summary_max_chars)
        )
        self._summary_model = (
            settings.context_compact_model or settings.models[0]
        ).strip()
        self._summary_api_base_url = (
            (settings.context_compact_api_base_url or settings.api_base_url)
            .strip()
            .rstrip("/")
        )
        self._summary_api_key = (
            settings.context_compact_api_key or settings.api_key
        ).strip()
        self._retry_after: dict[tuple[str, str], float] = {}
        self._budget = ContextBudget(settings.context_max_input_tokens)

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
        key = (group_id, user_id)
        if time.monotonic() < self._retry_after.get(key, 0):
            return False

        history = memory.get_history(group_id, user_id)
        keep_messages = max(2, min(self._keep_recent_turns, memory.history_turns) * 2)
        if len(history) <= keep_messages:
            return False

        old_history = history[:-keep_messages]
        recent_history = history[-keep_messages:]
        # Catch up in bounded chunks after a long outage. Do not discard turns
        # that were not actually supplied to the summarizer.
        while (
            self._budget.count(self._summary_messages(old_history), [])
            > self._budget.max_tokens
            and len(old_history) > 2
        ):
            cut = max(1, len(old_history) // 2)
            if old_history[cut]["role"] == "assistant" and cut > 1:
                cut -= 1
            recent_history = old_history[cut:] + recent_history
            old_history = old_history[:cut]
        try:
            summary = await self._summarize_old_history(old_history, trace_id=trace_id)
        except Exception as exc:  # noqa: BLE001 - summarizer failure preserves the original history
            self._retry_after[key] = time.monotonic() + 60
            log_flow(
                "context.compaction_failed", trace_id, error_type=type(exc).__name__
            )
            return False
        if not summary:
            self._retry_after[key] = time.monotonic() + 60
            log_flow(
                "context.compaction_rejected",
                trace_id,
                reason="invalid_or_empty_summary",
            )
            return False

        summary_message = {
            "role": "assistant",
            "content": serialize_session_summary(summary),
        }
        if len(summary_message["content"]) >= sum(
            len(item["content"]) for item in old_history
        ):
            self._retry_after[key] = time.monotonic() + 60
            return False
        memory.replace_history(group_id, user_id, [summary_message, *recent_history])
        session_memory = build_user_session_memory_context(
            memory.get_history(group_id, user_id),
            recent_turn_limit=keep_messages,
        )

        if trace_id:
            log_flow(
                stage="context.compacted",
                trace_id=trace_id,
                group_id=group_id,
                user_id=user_id,
                original_chars=total_chars,
                keep_recent_turns=len(recent_history) // 2,
                summary_chars=len(summary_message["content"]),
                compacted_chars=memory.total_content_length(group_id, user_id),
                summary_payload=summary,
                user_session_memory=session_memory,
            )
        return True

    async def _summarize_old_history(
        self, history: list[dict[str, str]], trace_id: str = ""
    ) -> UserSessionSummaryContext | None:
        messages = self._budget.fit(
            self._summary_messages(history), [], query="", trace_id=trace_id
        )
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
            return None
        # Drop low-priority entries before cutting critical task restrictions.
        for field in (
            "raw_summary",
            "established_facts",
            "recent_topic",
            "user_preferences",
            "open_loops",
            "restrictions",
        ):
            while (
                len(serialize_session_summary(parsed)) > self._summary_max_chars
                and parsed[field]
            ):
                value = parsed[field]
                if isinstance(value, list):
                    value.pop()
                else:
                    parsed[field] = value[
                        : max(
                            0,
                            len(value)
                            - (
                                len(serialize_session_summary(parsed))
                                - self._summary_max_chars
                            ),
                        )
                    ]
        if len(serialize_session_summary(parsed)) > self._summary_max_chars or not any(
            parsed.values()
        ):
            return None
        return parsed

    def _summary_messages(self, history: list[dict[str, str]]) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "你是会话压缩器。请将给定历史会话压缩为稳定 JSON。"
                    "你必须只输出 JSON 对象，不要输出解释。"
                    "字段固定为：user_preferences, open_loops, established_facts, recent_topic, restrictions, raw_summary。"
                    "user_preferences/open_loops/established_facts/restrictions 为字符串列表。"
                    "只保留用户明确表达的信息，不要猜测，不要把助手旧回答变成已确认事实。"
                    "保留仍有效的偏好、限制和未完成任务；用户纠正的信息替换旧信息，完成或取消的任务移除。"
                    "不保留可能过期的活动时间和游戏数值，相关事实后续重新检索。"
                    "recent_topic 用一句中文概括持续话题。"
                    "raw_summary 用一段简短中文保留总体摘要。"
                    f"整个 JSON 最多 {self._summary_max_chars} 字符。"
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

    def _parse_structured_summary(self, raw: str) -> UserSessionSummaryContext | None:
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
        list_fields = (
            "user_preferences",
            "open_loops",
            "established_facts",
            "restrictions",
        )
        if any(
            not isinstance(payload.get(key), list)
            or any(not isinstance(item, str) for item in payload[key])
            for key in list_fields
        ):
            return None
        if any(
            not isinstance(payload.get(key), str)
            for key in ("recent_topic", "raw_summary")
        ):
            return None
        if not any(
            payload[key] for key in (*list_fields, "recent_topic", "raw_summary")
        ):
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
