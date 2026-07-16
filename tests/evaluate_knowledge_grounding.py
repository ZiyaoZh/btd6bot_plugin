from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(PLUGIN_ROOT))

from bot_app.agent.runner import AgentRunner  # noqa: E402
from bot_app.agent.tool_registry import ToolRegistry  # noqa: E402
from bot_app.llm_client import LLMResponse  # noqa: E402
from bot_app.memory import GroupChatMemory  # noqa: E402
from bot_app.protocol import EventPayload  # noqa: E402
from bot_app.settings import AppSettings  # noqa: E402
from bot_app.tools import ToolDependencies  # noqa: E402
from evaluate_knowledge_search import DEFAULT_CASE_FILE, load_cases  # noqa: E402


@dataclass(frozen=True)
class GroundingEvalResult:
    query: str
    expected_first: str
    tool_names: list[str]
    search_count: int
    read_count: int
    successful_read_count: int
    matched_count: int
    first_matched_file: str
    answer_grounded: bool
    fallback_reason: str
    read_mode: str
    paragraph_count: int
    metadata_present: bool
    metadata_title: str
    metadata_tags: list[str]
    metadata_detected_fields: list[str]
    rendered: bool
    response_preview: str


class EvalDb:
    def get_recent_group_messages(
        self, group_id: str, limit: int
    ) -> list[dict[str, Any]]:
        return []


class EvalContextManager:
    async def compact_if_needed(
        self,
        *,
        memory: GroupChatMemory,
        group_id: str,
        user_id: str,
        trace_id: str = "",
    ) -> bool:
        return False


class DeterministicKnowledgeLLM:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def chat_completion(self, **kwargs: Any) -> LLMResponse:
        self.requests.append(kwargs)
        purpose = str(kwargs.get("purpose", "") or "")
        if purpose != "agent_turn":
            raise AssertionError(f"unexpected LLM purpose: {purpose}")
        content = self._render_from_tool_results(kwargs)

        return LLMResponse(
            content=content,
            assistant_message={"role": "assistant", "content": content},
            tool_calls=[],
            total_tokens=1,
            raw={},
            model="deterministic-eval",
        )

    def _render_from_tool_results(self, kwargs: dict[str, Any]) -> str:
        messages = kwargs.get("messages", [])
        for message in reversed(messages):
            try:
                payload = json.loads(str(message.get("content", "") or "{}"))
            except json.JSONDecodeError:
                continue
            if message.get("role") == "tool":
                rendered = self._render_read_result(message.get("name"), payload)
                if rendered:
                    return rendered
            if (
                message.get("role") == "user"
                and isinstance(payload, dict)
                and payload.get("kind") == "harness_tool_observations"
            ):
                for item in reversed(payload.get("results", [])):
                    if not isinstance(item, dict):
                        continue
                    rendered = self._render_read_result(
                        item.get("name"), item.get("result", {})
                    )
                    if rendered:
                        return rendered
        return "没有可用知识库证据，不能确认结论。"

    @staticmethod
    def _render_read_result(name: object, result: object) -> str:
        if name != "read_game_knowledge_file" or not isinstance(result, dict):
            return ""
        data = result.get("data", {})
        data = data if isinstance(data, dict) else {}
        content = str(data.get("content", "") or "").strip()
        if result.get("ok", False) and content:
            return f"基于知识库片段回答：{content[:160]}"
        return ""


def make_settings() -> AppSettings:
    return AppSettings(
        env_file=".env",
        target_group_id="1000",
        target_group_ids=("1000",),
        api_key="eval-key",
        system_prompt="你是测试机器人。",
        max_tool_rounds=4,
        recent_group_window_size=0,
    )


def make_event(text: str) -> EventPayload:
    return {
        "module": "knowledge_grounding_eval",
        "event_type": "group_message",
        "group_id": "1000",
        "user_id": "2000",
        "text": text,
        "bot_mentioned": True,
        "mentioned_qqs": [],
        "identity": {
            "nickname": "eval",
            "card": "",
            "role": "member",
            "title": "",
            "level": "",
        },
    }


def make_runner(llm: DeterministicKnowledgeLLM) -> AgentRunner:
    dependencies = ToolDependencies.create(
        db=EvalDb(),
        plugin_root=PLUGIN_ROOT,
        manual_file=PLUGIN_ROOT / "manuals" / "program_manual.md",
    )
    return AgentRunner(
        llm=llm,  # type: ignore[arg-type]
        memory=GroupChatMemory(history_turns=2),
        settings=make_settings(),
        tool_registry=ToolRegistry(dependencies),
        tool_dependencies=dependencies,
        context_manager=EvalContextManager(),  # type: ignore[arg-type]
    )


