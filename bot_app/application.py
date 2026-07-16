from __future__ import annotations

import asyncio
import re
import traceback
from pathlib import Path
from typing import Any
from uuid import uuid4

from .agent import Agent
from .db_manager import BotDatabase
from .harness.run_store import InMemoryRunStore
from .harness.sessions import SessionLockManager
from .harness.types import ModelTurnHandler, ModelTurnUpdate
from .llm_client import LLMClient, LLMRequestError
from .logging_utils import log_flow
from .protocol import EventPayload
from .receiver import GroupMessageReceiver
from .sender import MessageSender
from .settings import AppSettings


MODEL_TURN_MESSAGES_SETTING = "model_turn_messages_enabled"


class BotApplication:
    """Channel-neutral application boundary around the Agent transaction."""

    def __init__(
        self,
        *,
        settings: AppSettings,
        receiver: GroupMessageReceiver,
        sender: MessageSender,
        agent: Agent,
        llm: LLMClient,
        database: BotDatabase | None = None,
        sessions: SessionLockManager | None = None,
        runs: InMemoryRunStore | None = None,
    ) -> None:
        self.settings = settings
        self._receiver = receiver
        self._sender = sender
        self._agent = agent
        self._llm = llm
        self._database = database
        self._sessions = sessions or SessionLockManager()
        self._runs = runs or InMemoryRunStore()
        self._enabled = True
        self._volatile_model_turn_settings: dict[str, bool] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def handle_group_message(self, msg: Any) -> None:
        trace_id = uuid4().hex[:12]
        group_id = str(msg.group_id)
        user_id = str(msg.user_id)
        log_flow(
            stage="receiver.raw_message",
            trace_id=trace_id,
            raw_message=msg.raw_message,
            group_id=group_id,
            user_id=user_id,
        )

        if self._receiver.should_track_message(msg):
            self._receiver.sync_message_to_db(msg)
            log_flow(
                stage="db.synced",
                trace_id=trace_id,
                group_id=group_id,
                user_id=user_id,
            )

        if not self._receiver.should_accept(msg):
            log_flow(
                stage="receiver.rejected", trace_id=trace_id, reason="message filtered"
            )
            return

        if await self._handle_admin_control(msg, trace_id=trace_id):
            return
        if not self._enabled:
            log_flow(
                stage="receiver.rejected", trace_id=trace_id, reason="bot is stopped"
            )
            return

        text = self._receiver.normalize_text(msg.raw_message or "")
        if not text:
            log_flow(
                stage="receiver.rejected",
                trace_id=trace_id,
                reason="empty text after normalize",
            )
            return

        if text in {"/reset", "#reset", "重置会话"}:
            session_id = f"{group_id}:{user_id}"
            async with self._sessions.hold(session_id):
                self._agent.reset_user_context(group_id, user_id)
            await self._sender.send_group_text(
                group_id=group_id,
                text="你的会话上下文已重置",
                at_user_id=user_id,
            )
            log_flow(
                stage="memory.reset",
                trace_id=trace_id,
                group_id=group_id,
                user_id=user_id,
            )
            return

        event = self._receiver.build_event_payload(msg, text)
        log_flow(stage="receiver.event_payload", trace_id=trace_id, event_payload=event)
        await self.process_event(
            event=event,
            trace_id=trace_id,
            reply_group_id=group_id,
            at_user_id=user_id,
            send_failure_notice=True,
        )

    async def process_event(
        self,
        *,
        event: EventPayload,
        trace_id: str,
        reply_group_id: str,
        at_user_id: str | None,
        send_failure_notice: bool,
    ) -> None:
        session_id = f"{event['group_id']}:{event['user_id']}"
        show_model_turns = self._model_turn_messages_enabled(reply_group_id)
        self._runs.start(trace_id, session_id)
        try:
            async with self._sessions.hold(session_id):
                self._runs.transition(trace_id, "running")
                on_model_turn = (
                    self._build_model_turn_handler(
                        trace_id=trace_id,
                        group_id=reply_group_id,
                        at_user_id=at_user_id,
                    )
                    if show_model_turns
                    else None
                )
                response = await asyncio.wait_for(
                    self._agent.run(
                        event,
                        trace_id=trace_id,
                        commit=False,
                        on_model_turn=on_model_turn,
                    ),
                    timeout=self.settings.run_timeout_seconds,
                )
                self._runs.transition(
                    trace_id,
                    "generated",
                    terminal_reason=response.terminal_reason,
                    should_send=response.should_send,
                )
                if not response.should_send:
                    self._runs.transition(trace_id, "no_reply")
                    log_flow(stage="agent.noop", trace_id=trace_id)
                    return

                final_reply = response.text[: self.settings.max_reply_chars]
                if response.response_type == "image" and response.image_path:
                    await self._sender.send_group_image(
                        group_id=reply_group_id,
                        image_path=response.image_path,
                        text=final_reply,
                        at_user_id=at_user_id,
                    )
                    log_flow(
                        stage="sender.sent",
                        trace_id=trace_id,
                        reply=final_reply,
                        image_path=response.image_path,
                    )
                else:
                    await self._sender.send_group_text(
                        group_id=reply_group_id,
                        text=final_reply,
                        at_user_id=at_user_id,
                    )
                    log_flow(stage="sender.sent", trace_id=trace_id, reply=final_reply)

                self._runs.transition(trace_id, "delivered")
                await self._agent.commit_turn(event, response, trace_id=trace_id)
                self._runs.transition(trace_id, "committed")
        except Exception as exc:
            self._runs.transition(
                trace_id,
                "failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            log_flow(
                stage="pipeline.error",
                trace_id=trace_id,
                error_type=type(exc).__name__,
                error=str(exc),
                traceback=traceback.format_exc(),
            )
            if isinstance(exc, LLMRequestError):
                log_flow(
                    stage="pipeline.silent_drop",
                    trace_id=trace_id,
                    reason="llm_request_failed_after_retries",
                )
                return
            if not send_failure_notice:
                return
            public_error = (
                "请求处理超时，请稍后重试。"
                if isinstance(exc, asyncio.TimeoutError)
                else f"请求处理失败: {type(exc).__name__}"
            )
            await self._sender.send_group_text(
                group_id=reply_group_id,
                text=public_error,
                at_user_id=at_user_id,
            )
            log_flow(stage="sender.sent", trace_id=trace_id, reply=public_error)
        finally:
            total_tokens = self._llm.pop_trace_total_tokens(trace_id)
            log_flow(
                stage="pipeline.finalized",
                trace_id=trace_id,
                total_tokens=total_tokens,
                run_status=(
                    self._runs.get(trace_id).status
                    if self._runs.get(trace_id)
                    else "unknown"
                ),
            )

    async def _handle_admin_control(self, msg: Any, *, trace_id: str) -> bool:
        control = self._receiver.parse_admin_control(msg)
        if not control["matched"]:
            return False
        log_flow(stage="receiver.admin_control", trace_id=trace_id, control=control)
        group_id = str(msg.group_id)
        user_id = str(msg.user_id)
        if not control["authorized"]:
            text = f"该指令仅限群主/管理员在带@{self.settings.bot_name}时使用"
        elif control["action"] == "stop":
            self._enabled = False
            text = "已终止机器人对话处理"
        elif control["action"] == "start":
            self._enabled = True
            text = "已启动机器人对话处理"
        elif control["action"] == "proactive_on":
            self._receiver.set_proactive_chat_enabled(True)
            _persist_env_bool(
                self.settings.env_file, "BOT_PROACTIVE_CHAT_ENABLED", True
            )
            text = "已开启主动接话"
        elif control["action"] == "proactive_off":
            self._receiver.set_proactive_chat_enabled(False)
            _persist_env_bool(
                self.settings.env_file, "BOT_PROACTIVE_CHAT_ENABLED", False
            )
            text = "已关闭主动接话，仅响应@消息"
        elif control["action"] == "model_turns_on":
            self._set_model_turn_messages_enabled(group_id, True)
            text = "已开启本群模型过程消息"
        elif control["action"] == "model_turns_off":
            self._set_model_turn_messages_enabled(group_id, False)
            text = "已关闭本群模型过程消息，仅发送最终结果"
        else:
            return False

        await self._sender.send_group_text(
            group_id=group_id,
            text=text,
            at_user_id=user_id,
        )
        log_flow(stage="sender.sent", trace_id=trace_id, reply=text)
        return True

    def _model_turn_messages_enabled(self, group_id: str) -> bool:
        if self._database is not None:
            return self._database.get_group_bool_setting(
                group_id,
                MODEL_TURN_MESSAGES_SETTING,
                default=False,
            )
        return self._volatile_model_turn_settings.get(str(group_id), False)

    def _set_model_turn_messages_enabled(
        self, group_id: str, enabled: bool
    ) -> None:
        if self._database is not None:
            self._database.set_group_setting(
                group_id,
                MODEL_TURN_MESSAGES_SETTING,
                "true" if enabled else "false",
            )
            return
        self._volatile_model_turn_settings[str(group_id)] = bool(enabled)

    def _build_model_turn_handler(
        self,
        *,
        trace_id: str,
        group_id: str,
        at_user_id: str | None,
    ) -> ModelTurnHandler:
        async def _send(update: ModelTurnUpdate) -> None:
            # The terminal model turn is delivered through the normal final-response path.
            if update.is_final:
                return
            text = _format_model_turn(update, max_chars=self.settings.max_reply_chars)
            if not text:
                return
            try:
                await self._sender.send_group_text(
                    group_id=group_id,
                    text=text,
                    at_user_id=at_user_id,
                )
                log_flow(
                    stage="sender.model_turn.sent",
                    trace_id=trace_id,
                    model_call=update.index,
                    reply=text,
                )
            except Exception as exc:
                log_flow(
                    stage="sender.model_turn.failed",
                    trace_id=trace_id,
                    model_call=update.index,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )

        return _send


def _format_model_turn(update: ModelTurnUpdate, *, max_chars: int) -> str:
    return update.content.strip()[: max(1, int(max_chars))]


def _persist_env_bool(env_file: str, key: str, enabled: bool) -> None:
    path = Path(env_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    value = "true" if enabled else "false"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    updated = False
    new_lines: list[str] = []
    for line in lines:
        if pattern.match(line):
            new_lines.append(f"{key}={value}")
            updated = True
        else:
            new_lines.append(line)
    if not updated:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.append(f"{key}={value}")
    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
