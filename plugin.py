from __future__ import annotations

import os
import re
import traceback
from pathlib import Path
from uuid import uuid4

from ncatbot.core import registrar
from ncatbot.event.qq import GroupMessageEvent
from ncatbot.plugin import NcatBotPlugin

from .bot_app.command_processor import CommandProcessor
from .bot_app.db_manager import BotDatabase
from .bot_app.llm_client import LLMClient, LLMRequestError
from .bot_app.llm_processor import LLMMessageProcessor
from .bot_app.logging_utils import configure_flow_logger, log_flow
from .bot_app.memory import GroupChatMemory
from .bot_app.receiver import GroupMessageReceiver
from .bot_app.sender import MessageSender
from .bot_app.settings import AppSettings, load_settings
from .bot_app.tool_selector import ToolSelector


class _PluginBotProxy:
    def __init__(self, api) -> None:
        self.api = api


class Btd6BotPlugin(NcatBotPlugin):
    name = "btd6bot_plugin"
    version = "0.1.0"
    author = "btd6bot"
    description = "BTD6 群聊智能插件：支持路由决策、多轮工具编排、知识库检索、活动资讯/排行榜与消息持久化。"

    def _prepare_plugin_paths(self) -> None:
        plugin_root = Path(__file__).resolve().parent
        logs_dir = plugin_root / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        def _set_if_relative(key: str, relative_path: str, force: bool = False) -> None:
            raw = os.getenv(key, "").strip()
            if not force and raw and Path(raw).is_absolute():
                return
            os.environ[key] = str(plugin_root / relative_path)

        _set_if_relative("BOT_MANUAL_FILE", "manuals/program_manual.md")
        _set_if_relative("BOT_COMMAND_GUIDE_DIR", "command_skills")
        _set_if_relative("LLM_SYSTEM_PROMPT_FILE", "prompts/system_prompt.md")
        _set_if_relative("LLM_ROUTER_TOOL_HINTS_FILE", "prompts/router_tool_hints.md")
        _set_if_relative("BOT_FLOW_LOG_FILE", "logs/network.log", force=True)
        _set_if_relative("BOT_DB_FILE", "data/bot_data.db", force=True)

    async def on_load(self) -> None:
        self._prepare_plugin_paths()

        self._settings: AppSettings = load_settings()
        configure_flow_logger(self._settings.flow_log_file)

        db = BotDatabase(self._settings.db_file)
        self._receiver = GroupMessageReceiver(self._settings, db=db)
        self._sender = MessageSender(_PluginBotProxy(self.api))
        memory = GroupChatMemory(history_turns=self._settings.history_turns)
        self._llm = LLMClient(settings=self._settings)
        self._llm_processor = LLMMessageProcessor(
            llm=self._llm,
            memory=memory,
            settings=self._settings,
        )
        self._tool_selector = ToolSelector()
        self._command_processor = CommandProcessor(
            db=db,
            manual_file=self._settings.manual_file,
            command_guide_dir=self._settings.command_guide_dir,
        )
        self._bot_enabled = True
        lifecycle_trace_id = f"{self.name}.lifecycle"

        log_flow(
            stage="plugin.loaded",
            trace_id=lifecycle_trace_id,
            plugin=self.name,
            target_groups=list(self._settings.target_group_ids),
        )

    async def on_close(self) -> None:
        log_flow(stage="plugin.closed", trace_id=f"{self.name}.lifecycle", plugin=self.name)

    @registrar.qq.on_group_message()
    async def on_group_message(self, msg: GroupMessageEvent):
        trace_id = uuid4().hex[:12]
        log_flow(
            stage="receiver.raw_message",
            trace_id=trace_id,
            raw_message=msg.raw_message,
            group_id=str(msg.group_id),
            user_id=str(msg.user_id),
        )

        if self._receiver.should_track_message(msg):
            self._receiver.sync_message_to_db(msg)
            log_flow(stage="db.synced", trace_id=trace_id, group_id=str(msg.group_id), user_id=str(msg.user_id))

        if not self._receiver.should_accept(msg):
            log_flow(stage="receiver.rejected", trace_id=trace_id, reason="message filtered")
            return

        admin_control = self._receiver.parse_admin_control(msg)
        if admin_control["matched"]:
            log_flow(stage="receiver.admin_control", trace_id=trace_id, control=admin_control)

            if not admin_control["authorized"]:
                await self._sender.send_group_text(
                    group_id=str(msg.group_id),
                    text=f"该指令仅限群主/管理员在带@{self._settings.bot_name}时使用",
                    at_user_id=str(msg.user_id),
                )
                log_flow(
                    stage="sender.sent",
                    trace_id=trace_id,
                    reply=f"该指令仅限群主/管理员在带@{self._settings.bot_name}时使用",
                )
                return

            if admin_control["action"] == "stop":
                self._bot_enabled = False
                await self._sender.send_group_text(
                    group_id=str(msg.group_id),
                    text="已终止机器人对话处理",
                    at_user_id=str(msg.user_id),
                )
                log_flow(stage="sender.sent", trace_id=trace_id, reply="已终止机器人对话处理")
                return

            if admin_control["action"] == "start":
                self._bot_enabled = True
                await self._sender.send_group_text(
                    group_id=str(msg.group_id),
                    text="已启动机器人对话处理",
                    at_user_id=str(msg.user_id),
                )
                log_flow(stage="sender.sent", trace_id=trace_id, reply="已启动机器人对话处理")
                return

            if admin_control["action"] == "proactive_on":
                self._receiver.set_proactive_chat_enabled(True)
                _persist_env_bool(self._settings.env_file, "BOT_PROACTIVE_CHAT_ENABLED", True)
                await self._sender.send_group_text(
                    group_id=str(msg.group_id),
                    text="已开启主动接话",
                    at_user_id=str(msg.user_id),
                )
                log_flow(stage="sender.sent", trace_id=trace_id, reply="已开启主动接话")
                return

            if admin_control["action"] == "proactive_off":
                self._receiver.set_proactive_chat_enabled(False)
                _persist_env_bool(self._settings.env_file, "BOT_PROACTIVE_CHAT_ENABLED", False)
                await self._sender.send_group_text(
                    group_id=str(msg.group_id),
                    text="已关闭主动接话，仅响应@消息",
                    at_user_id=str(msg.user_id),
                )
                log_flow(stage="sender.sent", trace_id=trace_id, reply="已关闭主动接话，仅响应@消息")
                return

        if not self._bot_enabled:
            log_flow(stage="receiver.rejected", trace_id=trace_id, reason="bot is stopped")
            return

        text = self._receiver.normalize_text(msg.raw_message or "")
        if not text:
            log_flow(stage="receiver.rejected", trace_id=trace_id, reason="empty text after normalize")
            return

        if text in {"/reset", "#reset", "重置会话"}:
            self._llm_processor.reset_user_context(str(msg.group_id), str(msg.user_id))
            log_flow(stage="memory.reset", trace_id=trace_id, group_id=str(msg.group_id), user_id=str(msg.user_id))
            await self._sender.send_group_text(
                group_id=str(msg.group_id),
                text="你的会话上下文已重置",
                at_user_id=str(msg.user_id),
            )
            log_flow(stage="sender.sent", trace_id=trace_id, reply="你的会话上下文已重置")
            return

        event_payload = self._receiver.build_event_payload(msg, text)
        log_flow(stage="receiver.event_payload", trace_id=trace_id, event_payload=event_payload)

        try:
            intent = await self._llm_processor.plan_intent(event_payload, trace_id=trace_id)
            log_flow(stage="judge.route", trace_id=trace_id, target=intent["target"], intent=intent)

            if intent["target"] == "noop":
                log_flow(stage="judge.noop", trace_id=trace_id, reason=intent.get("reason", ""))
                return

            if intent["target"] in {"tool_selector", "command_processor"}:
                if intent["target"] == "tool_selector":
                    guide_bundle = await self._tool_selector.load_tool_guides(intent, event_payload, self._command_processor)
                    log_flow(stage="tool_selector.bundle", trace_id=trace_id, guide_bundle=guide_bundle)
                    tool_history: list[dict] = [{"intent": intent, "command_result": guide_bundle}]
                else:
                    command_result = await self._command_processor.execute(intent, event_payload)
                    log_flow(stage="command.result", trace_id=trace_id, command_result=command_result)
                    tool_history = [{"intent": intent, "command_result": command_result}]

                final_reply = ""

                for round_index in range(self._settings.max_tool_rounds):
                    decision = await self._llm_processor.decide_next_tool_step(
                        event_payload,
                        tool_history,
                        trace_id=trace_id,
                    )
                    log_flow(
                        stage="tool.decision",
                        trace_id=trace_id,
                        round=round_index + 1,
                        decision=decision,
                    )

                    if decision["decision"] == "final":
                        final_reply = str(decision.get("final_reply", "") or "").strip()
                        break

                    next_intent = decision["intent"]
                    if next_intent.get("target") == "tool_selector":
                        guide_bundle = await self._tool_selector.load_tool_guides(
                            next_intent,
                            event_payload,
                            self._command_processor,
                        )
                        log_flow(stage="tool_selector.bundle", trace_id=trace_id, guide_bundle=guide_bundle)
                        tool_history.append({"intent": next_intent, "command_result": guide_bundle})
                    else:
                        command_result = await self._command_processor.execute(next_intent, event_payload)
                        log_flow(stage="command.result", trace_id=trace_id, command_result=command_result)
                        tool_history.append({"intent": next_intent, "command_result": command_result})

                if not final_reply:
                    final_reply = await self._llm_processor.render_command_result(
                        event_payload,
                        tool_history[-1]["intent"],
                        tool_history[-1]["command_result"],
                        trace_id=trace_id,
                    )

                last_result = tool_history[-1].get("command_result", {}) if tool_history else {}
                response_type = str(last_result.get("response_type", "text") or "text").strip().lower()
                image_path = str(last_result.get("image_path", "") or "").strip()
            else:
                final_reply = await self._llm_processor.chat_reply(event_payload, trace_id=trace_id)
                response_type = "text"
                image_path = ""

            total_tokens = self._llm.pop_trace_total_tokens(trace_id)
            final_reply = _append_token_cost_line(final_reply, total_tokens, self._settings.max_reply_chars)

            if response_type == "image" and image_path:
                await self._sender.send_group_image(
                    group_id=event_payload["group_id"],
                    image_path=image_path,
                    text=final_reply,
                    at_user_id=event_payload["user_id"],
                )
                log_flow(stage="sender.sent", trace_id=trace_id, reply=final_reply, image_path=image_path)
            else:
                await self._sender.send_group_text(
                    group_id=event_payload["group_id"],
                    text=final_reply,
                    at_user_id=event_payload["user_id"],
                )
                log_flow(stage="sender.sent", trace_id=trace_id, reply=final_reply)
        except Exception as exc:
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

            public_error = f"请求处理失败: {type(exc).__name__}"
            await self._sender.send_group_text(
                group_id=str(msg.group_id),
                text=public_error,
                at_user_id=str(msg.user_id),
            )
            log_flow(stage="sender.sent", trace_id=trace_id, reply=public_error)


def _append_token_cost_line(text: str, total_tokens: int, max_chars: int) -> str:
    base = (text or "").strip()
    cost_line = f"本次花费{max(0, int(total_tokens))}tokens"
    combined = f"{base}\n{cost_line}" if base else cost_line

    if len(combined) <= max_chars:
        return combined

    kept_cost = f"\n{cost_line}"
    head_limit = max(0, max_chars - len(kept_cost))
    trimmed = base[:head_limit].rstrip()
    return f"{trimmed}{kept_cost}" if trimmed else cost_line[:max_chars]


def _persist_env_bool(env_file: str, key: str, enabled: bool) -> None:
    path = Path(env_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    value = "true" if enabled else "false"

    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

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
        if new_lines and new_lines[-1].strip() != "":
            new_lines.append("")
        new_lines.append(f"{key}={value}")

    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
