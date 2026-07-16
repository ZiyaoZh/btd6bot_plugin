from __future__ import annotations

import asyncio
import json
from typing import Any

from ..agent.tool_registry import ToolRegistry
from ..agent.types import ToolCallRecord
from ..logging_utils import log_flow
from ..protocol import CommandResultPayload, EventPayload
from ..tools.base import fail_result
from .types import ToolInvocation


class ToolExecutor:
    """Apply deterministic validation, timeout and error handling to tools."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        timeout_seconds: float = 30.0,
        max_result_chars: int = 60_000,
    ) -> None:
        self._registry = registry
        self._timeout_seconds = max(0.1, float(timeout_seconds))
        self._max_result_chars = max(1_000, int(max_result_chars))

    @property
    def schemas(self) -> list[dict[str, Any]]:
        return self._registry.to_openai_tools()

    async def execute_batch(
        self,
        invocations: list[ToolInvocation],
        event: EventPayload,
        *,
        trace_id: str = "",
    ) -> list[ToolCallRecord]:
        records: list[ToolCallRecord] = []
        for invocation in invocations:
            result = await self.execute(invocation, event, trace_id=trace_id)
            records.append(
                ToolCallRecord(
                    name=invocation.name,
                    arguments=invocation.arguments,
                    result=result,
                    call_id=invocation.id,
                    source=invocation.source,
                )
            )
        return records

    async def execute(
        self,
        invocation: ToolInvocation,
        event: EventPayload,
        *,
        trace_id: str = "",
    ) -> CommandResultPayload:
        spec = self._registry.get_spec(invocation.name)
        if spec is None:
            return fail_result(
                module="harness.tool_executor",
                command=invocation.name,
                data={"arguments": invocation.arguments},
                message=f"未知工具: {invocation.name}",
                extra_prompt="请改用 tools 中真实存在的工具。",
            )

        if invocation.argument_error:
            return fail_result(
                module="harness.tool_executor",
                command=invocation.name,
                data={
                    "arguments": invocation.arguments,
                    "validation_errors": [invocation.argument_error],
                },
                message="工具参数不是有效的 JSON 对象。",
                extra_prompt="请根据工具参数定义修正后重试。",
            )

        errors = _validate_schema(invocation.arguments, spec["parameters"])
        if errors:
            return fail_result(
                module="harness.tool_executor",
                command=invocation.name,
                data={"arguments": invocation.arguments, "validation_errors": errors},
                message="工具参数不符合 schema。",
                extra_prompt="请根据工具参数定义修正后重试。",
            )

        if trace_id:
            log_flow(
                stage="harness.tool.started",
                trace_id=trace_id,
                tool_call_id=invocation.id,
                tool_name=invocation.name,
                source=invocation.source,
                arguments=invocation.arguments,
            )

        timeout_seconds = (
            self._registry.get_execution_timeout_seconds(invocation.name)
            or self._timeout_seconds
        )
        try:
            result = await asyncio.wait_for(
                self._registry.execute(invocation.name, invocation.arguments, event),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            result = fail_result(
                module="harness.tool_executor",
                command=invocation.name,
                data={"timeout_seconds": timeout_seconds},
                message=f"工具执行超时（{timeout_seconds:g} 秒）。",
                extra_prompt="请说明工具超时，并建议稍后重试或缩小查询范围。",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            result = fail_result(
                module="harness.tool_executor",
                command=invocation.name,
                data={"error_type": type(exc).__name__},
                message=f"工具执行失败: {type(exc).__name__}",
                extra_prompt="请说明工具暂时不可用，不要编造结果。",
            )

        result = _limit_result(result, self._max_result_chars)
        if trace_id:
            log_flow(
                stage="harness.tool.finished",
                trace_id=trace_id,
                tool_call_id=invocation.id,
                tool_name=invocation.name,
                ok=result.get("ok", False),
                command_result=result,
            )
        return result


def _validate_schema(
    value: Any, schema: dict[str, Any], path: str = "arguments"
) -> list[str]:
    errors: list[str] = []
    expected = schema.get("type")
    type_map = {
        "object": dict,
        "array": list,
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
    }
    python_type = type_map.get(str(expected))
    if python_type is not None:
        valid = isinstance(value, python_type)
        if expected in {"integer", "number"} and isinstance(value, bool):
            valid = False
        if not valid:
            return [f"{path} must be {expected}"]

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        properties = properties if isinstance(properties, dict) else {}
        required = schema.get("required", [])
        if isinstance(required, list):
            for name in required:
                if name not in value:
                    errors.append(f"{path}.{name} is required")
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in properties:
                    errors.append(f"{path}.{name} is not allowed")
        for name, item in value.items():
            item_schema = properties.get(name)
            if isinstance(item_schema, dict):
                errors.extend(_validate_schema(item, item_schema, f"{path}.{name}"))

    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            errors.extend(_validate_schema(item, schema["items"], f"{path}[{index}]"))

    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        errors.append(f"{path} must be one of {enum}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path} must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path} must be <= {schema['maximum']}")
    return errors


def _limit_result(result: CommandResultPayload, max_chars: int) -> CommandResultPayload:
    serialized = json.dumps(result, ensure_ascii=False, default=str)
    if len(serialized) <= max_chars:
        return result

    limited = dict(result)
    data = dict(result.get("data", {}) or {})
    overflow = len(serialized) - max_chars
    for key in ("content", "text", "raw", "html", "guide_text"):
        value = data.get(key)
        if isinstance(value, str) and value:
            keep = max(500, len(value) - overflow - 200)
            data[key] = value[:keep]
            data[f"{key}_truncated"] = True
            break
    limited["data"] = data
    text_budget = max(100, max_chars // 4)
    limited["message"] = str(limited.get("message", "") or "")[:text_budget]
    limited["extra_prompt"] = (
        str(limited.get("extra_prompt", "") or "")[:text_budget]
        + "\n工具结果因长度限制已截断，只能依据保留内容回答。"
    ).strip()
    if len(json.dumps(limited, ensure_ascii=False, default=str)) <= max_chars:
        return limited  # type: ignore[return-value]

    critical_keys = (
        "file_path",
        "content_chars",
        "chars",
        "matched_count",
        "matched_paths",
        "knowledge_query",
        "knowledge_query_raw",
        "result_count",
        "query",
    )
    compact_data = {
        key: _compact_value(data[key]) for key in critical_keys if key in data
    }
    compact_data["result_truncated"] = True
    limited["data"] = compact_data
    base_length = len(json.dumps(limited, ensure_ascii=False, default=str))
    preview_budget = max(0, max_chars - base_length - 40)
    if preview_budget:
        compact_data["result_preview"] = json.dumps(
            data, ensure_ascii=False, default=str
        )[:preview_budget]
    while len(
        json.dumps(limited, ensure_ascii=False, default=str)
    ) > max_chars and compact_data.get("result_preview"):
        compact_data["result_preview"] = str(compact_data["result_preview"])[:-100]
    if len(json.dumps(limited, ensure_ascii=False, default=str)) > max_chars:
        limited["data"] = {"result_truncated": True}
    return limited  # type: ignore[return-value]


def _compact_value(value: Any) -> Any:
    if isinstance(value, str):
        return value[:1000]
    if isinstance(value, list):
        return [_compact_value(item) for item in value[:10]]
    if isinstance(value, dict):
        return {
            str(key): _compact_value(item) for key, item in list(value.items())[:20]
        }
    return value
