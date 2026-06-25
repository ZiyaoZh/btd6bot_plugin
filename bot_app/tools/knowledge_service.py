from __future__ import annotations

import importlib.util
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any, Iterator

from ..protocol import CommandResultPayload, EventPayload
from .base import fail_result, ok_result


def build_search_game_knowledge_result(
    *,
    kb_root: Path,
    event: EventPayload,
    arguments: dict[str, Any],
    module: str,
) -> CommandResultPayload:
    raw_query = str(arguments.get("query", "") or "").strip() or event["text"]
    query = sanitize_kb_query(raw_query)
    top_n_raw = arguments.get("top_n", 5)
    try:
        top_n = int(top_n_raw)
    except (TypeError, ValueError):
        top_n = 5
    top_n = max(1, min(top_n, 20))

    corpus_dir = kb_root / "wiki_data_second_cleaned" / "btd6"
    db_path = kb_root / "search_index.db"
    alias_file_path = kb_root / "aliases.json"
    alias_file = alias_file_path if alias_file_path.exists() else None

    try:
        kb_interface = _load_kb_interface(kb_root)
        paths = kb_interface.interface_search(
            query=query,
            top_n=top_n,
            db_path=db_path,
            corpus_dir=corpus_dir,
            alias_file=alias_file,
        )
    except Exception as exc:
        return fail_result(
            module=module,
            command="search_game_knowledge",
            data={
                "raw_query": raw_query,
                "query": query,
                "knowledge_query": query,
                "knowledge_query_raw": raw_query,
                "top_n": top_n,
                "matched_count": 0,
                "matched_paths": [],
            },
            message=f"游戏知识检索失败: {type(exc).__name__}",
            extra_prompt="请简要说明检索失败，并建议稍后重试或检查知识库索引文件。",
        )

    matched_paths = [str(path) for path in paths]
    return ok_result(
        module=module,
        command="search_game_knowledge",
        data={
            "raw_query": raw_query,
            "query": query,
            "knowledge_query": query,
            "knowledge_query_raw": raw_query,
            "top_n": top_n,
            "matched_count": len(matched_paths),
            "matched_paths": matched_paths,
        },
        message="游戏知识检索完成",
        extra_prompt=(
            "你已经拿到候选文件路径。"
            "请先从 matched_paths 中选择最可能有答案的 1 个文件，再调用 read_game_knowledge_file 读取内容。"
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
    keywords_query = build_extract_keywords_query(arguments)
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
            extra_prompt="仅允许读取 btd6_knowledge_base/wiki_data_second_cleaned/btd6 目录内文件。",
        )

    if not safe_path.exists() or not safe_path.is_file():
        return fail_result(
            module=module,
            command="read_game_knowledge_file",
            data={"file_path": str(safe_path), "content": ""},
            message="目标文件不存在。",
            extra_prompt="请改用 search_game_knowledge 返回的路径。",
        )

    if safe_path.suffix.lower() not in {".html", ".htm", ".md", ".txt"}:
        return fail_result(
            module=module,
            command="read_game_knowledge_file",
            data={"file_path": str(safe_path), "content": ""},
            message="文件类型不允许读取。",
            extra_prompt="仅支持读取 html/htm/md/txt 文件。",
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

    corpus_dir = kb_root / "wiki_data_second_cleaned" / "btd6"
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


def split_keywords_for_view(keywords_query: str) -> list[str]:
    parts = re.split(r"[\s,，、;；|+/]+", keywords_query or "")
    return [part.strip() for part in parts if part.strip()]


def resolve_safe_kb_path(raw_path: str, *, kb_root: Path) -> Path | None:
    allowed_roots = [
        (kb_root / "wiki_data_second_cleaned" / "btd6").resolve(strict=False),
        (kb_root / "wiki_data_cleaned" / "btd6").resolve(strict=False),
    ]
    raw_candidate = Path(raw_path)
    candidates = [raw_candidate] if raw_candidate.is_absolute() else []
    if not raw_candidate.is_absolute():
        candidates.extend([kb_root / raw_candidate, kb_root.parent / raw_candidate])

    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=False)
        except Exception:
            continue
        if any(resolved.is_relative_to(root) for root in allowed_roots):
            return resolved
    return None


def extract_readable_content(text: str) -> str:
    stripped = text
    try:
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        stripped = soup.get_text("\n", strip=True)
    except Exception:
        stripped = text

    return "\n".join(line.strip() for line in stripped.splitlines() if line.strip())


def sanitize_kb_query(query: str) -> str:
    weak_terms = (
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
    )
    cleaned = query
    for term in weak_terms:
        cleaned = cleaned.replace(term, " ")
    cleaned = re.sub(r"[，。,.!?！？；;:/\\|]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or query.strip()


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
