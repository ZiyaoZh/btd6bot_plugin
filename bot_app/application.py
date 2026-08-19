from __future__ import annotations

import asyncio
import traceback
from typing import Any
from uuid import uuid4

from .agent import Agent
from .artifact_store import ArtifactStore
from .db_manager import BotDatabase
from .harness.run_store import InMemoryRunStore
from .harness.sessions import SessionLockManager
from .harness.types import ModelTurnHandler, ModelTurnUpdate
from .llm_client import LLMClient, LLMRequestError
from .logging_utils import log_flow
from .protocol import EventPayload
from .receiver import (
    GroupMessageReceiver,
    has_message_content,
    redact_event_payload_for_log,
)
from .sender import MessageSender
from .settings import AppSettings

MODEL_TURN_MESSAGES_SETTING = "model_turn_messages_enabled"
BOT_ENABLED_SETTING = "bot_enabled"


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
        artifact_store: ArtifactStore | None = None,
        sessions: SessionLockManager | None = None,
        runs: InMemoryRunStore | None = None,
    ) -> None:
        self.settings = settings
        self._receiver = receiver
        self._sender = sender
        self._agent = agent
        self._llm = llm
        self._database = database
        self.artifact_store = artifact_store
        self._sessions = sessions or SessionLockManager()
        self._runs = runs or InMemoryRunStore()
        self._volatile_group_enabled_settings: dict[str, bool] = {}
        self._volatile_model_turn_settings: dict[str, bool] = {}
        self._media_tasks: set[asyncio.Task[None]] = set()

    @property
    def enabled(self) -> bool:
        return any(
            self.is_group_enabled(group_id)
            for group_id in self.settings.target_group_ids
        )

    def is_group_enabled(self, group_id: str) -> bool:
        if self._database is not None:
            return self._database.get_group_bool_setting(
                group_id,
                BOT_ENABLED_SETTING,
                default=True,
            )
        return self._volatile_group_enabled_settings.get(str(group_id), True)

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

        images = []
        if self._receiver.should_track_message(msg):
            images = self._receiver.sync_message_to_db(msg)
            log_flow(
                stage="db.synced",
                trace_id=trace_id,
                group_id=group_id,
                user_id=user_id,
            )

        accepted = self._receiver.should_accept(msg)
        if images and self.settings.vision_enabled:
            cache_call = self._cache_message_images(
                msg,
                images,
                trace_id=trace_id,
            )
            if accepted:
                await cache_call
            else:
                task = asyncio.create_task(cache_call)
                self._media_tasks.add(task)
                task.add_done_callback(self._media_tasks.discard)

        if not accepted:
            log_flow(
                stage="receiver.rejected", trace_id=trace_id, reason="message filtered"
            )
            return

        if await self._handle_admin_control(msg, trace_id=trace_id):
            return
        if not self.is_group_enabled(group_id):
            log_flow(
                stage="receiver.rejected",
                trace_id=trace_id,
                reason="bot is stopped in this group",
                group_id=group_id,
            )
            return

        text = self._receiver.normalize_text(msg.raw_message or "")
        if not has_message_content(text, getattr(msg, "message", None)):
            log_flow(
                stage="receiver.rejected",
                trace_id=trace_id,
                reason="empty text and no images after normalize",
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
        log_flow(
            stage="receiver.event_payload",
            trace_id=trace_id,
            event_payload=redact_event_payload_for_log(event),
        )
        await self.process_event(
            event=event,
            trace_id=trace_id,
            reply_group_id=group_id,
            at_user_id=user_id,
            send_failure_notice=True,
        )

    async def _cache_message_images(
        self,
        msg: Any,
        images: list[Any],
        *,
        trace_id: str,
    ) -> None:
        try:
            await asyncio.to_thread(self._receiver.cache_message_images, msg, images)
        except Exception as exc:
            log_flow(
                stage="media.cache_failed",
                trace_id=trace_id,
                group_id=str(msg.group_id),
                message_id=str(getattr(msg, "message_id", "") or ""),
                image_count=len(images),
                error_type=type(exc).__name__,
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
        if not self.is_group_enabled(reply_group_id):
            log_flow(
                stage="application.event_skipped",
                trace_id=trace_id,
                reason="bot is stopped in this group",
                group_id=reply_group_id,
                event_type=event["event_type"],
            )
            return

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
            if control["reason"].startswith("missing mention"):
                text = f"使用系统指令时请带@{self.settings.bot_name}"
            else:
                text = f"该指令仅限群主/管理员在带@{self.settings.bot_name}时使用"
        elif control["action"] == "stop":
            self._set_group_enabled(group_id, False)
            text = "已终止本群机器人对话处理"
        elif control["action"] == "start":
            self._set_group_enabled(group_id, True)
            text = "已启动本群机器人对话处理"
        elif control["action"] == "proactive_on":
            self._receiver.set_proactive_chat_enabled(group_id, True)
            text = "已开启本群主动接话"
        elif control["action"] == "proactive_off":
            self._receiver.set_proactive_chat_enabled(group_id, False)
            text = "已关闭本群主动接话，仅响应@消息"
        elif control["action"] == "model_turns_on":
            self._set_model_turn_messages_enabled(group_id, True)
            text = "已开启本群模型过程消息"
        elif control["action"] == "model_turns_off":
            self._set_model_turn_messages_enabled(group_id, False)
            text = "已关闭本群模型过程消息，仅发送最终结果"
        elif control["action"] == "system_commands":
            text = self._format_system_command_list(group_id)
        else:
            return False

        await self._sender.send_group_text(
            group_id=group_id,
            text=text,
            at_user_id=user_id,
        )
        log_flow(stage="sender.sent", trace_id=trace_id, reply=text)
        return True

    def _set_group_enabled(self, group_id: str, enabled: bool) -> None:
        if self._database is not None:
            self._database.set_group_setting(
                group_id,
                BOT_ENABLED_SETTING,
                "true" if enabled else "false",
            )
            return
        self._volatile_group_enabled_settings[str(group_id)] = bool(enabled)

    def _format_system_command_list(self, group_id: str) -> str:
        mention = f"@{self.settings.bot_name}"
        bot_status = "开启" if self.is_group_enabled(group_id) else "关闭"
        proactive_status = (
            "开启" if self._receiver.is_proactive_chat_enabled(group_id) else "关闭"
        )
        model_turn_status = (
            "开启" if self._model_turn_messages_enabled(group_id) else "关闭"
        )
        return "\n".join(
            [
                "本群系统指令（开关操作仅限群主/管理员）：",
                f"{mention} 启动 / 终止",
                f"{mention} 开启主动接话 / 关闭主动接话",
                f"{mention} 开启模型过程 / 关闭模型过程",
                f"{mention} 系统指令列表",
                "",
                "本群当前状态：",
                f"对话处理：{bot_status}",
                f"主动接话：{proactive_status}",
                f"模型过程消息：{model_turn_status}",
            ]
        )

    def _model_turn_messages_enabled(self, group_id: str) -> bool:
        if self._database is not None:
            return self._database.get_group_bool_setting(
                group_id,
                MODEL_TURN_MESSAGES_SETTING,
                default=False,
            )
        return self._volatile_model_turn_settings.get(str(group_id), False)

    def _set_model_turn_messages_enabled(self, group_id: str, enabled: bool) -> None:
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
