from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from ..agent.types import ToolCallRecord
from ..image_prompting import is_infographic_request
from ..protocol import EventPayload
from .types import ToolInvocation


@dataclass(frozen=True)
class ImageRequest:
    kind: str
    activity_types: tuple[str, ...] = ()
    leaderboard_type: str = ""


class ImageGenerationPolicy:
    """Validate image failures while the Model decides whether to reuse or create."""

    def required_tool_calls(
        self, event: EventPayload, records: list[ToolCallRecord]
    ) -> list[ToolInvocation]:
        # The dynamic artifact catalog must be reviewed by the Model before any
        # acquisition or generation choice is made.
        del event, records
        return []

    def final_override(self, event: EventPayload, records: list[ToolCallRecord]) -> str:
        request = classify_image_request(event.get("text", ""))
        if request is None:
            return ""
        reused_images = [
            record
            for record in self._records(records, "read_artifact")
            if record.result.get("ok", False)
            and str(record.result.get("response_type", "") or "").lower() == "image"
            and str(record.result.get("image_path", "") or "").strip()
        ]
        if reused_images:
            return ""

        terminal_tools = (
            ("get_activity_leaderboard",)
            if request.kind == "leaderboard"
            else ("generate_image",)
        )
        for name in terminal_tools:
            attempts = self._records(records, name)
            if attempts and not attempts[-1].result.get("ok", False):
                return str(attempts[-1].result.get("message", "") or "").strip() or (
                    "图片生成失败，请稍后重试。"
                )

        if request.activity_types and not self._records(records, "generate_image"):
            attempted = [
                record
                for activity_type in request.activity_types
                for record in self._activity_records(records, activity_type)
            ]
            if attempted:
                if not any(record.result.get("ok", False) for record in attempted):
                    return "活动信息获取失败，暂时不能可靠生成情报图，请稍后重试。"
                return "活动信息已经取得，但图片尚未生成完成，请稍后重试。"
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
            if record.name == "get_activity_detail"
            and str(record.arguments.get("activity_type", "") or "").lower()
            == activity_type
        ]

def classify_image_request(text: str) -> ImageRequest | None:
    normalized = _normalize(text)
    if not normalized or _is_image_meta_question(normalized):
        return None

    activity_types = _activity_types(normalized)
    if (
        activity_types
        and _has_leaderboard_intent(normalized)
        and any(term in normalized for term in ("图片", "图", "海报"))
    ):
        leaderboard_type = _leaderboard_type(normalized, activity_types)
        if leaderboard_type:
            return ImageRequest(kind="leaderboard", leaderboard_type=leaderboard_type)

    has_visual_intent = _has_visual_intent(normalized)
    if not has_visual_intent:
        return None

    if activity_types and _has_leaderboard_intent(normalized):
        leaderboard_type = _leaderboard_type(normalized, activity_types)
        if leaderboard_type:
            return ImageRequest(kind="leaderboard", leaderboard_type=leaderboard_type)

    if activity_types and not _is_explicit_creative_art(normalized):
        return ImageRequest(kind="infographic", activity_types=activity_types)

    if is_infographic_request(normalized):
        if not activity_types and _is_btd6_activity_overview(normalized):
            activity_types = ("race", "boss", "odyssey", "daily")
        return ImageRequest(kind="infographic", activity_types=activity_types)

    if _is_context_dependent_short_request(normalized):
        return None
    return ImageRequest(kind="creative")


def _activity_types(text: str) -> tuple[str, ...]:
    found: list[str] = []
    mappings = (
        ("race", ("竞速", "race")),
        ("boss", ("boss", "首领")),
        ("odyssey", ("征程", "奥德赛", "odyssey")),
        ("daily", ("每日挑战", "日挑", "daily challenge")),
    )
    for activity_type, terms in mappings:
        if any(term in text for term in terms):
            found.append(activity_type)
    return tuple(found)


def _has_visual_intent(text: str) -> bool:
    visual_terms = (
        "图",
        "图片",
        "海报",
        "插画",
        "壁纸",
        "头像",
        "封面",
        "视觉",
        "image",
        "picture",
        "poster",
        "illustration",
        "infographic",
    )
    create_terms = (
        "画",
        "绘制",
        "生成",
        "制作",
        "设计",
        "做一张",
        "做个",
        "来一张",
        "来个",
        "出一张",
        "draw",
        "generate",
        "create",
        "make",
    )
    has_visual_noun = any(term in text for term in visual_terms)
    has_create_verb = any(term in text for term in create_terms)
    if has_create_verb and has_visual_noun:
        return True
    if any(term in text for term in ("画", "绘制", "draw")):
        return True
    return is_infographic_request(text) or bool(
        re.search(r"(?:海报|插画|壁纸|头像|封面)\s*[吧呀啊]?[。！!？?]?$", text)
    )


def _has_leaderboard_intent(text: str) -> bool:
    return any(
        term in text for term in ("排行榜", "排行", "榜单", "排名", "leaderboard")
    )


def _leaderboard_type(text: str, activity_types: tuple[str, ...]) -> str:
    if "race" in activity_types:
        return "race"
    if "boss" in activity_types:
        return "boss-elite" if "精英" in text or "elite" in text else "boss-standard"
    return ""


def _is_explicit_creative_art(text: str) -> bool:
    return any(term in text for term in ("插画", "壁纸", "头像", "同人", "场景画"))


def _is_btd6_activity_overview(text: str) -> bool:
    return any(term in text for term in ("btd6", "气球塔防", "活动"))


def _is_image_meta_question(text: str) -> bool:
    meta_terms = (
        "生图工具",
        "画图工具",
        "图片工具",
        "生图提示词",
        "绘图提示词",
        "图片提示词",
        "生图接口",
        "图片接口",
        "为什么生成",
        "生成失败",
        "画图失败",
        "怎么生成图片",
        "如何生成图片",
        "怎么画图",
        "如何画图",
        "会画图吗",
        "能画图吗",
    )
    if any(term in text for term in meta_terms):
        return True
    question_terms = (
        "是什么",
        "是谁",
        "哪里",
        "为什么",
        "怎么样",
        "什么意思",
        "吗",
        "？",
        "?",
    )
    return any(term in text for term in question_terms) and not _has_creation_command(
        text
    )


def _has_creation_command(text: str) -> bool:
    command_terms = (
        "帮我画",
        "给我画",
        "请画",
        "画一张",
        "画一个",
        "画个",
        "画只",
        "画幅",
        "画张",
        "绘制一",
        "生成一张",
        "生成一个",
        "生成图片",
        "制作一张",
        "设计一张",
        "做一张",
        "做个",
        "来一张",
        "来个",
        "出一张",
        "draw ",
        "generate ",
        "create ",
        "make ",
    )
    return any(term in text for term in command_terms)


def _is_context_dependent_short_request(text: str) -> bool:
    if len(text) > 18:
        return False
    return any(
        term in text
        for term in (
            "画出来",
            "生成出来",
            "照这个",
            "照着这个",
            "按这个",
            "刚才那个",
            "上面那个",
            "之前那个",
        )
    )


def _normalize(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())
