from __future__ import annotations

import importlib.util
import json
import re
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any, Iterator

from ..protocol import CommandResultPayload, EventPayload
from .base import fail_result, ok_result


@dataclass(frozen=True)
class SearchQueryPlan:
    raw: str
    normalized: str
    invocation_stripped: str
    entity_terms: list[str]
    mechanic_terms: list[str]
    upgrade_terms: list[str]
    variants: list[str]


def build_search_game_knowledge_result(
    *,
    kb_root: Path,
    event: EventPayload,
    arguments: dict[str, Any],
    module: str,
) -> CommandResultPayload:
    raw_query = str(arguments.get("query", "") or "").strip() or event["text"]
    query_plan = build_kb_query_plan(raw_query, kb_root=kb_root)
    query = query_plan.normalized
    top_n_raw = arguments.get("top_n", 5)
    try:
        top_n = int(top_n_raw)
    except (TypeError, ValueError):
        top_n = 5
    top_n = max(1, min(top_n, 20))

    corpus_dir = _knowledge_corpus_dir(kb_root)
    alias_file_path = kb_root / "aliases.json"
    alias_file = alias_file_path if alias_file_path.exists() else None

    try:
        kb_interface = _load_kb_interface(kb_root)
        if hasattr(kb_interface, "interface_search_with_metadata"):
            candidate_items = kb_interface.interface_search_with_metadata(
                query=query,
                top_n=top_n,
                corpus_dir=corpus_dir,
                alias_file=alias_file,
                query_variants=query_plan.variants,
                entity_terms=query_plan.entity_terms,
                mechanic_terms=query_plan.mechanic_terms,
                upgrade_terms=query_plan.upgrade_terms,
            )
            paths = [
                str(item.get("path", "") or "")
                for item in candidate_items
                if str(item.get("path", "") or "").strip()
            ]
        else:
            paths = kb_interface.interface_search(
                query=query,
                top_n=top_n,
                corpus_dir=corpus_dir,
                alias_file=alias_file,
            )
            candidate_items = [
                {"path": str(path), "score": 0.0, "confidence": 0.0, "reasons": []}
                for path in paths
            ]
    except Exception as exc:
        return fail_result(
            module=module,
            command="search_game_knowledge",
            data={
                "raw_query": raw_query,
                "query": query,
                "knowledge_query": query,
                "knowledge_query_raw": raw_query,
                "query_plan": asdict(query_plan),
                "top_n": top_n,
                "matched_count": 0,
                "matched_paths": [],
                "candidate_details": [],
                "confidence": 0.0,
                "score_margin": 0.0,
            },
            message=f"游戏知识检索失败: {type(exc).__name__}",
            extra_prompt="请简要说明检索失败，并建议稍后重试或检查 Markdown 知识库目录。",
        )

    matched_paths = [str(path) for path in paths]
    candidate_details = normalize_candidate_details(candidate_items)
    top_confidence = (
        float(candidate_details[0].get("confidence", 0.0))
        if candidate_details
        else 0.0
    )
    score_margin = compute_score_margin(candidate_details)
    return ok_result(
        module=module,
        command="search_game_knowledge",
        data={
            "raw_query": raw_query,
            "query": query,
            "knowledge_query": query,
            "knowledge_query_raw": raw_query,
            "query_plan": asdict(query_plan),
            "top_n": top_n,
            "matched_count": len(matched_paths),
            "matched_paths": matched_paths,
            "candidate_details": candidate_details,
            "confidence": top_confidence,
            "score_margin": score_margin,
        },
        message="游戏知识检索完成",
        extra_prompt=(
            "你已经拿到候选文件路径。"
            "若 confidence 较高且 top1 明确，请先读取 top1；若 confidence 较低或候选分数接近，请读取前 2-3 个候选片段。"
            "若 matched_count=0，明确说明未检索到匹配文件，并建议用户换关键词。"
        ),
    )


