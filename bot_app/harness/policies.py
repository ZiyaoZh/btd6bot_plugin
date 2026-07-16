from __future__ import annotations

from typing import Callable
from uuid import uuid4

from ..agent.types import ToolCallRecord
from ..protocol import EventPayload
from .types import ToolInvocation


class GroundingPolicy:
    """Enforce evidence requirements for BTD6 knowledge answers."""

    _FACT_TERMS = (
        "数据",
        "机制",
        "资料",
        "属性",
        "伤害",
        "攻速",
        "价格",
        "射程",
        "穿透",
        "升级",
        "路线",
        "上路",
        "中路",
        "下路",
        "收益",
        "加成",
        "技能",
        "破铅",
        "反迷彩",
    )
    _DOMAIN_TERMS = (
        "btd6",
        "气球塔防",
        "猴",
        "猴子",
        "塔",
        "回合",
        "ddt",
        "zomg",
        "bad",
        "moab",
        "太阳神庙",
        "永久啤酒",
        "野兽之主",
        "驯兽师",
        "人鱼猴",
        "鱼人猴",
        "beast handler",
        "mermonkey",
        "brickell",
    )

    def __init__(self, query_classifier: Callable[[str], bool] | None = None) -> None:
        self._query_classifier = query_classifier

    def is_knowledge_query(self, text: str) -> bool:
        if self._query_classifier is not None:
            try:
                if self._query_classifier(text):
                    return True
            except Exception:
                pass
        normalized = str(text or "").lower()
        has_domain = any(term in normalized for term in self._DOMAIN_TERMS)
        has_fact_intent = any(term in normalized for term in self._FACT_TERMS)
        explicit_lookup = any(term in normalized for term in ("查一下", "查查", "百科"))
        return has_domain and (has_fact_intent or explicit_lookup)

    def required_tool_calls(
        self, event: EventPayload, records: list[ToolCallRecord]
    ) -> list[ToolInvocation]:
        if not self.is_knowledge_query(event["text"]):
            return []

        searches = self._records(records, "search_game_knowledge")
        if not searches:
            return [
                ToolInvocation(
                    id=self._policy_call_id(),
                    name="search_game_knowledge",
                    arguments={"query": event["text"]},
                    source="policy",
                )
            ]

        search_data = searches[-1].result.get("data", {}) or {}
        matched_count = _as_int(search_data.get("matched_count"))
        matched_paths = _string_list(search_data.get("matched_paths"))
        if matched_count <= 0 or not matched_paths:
            return []

        reads = self._records(records, "read_game_knowledge_file")
        if any(self._successful_read(record) for record in reads):
            return []

        attempted_paths = {
            str(record.arguments.get("file_path", "") or "").strip() for record in reads
        }
        target_count = self._read_target_count(search_data)
        unread_paths = [
            path
            for path in matched_paths[:target_count]
            if path and path not in attempted_paths
        ]
        return [
            ToolInvocation(
                id=self._policy_call_id(),
                name="read_game_knowledge_file",
                arguments={"file_path": path, "query": event["text"]},
                source="policy",
            )
            for path in unread_paths
        ]

    def final_override(self, event: EventPayload, records: list[ToolCallRecord]) -> str:
        if not self.is_knowledge_query(event["text"]):
            return ""
        if self.has_successful_web_search(records):
            return ""

        searches = self._records(records, "search_game_knowledge")
        if not searches:
            return "这个游戏知识问题还没有经过资料检索，我暂时不能可靠回答。"

        last_search = searches[-1].result
        search_data = last_search.get("data", {}) or {}
        query = str(
            search_data.get("knowledge_query")
            or search_data.get("knowledge_query_raw")
            or ""
        ).strip()
        if not last_search.get("ok", False):
            return str(last_search.get("message", "") or "").strip() or (
                "游戏知识检索失败，暂时不能基于知识库证据回答。"
            )

        matched_count = _as_int(search_data.get("matched_count"))
        if matched_count <= 0:
            subject = f"「{query}」" if query else "这个问题"
            return (
                f"知识库里暂时没有检索到与{subject}匹配的资料，"
                "我不能基于知识库证据确认结论。可以换个塔名、升级名或更具体的关键词再查。"
            )

        reads = self._records(records, "read_game_knowledge_file")
        if not reads:
            return (
                "知识库命中了候选页面，但还没有读取到页面内容，暂时不能基于证据回答。"
            )
        if not any(self._successful_read(record) for record in reads):
            return (
                "知识库命中了候选页面，但没有成功读取到可用内容，"
                "暂时不能基于证据回答。可以换个关键词或稍后重试。"
            )
        return ""

    def has_successful_web_search(self, records: list[ToolCallRecord]) -> bool:
        for record in self._records(records, "search_web"):
            if not record.result.get("ok", False):
                continue
            data = record.result.get("data", {}) or {}
            if _as_int(data.get("result_count")) > 0:
                return True
            results = data.get("results", [])
            if isinstance(results, list) and results:
                return True
        return False

    def build_summary(
        self,
        *,
        event: EventPayload,
        records: list[ToolCallRecord],
        final_text: str,
    ) -> dict[str, object] | None:
        searches = self._records(records, "search_game_knowledge")
        reads = self._records(records, "read_game_knowledge_file")
        web_records = self._records(records, "search_web")
        if not searches and not reads and not web_records:
            return None

        last_search = searches[-1].result if searches else None
        search_data = (last_search or {}).get("data", {}) or {}
        read_paths: list[str] = []
        successful_paths: list[str] = []
        content_chars = 0
        for record in reads:
            data = record.result.get("data", {}) or {}
            path = str(
                data.get("file_path", "") or record.arguments.get("file_path", "")
            ).strip()
            if path:
                read_paths.append(path)
                if self._successful_read(record):
                    successful_paths.append(path)
            content_chars += _as_int(data.get("content_chars", data.get("chars")))

        web_queries: list[str] = []
        web_result_count = 0
        for record in web_records:
            data = record.result.get("data", {}) or {}
            query = str(data.get("query", "") or "").strip()
            if query:
                web_queries.append(query)
            count = _as_int(data.get("result_count"))
            if count <= 0 and isinstance(data.get("results"), list):
                count = len(data["results"])
            web_result_count += max(0, count)

        web_grounded = self.has_successful_web_search(records)
        answer_grounded = bool(successful_paths) or web_grounded
        fallback = (
            self.final_override(event, records)
            if self.is_knowledge_query(event["text"])
            else ""
        )
        return {
            "knowledge_query": str(
                search_data.get("knowledge_query")
                or search_data.get("knowledge_query_raw")
                or event["text"]
            ).strip(),
            "matched_count": _as_int(search_data.get("matched_count")),
            "matched_paths": _string_list(search_data.get("matched_paths")),
            "candidate_details": search_data.get("candidate_details", [])
            if isinstance(search_data.get("candidate_details", []), list)
            else [],
            "confidence": _as_float(search_data.get("confidence")),
            "score_margin": _as_float(search_data.get("score_margin")),
            "read_count": len(read_paths),
            "read_paths": read_paths,
            "successful_read_count": len(successful_paths),
            "successful_read_paths": successful_paths,
            "web_search_count": len(web_records),
            "web_search_queries": web_queries,
            "web_search_result_count": web_result_count,
            "successful_web_search_count": sum(
                1
                for record in web_records
                if record.result.get("ok", False)
                and (
                    _as_int((record.result.get("data", {}) or {}).get("result_count"))
                    > 0
                    or bool((record.result.get("data", {}) or {}).get("results", []))
                )
            ),
            "web_grounded": web_grounded,
            "answer_grounded": answer_grounded,
            "fallback_reason": fallback if fallback and final_text == fallback else "",
            "final_response_has_text": bool((final_text or "").strip()),
            "final_response_preview": (final_text or "").strip()[:120],
            "content_chars": content_chars,
        }

    def _read_target_count(self, search_data: dict[str, object]) -> int:
        count = _as_int(search_data.get("matched_count"))
        if count <= 1:
            return max(0, count)
        confidence = _as_float(search_data.get("confidence"))
        margin = _as_float(search_data.get("score_margin"))
        details = search_data.get("candidate_details", [])
        reasons: list[str] = []
        if isinstance(details, list) and details and isinstance(details[0], dict):
            raw_reasons = details[0].get("reasons", [])
            if isinstance(raw_reasons, list):
                reasons = [str(item) for item in raw_reasons]
        if confidence >= 0.9 and margin >= 120:
            return 1
        if any(reason in {"exact_title", "exact_path_stem"} for reason in reasons):
            return 1
        if confidence >= 0.75 and margin >= 60:
            return min(2, count)
        return min(3, count)

    @staticmethod
    def _successful_read(record: ToolCallRecord) -> bool:
        data = record.result.get("data", {}) or {}
        return (
            bool(record.result.get("ok", False))
            and _as_int(data.get("content_chars", data.get("chars"))) > 0
        )

    @staticmethod
    def _records(records: list[ToolCallRecord], name: str) -> list[ToolCallRecord]:
        return [record for record in records if record.name == name]

    @staticmethod
    def _policy_call_id() -> str:
        return f"policy_{uuid4().hex[:12]}"


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]
