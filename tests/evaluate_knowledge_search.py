from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
KB_ROOT = PLUGIN_ROOT / "btd6_knowledge_base"

sys.path.insert(0, str(PLUGIN_ROOT))
sys.path.insert(0, str(KB_ROOT))

from bot_app.tools.knowledge_service import sanitize_kb_query  # noqa: E402
from search_framework import search_file_paths  # noqa: E402


@dataclass(frozen=True)
class SearchEvalCase:
    query: str
    expected_first: str
    should_hit: bool = True
    requires_grounded_answer: bool = True
    source: str = ""
    notes: str = ""


@dataclass(frozen=True)
class SearchEvalResult:
    query: str
    sanitized_query: str
    expected_first: str
    should_hit: bool
    requires_grounded_answer: bool
    source: str
    notes: str
    matched_count: int
    matched_files: list[str]
    hit: bool
    first_doc_success: bool


DEFAULT_CASE_FILE = Path(__file__).with_name("knowledge_search_eval_cases.json")


def load_cases(case_file: Path) -> list[SearchEvalCase]:
    raw = json.loads(case_file.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("Case file must contain a JSON array")

    cases: list[SearchEvalCase] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Case #{index} must be an object")
        query = str(item.get("query", "") or "").strip()
        expected_first = str(item.get("expected_first", "") or "").strip()
        if not query or not expected_first:
            raise ValueError(f"Case #{index} requires query and expected_first")
        cases.append(
            SearchEvalCase(
                query=query,
                expected_first=expected_first,
                should_hit=bool(item.get("should_hit", True)),
                requires_grounded_answer=bool(
                    item.get("requires_grounded_answer", True)
                ),
                source=str(item.get("source", "") or "").strip(),
                notes=str(item.get("notes", "") or "").strip(),
            )
        )
    return cases


def evaluate_case(case: SearchEvalCase, *, top_n: int) -> SearchEvalResult:
    sanitized_query = sanitize_kb_query(case.query)
    matched_paths = search_file_paths(
        db_path=KB_ROOT / "search_index.db",
        query=sanitized_query,
        top_k=top_n,
        corpus_dir=KB_ROOT / "wiki_data_second_cleaned" / "btd6",
        alias_file=KB_ROOT / "aliases.json",
    )
    matched_files = [Path(path).name for path in matched_paths]
    return SearchEvalResult(
        query=case.query,
        sanitized_query=sanitized_query,
        expected_first=case.expected_first,
        should_hit=case.should_hit,
        requires_grounded_answer=case.requires_grounded_answer,
        source=case.source,
        notes=case.notes,
        matched_count=len(matched_files),
        matched_files=matched_files,
        hit=case.expected_first in matched_files,
        first_doc_success=bool(matched_files)
        and matched_files[0] == case.expected_first,
    )


def build_report(cases: list[SearchEvalCase], *, top_n: int) -> dict[str, object]:
    results = [evaluate_case(case, top_n=top_n) for case in cases]
    total = len(results)
    should_hit_results = [item for item in results if item.should_hit]
    should_hit_total = len(should_hit_results)
    hit_count = sum(1 for item in should_hit_results if item.hit)
    first_doc_success_count = sum(
        1 for item in should_hit_results if item.first_doc_success
    )
    no_result_count = sum(1 for item in results if item.matched_count == 0)
    grounded_required_count = sum(1 for item in results if item.requires_grounded_answer)
    source_counts: dict[str, int] = {}
    for item in results:
        source = item.source or "unspecified"
        source_counts[source] = source_counts.get(source, 0) + 1
    return {
        "total": total,
        "knowledge_query_count": total,
        "should_hit_count": should_hit_total,
        "grounded_required_count": grounded_required_count,
        "top_n": top_n,
        "search_hit_rate": hit_count / should_hit_total
        if should_hit_total
        else 0.0,
        "first_doc_success_rate": first_doc_success_count / should_hit_total
        if should_hit_total
        else 0.0,
        "no_result_rate": no_result_count / total if total else 0.0,
        "source_counts": source_counts,
        "results": [asdict(item) for item in results],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate BTD6 knowledge search cases")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASE_FILE)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full JSON report instead of a compact text summary.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_report(load_cases(args.cases), top_n=max(1, args.top_n))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            "knowledge_search_eval "
            f"knowledge_query_count={report['knowledge_query_count']} "
            f"should_hit_count={report['should_hit_count']} "
            f"grounded_required_count={report['grounded_required_count']} "
            f"search_hit_rate={report['search_hit_rate']:.3f} "
            f"first_doc_success_rate={report['first_doc_success_rate']:.3f} "
            f"no_result_rate={report['no_result_rate']:.3f}"
        )
        for item in report["results"]:
            status = "PASS" if item["first_doc_success"] else "FAIL"
            print(
                f"{status} {item['query']} -> {item['sanitized_query']} "
                f"first={item['matched_files'][:1]} expected={item['expected_first']}"
            )

    if report["first_doc_success_rate"] < 1.0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
