from __future__ import annotations

import os
import re
import traceback
from pathlib import Path
from typing import Any
from uuid import uuid4

from ncatbot.core import registrar
from ncatbot.event.qq import GroupMessageEvent
from ncatbot.plugin import NcatBotPlugin

from .bot_app.db_manager import BotDatabase
from .bot_app.llm_client import LLMClient, LLMRequestError
from .bot_app.agent import AgentRunner
from .bot_app.agent.tool_registry import ToolRegistry
from .bot_app.logging_utils import configure_flow_logger, log_flow
from .bot_app.memory import GroupChatMemory
from .bot_app.receiver import GroupMessageReceiver
from .bot_app.sender import MessageSender
from .bot_app.settings import AppSettings, load_settings
from .bot_app.tools import ToolRuntime


DEFAULT_DAILY_ACTIVITY_BRIEF_PROMPT = (
    "请生成今天的 BTD6 活动简报，并直接整理成适合发送到群里的晨间播报。"
    "你必须优先调用现有活动查询能力，自行完成信息收集后再给最终答复。"
    "请至少覆盖 race、boss、odyssey、daily 四类活动；某一类如果暂时查不到，要明确说明缺失，不要编造。"
    "输出要求：只输出纯文本，不要 Markdown、不要代码块、不要 JSON、不要工具名、不要文件路径。"
    "先用一句简短开场，再按活动分段概括名称、时间、关键规则或亮点。"
    "不要发送排行榜，不要图片，不要要求用户补充信息。"
    "整体保持简洁，控制在 600 字以内。"
)


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
        tool_runtime = ToolRuntime.create(
            db=db,
            plugin_root=Path(__file__).resolve().parent,
            manual_file=self._settings.manual_file,
            command_guide_dir=self._settings.command_guide_dir,
        )
        self._agent = AgentRunner(
            llm=self._llm,
            memory=memory,
            settings=self._settings,
            tool_registry=ToolRegistry(tool_runtime),
        )
        self._bot_enabled = True
        self._daily_activity_brief_config = self._load_daily_activity_brief_config()
        self._register_daily_activity_brief_task()
        lifecycle_trace_id = f"{self.name}.lifecycle"

        log_flow(
            stage="plugin.loaded",
            trace_id=lifecycle_trace_id,
            plugin=self.name,
            target_groups=list(self._settings.target_group_ids),
        )

    async def on_close(self) -> None:
        log_flow(
            stage="plugin.closed", trace_id=f"{self.name}.lifecycle", plugin=self.name
        )

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
            log_flow(
                stage="db.synced",
                trace_id=trace_id,
                group_id=str(msg.group_id),
                user_id=str(msg.user_id),
            )

        if not self._receiver.should_accept(msg):
            log_flow(
                stage="receiver.rejected", trace_id=trace_id, reason="message filtered"
            )
            return

        admin_control = self._receiver.parse_admin_control(msg)
        if admin_control["matched"]:
            log_flow(
                stage="receiver.admin_control", trace_id=trace_id, control=admin_control
            )

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
                log_flow(
                    stage="sender.sent", trace_id=trace_id, reply="已终止机器人对话处理"
                )
                return

            if admin_control["action"] == "start":
                self._bot_enabled = True
                await self._sender.send_group_text(
                    group_id=str(msg.group_id),
                    text="已启动机器人对话处理",
                    at_user_id=str(msg.user_id),
                )
                log_flow(
                    stage="sender.sent", trace_id=trace_id, reply="已启动机器人对话处理"
                )
                return

            if admin_control["action"] == "proactive_on":
                self._receiver.set_proactive_chat_enabled(True)
                _persist_env_bool(
                    self._settings.env_file, "BOT_PROACTIVE_CHAT_ENABLED", True
                )
                await self._sender.send_group_text(
                    group_id=str(msg.group_id),
                    text="已开启主动接话",
                    at_user_id=str(msg.user_id),
                )
                log_flow(stage="sender.sent", trace_id=trace_id, reply="已开启主动接话")
                return

            if admin_control["action"] == "proactive_off":
                self._receiver.set_proactive_chat_enabled(False)
                _persist_env_bool(
                    self._settings.env_file, "BOT_PROACTIVE_CHAT_ENABLED", False
                )
                await self._sender.send_group_text(
                    group_id=str(msg.group_id),
                    text="已关闭主动接话，仅响应@消息",
                    at_user_id=str(msg.user_id),
                )
                log_flow(
                    stage="sender.sent",
                    trace_id=trace_id,
                    reply="已关闭主动接话，仅响应@消息",
                )
                return

        if not self._bot_enabled:
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
            self._agent.reset_user_context(str(msg.group_id), str(msg.user_id))
            log_flow(
                stage="memory.reset",
                trace_id=trace_id,
                group_id=str(msg.group_id),
                user_id=str(msg.user_id),
            )
            await self._sender.send_group_text(
                group_id=str(msg.group_id),
                text="你的会话上下文已重置",
                at_user_id=str(msg.user_id),
            )
            log_flow(
                stage="sender.sent", trace_id=trace_id, reply="你的会话上下文已重置"
            )
            return

        event_payload = self._receiver.build_event_payload(msg, text)
        log_flow(
            stage="receiver.event_payload",
            trace_id=trace_id,
            event_payload=event_payload,
        )

        await self._process_event_payload(
            event_payload=event_payload,
            trace_id=trace_id,
            reply_group_id=str(msg.group_id),
            at_user_id=str(msg.user_id),
            include_token_cost=True,
            send_failure_notice=True,
        )

    async def daily_activity_brief_task(self) -> None:
        trace_id = uuid4().hex[:12]
        config = self._load_daily_activity_brief_config()
        enabled = bool(config.get("enabled", True))
        prompt = (
            str(config.get("prompt", "") or "").strip()
            or DEFAULT_DAILY_ACTIVITY_BRIEF_PROMPT
        )
        group_ids = self._resolve_daily_activity_brief_group_ids(config)

        log_flow(
            stage="schedule.daily_activity_brief.triggered",
            trace_id=trace_id,
            enabled=enabled,
            target_groups=group_ids,
        )

        if not enabled:
            log_flow(
                stage="schedule.daily_activity_brief.skipped",
                trace_id=trace_id,
                reason="disabled by config",
            )
            return

        if not self._bot_enabled:
            log_flow(
                stage="schedule.daily_activity_brief.skipped",
                trace_id=trace_id,
                reason="bot is stopped",
            )
            return

        for group_id in group_ids:
            event_payload = self._build_scheduled_event_payload(
                group_id=group_id, prompt=prompt
            )
            group_trace_id = f"{trace_id}.{group_id}"
            log_flow(
                stage="schedule.daily_activity_brief.event_payload",
                trace_id=group_trace_id,
                event_payload=event_payload,
            )
            await self._process_event_payload(
                event_payload=event_payload,
                trace_id=group_trace_id,
                reply_group_id=group_id,
                at_user_id=None,
                include_token_cost=False,
                send_failure_notice=False,
            )

    async def _process_event_payload(
        self,
        event_payload: dict[str, Any],
        trace_id: str,
        reply_group_id: str,
        at_user_id: str | None,
        include_token_cost: bool,
        send_failure_notice: bool,
    ) -> None:
        try:
            agent_response = await self._agent.run(event_payload, trace_id=trace_id)
            if not agent_response.should_send:
                log_flow(stage="agent.noop", trace_id=trace_id)
                return

            reply_for_memory = agent_response.text
            if include_token_cost:
                final_reply = _append_token_cost_line(
                    reply_for_memory,
                    agent_response.token_usage,
                    self._settings.max_reply_chars,
                )
                self._llm.pop_trace_total_tokens(trace_id)
            else:
                final_reply = reply_for_memory[: self._settings.max_reply_chars]
                self._llm.pop_trace_total_tokens(trace_id)

            if agent_response.response_type == "image" and agent_response.image_path:
                await self._sender.send_group_image(
                    group_id=reply_group_id,
                    image_path=agent_response.image_path,
                    text=final_reply,
                    at_user_id=at_user_id,
                )
                log_flow(
                    stage="sender.sent",
                    trace_id=trace_id,
                    reply=final_reply,
                    image_path=agent_response.image_path,
                )
            else:
                await self._sender.send_group_text(
                    group_id=reply_group_id,
                    text=final_reply,
                    at_user_id=at_user_id,
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

            if not send_failure_notice:
                return

            public_error = f"请求处理失败: {type(exc).__name__}"
            await self._sender.send_group_text(
                group_id=reply_group_id,
                text=public_error,
                at_user_id=at_user_id,
            )
            log_flow(stage="sender.sent", trace_id=trace_id, reply=public_error)

    def _load_daily_activity_brief_config(self) -> dict[str, Any]:
        defaults = {
            "enabled": True,
            "schedule": "07:00",
            "prompt": DEFAULT_DAILY_ACTIVITY_BRIEF_PROMPT,
            "group_ids": [],
        }
        raw = self.get_config("daily_activity_brief", {})
        config = raw if isinstance(raw, dict) else {}
        merged = {**defaults, **config}
        group_ids = merged.get("group_ids", [])
        if not isinstance(group_ids, list):
            group_ids = []
        merged["group_ids"] = [
            str(group_id).strip() for group_id in group_ids if str(group_id).strip()
        ]

        if merged != config:
            self.set_config("daily_activity_brief", merged)

        return merged

    def _register_daily_activity_brief_task(self) -> None:
        enabled = bool(self._daily_activity_brief_config.get("enabled", True))
        schedule = (
            str(self._daily_activity_brief_config.get("schedule", "") or "").strip()
            or "07:00"
        )
        if not enabled:
            log_flow(
                stage="schedule.daily_activity_brief.disabled",
                trace_id=f"{self.name}.daily_activity_brief",
                schedule=schedule,
            )
            return

        added = self.add_scheduled_task("daily_activity_brief_task", schedule)
        log_flow(
            stage="schedule.daily_activity_brief.registered",
            trace_id=f"{self.name}.daily_activity_brief",
            enabled=enabled,
            schedule=schedule,
            added=added,
            target_groups=self._resolve_daily_activity_brief_group_ids(
                self._daily_activity_brief_config
            ),
        )

    def _resolve_daily_activity_brief_group_ids(
        self, config: dict[str, Any]
    ) -> list[str]:
        configured = config.get("group_ids", [])
        if isinstance(configured, list):
            group_ids = [
                str(group_id).strip()
                for group_id in configured
                if str(group_id).strip()
            ]
            if group_ids:
                return group_ids
        return list(self._settings.target_group_ids)

    def _build_scheduled_event_payload(
        self, group_id: str, prompt: str
    ) -> dict[str, Any]:
        return {
            "module": "scheduler",
            "event_type": "scheduled_daily_activity_brief",
            "group_id": group_id,
            "user_id": "__daily_activity_brief__",
            "text": prompt,
            "bot_mentioned": True,
            "mentioned_qqs": [],
            "identity": {
                "nickname": "系统定时任务",
                "card": "",
                "role": "system",
                "title": "",
                "level": "",
            },
        }


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
