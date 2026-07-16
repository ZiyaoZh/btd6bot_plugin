from __future__ import annotations

import asyncio
from dataclasses import dataclass
from http.client import IncompleteRead, RemoteDisconnected
import json
from threading import Lock
from typing import Any
from urllib import error, request

from .logging_utils import log_flow
from .settings import AppSettings


class LLMRequestError(RuntimeError):
    """Raised when a request to LLM backend fails."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = True,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


@dataclass(frozen=True)
class LLMToolCall:
    id: str
    name: str
    arguments: str

    def to_log_payload(self) -> dict[str, str]:
        return {
            "id": self.id,
            "name": self.name,
            "arguments": self.arguments,
        }


@dataclass(frozen=True)
class LLMResponse:
    content: str
    assistant_message: dict[str, Any]
    tool_calls: list[LLMToolCall]
    total_tokens: int
    raw: dict[str, Any]
    model: str


class LLMClient:
    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._token_lock = Lock()
        self._trace_token_totals: dict[str, int] = {}

    async def chat(
        self,
        messages: list[dict[str, Any]],
        trace_id: str = "",
        purpose: str = "chat",
    ) -> str:
        response = await self.chat_completion(
            messages=messages, trace_id=trace_id, purpose=purpose
        )
        return response.content

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        trace_id: str = "",
        purpose: str = "chat",
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        per_model_attempts = self._settings.llm_max_retries + 1
        last_error: Exception | None = None
        models = list(self._settings.models)
        total_attempts = per_model_attempts * max(1, len(models))
        global_attempt = 0

        for model_index, model in enumerate(models, start=1):
            for model_attempt in range(1, per_model_attempts + 1):
                global_attempt += 1
                try:
                    return await asyncio.to_thread(
                        self._chat_sync,
                        messages,
                        trace_id,
                        purpose,
                        model,
                        self._settings.api_base_url,
                        self._settings.api_key,
                        0.7 if temperature is None else float(temperature),
                        True,
                        tools,
                        tool_choice,
                        response_format,
                    )
                except LLMRequestError as exc:
                    last_error = exc
                    if not exc.retryable:
                        if trace_id:
                            log_flow(
                                stage="llm.retry_skipped",
                                trace_id=trace_id,
                                purpose=purpose,
                                model=model,
                                model_index=model_index,
                                model_count=len(models),
                                status_code=exc.status_code,
                                reason="non_retryable_error",
                                error=str(exc),
                            )
                        break
                    if trace_id:
                        log_flow(
                            stage="llm.retry",
                            trace_id=trace_id,
                            purpose=purpose,
                            model=model,
                            model_index=model_index,
                            model_count=len(models),
                            model_attempt=model_attempt,
                            model_max_attempts=per_model_attempts,
                            attempt=global_attempt,
                            max_attempts=total_attempts,
                            error=str(exc),
                        )
                    if model_attempt >= per_model_attempts:
                        break
                    await asyncio.sleep(min(0.5 * model_attempt, 2.0))

        if last_error is not None:
            raise last_error
        raise LLMRequestError("LLM 请求失败")

    async def chat_with_override(
        self,
        messages: list[dict[str, Any]],
        trace_id: str,
        purpose: str,
        model: str,
        api_base_url: str,
        api_key: str,
        temperature: float = 0.7,
    ) -> str:
        response = await asyncio.to_thread(
            self._chat_sync,
            messages,
            trace_id,
            purpose,
            model,
            api_base_url,
            api_key,
            float(temperature),
            True,
            None,
            None,
            None,
        )
        return response.content

    def pop_trace_total_tokens(self, trace_id: str) -> int:
        if not trace_id:
            return 0
        with self._token_lock:
            return int(self._trace_token_totals.pop(trace_id, 0) or 0)

    def _chat_sync(
        self,
        messages: list[dict[str, Any]],
        trace_id: str,
        purpose: str,
        model: str,
        api_base_url: str,
        api_key: str,
        temperature: float,
        accumulate_tokens: bool,
        tools: list[dict[str, Any]] | None,
        tool_choice: str | dict[str, Any] | None,
        response_format: dict[str, Any] | None,
    ) -> LLMResponse:
        url = f"{api_base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools is not None:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        if response_format is not None:
            payload["response_format"] = response_format

        if trace_id:
            log_flow(
                stage="llm.request",
                trace_id=trace_id,
                purpose=purpose,
                model=model,
                api_base_url=api_base_url,
                payload=payload,
            )

        body = json.dumps(payload).encode("utf-8")

        req = request.Request(
            url=url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )

        try:
            with request.urlopen(req, timeout=self._settings.request_timeout) as resp:
                raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            assistant_message = data["choices"][0]["message"]
            content = assistant_message.get("content", "")
            answer = str(content or "").strip()
            tool_calls = self._parse_tool_calls(assistant_message)
            usage = data.get("usage", {}) if isinstance(data, dict) else {}
            total_tokens_raw = (
                usage.get("total_tokens", 0) if isinstance(usage, dict) else 0
            )
            try:
                total_tokens = int(total_tokens_raw)
            except (TypeError, ValueError):
                total_tokens = 0

            if trace_id and total_tokens > 0 and accumulate_tokens:
                with self._token_lock:
                    self._trace_token_totals[trace_id] = (
                        self._trace_token_totals.get(trace_id, 0) + total_tokens
                    )

            if trace_id:
                log_flow(
                    stage="llm.response",
                    trace_id=trace_id,
                    purpose=purpose,
                    response=data,
                    answer=answer,
                    tool_calls=[tool_call.to_log_payload() for tool_call in tool_calls],
                    total_tokens=total_tokens,
                )
            return LLMResponse(
                content=answer,
                assistant_message=self._normalize_assistant_message(assistant_message),
                tool_calls=tool_calls,
                total_tokens=total_tokens,
                raw=data,
                model=model,
            )
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            if trace_id:
                log_flow(
                    stage="llm.error",
                    trace_id=trace_id,
                    purpose=purpose,
                    error_type="HTTPError",
                    error_code=exc.code,
                    detail=detail,
                )
            raise LLMRequestError(
                f"LLM 接口 HTTP 错误: {exc.code} {detail}",
                retryable=_is_retryable_http_status(exc.code),
                status_code=exc.code,
            ) from exc
        except error.URLError as exc:
            if trace_id:
                log_flow(
                    stage="llm.error",
                    trace_id=trace_id,
                    purpose=purpose,
                    error_type="URLError",
                    detail=str(exc.reason),
                )
            raise LLMRequestError(f"LLM 接口连接失败: {exc.reason}") from exc
        except TimeoutError as exc:
            if trace_id:
                log_flow(
                    stage="llm.error",
                    trace_id=trace_id,
                    purpose=purpose,
                    error_type="TimeoutError",
                    detail=str(exc),
                )
            raise LLMRequestError(f"LLM 接口超时: {exc}") from exc
        except IncompleteRead as exc:
            if trace_id:
                log_flow(
                    stage="llm.error",
                    trace_id=trace_id,
                    purpose=purpose,
                    error_type="IncompleteRead",
                    detail=str(exc),
                )
            raise LLMRequestError(f"LLM 响应中断: {exc}") from exc
        except RemoteDisconnected as exc:
            if trace_id:
                log_flow(
                    stage="llm.error",
                    trace_id=trace_id,
                    purpose=purpose,
                    error_type="RemoteDisconnected",
                    detail=str(exc),
                )
            raise LLMRequestError(f"LLM 连接被远端断开: {exc}") from exc
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            if trace_id:
                log_flow(
                    stage="llm.error",
                    trace_id=trace_id,
                    purpose=purpose,
                    error_type=type(exc).__name__,
                    detail=str(exc),
                )
            raise LLMRequestError("LLM 返回格式异常") from exc

    def _parse_tool_calls(self, assistant_message: dict[str, Any]) -> list[LLMToolCall]:
        raw_tool_calls = assistant_message.get("tool_calls", [])
        if not isinstance(raw_tool_calls, list):
            return []

        parsed: list[LLMToolCall] = []
        for index, item in enumerate(raw_tool_calls):
            if not isinstance(item, dict):
                continue
            function = item.get("function", {})
            if not isinstance(function, dict):
                function = {}
            name = str(function.get("name", "") or "").strip()
            if not name:
                continue
            parsed.append(
                LLMToolCall(
                    id=str(item.get("id", "") or f"tool_call_{index}"),
                    name=name,
                    arguments=str(function.get("arguments", "") or "{}"),
                )
            )
        return parsed

    def _normalize_assistant_message(
        self, assistant_message: dict[str, Any]
    ) -> dict[str, Any]:
        normalized = {
            "role": "assistant",
            "content": assistant_message.get("content", ""),
        }
        tool_calls = assistant_message.get("tool_calls", [])
        if isinstance(tool_calls, list) and tool_calls:
            normalized["tool_calls"] = tool_calls
        for field in ("reasoning_content", "reasoning", "reasoning_details"):
            if field in assistant_message:
                normalized[field] = assistant_message[field]
        return normalized


def _is_retryable_http_status(status_code: int) -> bool:
    return status_code in {408, 409, 425, 429} or status_code >= 500
