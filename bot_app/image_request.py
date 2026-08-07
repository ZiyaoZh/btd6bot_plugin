from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .image_prompting import is_infographic_request

OVERVIEW_ACTIVITY_TYPES = ("race", "boss", "odyssey", "daily")


@dataclass(frozen=True)
class ImageRequest:
    kind: str
    activity_types: tuple[str, ...] = ()
    leaderboard_type: str = ""
    event_scope: str = ""
    difficulty: str = ""


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

    if not _has_visual_intent(normalized):
        return None

    if _has_collection_event_intent(normalized):
        return ImageRequest(kind="collection")

    if activity_types and _has_leaderboard_intent(normalized):
        leaderboard_type = _leaderboard_type(normalized, activity_types)
        if leaderboard_type:
            return ImageRequest(kind="leaderboard", leaderboard_type=leaderboard_type)

    if activity_types and not _is_explicit_creative_art(normalized):
        return ImageRequest(
            kind="infographic",
            activity_types=activity_types,
            event_scope=_event_scope(normalized),
            difficulty=_difficulty(normalized),
        )

    if is_infographic_request(normalized):
        if not activity_types and _is_btd6_activity_overview(normalized):
            activity_types = ("race", "boss", "odyssey", "daily")
        return ImageRequest(
            kind="infographic",
            activity_types=activity_types,
            event_scope=_event_scope(normalized),
            difficulty=_difficulty(normalized),
        )

    if _is_context_dependent_short_request(normalized):
        return None
    return ImageRequest(kind="creative")


def activity_brief_selection_error(
    request: ImageRequest | None,
    *,
    activity_type: str,
    event_scope: str,
    difficulty: str,
) -> str:
    if request is None or request.kind != "infographic" or not request.activity_types:
        return ""
    is_overview = set(request.activity_types) == set(OVERVIEW_ACTIVITY_TYPES)
    if len(request.activity_types) != 1 and not is_overview:
        return "可生成单项活动图或包含全部四类活动的总览图，请调整活动范围。"
    requested_type = "all" if is_overview else request.activity_types[0]
    if requested_type not in {"all", "race", "boss", "odyssey", "daily"}:
        return "不支持该活动类型的确定性情报图。"
    if activity_type != requested_type:
        return "活动情报图类型与用户请求不一致。"
    expected_scope = request.event_scope or (
        "relevant" if requested_type == "all" else "current"
    )
    if event_scope != expected_scope:
        return "活动情报图范围与用户请求不一致。"
    if requested_type in {"all", "odyssey"}:
        expected_difficulty = request.difficulty or "hard"
        if difficulty != expected_difficulty:
            return "征程情报图难度与用户请求不一致。"
    return ""


def factual_btd6_artifact_error(
    request: ImageRequest | None,
    *,
    kind: str,
    metadata: dict[str, Any],
) -> str:
    if request is None:
        return ""
    if request.kind == "leaderboard":
        if kind != "activity.leaderboard":
            return "活动排行榜只能复用专用排行榜产物。"
        actual_type = str(metadata.get("leaderboard_type", "") or "").lower()
        if actual_type != request.leaderboard_type:
            return "排行榜产物类型与用户请求不一致。"
        return ""
    if request.kind == "collection":
        return "" if kind == "activity.collection" else "收集活动图只能复用专用轮换产物。"
    if request.kind != "infographic" or not request.activity_types:
        return ""
    if kind != "activity.brief":
        return "事实型活动图只能复用确定性活动情报图产物。"
    return activity_brief_selection_error(
        request,
        activity_type=str(metadata.get("activity_type", "") or "").lower(),
        event_scope=str(metadata.get("event_scope", "") or "").lower(),
        difficulty=str(metadata.get("difficulty", "hard") or "hard").lower(),
    )


def _activity_types(text: str) -> tuple[str, ...]:
    found: list[str] = []
    if "竞速" in text or (
        "race" in text and (_has_btd6_activity_context(text) or _has_leaderboard_intent(text))
    ):
        found.append("race")
    if any(term in text for term in ("boss", "首领")):
        found.append("boss")
    if any(term in text for term in ("征程", "奥德赛", "odyssey")):
        found.append("odyssey")
    if any(term in text for term in ("每日挑战", "日挑", "daily challenge")):
        found.append("daily")
    return tuple(found)


