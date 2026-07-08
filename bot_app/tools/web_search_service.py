from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import requests
from bs4 import BeautifulSoup

from ..protocol import CommandResultPayload
from .base import fail_result, ok_result


DUCKDUCKGO_LITE_URL = "https://lite.duckduckgo.com/lite/"
DUCKDUCKGO_HTML_URL = "https://html.duckduckgo.com/html/"
DEFAULT_TIMEOUT_SECONDS = 6.0
DEFAULT_TOP_N = 5
MAX_TOP_N = 8
MAX_QUERY_CHARS = 240
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)


def build_search_web_result(
    *,
    arguments: dict[str, Any],
    module: str,
) -> CommandResultPayload:
    raw_query = str(arguments.get("query", "") or "").strip()
    query = sanitize_web_query(raw_query)
    if not query:
        return fail_result(
            module=module,
            command="search_web",
            data={
                "query": "",
                "raw_query": raw_query,
                "result_count": 0,
                "results": [],
                "provider": "duckduckgo_lite_html",
            },
            message="缺少联网搜索关键词。",
            extra_prompt="请用用户原问题或补充主题、时间、地区、来源类型等限定词后重试 search_web。",
        )

    top_n = _coerce_int(arguments.get("top_n"), default=DEFAULT_TOP_N)
    top_n = max(1, min(top_n, MAX_TOP_N))
    timeout = _coerce_float(
        arguments.get("timeout_seconds"),
        default=DEFAULT_TIMEOUT_SECONDS,
    )
    timeout = max(2.0, min(timeout, 10.0))

    try:
        results = search_web(query=query, top_n=top_n, timeout=timeout)
    except Exception as exc:
        return fail_result(
            module=module,
            command="search_web",
            data={
                "query": query,
                "raw_query": raw_query,
                "top_n": top_n,
                "result_count": 0,
                "results": [],
                "provider": "duckduckgo_lite_html",
            },
            message=f"联网搜索失败: {type(exc).__name__}",
            extra_prompt=(
                "请说明联网搜索失败，并建议用户稍后重试；"
                "不要基于没有返回的网络资料编造结论。"
                "如果用户请求涉及违法违规、规避监管、侵犯隐私、侵权盗版、危险行为或其他法律法规禁止事项，"
                "不得提供操作性指导，只能拒绝或给出合法合规的替代方向。"
            ),
        )

    result_count = len(results)
    return ok_result(
        module=module,
        command="search_web",
        data={
            "query": query,
            "raw_query": raw_query,
            "top_n": top_n,
            "result_count": result_count,
            "results": results,
            "provider": "duckduckgo_lite_html",
        },
        message="联网搜索完成" if result_count else "联网搜索没有找到结果",
        extra_prompt=(
            "这是联网搜索返回的网页候选。请只依据 results 中的 title、snippet、url 回答；"
            "若结果不足以确认结论，请明确说不确定，并给出可继续查询的方向。"
            "涉及时效性信息时，说明信息可能随时间变化。"
            "涉及法律、医疗、金融、安全等高风险主题时，只能提供一般信息，提醒用户以官方渠道或专业人士意见为准。"
            "如果用户请求涉及违法违规、规避监管、侵犯隐私、侵权盗版、危险行为或其他法律法规禁止事项，"
            "不得提供操作性指导、规避方法或可执行步骤；应简要拒绝并提供合法合规的替代建议。"
        ),
    )


def search_web(*, query: str, top_n: int, timeout: float) -> list[dict[str, Any]]:
    last_error: Exception | None = None
    saw_successful_response = False
    endpoints = (
        (DUCKDUCKGO_LITE_URL, 2, timeout),
        (DUCKDUCKGO_HTML_URL, 1, min(timeout, 4.0)),
    )
    for url, attempts, endpoint_timeout in endpoints:
        for _ in range(attempts):
            try:
                response = requests.post(
                    url,
                    data={"q": query, "kl": "cn-zh"},
                    headers={"User-Agent": USER_AGENT},
                    timeout=endpoint_timeout,
                )
                response.raise_for_status()
                saw_successful_response = True
                results = parse_duckduckgo_results(response.text, top_n=top_n)
                if results:
                    return results
            except Exception as exc:
                last_error = exc

    if last_error is not None and not saw_successful_response:
        raise last_error
    return []


def parse_duckduckgo_results(html: str, *, top_n: int) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html or "", "html.parser")
    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for node in soup.select(".result"):
        link = node.select_one("a.result__a")
        if link is None:
            continue

        title = _normalize_text(link.get_text(" ", strip=True))
        url = resolve_duckduckgo_url(str(link.get("href", "") or ""))
        if not title or not _is_http_url(url):
            continue
        if url in seen_urls:
            continue

        snippet_node = node.select_one(".result__snippet")
        snippet = (
            _normalize_text(snippet_node.get_text(" ", strip=True))
            if snippet_node is not None
            else ""
        )
        seen_urls.add(url)
        results.append(
            {
                "rank": len(results) + 1,
                "title": title[:180],
                "url": url,
                "source_domain": _source_domain(url),
                "snippet": snippet[:500],
            }
        )
        if len(results) >= top_n:
            break

    if len(results) < top_n:
        for link in soup.select("a.result-link"):
            title = _normalize_text(link.get_text(" ", strip=True))
            url = resolve_duckduckgo_url(str(link.get("href", "") or ""))
            if not title or not _is_http_url(url):
                continue
            if url in seen_urls:
                continue

            snippet = ""
            row = link.find_parent("tr")
            snippet_row = row.find_next_sibling("tr") if row is not None else None
            if snippet_row is not None:
                snippet = _normalize_text(snippet_row.get_text(" ", strip=True))

            seen_urls.add(url)
            results.append(
                {
                    "rank": len(results) + 1,
                    "title": title[:180],
                    "url": url,
                    "source_domain": _source_domain(url),
                    "snippet": snippet[:500],
                }
            )
            if len(results) >= top_n:
                break

    return results


def resolve_duckduckgo_url(href: str) -> str:
    href = str(href or "").strip()
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    if href.startswith("/"):
        href = "https://duckduckgo.com" + href

    parsed = urlparse(href)
    query = parse_qs(parsed.query)
    uddg = query.get("uddg", [])
    if uddg:
        return unquote(str(uddg[0] or "").strip())
    return href


def sanitize_web_query(query: str) -> str:
    cleaned = str(query or "").strip()
    cleaned = re.sub(r"\[CQ:at,qq=\d+\]", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"@\S+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > MAX_QUERY_CHARS:
        cleaned = cleaned[:MAX_QUERY_CHARS].rsplit(" ", 1)[0] or cleaned[:MAX_QUERY_CHARS]
    return cleaned


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _is_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _source_domain(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc.lower()


def _coerce_int(value: object, *, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _coerce_float(value: object, *, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