def build_read_game_knowledge_file_result(
    *,
    kb_root: Path,
    arguments: dict[str, Any],
    module: str,
) -> CommandResultPayload:
    raw_path = str(arguments.get("file_path", "") or "").strip()
    raw_keywords_query = build_extract_keywords_query(arguments)
    keywords_query = build_extract_query_for_search(
        raw_keywords_query,
        kb_root=kb_root,
    )
    max_chars_raw = arguments.get("max_chars", 8000)
    try:
        max_chars = int(max_chars_raw)
    except (TypeError, ValueError):
        max_chars = 8000
    max_chars = max(500, min(max_chars, 20000))

    if not raw_path:
        return fail_result(
            module=module,
            command="read_game_knowledge_file",
            data={"file_path": "", "content": ""},
            message="缺少参数 file_path。",
            extra_prompt="请先从 search_game_knowledge 的 matched_paths 中选择一个 file_path。",
        )

    safe_path = resolve_safe_kb_path(raw_path, kb_root=kb_root)
    if safe_path is None:
        return fail_result(
            module=module,
            command="read_game_knowledge_file",
            data={"file_path": raw_path, "content": ""},
            message="拒绝读取该路径：超出知识库允许范围。",
            extra_prompt="仅允许读取 btd6_knowledge_base/knowledge_docs 目录内文件。",
        )

    if not safe_path.exists() or not safe_path.is_file():
        return fail_result(
            module=module,
            command="read_game_knowledge_file",
            data={"file_path": str(safe_path), "content": ""},
            message="目标文件不存在。",
            extra_prompt="请改用 search_game_knowledge 返回的路径。",
        )

    if safe_path.suffix.lower() != ".md":
        return fail_result(
            module=module,
            command="read_game_knowledge_file",
            data={"file_path": str(safe_path), "content": ""},
            message="文件类型不允许读取。",
            extra_prompt="仅支持读取 Markdown 文件。",
        )

    try:
        text = safe_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:
        return fail_result(
            module=module,
            command="read_game_knowledge_file",
            data={"file_path": str(safe_path), "content": ""},
            message=f"读取知识库文件失败: {type(exc).__name__}",
            extra_prompt="请说明读取失败并建议更换文件重试。",
        )

    content = extract_readable_content(text)
    metadata = build_kb_content_metadata(
        content,
        file_path=safe_path,
        keywords_query=keywords_query,
    )
    if keywords_query:
        paragraph_content = extract_markdown_sections(
            kb_root=kb_root,
            safe_path=safe_path,
            keywords_query=keywords_query,
            max_chars=max_chars,
        )
        if paragraph_content:
            paragraph_text = str(paragraph_content.get("text", "") or "").strip()
            if paragraph_text:
                paragraph_metadata = build_kb_content_metadata(
                    paragraph_text,
                    file_path=safe_path,
                    keywords_query=keywords_query,
                )
                paragraph_metadata["paragraph_count"] = len(
                    paragraph_content.get("paragraphs", []) or []
                )
                return ok_result(
                    module=module,
                    command="read_game_knowledge_file",
                    data={
                        "file_path": str(safe_path),
                        "max_chars": max_chars,
                        "content": paragraph_text,
                        "mode": "paragraph_extract",
                        "keywords": split_keywords_for_view(
                            sanitize_kb_query(keywords_query)
                        ),
                        "paragraphs": paragraph_content.get("paragraphs", []),
                        "metadata": paragraph_metadata,
                        "content_chars": len(paragraph_text),
                    },
                    message="知识库文件已按段落索引提取相关片段",
                    extra_prompt=(
                        "这是从知识库段落索引中按用户问题提取的相关片段。"
                        "请优先依据 content 回答；若片段没有覆盖问题，不要编造。"
                    ),
                )

        extracted_content = extract_keyword_windows(
            content,
            keywords_query=keywords_query,
            max_chars=max_chars,
        )
        if extracted_content:
            return ok_result(
                module=module,
                command="read_game_knowledge_file",
                data={
                    "file_path": str(safe_path),
                    "max_chars": max_chars,
                    "content": extracted_content,
                    "mode": "keyword_extract",
                    "keywords": split_keywords_for_view(
                        sanitize_kb_query(keywords_query)
                    ),
                    "metadata": metadata,
                    "content_chars": len(extracted_content),
                },
                message="知识库文件已按关键词提取相关片段",
                extra_prompt=(
                    "这是从知识库文件中按用户问题提取的相关片段。"
                    "请优先依据 content 回答；若片段没有覆盖问题，不要编造。"
                ),
            )

    if len(content) > 8000:
        return _build_long_document_result(
            kb_root=kb_root,
            safe_path=safe_path,
            keywords_query=keywords_query,
            max_chars=max_chars,
            module=module,
        )

    content = content[:max_chars]
    return ok_result(
        module=module,
        command="read_game_knowledge_file",
        data={
            "file_path": str(safe_path),
            "max_chars": max_chars,
            "content": content,
            "mode": "full",
            "metadata": metadata,
            "content_chars": len(content),
        },
        message="知识库文件读取完成",
        extra_prompt=(
            "请优先依据 content 回答用户问题。"
            "若内容不足，请再次从 matched_paths 选择其他文件读取，不要编造。"
        ),
    )