async def evaluate_query(query: str, expected_first: str) -> GroundingEvalResult:
    llm = DeterministicKnowledgeLLM()
    runner = make_runner(llm)
    response = await runner.run(make_event(query), trace_id="")
    summary = runner._build_knowledge_summary(  # type: ignore[attr-defined]
        event=make_event(query),
        planning_result=None,
        tool_records=response.tool_calls,
        final_text=response.text,
    )
    summary = summary or {}
    matched_paths = [Path(path).name for path in summary.get("matched_paths", [])]
    read_data = extract_first_read_data(response.tool_calls)
    read_metadata = read_data.get("metadata", {}) if isinstance(read_data, dict) else {}
    read_metadata = read_metadata if isinstance(read_metadata, dict) else {}
    return GroundingEvalResult(
        query=query,
        expected_first=expected_first,
        tool_names=[record.name for record in response.tool_calls],
        search_count=sum(
            1
            for record in response.tool_calls
            if record.name == "search_game_knowledge"
        ),
        read_count=sum(
            1
            for record in response.tool_calls
            if record.name == "read_game_knowledge_file"
        ),
        successful_read_count=int(summary.get("successful_read_count", 0) or 0),
        matched_count=int(summary.get("matched_count", 0) or 0),
        first_matched_file=matched_paths[0] if matched_paths else "",
        answer_grounded=bool(summary.get("answer_grounded", False)),
        fallback_reason=str(summary.get("fallback_reason", "") or ""),
        read_mode=str(read_data.get("mode", "") or ""),
        paragraph_count=len(read_data.get("paragraphs", []) or []),
        metadata_present=bool(read_metadata),
        metadata_title=str(read_metadata.get("title", "") or ""),
        metadata_tags=[
            str(item) for item in read_metadata.get("tags", []) if str(item).strip()
        ],
        metadata_detected_fields=[
            str(item)
            for item in read_metadata.get("detected_fields", [])
            if str(item).strip()
        ],
        rendered=any(
            request.get("purpose") == "agent_turn" for request in llm.requests
        ),
        response_preview=response.text[:160],
    )


def extract_first_read_data(tool_records: list[Any]) -> dict[str, Any]:
    for record in tool_records:
        if getattr(record, "name", "") != "read_game_knowledge_file":
            continue
        result = getattr(record, "result", {}) or {}
        data = result.get("data", {}) if isinstance(result, dict) else {}
        return data if isinstance(data, dict) else {}
    return {}


async def build_report(case_file: Path, *, limit: int) -> dict[str, Any]:
    cases = load_cases(case_file)
    if limit > 0:
        cases = cases[:limit]
    results = [
        await evaluate_query(case.query, case.expected_first)
        for case in cases
        if case.requires_grounded_answer
    ]
    total = len(results)
    grounded_count = sum(1 for item in results if item.answer_grounded)
    search_count = sum(1 for item in results if item.search_count > 0)
    read_count = sum(1 for item in results if item.read_count > 0)
    first_doc_success_count = sum(
        1 for item in results if item.first_matched_file == item.expected_first
    )
    ungrounded_fallback_count = sum(
        1 for item in results if item.fallback_reason and not item.answer_grounded
    )
    metadata_count = sum(1 for item in results if item.metadata_present)
    metadata_title_count = sum(1 for item in results if item.metadata_title)
    paragraph_extract_count = sum(
        1 for item in results if item.read_mode == "paragraph_extract"
    )
    paragraph_result_count = sum(1 for item in results if item.paragraph_count > 0)
    return {
        "knowledge_query_count": total,
        "search_call_rate": search_count / total if total else 0.0,
        "read_call_rate": read_count / total if total else 0.0,
        "first_doc_success_rate": first_doc_success_count / total if total else 0.0,
        "grounded_answer_rate": grounded_count / total if total else 0.0,
        "ungrounded_fallback_rate": ungrounded_fallback_count / total if total else 0.0,
        "metadata_present_rate": metadata_count / total if total else 0.0,
        "metadata_title_rate": metadata_title_count / total if total else 0.0,
        "paragraph_extract_rate": paragraph_extract_count / total if total else 0.0,
        "paragraph_result_rate": paragraph_result_count / total if total else 0.0,
        "results": [asdict(item) for item in results],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate end-to-end knowledge grounding"
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASE_FILE)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def print_text_report(report: dict[str, Any]) -> None:
    print(
        "knowledge_grounding_eval "
        f"knowledge_query_count={report['knowledge_query_count']} "
        f"search_call_rate={report['search_call_rate']:.3f} "
        f"read_call_rate={report['read_call_rate']:.3f} "
        f"first_doc_success_rate={report['first_doc_success_rate']:.3f} "
        f"grounded_answer_rate={report['grounded_answer_rate']:.3f} "
        f"ungrounded_fallback_rate={report['ungrounded_fallback_rate']:.3f} "
        f"metadata_present_rate={report['metadata_present_rate']:.3f} "
        f"metadata_title_rate={report['metadata_title_rate']:.3f} "
        f"paragraph_extract_rate={report['paragraph_extract_rate']:.3f} "
        f"paragraph_result_rate={report['paragraph_result_rate']:.3f}"
    )
    for item in report["results"]:
        status = "PASS" if item["answer_grounded"] else "FAIL"
        print(
            f"{status} {item['query']} tools={item['tool_names']} "
            f"first={item['first_matched_file']} expected={item['expected_first']} "
            f"read_mode={item['read_mode']} paragraphs={item['paragraph_count']} "
            f"fallback={item['fallback_reason']}"
        )


def main() -> None:
    args = parse_args()
    report = asyncio.run(build_report(args.cases, limit=max(0, args.limit)))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_text_report(report)
    if report["grounded_answer_rate"] < 1.0:
        raise SystemExit(1)
    if report["metadata_present_rate"] < 1.0:
        raise SystemExit(1)
    if report["paragraph_extract_rate"] < 1.0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
