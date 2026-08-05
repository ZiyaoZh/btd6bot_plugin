from __future__ import annotations

from uuid import uuid4

from ..agent.types import ToolCallRecord
from ..image_request import OVERVIEW_ACTIVITY_TYPES, classify_image_request
from ..protocol import EventPayload
from .types import ToolInvocation


class ImageGenerationPolicy:
    """Validate image failures while the Model decides whether to reuse or create."""

    def required_tool_calls(
        self, event: EventPayload, records: list[ToolCallRecord]
    ) -> list[ToolInvocation]:
        if event.get("event_type") == "scheduled_daily_activity_brief":
            attempts = self._records(records, "get_activity_brief_image")
            if not attempts:
                return [
                    ToolInvocation(
                        id=f"policy_activity_overview_{uuid4().hex[:12]}",
                        name="get_activity_brief_image",
                        arguments={
                            "activity_type": "all",
                            "difficulty": "hard",
                            "event_scope": "current",
                        },
                        source="policy",
                    )
                ]
        # The dynamic artifact catalog must be reviewed by the Model before any
        # acquisition or generation choice is made for ordinary chat requests.
        return []

    def final_override(self, event: EventPayload, records: list[ToolCallRecord]) -> str:
        request = classify_image_request(event.get("text", ""))
        if request is None:
            return ""
        if request.kind == "infographic" and request.activity_types:
            is_overview = set(request.activity_types) == set(OVERVIEW_ACTIVITY_TYPES)
            if len(request.activity_types) != 1 and not is_overview:
                return "可生成单项活动图或包含全部四类活动的总览图，请调整活动范围。"
        reused_images = [
            record
            for record in self._records(records, "read_artifact")
            if record.result.get("ok", False)
            and str(record.result.get("response_type", "") or "").lower() == "image"
            and str(record.result.get("image_path", "") or "").strip()
        ]
        if reused_images:
            return ""

        if request.kind == "leaderboard":
            terminal_tools = ("get_activity_leaderboard",)
        elif request.kind == "collection":
            terminal_tools = ("get_collection_event_info",)
        elif request.kind == "infographic" and request.activity_types:
            terminal_tools = ("get_activity_brief_image",)
        else:
            terminal_tools = ("generate_image",)
        for name in terminal_tools:
            attempts = self._records(records, name)
            if attempts and not attempts[-1].result.get("ok", False):
                return str(attempts[-1].result.get("message", "") or "").strip() or (
                    "图片生成失败，请稍后重试。"
                )

        if request.kind == "infographic" and request.activity_types:
            if self._records(records, "get_activity_brief_image"):
                return ""
            wrong_tool_attempts = self._records(records, "generate_image")
            if wrong_tool_attempts:
                return str(wrong_tool_attempts[-1].result.get("message", "") or "").strip() or (
                    "事实型活动图不能使用通用 AI 生图。"
                )
            attempted = [
                record
                for activity_type in request.activity_types
                for record in self._activity_records(records, activity_type)
            ]
            if set(request.activity_types) == set(OVERVIEW_ACTIVITY_TYPES):
                attempted.extend(self._activity_records(records, "all"))
            if attempted:
                if not any(record.result.get("ok", False) for record in attempted):
                    return "活动信息获取失败，暂时不能可靠生成情报图，请稍后重试。"
                return "活动信息已经取得，但图片尚未生成完成，请稍后重试。"
            return "活动情报图尚未生成完成，请稍后重试。"
        return ""

    @staticmethod
    def _records(records: list[ToolCallRecord], name: str) -> list[ToolCallRecord]:
        return [record for record in records if record.name == name]

    @staticmethod
    def _activity_records(
        records: list[ToolCallRecord], activity_type: str
    ) -> list[ToolCallRecord]:
        return [
            record
            for record in records
            if record.name in {"get_activity_detail", "get_activity_brief_image"}
            and str(record.arguments.get("activity_type", "") or "").lower()
            == activity_type
        ]