def _build_long_document_result(
    *,
    kb_root: Path,
    safe_path: Path,
    keywords_query: str,
    max_chars: int,
    module: str,
) -> CommandResultPayload:
    if not keywords_query:
        return fail_result(
            module=module,
            command="read_game_knowledge_file",
            data={
                "file_path": str(safe_path),
                "content": "",
                "mode": "extract",
                "keywords": [],
            },
            message="文档过长，缺少关键词，无法执行文本块提取。",
            extra_prompt=(
                "该文档长度超过 8000 字符，不再返回整篇内容。"
                "请补充 keywords（可用用户问题关键词）后重试 read_game_knowledge_file。"
            ),
        )

    corpus_dir = _knowledge_corpus_dir(kb_root)
    try:
        kb_interface = _load_kb_interface(kb_root)
        extract_result = kb_interface.interface_extract(
            file_name=str(safe_path),
            keywords_query=keywords_query,
            corpus_dir=corpus_dir,
            max_chars=max_chars,
        )
    except Exception as exc:
        return fail_result(
            module=module,
            command="read_game_knowledge_file",
            data={
                "file_path": str(safe_path),
                "content": "",
                "mode": "extract",
                "keywords": split_keywords_for_view(keywords_query),
            },
            message=f"关键词提取失败: {type(exc).__name__}",
            extra_prompt="请更换关键词重试，或选择其他候选文件。",
        )

    extracted_text = str(extract_result.get("text", "") or "").strip()
    metadata = build_kb_content_metadata(
        extracted_text,
        file_path=safe_path,
        keywords_query=keywords_query,
    )
    return ok_result(
        module=module,
        command="read_game_knowledge_file",
        data={
            "file_path": str(extract_result.get("file", str(safe_path))),
            "max_chars": int(extract_result.get("max_chars", max_chars) or max_chars),
            "content": extracted_text,
            "mode": "extract",
            "keywords": extract_result.get(
                "keywords", split_keywords_for_view(keywords_query)
            ),
            "metadata": metadata,
            "chars": int(
                extract_result.get("chars", len(extracted_text)) or len(extracted_text)
            ),
            "content_chars": len(extracted_text),
        },
        message="知识库长文已按关键词提取相关文本块",
        extra_prompt=(
            "这是从长文中提取的相关文本块。"
            "请优先基于 content 回答。"
            "若信息不足，更换更简单的关键词或换文件继续提取。"
        ),
    )


def extract_markdown_sections(
    *,
    kb_root: Path,
    safe_path: Path,
    keywords_query: str,
    max_chars: int,
) -> dict[str, Any]:
    corpus_dir = _knowledge_corpus_dir(kb_root)
    alias_file_path = kb_root / "aliases.json"
    alias_file = alias_file_path if alias_file_path.exists() else None
    literal_query = strip_kb_query_invocation_noise(keywords_query)
    normalized_query = normalize_btd6_search_terms(literal_query)
    # Keep structural Wiki wording such as "路线3" alongside the semantic
    # rewrite "下路" so Markdown heading extraction can match either form.
    paragraph_query = " ".join(_dedupe_texts([literal_query, normalized_query]))
    try:
        kb_interface = _load_kb_interface(kb_root)
        result = kb_interface.interface_extract_paragraphs(
            file_name=str(safe_path),
            keywords_query=paragraph_query or keywords_query,
            corpus_dir=corpus_dir,
            alias_file=alias_file,
            max_chars=max_chars,
        )
    except Exception:
        return {}
    return result if isinstance(result, dict) else {}


def build_extract_keywords_query(arguments: dict[str, Any]) -> str:
    for key in ("keywords", "query", "keyword"):
        value = arguments.get(key, "")
        if isinstance(value, list):
            merged = " ".join(str(item).strip() for item in value if str(item).strip())
        else:
            merged = str(value or "").strip()
        if merged:
            return merged
    return ""


def build_extract_query_for_search(raw_query: str, *, kb_root: Path) -> str:
    raw = str(raw_query or "").strip()
    if not raw:
        return ""
    query_plan = build_kb_query_plan(raw, kb_root=kb_root)
    focused_terms = [
        *query_plan.entity_terms,
        *query_plan.mechanic_terms,
        *query_plan.upgrade_terms,
    ]
    if focused_terms:
        return " ".join(_dedupe_texts(focused_terms))
    return query_plan.normalized or raw


