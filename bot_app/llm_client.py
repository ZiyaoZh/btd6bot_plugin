from __future__ import annotations

import asyncio
from http.client import IncompleteRead, RemoteDisconnected
import json
from threading import Lock
from urllib import error, request

from .logging_utils import log_flow
from .settings import AppSettings


class LLMRequestError(RuntimeError):
    """Raised when a request to LLM backend fails."""


class LLMClient:
    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._token_lock = Lock()
        self._trace_token_totals: dict[str, int] = {}

    async def chat(
        self,
        messages: list[dict[str, str]],
        trace_id: str = "",
        purpose: str = "chat",
    ) -> str:
        max_attempts = self._settings.llm_max_retries + 1
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                return await asyncio.to_thread(self._chat_sync, messages, trace_id, purpose)
            except LLMRequestError as exc:
                last_error = exc
                if trace_id:
                    log_flow(
                        stage="llm.retry",
                        trace_id=trace_id,
                        purpose=purpose,
                        attempt=attempt,
                        max_attempts=max_attempts,
                        error=str(exc),
                    )
                if attempt >= max_attempts:
                    break
                await asyncio.sleep(min(0.5 * attempt, 2.0))

        if last_error is not None:
            raise last_error
        raise LLMRequestError("LLM 请求失败")

    def pop_trace_total_tokens(self, trace_id: str) -> int:
        if not trace_id:
            return 0
        with self._token_lock:
            return int(self._trace_token_totals.pop(trace_id, 0) or 0)

    def _chat_sync(self, messages: list[dict[str, str]], trace_id: str, purpose: str) -> str:
        url = f"{self._settings.api_base_url}/chat/completions"
        payload = {
            "model": self._settings.model,
            "messages": messages,
            "temperature": 0.7,
        }

        if trace_id:
            log_flow(
                stage="llm.request",
                trace_id=trace_id,
                purpose=purpose,
                model=self._settings.model,
                api_base_url=self._settings.api_base_url,
                payload=payload,
            )

        body = json.dumps(payload).encode("utf-8")

        req = request.Request(
            url=url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._settings.api_key}",
            },
        )

        try:
            with request.urlopen(req, timeout=self._settings.request_timeout) as resp:
                raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            content = data["choices"][0]["message"]["content"]
            answer = str(content).strip()
            usage = data.get("usage", {}) if isinstance(data, dict) else {}
            total_tokens_raw = usage.get("total_tokens", 0) if isinstance(usage, dict) else 0
            try:
                total_tokens = int(total_tokens_raw)
            except (TypeError, ValueError):
                total_tokens = 0

            if trace_id and total_tokens > 0:
                with self._token_lock:
                    self._trace_token_totals[trace_id] = self._trace_token_totals.get(trace_id, 0) + total_tokens

            if trace_id:
                log_flow(
                    stage="llm.response",
                    trace_id=trace_id,
                    purpose=purpose,
                    response=data,
                    answer=answer,
                    total_tokens=total_tokens,
                )
            return answer
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
            raise LLMRequestError(f"LLM 接口 HTTP 错误: {exc.code} {detail}") from exc
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
