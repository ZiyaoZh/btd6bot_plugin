"""Replay redacted request logs offline; output counts only, never chat text.

This measures payload size and protocol preservation, not answer correctness.
It intentionally applies only the request budget, leaving historical retrieval
and image selection unchanged for a conservative comparison of that layer.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot_app.context_budget import ContextBudget, ContextBudgetExceeded


def current_question(messages: list[dict]) -> str:
    for message in messages:
        content = message.get("content")
        parts = content if isinstance(content, list) else [{"text": content}]
        for part in parts:
            try:
                data = json.loads(part.get("text", ""))
                context = data.get("reply_context", {})
                if context:
                    return str(context.get("current_event", {}).get("text", ""))
            except (ValueError, TypeError, AttributeError):
                pass
    return ""


def protocol(messages: list[dict]) -> list[tuple]:
    return [
        (
            m.get("role"),
            m.get("tool_call_id"),
            tuple(t.get("id") for t in m.get("tool_calls", [])),
        )
        for m in messages
    ]


def stats(values: list[int]) -> dict[str, int]:
    ordered = sorted(values)
    return (
        {
            "p50": ordered[(len(ordered) - 1) // 2],
            "p95": ordered[math.ceil(len(ordered) * 0.95) - 1],
            "max": ordered[-1],
        }
        if ordered
        else {}
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs", type=Path, required=True)
    parser.add_argument("--since", default="2026-10-07")
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--max-tokens", type=int, default=24000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    budget = ContextBudget(args.max_tokens)
    requests: dict[str, dict] = {}
    # Retain the largest request of each task, to include multi-tool growth.
    for path in args.logs.glob("network.log*"):
        for line in path.open(encoding="utf-8"):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if (
                row.get("stage") != "llm.request"
                or row.get("purpose") != "agent_turn"
                or row.get("ts", "")[:10] < args.since
            ):
                continue
            key = row["trace_id"]
            payload = row["payload"]
            size = len(json.dumps(payload, ensure_ascii=False))
            if key not in requests or size > requests[key]["size"]:
                requests[key] = {"payload": payload, "size": size}
    ordered = sorted(requests.values(), key=lambda item: item["size"])
    count = min(max(1, args.limit), len(ordered))
    selected = [
        ordered[round(i * (len(ordered) - 1) / max(1, count - 1))] for i in range(count)
    ]
    before, after = [], []
    failures = 0
    protocol_failures = 0
    for item in selected:
        payload = item["payload"]
        messages, tools = payload["messages"], payload.get("tools", [])
        before.append(budget.count(messages, tools))
        try:
            result = budget.fit(messages, tools, query=current_question(messages))
        except ContextBudgetExceeded:
            failures += 1
            continue
        after.append(budget.count(result, tools))
        protocol_failures += protocol(result) != protocol(messages)
        protocol_failures += current_question(result) != current_question(messages)
    report = {
        "since": args.since,
        "sampled_tasks": count,
        "available_tasks": len(ordered),
        "sampling": "one largest request per task; evenly sampled across serialized size",
        "measurement": "local token estimate including image reserves; not provider usage or answer quality",
        "before": stats(before),
        "after": stats(after),
        "requests_over_budget_before": sum(n > args.max_tokens for n in before),
        "requests_rejected_after": failures,
        "protocol_or_current_question_changes": protocol_failures,
        "total_estimated_token_reduction_percent": round(
            (1 - sum(after) / sum(before)) * 100, 1
        )
        if before and not failures
        else None,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 1 if failures or protocol_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