def build_kb_content_metadata(
    content: str,
    *,
    file_path: Path,
    keywords_query: str,
) -> dict[str, Any]:
    title = file_path.stem
    metadata = {
        "title": title,
        "path": str(file_path),
        "tower_or_hero": title,
        "upgrade_path": "",
        "upgrade_tier": "",
        "tags": infer_kb_tags(content, title=title, query=keywords_query),
        "mechanics": infer_kb_mechanics(content, query=keywords_query),
        "detected_fields": infer_kb_detected_fields(content),
    }
    upgrade_code = infer_upgrade_code(keywords_query)
    if upgrade_code:
        metadata["upgrade_tier"] = upgrade_code
        metadata["upgrade_path"] = infer_upgrade_path(upgrade_code)
    return metadata


def infer_kb_tags(content: str, *, title: str, query: str) -> list[str]:
    haystack = f"{title} {query} {content[:1200]}".lower()
    tags: list[str] = []
    tag_terms = {
        "tower": ("防御塔", "猴", "炮", "工厂", "农场"),
        "hero": ("英雄", "昆西", "本杰明", "杰拉尔多", "科沃斯"),
        "upgrade": ("升级价格", "解锁经验", "0-0-", "路线"),
        "data": ("价格", "伤害", "穿透", "攻速", "射程"),
        "mechanic": ("ddt", "moab", "迷彩", "铅", "紫气球", "收益", "加成"),
    }
    for tag, terms in tag_terms.items():
        if any(term.lower() in haystack for term in terms):
            tags.append(tag)
    return tags


def infer_kb_mechanics(content: str, *, query: str) -> list[str]:
    haystack = f"{query} {content}".lower()
    mechanics: list[str] = []
    mechanic_terms = {
        "DDT": ("ddt",),
        "MOAB": ("moab", "zomg", "bfb", "bad", "大型气球"),
        "camo": ("迷彩", "反迷彩", "侦测"),
        "lead": ("铅", "破铅"),
        "purple": ("紫气球", "紫"),
        "income": ("收益", "现金", "金钱", "香蕉", "加成"),
        "range": ("射程", "范围"),
    }
    for mechanic, terms in mechanic_terms.items():
        if any(term.lower() in haystack for term in terms):
            mechanics.append(mechanic)
    return mechanics


def infer_kb_detected_fields(content: str) -> list[str]:
    fields: list[str] = []
    for field in ("价格", "伤害", "穿透", "攻速", "射程", "升级价格", "解锁经验"):
        if field in content:
            fields.append(field)
    return fields


def infer_upgrade_code(query: str) -> str:
    match = re.search(r"(?<![\d-])([0-5])-([0-5])-([0-5])(?![\d-])", query or "")
    if match:
        return "".join(match.groups())
    match = re.search(r"(?<![\d-])([0-5]{3})(?![\d-])", query or "")
    return match.group(1) if match else ""


def infer_upgrade_path(upgrade_code: str) -> str:
    if len(upgrade_code) != 3:
        return ""
    tiers = [int(ch) for ch in upgrade_code]
    if not any(tiers):
        return ""
    path_index = max(range(3), key=lambda index: tiers[index])
    return ("top", "middle", "bottom")[path_index]


def split_keywords_for_view(keywords_query: str) -> list[str]:
    parts = re.split(r"[\s,，、;；|+/]+", keywords_query or "")
    return [part.strip() for part in parts if part.strip()]


