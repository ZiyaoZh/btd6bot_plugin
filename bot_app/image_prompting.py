from __future__ import annotations

import re


MAX_IMAGE_PROMPT_CHARS = 4000
STRUCTURED_PROMPT_MARKER = "[结构化生图任务 v2]"

_INFOGRAPHIC_TERMS = (
    "情报图",
    "信息图",
    "简报图",
    "速览图",
    "一图流",
    "数据图",
    "攻略图",
    "活动图",
    "活动海报",
    "总览图",
    "早报图",
    "infographic",
    "info graphic",
)


def build_image_generation_prompt(
    prompt: str,
    *,
    user_request: str = "",
    source_facts: str = "",
    mode: str = "auto",
    title: str = "",
) -> str:
    """Turn a short visual request into an execution-ready image prompt.

    The function is intentionally deterministic so model-originated and
    policy-originated image calls receive the same quality and grounding rules.
    """

    raw_prompt = _clean_text(prompt)
    original_request = _clean_text(user_request) or raw_prompt
    if raw_prompt.startswith(STRUCTURED_PROMPT_MARKER):
        return raw_prompt[:MAX_IMAGE_PROMPT_CHARS]

    resolved_mode = _resolve_mode(
        mode,
        "\n".join(
            item for item in (raw_prompt, original_request, source_facts) if item
        ),
    )
    if resolved_mode == "infographic":
        return _build_infographic_prompt(
            raw_prompt=raw_prompt,
            original_request=original_request,
            source_facts=source_facts,
            title=title,
        )
    return _build_creative_prompt(
        raw_prompt=raw_prompt,
        original_request=original_request,
    )


def is_infographic_request(text: str) -> bool:
    normalized = _clean_text(text).lower()
    return any(term in normalized for term in _INFOGRAPHIC_TERMS)


def compact_source_facts(source_facts: str, *, max_chars: int) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for raw_line in _clean_text(source_facts).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"^[-*]\s*", "", line)
        if not line or _is_source_metadata(line):
            continue
        line = re.sub(r"https?://\S+", "", line).strip()
        line = re.sub(r"\s+", " ", line)
        if not line or line in seen:
            continue
        seen.add(line)
        lines.append(line)

    rendered = "\n".join(lines)
    if len(rendered) <= max_chars:
        return rendered
    clipped = rendered[: max(1, max_chars - 28)].rstrip()
    return f"{clipped}\n[来源内容过长，已保留前述核心事实]"


def _build_infographic_prompt(
    *,
    raw_prompt: str,
    original_request: str,
    source_facts: str,
    title: str,
) -> str:
    heading = _clean_text(title) or "中文信息情报图"
    request = original_request or raw_prompt or heading
    prefix = (
        f"{STRUCTURED_PROMPT_MARKER}\n"
        "任务类型：事实型中文信息图，不是概念插画。\n"
        f"画面主题：{heading}\n"
        f"用户原始要求：{request}\n\n"
        "信息真实性硬要求：\n"
        "1. 下方“必须呈现的事实”是画面内容的唯一事实来源，逐项准确呈现；"
        "不得改写数字、日期、活动名称、地图、模式或限制条件，不得编造缺失信息。\n"
        "2. 活动名称、活动时间、地图/模式、回合与资金、关键限制和特殊规则必须优先可读。"
        "空间不足时压缩装饰、缩短非事实文案，不能删掉核心活动信息。\n"
        "3. 所有正文使用清晰、自然、可辨认的简体中文，不生成乱码、伪文字、Lorem ipsum、"
        "虚构按钮、虚构排名或无来源数字。\n\n"
        "版式要求：\n"
        "1. 竖版信息海报构图，标题区、核心时间区、规则信息区、限制/亮点区层级清楚，"
        "适合手机群聊中直接阅读。\n"
        "2. 至少 75% 的有效画面用于信息展示；角色、气球、地图纹理等插画和装饰最多占 25%，"
        "只能辅助分区，不能成为主视觉或遮挡文字。\n"
        "3. 使用高对比、整洁网格、明确留白和少量图标；不要做成全屏角色插画、电影海报、"
        "纯氛围图、只有大标题的宣传画或密集小字截图。\n"
        "4. BTD6 主题可以使用明快游戏色彩和克制的猴子/气球元素，但信息准确性与可读性优先。\n\n"
        "必须呈现的事实：\n"
    )
    suffix = (
        "\n\n输出检查：在生成前确认活动名称和时间没有遗漏，所有数字与来源事实一致，"
        "插画没有挤占信息区，画面无需额外文字说明也能读懂。"
    )
    fact_budget = max(400, MAX_IMAGE_PROMPT_CHARS - len(prefix) - len(suffix))
    facts = compact_source_facts(source_facts or raw_prompt, max_chars=fact_budget)
    if not facts:
        facts = (
            f"主题要求：{raw_prompt or request}\n"
            "未提供可核验的外部事实；不要自行添加日期、数据、排名或活动规则。"
        )
    return f"{prefix}{facts}{suffix}"[:MAX_IMAGE_PROMPT_CHARS]


def _build_creative_prompt(*, raw_prompt: str, original_request: str) -> str:
    request = raw_prompt or original_request
    prompt = (
        f"{STRUCTURED_PROMPT_MARKER}\n"
        "任务类型：创意图片。\n"
        f"用户原始要求：{original_request or request}\n"
        f"核心画面：{request}\n\n"
        "生成要求：准确抓住用户指定的主体和动作，把主体作为第一视觉焦点；"
        "补全合理的场景、构图、景别、光线、材质和色彩关系，使画面完整且有明确叙事。"
        "不要擅自改变主体、数量、身份或用户明确指定的风格。"
        "除非用户明确要求文字设计，否则画面中不要加入标题、说明文字、水印、边框或界面元素。"
        "避免无关角色、空泛大背景、主体过小、杂乱构图和模板化装饰。"
    )
    return prompt[:MAX_IMAGE_PROMPT_CHARS]


def _resolve_mode(mode: str, combined_text: str) -> str:
    normalized_mode = _clean_text(mode).lower()
    if normalized_mode in {"infographic", "creative"}:
        return normalized_mode
    return "infographic" if is_infographic_request(combined_text) else "creative"


def _is_source_metadata(line: str) -> bool:
    normalized = line.strip().lower()
    return normalized.startswith(
        (
            "来源:",
            "文件:",
            "json:",
            "图片:",
            "数据来源:",
            "生成时间:",
            "id:",
            "排行榜:",
        )
    )


def _clean_text(value: str) -> str:
    return str(value or "").strip()