def _has_visual_intent(text: str) -> bool:
    visual_terms = (
        "图", "图片", "海报", "插画", "壁纸", "头像", "封面", "视觉",
        "image", "picture", "poster", "illustration", "infographic",
    )
    create_terms = (
        "画", "绘制", "生成", "制作", "设计", "做一张", "做个", "来一张",
        "来个", "出一张", "draw", "generate", "create", "make",
    )
    if any(term in text for term in create_terms) and any(term in text for term in visual_terms):
        return True
    if any(term in text for term in ("画", "绘制", "draw")):
        return True
    return is_infographic_request(text) or bool(
        re.search(r"(?:海报|插画|壁纸|头像|封面)\s*[吧呀啊]?[。！!？?]?$", text)
    )


def _has_leaderboard_intent(text: str) -> bool:
    return any(term in text for term in ("排行榜", "排行", "榜单", "排名", "leaderboard"))


def _has_collection_event_intent(text: str) -> bool:
    return any(term in text for term in ("收集活动", "收集轮换", "collection event"))


def _leaderboard_type(text: str, activity_types: tuple[str, ...]) -> str:
    if "race" in activity_types:
        return "race"
    if "boss" in activity_types:
        return "boss-elite" if "精英" in text or "elite" in text else "boss-standard"
    return ""


def _is_explicit_creative_art(text: str) -> bool:
    return any(
        term in text
        for term in (
            "插画", "壁纸", "头像", "同人", "场景画", "场景图", "战斗场景",
            "卡通", "概念艺术", "concept art", "fan art", "illustration",
        )
    )


def _is_btd6_activity_overview(text: str) -> bool:
    return any(term in text for term in ("btd6", "气球塔防", "活动"))


def _has_btd6_activity_context(text: str) -> bool:
    return _is_btd6_activity_overview(text) or is_infographic_request(text)


def _event_scope(text: str) -> str:
    if any(term in text for term in ("下一期", "下期", "upcoming")):
        return "upcoming"
    if any(term in text for term in ("当前", "本期", "current", "进行中")):
        return "current"
    if any(term in text for term in ("已结束", "上一期", "最近结束", "ended")):
        return "ended"
    if any(term in text for term in ("相关活动", "自动选择", "relevant")):
        return "relevant"
    if any(term in text for term in ("最新", "latest")):
        return "latest"
    return ""


def _difficulty(text: str) -> str:
    if any(term in text for term in ("简单", "easy")):
        return "easy"
    if any(term in text for term in ("中级", "medium")):
        return "medium"
    if any(term in text for term in ("困难", "hard")):
        return "hard"
    return ""


def _is_image_meta_question(text: str) -> bool:
    meta_terms = (
        "生图工具", "画图工具", "图片工具", "生图提示词", "绘图提示词",
        "图片提示词", "生图接口", "图片接口", "为什么生成", "生成失败",
        "画图失败", "怎么生成图片", "如何生成图片", "怎么画图", "如何画图",
        "会画图吗", "能画图吗",
    )
    if any(term in text for term in meta_terms):
        return True
    question_terms = ("是什么", "是谁", "哪里", "为什么", "怎么样", "什么意思", "吗", "？", "?")
    return any(term in text for term in question_terms) and not _has_creation_command(text)


def _has_creation_command(text: str) -> bool:
    command_terms = (
        "帮我画", "给我画", "请画", "画一张", "画一个", "画个", "画只", "画幅",
        "画张", "绘制一", "生成一张", "生成一个", "生成图片", "制作一张", "设计一张",
        "做一张", "做个", "来一张", "来个", "出一张", "draw ", "generate ",
        "create ", "make ",
    )
    return any(term in text for term in command_terms)


def _is_context_dependent_short_request(text: str) -> bool:
    if len(text) > 18:
        return False
    return any(
        term in text
        for term in (
            "画出来", "生成出来", "照这个", "照着这个", "按这个", "刚才那个",
            "上面那个", "之前那个",
        )
    )


def _normalize(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())