def extract_keyword_windows(
    content: str,
    *,
    keywords_query: str,
    max_chars: int,
) -> str:
    keywords = split_keywords_for_view(sanitize_kb_query(keywords_query))
    if not content or not keywords:
        return ""

    lowered_content = content.lower()
    windows: list[tuple[int, int, int, int]] = []
    window_size = max(200, min(max_chars, 2000))
    half_window = max(100, window_size // 2)
    for keyword in keywords:
        lowered_keyword = keyword.lower()
        if not lowered_keyword:
            continue
        start = 0
        while True:
            hit = lowered_content.find(lowered_keyword, start)
            if hit < 0:
                break
            window_start = max(0, hit - half_window)
            window_end = min(len(content), hit + len(keyword) + half_window)
            score = _keyword_window_score(
                content[window_start:window_end],
                keywords,
            )
            windows.append((score, _keyword_weight(lowered_keyword), window_start, window_end))
            start = hit + len(lowered_keyword)

    if not windows:
        return ""

    windows.sort(key=lambda item: (-item[0], -item[1], item[2]))
    selected: list[tuple[int, int]] = []
    current_chars = 0
    for _, _, start, end in windows:
        if any(not (end < used_start or start > used_end) for used_start, used_end in selected):
            continue
        clipped = content[start:end].strip()
        if not clipped:
            continue
        sep_len = 2 if selected else 0
        if current_chars + sep_len + len(clipped) > max_chars:
            remaining = max_chars - current_chars - sep_len
            if remaining < 200:
                continue
            end = min(end, start + remaining)
            clipped = content[start:end].strip()
        selected.append((start, end))
        current_chars += sep_len + len(clipped)
        if current_chars >= max_chars:
            break

    selected.sort()
    parts: list[str] = []
    for start, end in selected:
        part = content[start:end].strip()
        if start > 0:
            part = "..." + part
        if end < len(content):
            part = part + "..."
        parts.append(part)
    return "\n\n".join(parts).strip()[:max_chars]


def _keyword_window_score(text: str, keywords: list[str]) -> int:
    lowered = text.lower()
    score = 0
    for keyword in keywords:
        key = keyword.lower()
        if key and key in lowered:
            score += _keyword_weight(key)
    return score


def _keyword_weight(keyword: str) -> int:
    if re.fullmatch(r"[0-5]{3}|\d-\d-\d", keyword):
        return 1
    if keyword in {"野兽之主", "人鱼猴", "beast handler", "mermaid monkey"}:
        return 3
    if keyword in {"ddt", "moab", "bad", "zomg", "bfb"}:
        return 10
    if keyword in {"上路", "中路", "下路"}:
        return 10
    return 15


def normalize_candidate_details(
    candidate_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    for item in candidate_items:
        path = str(item.get("path", "") or "").strip()
        if not path:
            continue
        reasons = item.get("reasons", [])
        if not isinstance(reasons, list):
            reasons = [str(reasons)]
        details.append(
            {
                "path": path,
                "title": str(item.get("title", "") or "").strip(),
                "score": _safe_float(item.get("score"), default=0.0),
                "confidence": _safe_float(item.get("confidence"), default=0.0),
                "reasons": [str(reason) for reason in reasons if str(reason).strip()],
                "query_variant": str(item.get("query_variant", "") or "").strip(),
                "channel": str(item.get("channel", "") or "").strip(),
                "snippet": str(item.get("snippet", "") or "").strip()[:240],
            }
        )
    return details


def compute_score_margin(candidate_details: list[dict[str, Any]]) -> float:
    if len(candidate_details) < 2:
        if not candidate_details:
            return 0.0
        return _safe_float(candidate_details[0].get("score"), default=0.0)
    return round(
        _safe_float(candidate_details[0].get("score"), default=0.0)
        - _safe_float(candidate_details[1].get("score"), default=0.0),
        3,
    )


def _safe_float(value: object, *, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def build_kb_query_plan(raw_query: str, *, kb_root: Path) -> SearchQueryPlan:
    raw = str(raw_query or "").strip()
    invocation_stripped = strip_kb_query_invocation_noise(raw)
    normalized_terms = normalize_btd6_search_terms(invocation_stripped)
    normalized = sanitize_kb_query(raw)
    entity_terms = detect_entity_terms(
        " ".join([raw, invocation_stripped, normalized_terms, normalized]),
        kb_root=kb_root,
    )
    entity_terms = prioritize_entity_terms(entity_terms, primary_query=normalized)
    mechanic_terms = detect_mechanic_terms(raw)
    upgrade_terms = detect_upgrade_terms(raw)

    if "野兽之主" in entity_terms and any(
        term in {"004", "0-0-4"} for term in upgrade_terms
    ):
        entity_terms = _dedupe_texts([*entity_terms, "逆戟鲸"])

    variants = build_query_variants(
        raw=raw,
        invocation_stripped=invocation_stripped,
        normalized_terms=normalized_terms,
        normalized=normalized,
        entity_terms=entity_terms,
        mechanic_terms=mechanic_terms,
        upgrade_terms=upgrade_terms,
    )
    return SearchQueryPlan(
        raw=raw,
        normalized=normalized,
        invocation_stripped=invocation_stripped,
        entity_terms=entity_terms,
        mechanic_terms=mechanic_terms,
        upgrade_terms=upgrade_terms,
        variants=variants,
    )


def build_query_variants(
    *,
    raw: str,
    invocation_stripped: str,
    normalized_terms: str,
    normalized: str,
    entity_terms: list[str],
    mechanic_terms: list[str],
    upgrade_terms: list[str],
) -> list[str]:
    variants: list[str] = [
        normalized,
        normalized_terms,
        invocation_stripped,
    ]
    if entity_terms:
        variants.append(" ".join(entity_terms))
        if mechanic_terms:
            variants.append(" ".join([*entity_terms, *mechanic_terms]))
        if upgrade_terms:
            variants.append(" ".join([*entity_terms, *upgrade_terms]))
        if mechanic_terms or upgrade_terms:
            variants.append(" ".join([*entity_terms, *mechanic_terms, *upgrade_terms]))
    if mechanic_terms and upgrade_terms:
        variants.append(" ".join([*mechanic_terms, *upgrade_terms]))
    variants.append(raw)
    return _dedupe_texts(variants)


def detect_entity_terms(query: str, *, kb_root: Path) -> list[str]:
    text = re.sub(r"\s+", " ", str(query or "")).strip()
    folded = text.lower()
    terms: list[str] = []

    for alias, canonical in service_alias_map(kb_root).items():
        if alias and alias in folded:
            terms.append(canonical)

    for title in kb_title_terms(kb_root):
        if not title:
            continue
        if title.lower() in folded:
            terms.append(title)

    generic_terms = {"猴子", "防御塔", "英雄", "技能"}
    return [term for term in _dedupe_texts(terms) if term not in generic_terms]


def prioritize_entity_terms(entity_terms: list[str], *, primary_query: str) -> list[str]:
    primary = str(primary_query or "").strip().lower()
    if not primary:
        return entity_terms
    exact = [term for term in entity_terms if term.lower() == primary]
    containing = [
        term
        for term in entity_terms
        if term.lower() != primary and term.lower() in primary
    ]
    remaining = [
        term
        for term in entity_terms
        if term.lower() != primary and term.lower() not in primary
    ]
    return _dedupe_texts([*exact, *containing, *remaining])


def detect_mechanic_terms(query: str) -> list[str]:
    folded = str(query or "").lower()
    mechanic_aliases = {
        "DDT": ("ddt",),
        "MOAB": ("moab", "飞艇", "大型气球"),
        "BAD": ("bad",),
        "ZOMG": ("zomg",),
        "BFB": ("bfb",),
        "迷彩": ("迷彩", "反迷彩", "侦测", "隐形"),
        "铅": ("铅", "破铅"),
        "紫气球": ("紫气球", "紫球"),
        "收益": ("收益", "现金", "金钱", "经济"),
        "射程": ("射程", "范围"),
    }
    terms: list[str] = []
    for canonical, aliases in mechanic_aliases.items():
        if any(alias.lower() in folded for alias in aliases):
            terms.append(canonical)
    return _dedupe_texts(terms)


def detect_upgrade_terms(query: str) -> list[str]:
    terms: list[str] = []
    upgrade_pattern = r"(?<![\d-])([0-5])-([0-5])-([0-5])(?![\d-])"
    for match in re.finditer(upgrade_pattern, query or ""):
        code = "".join(match.groups())
        terms.extend([code, f"{code[0]}-{code[1]}-{code[2]}"])
    for match in re.finditer(r"(?<![\d-])([0-5]{3})(?![\d-])", query or ""):
        code = match.group(1)
        terms.extend([code, f"{code[0]}-{code[1]}-{code[2]}"])
    return _dedupe_texts(terms)


def service_alias_map(kb_root: Path) -> dict[str, str]:
    alias_map = {
        "beast handler": "野兽之主",
        "beast": "野兽之主",
        "驯兽师": "野兽之主",
        "野兽": "野兽之主",
        "mermaid monkey": "人鱼猴",
        "mermonkey": "人鱼猴",
        "鱼人猴": "人鱼猴",
        "m.a.d": "M.A.D",
        "mad": "M.A.D",
        "pat": "帕特•法斯第",
        "brickell": "海军上将布里克尔",
        "etienne": "艾蒂安",
        "adora": "阿多拉",
        "quincy": "昆西",
        "gwen": "格温多琳",
        "geraldo": "杰拉尔多",
        "sauda": "萨乌达",
        "corvus": "科沃斯",
        "ezili": "艾泽里",
        "海盗": "海盗猴",
        "潜艇": "猴子潜艇",
    }
    alias_map.update(load_alias_canonicals(kb_root / "aliases.json"))
    return {key.lower(): value for key, value in alias_map.items() if key and value}


def load_alias_canonicals(alias_file: Path) -> dict[str, str]:
    if not alias_file.exists():
        return {}
    try:
        data = json.loads(alias_file.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}

    result: dict[str, str] = {}
    for raw_key, raw_values in data.items():
        key = str(raw_key or "").strip()
        values = raw_values if isinstance(raw_values, list) else [raw_values]
        clean_values = [
            str(value or "").strip()
            for value in values
            if str(value or "").strip()
        ]
        cjk_values = [
            value for value in clean_values if re.search(r"[\u4e00-\u9fff]", value)
        ]
        canonical = (
            key
            if re.search(r"[\u4e00-\u9fff]", key)
            else (cjk_values[0] if cjk_values else key)
        )
        if key:
            result[key] = canonical
        for value in clean_values:
            result[value] = canonical
    return result


def kb_title_terms(kb_root: Path) -> list[str]:
    corpus_dir = _knowledge_corpus_dir(kb_root)
    return _kb_title_terms_from_dir(corpus_dir)


def _kb_title_terms_from_dir(root: Path) -> list[str]:
    if not root.exists():
        return []
    titles = []
    for path in root.rglob("*.md"):
        if ".history" in path.parts or path.name == "README.md":
            continue
        title = path.stem.strip()
        if title:
            titles.append(title)
    titles = _dedupe_texts(titles)
    titles.sort(key=len, reverse=True)
    return titles


def _dedupe_texts(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = re.sub(r"\s+", " ", str(item or "")).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def resolve_safe_kb_path(raw_path: str, *, kb_root: Path) -> Path | None:
    allowed_root = (kb_root / "knowledge_docs").resolve(strict=False)
    raw_candidate = Path(raw_path)
    candidates = [raw_candidate] if raw_candidate.is_absolute() else []
    if not raw_candidate.is_absolute():
        candidates.extend([kb_root / raw_candidate, kb_root.parent / raw_candidate])

    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=False)
        except Exception:
            continue
        if resolved.is_relative_to(allowed_root):
            return resolved
    return None


def extract_readable_content(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if normalized.startswith("---\n"):
        frontmatter_end = normalized.find("\n---\n", 4)
        if frontmatter_end >= 0:
            normalized = normalized[frontmatter_end + 5 :]
    return "\n".join(
        line.rstrip() for line in normalized.splitlines() if line.strip()
    )


def _knowledge_corpus_dir(kb_root: Path) -> Path:
    return kb_root / "knowledge_docs"


def sanitize_kb_query(query: str) -> str:
    cleaned = strip_kb_query_invocation_noise(query)
    cleaned = normalize_btd6_search_terms(cleaned)
    weak_terms = (
        "数据",
        "详情",
        "详细",
        "说明",
        "介绍",
        "机制",
        "信息",
        "讲解",
        "告诉我",
        "说说",
        "看下",
        "看一下",
        "帮我",
        "请问",
        "一下",
        "是什么",
        "怎么",
        "如何",
        "有关",
        "相关",
        "价格",
        "伤害",
        "穿透",
        "攻速",
        "射程",
        "收益",
        "加成",
        "技能",
        "商店",
        "灵魂",
        "陷阱",
        "支援",
        "亡灵",
        "太阳神庙",
        "永久啤酒",
        "反迷彩",
        "破铅",
        "上路",
        "中路",
        "下路",
    )
    weak_english_terms = (
        "stats",
        "stat",
        "data",
        "detail",
        "details",
        "info",
        "information",
        "guide",
        "wiki",
        "about",
    )
    for term in weak_terms:
        cleaned = cleaned.replace(term, " ")
    for term in weak_english_terms:
        cleaned = re.sub(rf"\b{re.escape(term)}\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[，。,.!?！？；;:/\\|]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or query.strip()


def strip_kb_query_invocation_noise(query: str) -> str:
    cleaned = str(query or "").strip()
    cleaned = re.sub(r"\[CQ:at,qq=\d+\]", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"@\S+", " ", cleaned)
    cleaned = re.sub(
        r"^\s*(小污猫|污猫|机器人|bot|Bot)\s*[,，:：]?\s*",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    invocation_terms = (
        "帮我",
        "麻烦",
        "查一下",
        "查下",
        "查查",
        "搜一下",
        "搜下",
        "搜索",
        "检索",
        "请问",
        "请",
    )
    for term in invocation_terms:
        cleaned = cleaned.replace(term, " ")
    cleaned = re.sub(r"\b(bot|search)\b", " ", cleaned, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", cleaned).strip()


def normalize_btd6_search_terms(query: str) -> str:
    cleaned = str(query or "")

    phrase_aliases = {
        "beast handler": "野兽之主",
        "mermaid monkey": "人鱼猴",
        "m.a.d": "M.A.D",
    }
    for alias, normalized in phrase_aliases.items():
        cleaned = re.sub(
            rf"\b{re.escape(alias)}\b",
            f" {normalized} ",
            cleaned,
            flags=re.IGNORECASE,
        )

    route_aliases = {
        "路线1": "上路",
        "路线一": "上路",
        "一路线": "上路",
        "1路": "上路",
        "路线2": "中路",
        "路线二": "中路",
        "二路线": "中路",
        "2路": "中路",
        "路线3": "下路",
        "路线三": "下路",
        "三路线": "下路",
        "3路": "下路",
    }
    for alias, normalized in route_aliases.items():
        cleaned = cleaned.replace(alias, f" {normalized} ")

    entity_aliases = (
        "飞镖猴",
        "回旋镖猴",
        "大炮",
        "图钉炮",
        "冰猴",
        "胶水炮手",
        "狙击手猴",
        "猴子潜艇",
        "海盗猴",
        "飞机",
        "直升机",
        "迫击炮猴",
        "机枪猴",
        "法师猴",
        "超猴侠",
        "忍者猴",
        "炼金术士",
        "德鲁伊",
        "香蕉农场",
        "刺钉工厂",
        "猴子村",
        "工程师猴",
        "昆西",
        "格温多琳",
        "奥本",
        "本杰明",
        "艾泽里",
        "帕特",
        "布里克尔",
        "艾蒂安",
        "萨乌达",
        "杰拉尔多",
        "科沃斯",
        "罗莎莉娅",
        "西拉斯",
        "野兽之主",
        "人鱼猴",
        "逆戟鲸",
        "大白鲨",
        "巨齿鲨",
        "食人鱼",
        "梭子鱼",
        "巨型秃鹫",
        "哈斯特巨鹰",
        "霸王龙",
        "M.A.D",
        "气球终结者",
    )
    route_terms = ("上路", "中路", "下路")
    for entity in entity_aliases:
        for route in route_terms:
            cleaned = cleaned.replace(f"{entity}{route}", f"{entity} {route}")
            cleaned = cleaned.replace(f"{route}{entity}", f"{route} {entity}")
        for field in BTD6_FIELD_TERMS:
            cleaned = cleaned.replace(f"{entity}{field}", f"{entity} {field}")
            cleaned = cleaned.replace(f"{field}{entity}", f"{field} {entity}")

    simple_aliases = {
        "帕特": "帕特•法斯第",
        "布里克尔": "海军上将布里克尔",
        "超猴侠": "超猴侠",
        "下路野兽": "野兽之主 下路",
    }
    for alias, normalized in simple_aliases.items():
        cleaned = cleaned.replace(alias, f" {normalized} ")

    cleaned = re.sub(r"(?<=[\u4e00-\u9fff])([0-5]{3})(?![\d-])", r" \1", cleaned)
    cleaned = re.sub(r"(?<![\d-])([0-5]{3})(?=[\u4e00-\u9fff])", r"\1 ", cleaned)
    cleaned = re.sub(r"(?<![\d-])([0-5]{3})(?![\d-])", _expand_upgrade_code, cleaned)
    if "野兽之主" in cleaned and re.search(
        r"(?<![\d-])(?:004|0-0-4)(?![\d-])", cleaned
    ):
        cleaned = f"{cleaned} 逆戟鲸"
    cleaned = re.sub(
        r"(能不能|能否|可不可以|是否|可以|不能|能抓|能打|能破|能攻击|抓|打得动|打不动|打)",
        " ",
        cleaned,
    )

    return re.sub(r"\s+", " ", cleaned).strip()


def _expand_upgrade_code(match: re.Match[str]) -> str:
    code = match.group(1)
    return f" {code} {code[0]}-{code[1]}-{code[2]} "


BTD6_FIELD_TERMS = (
    "详细数据",
    "升级价格",
    "解锁经验",
    "太阳神庙",
    "永久啤酒",
    "反迷彩",
    "数据",
    "价格",
    "伤害",
    "穿透",
    "攻速",
    "射程",
    "收益",
    "加成",
    "技能",
    "商店",
    "灵魂",
    "陷阱",
    "支援",
    "亡灵",
    "机制",
    "破铅",
)


@lru_cache(maxsize=8)
def _load_kb_interface(kb_root: Path) -> ModuleType:
    module_path = kb_root / "kb_interface.py"
    if not module_path.exists():
        raise FileNotFoundError(str(module_path))

    module_name = f"_btd6_kb_interface_{abs(hash(str(module_path.resolve())))}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载知识库接口: {module_path}")

    module = importlib.util.module_from_spec(spec)
    with _temporary_sys_path(kb_root):
        spec.loader.exec_module(module)
    return module


@contextmanager
def _temporary_sys_path(path: Path) -> Iterator[None]:
    raw_path = str(path.resolve())
    inserted = raw_path not in sys.path
    if inserted:
        sys.path.insert(0, raw_path)
    try:
        yield
    finally:
        if inserted:
            try:
                sys.path.remove(raw_path)
            except ValueError:
                pass
