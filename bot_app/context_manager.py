from __future__ import annotations

import json

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
            "content": f"[历史会话摘要] {summary}",
        }
        memory.replace_history(group_id, user_id, [summary_message, *recent_history])

        if trace_id:
            log_flow(
                stage="context.compacted",
                trace_id=trace_id,
                group_id=group_id,
                user_id=user_id,
                original_chars=total_chars,
                keep_recent_turns=self._keep_recent_turns,
                summary_chars=len(summary),
                compacted_chars=memory.total_content_length(group_id, user_id),
            )
        return True

    async def _summarize_old_history(self, history: list[dict[str, str]], trace_id: str = "") -> str:
        messages = [
            {
                "role": "system",
                "content": (
                    "你是会话压缩器。请将给定历史会话压缩为一段中文总结，"
                    "保留关键上下文：用户偏好、未完成事项、重要结论、明确约束。"
                    "只输出纯文本，不要使用Markdown，不要分点。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": "compress_history",
                        "summary_requirements": "一段话，信息密度高，避免废话",
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
        return (summary or "").strip()[: self._summary_max_chars]