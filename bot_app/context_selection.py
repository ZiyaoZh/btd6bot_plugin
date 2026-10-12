"""Deterministic, source-preserving selection for context and history retrieval."""

from __future__ import annotations

import re


def query_terms(query: str) -> set[str]:
    terms = set(re.findall(r"[a-z0-9]+(?:[-.][a-z0-9]+)*", query.lower()))
    for word in re.findall(r"[\u4e00-\u9fff]+", query):
        if len(word) <= 12:
            terms.add(word)
        terms.update(word[index : index + 2] for index in range(len(word) - 1))
    return terms - {
        "这个",
        "那个",
        "一下",
        "什么",
        "怎么",
        "帮我",
        "请问",
        "可以",
        "现在",
        "之前",
    }


def relevance(text: str, terms: set[str]) -> float:
    folded = text.lower()
    return sum(min(8, len(term)) for term in terms if term in folded)


def evidence_excerpt(
    text: str, query: str, max_chars: int, *, label: str = "原文"
) -> tuple[str, list[dict[str, int]]]:
    """Select original lines, carrying nearby headings and Markdown table headers.

    Ranges refer to the supplied text, never to an inferred file offset. Callers
    must identify that source (a full file or an already extracted observation).
    """
    if len(text) <= max_chars:
        return text, [{"start_line": 1, "end_line": len(text.splitlines()) or 1}]
    lines = text.splitlines()
    terms = query_terms(query)
    headings: list[int] = []
    parents: dict[int, list[int]] = {}
    table_header: int | None = None
    for index, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s", line)
        if match:
            level = len(match[1])
            headings = [i for i in headings if len(lines[i].split()[0]) < level]
            headings.append(index)
        if re.match(r"^\s*\|?\s*:?-{3,}", line) and index:
            table_header = index - 1
        elif not line.strip().startswith("|"):
            table_header = None
        parents[index] = [
            *headings,
            *([table_header, table_header + 1] if table_header is not None else []),
        ]

    ranked = sorted(range(len(lines)), key=lambda i: (-relevance(lines[i], terms), i))
    selected: set[int] = set()
    displayed: dict[int, str] = {}
    used = 0
    # Reserve room for source labels and an explicit omission marker.
    allowance = max(0, max_chars - 180)
    for index in ranked:
        candidates = sorted(
            set(
                parents[index]
                + list(range(max(0, index - 1), min(len(lines), index + 2)))
            )
            - selected
        )
        cost = sum(len(lines[i]) + 1 for i in candidates) + 35
        if cost > allowance - used:
            # An oversized matching row must not lose to short irrelevant
            # lines. Drop neighbours first, then quote within that source line.
            candidates = sorted(set(parents[index] + [index]) - selected)
            remaining = allowance - used - 35
            if relevance(lines[index], terms) <= 0 or remaining < 120:
                continue
            per_line = max(40, remaining // max(1, len(candidates)) - 2)
            for i in candidates:
                line = lines[i]
                if len(line) > per_line:
                    matches = [
                        (len(t), line.lower().find(t))
                        for t in terms
                        if t in line.lower()
                    ]
                    position = max(matches, default=(0, 0))[1]
                    start = max(0, position - per_line // 4)
                    line = (
                        "[行内节选]" + line[start : start + max(0, per_line - 16)] + "…"
                    )
                displayed[i] = line
            cost = sum(len(displayed[i]) + 1 for i in candidates) + 35
            if cost > allowance - used:
                continue
        selected.update(candidates)
        used += cost
        if used >= allowance * 0.95:
            break
    if not selected:
        # A minified single line must still be selected around the query hit,
        # rather than silently returning the beginning of a huge document.
        index = ranked[0] if ranked else 0
        line = lines[index] if lines else text
        hits = [(len(t), line.lower().find(t)) for t in terms if t in line.lower()]
        start = max(0, max(hits, default=(0, 0))[1] - max_chars // 4)
        result = f"[{label} L{index + 1}，行内节选]\n{line[start : start + max(0, max_chars - 100)]}\n[其余内容已省略]"
        return result[:max_chars], [{"start_line": index + 1, "end_line": index + 1}]
    ranges: list[dict[str, int]] = []
    for index in sorted(selected):
        if ranges and ranges[-1]["end_line"] == index:
            ranges[-1]["end_line"] = index + 1
        else:
            ranges.append({"start_line": index + 1, "end_line": index + 1})
    blocks = [
        f"[{label} L{r['start_line']}-L{r['end_line']}]\n"
        + "\n".join(
            displayed.get(i, lines[i])
            for i in range(r["start_line"] - 1, r["end_line"])
        )
        for r in ranges
    ]
    return ("\n\n".join(blocks) + "\n[其余内容已省略；需要时按来源继续读取]")[
        :max_chars
    ], ranges
