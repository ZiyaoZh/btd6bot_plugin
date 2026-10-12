"""Bound every model request while retaining native tool-call protocol pairs."""

from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from typing import Any

from .context_selection import evidence_excerpt, query_terms, relevance
from .logging_utils import log_flow


class ContextBudgetExceeded(ValueError):
    pass


@lru_cache(maxsize=1)
def _encoder() -> Any:
    try:
        import tiktoken

        return tiktoken.get_encoding("o200k_base")
    except Exception:  # noqa: BLE001 - token counting must work offline without its cache
        return None


def estimate_tokens(value: Any, *, image_tokens: int = 2048) -> int:
    """Provider-neutral estimate; image reserve and 10% text margin are explicit.

    Unknown proxy model aliases need not share this tokenizer. With no local
    tokenizer, UTF-8 bytes provide a deliberately conservative text fallback.
    """
    if isinstance(value, dict):
        if value.get("type") == "image_url":
            return image_tokens
        return 4 + sum(
            estimate_tokens(k, image_tokens=image_tokens)
            + estimate_tokens(v, image_tokens=image_tokens)
            for k, v in value.items()
        )
    if isinstance(value, list):
        return 2 + sum(estimate_tokens(v, image_tokens=image_tokens) for v in value)
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    encoder = _encoder()
    return (
        int(len(encoder.encode(text, disallowed_special=())) * 1.1) + 1
        if encoder
        else len(text.encode("utf-8")) + 1
    )


def _compact_value(value: Any, query: str, limit: int) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return evidence_excerpt(value, query, limit, label="观察片段")[0]
    if isinstance(value, list):
        if len(value) > 20:
            terms = query_terms(query)
            selected = sorted(
                sorted(
                    range(len(value)),
                    key=lambda i: (
                        -relevance(json.dumps(value[i], ensure_ascii=False), terms)
                    ),
                )[:20]
            )
            value = [value[i] for i in selected]
        return [
            _compact_value(v, query, max(160, limit // max(1, min(len(value), 12))))
            for v in value[:20]
        ]
    if isinstance(value, dict):
        return {k: _compact_value(v, query, limit) for k, v in value.items()}
    return value


class ContextBudget:
    def __init__(self, max_tokens: int, *, image_tokens: int = 2048) -> None:
        self.max_tokens = max(1024, max_tokens)
        self.image_tokens = max(1, image_tokens)

    def count(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> int:
        return estimate_tokens(
            {"messages": messages, "tools": tools}, image_tokens=self.image_tokens
        )

    def fit(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        query: str,
        trace_id: str = "",
    ) -> list[dict[str, Any]]:
        before = self.count(messages, tools)
        fitted = deepcopy(messages)
        seen_results: dict[str, str] = {}
        for message in fitted:
            if message.get("role") != "tool":
                continue
            content = message.get("content", "")
            if not isinstance(content, str):
                continue
            if content in seen_results:
                message["content"] = json.dumps(
                    {
                        "duplicate_of_call_id": seen_results[content],
                        "message": "结果与前面的该次工具返回完全相同。",
                    },
                    ensure_ascii=False,
                )
            else:
                seen_results[content] = str(message.get("tool_call_id", ""))
        # Per-observation selection also applies below the global budget: a
        # single enormous section should not consume all remaining attention.
        original_contents = [message.get("content") for message in fitted]
        for limit in (8000, 4000, 2000, 1000, 400):
            for index, message in enumerate(fitted):
                # Select every tier from the original observation. Re-excerpting
                # an earlier excerpt can lose evidence and invent source offsets.
                content = original_contents[index]
                if not isinstance(content, str):
                    continue
                try:
                    payload = json.loads(content)
                except (ValueError, TypeError):
                    continue
                if message.get("role") == "tool" and isinstance(payload, dict):
                    self._compact_result(payload, query, limit)
                elif (
                    isinstance(payload, dict)
                    and payload.get("kind") == "harness_tool_observations"
                ):
                    for record in payload.get("results", []):
                        if isinstance(record, dict) and isinstance(
                            record.get("result"), dict
                        ):
                            self._compact_result(record["result"], query, limit)
                else:
                    continue
                message["content"] = json.dumps(payload, ensure_ascii=False)
            if self.count(fitted, tools) <= self.max_tokens:
                break
            self._trim_history(fitted)
        after = self.count(fitted, tools)
        # Compaction can be unavailable for many turns. Keep shrinking the
        # working history until it fits, rather than retrying the same oversized
        # session forever. The persisted original history remains intact.
        while after > self.max_tokens and self._trim_history(fitted):
            after = self.count(fitted, tools)
        log_flow(
            "context.budget",
            trace_id,
            estimated_tokens_before=before,
            estimated_tokens_after=after,
            max_input_tokens=self.max_tokens,
            image_token_reserve=self.image_tokens,
            message_count=len(fitted),
            tool_count=len(tools),
            fits=after <= self.max_tokens,
        )
        if after > self.max_tokens:
            # Never silently cut the user's current instructions, the system
            # rules, tool schemas, or provider-required assistant fields.
            raise ContextBudgetExceeded(
                f"estimated input {after} exceeds {self.max_tokens}"
            )
        return fitted

    @staticmethod
    def _compact_result(result: dict[str, Any], query: str, limit: int) -> None:
        data = result.get("data")
        if not isinstance(data, dict):
            return
        compacted = _compact_value(data, query, limit)
        if compacted != data:
            compacted["context_excerpted"] = True
            compacted["context_note"] = (
                "这是工具结果的局部证据，观察片段行号不是文件行号。省略部分不能视为不存在；使用具体 query 或工具提供的原始 excerpts/start_line 重新读取。"
            )
            result["data"] = compacted

    @staticmethod
    def _trim_history(messages: list[dict[str, Any]]) -> bool:
        changed = False
        for message in messages:
            if message.get("role") != "user":
                continue
            parts = message.get("content")
            candidates = parts if isinstance(parts, list) else [{"text": parts}]
            for part in candidates:
                if not isinstance(part, dict) or not isinstance(part.get("text"), str):
                    continue
                try:
                    payload = json.loads(part["text"])
                except ValueError:
                    continue
                context = (
                    payload.get("reply_context") if isinstance(payload, dict) else None
                )
                if not isinstance(context, dict):
                    continue
                group = context.get("recent_group_window", [])
                if group:
                    context["recent_group_window"] = group[len(group) // 2 + 1 :]
                    changed = True
                session = context.get("user_session_memory", {})
                summary = session.get("summary", {})
                if isinstance(summary, dict):
                    compacted_summary = _compact_value(
                        summary,
                        str(context.get("current_event", {}).get("text", "")),
                        600,
                    )
                    if compacted_summary != summary:
                        session["summary"] = compacted_summary
                        changed = True
                turns = session.get("recent_turns", [])
                if len(turns) > 2:
                    session["recent_turns"] = turns[2:]
                    changed = True
                else:
                    query = str(context.get("current_event", {}).get("text", ""))
                    for turn in turns:
                        text = str(turn.get("text", ""))
                        if len(text) > 1200:
                            turn["text"] = evidence_excerpt(
                                text, query, 1200, label="历史消息"
                            )[0]
                            turn["context_excerpted"] = True
                            changed = True
                recalled = context.get("retrieved_memory", [])
                if recalled:
                    context["retrieved_memory"] = recalled[:-1]
                    changed = True
                part["text"] = json.dumps(payload, ensure_ascii=False)
            if not isinstance(parts, list):
                message["content"] = candidates[0]["text"]
        return changed
