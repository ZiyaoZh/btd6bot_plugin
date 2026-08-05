from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from ncatbot.core import registrar
from ncatbot.event.qq import GroupMessageEvent
from ncatbot.plugin import NcatBotPlugin

from .bot_app.bootstrap import build_application
from .bot_app.logging_utils import log_flow
from .bot_app.settings import AppSettings, load_settings


LEGACY_DAILY_ACTIVITY_BRIEF_PROMPT = (
    "请生成今天的 BTD6 活动简报，并直接整理成适合发送到群里的晨间播报。"
    "你必须优先调用现有活动查询能力，自行完成信息收集后再给最终答复。"
    "请至少覆盖 race、boss、odyssey、daily 四类活动；某一类如果暂时查不到，要明确说明缺失，不要编造。"
    "输出要求：只输出纯文本，不要 Markdown、不要代码块、不要 JSON、不要工具名、不要文件路径。"
    "先用一句简短开场，再按活动分段概括名称、时间、关键规则或亮点。"
    "不要发送排行榜，不要图片，不要要求用户补充信息。"
    "整体保持简洁，控制在 600 字以内。"
)
DEFAULT_DAILY_ACTIVITY_BRIEF_PROMPT = (
    "请生成今天的 BTD6 每日活动早报。"
    "必须调用 get_activity_brief_image，并传入 activity_type=all、event_scope=current、difficulty=hard，"
    "用一张总览图覆盖竞速、Boss、征程和每日挑战；不要分别生成四张详图。"
    "总览图已经包含早报所需事实，不要再调用活动详情、排行榜或通用 AI 生图工具。"
    "工具成功后只输出一句简短自然的晨间图片说明，不要复述图中内容，不要输出文件路径。"
)


class _PluginBotProxy:
    def __init__(self, api) -> None:
        self.api = api


class Btd6BotPlugin(NcatBotPlugin):
    name = "btd6bot_plugin"
    version = "0.2.0"
    author = "btd6bot"
    description = (
        "BTD6 群聊智能插件：基于 Model + Harness 的原生工具编排、知识库检索与活动资讯。"
    )

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
        _set_if_relative("LLM_SYSTEM_PROMPT_FILE", "prompts/system_prompt.md")
        _set_if_relative("LLM_ROUTER_TOOL_HINTS_FILE", "prompts/router_tool_hints.md")
        _set_if_relative("BOT_FLOW_LOG_FILE", "logs/network.log", force=True)
        _set_if_relative("BOT_DB_FILE", "data/bot_data.db", force=True)

    async def on_load(self) -> None:
        self._prepare_plugin_paths()

        self._settings: AppSettings = load_settings()
        self._application = build_application(
            settings=self._settings,
            bot=_PluginBotProxy(self.api),
            plugin_root=Path(__file__).resolve().parent,
        )
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
        await self._application.handle_group_message(msg)

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

        if not self._application.enabled:
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
            await self._application.process_event(
                event=event_payload,
                trace_id=group_trace_id,
                reply_group_id=group_id,
                at_user_id=None,
                send_failure_notice=False,
            )

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
        if str(merged.get("prompt", "") or "").strip() == LEGACY_DAILY_ACTIVITY_BRIEF_PROMPT:
            merged["prompt"] = DEFAULT_DAILY_ACTIVITY_BRIEF_PROMPT
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
