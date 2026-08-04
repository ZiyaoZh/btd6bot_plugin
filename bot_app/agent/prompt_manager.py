from __future__ import annotations

import json

from ..artifact_store import ArtifactStore
from ..context_contracts import build_policy_hints_context
from ..protocol import EventPayload
from ..settings import AppSettings


class PromptManager:
    def __init__(
        self,
        settings: AppSettings,
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        self._settings = settings
        self._artifact_store = artifact_store

    def build_system_prompt(self, event: EventPayload) -> str:
        event_context = json.dumps(
            {
                "event_type": event["event_type"],
                "group_id": event["group_id"],
                "user_id": event["user_id"],
                "bot_mentioned": event["bot_mentioned"],
                "identity": event["identity"],
            },
            ensure_ascii=False,
        )
        policy_hints = build_policy_hints_context()
        tool_hints = (self._settings.router_tool_hints_prompt or "").strip()
        tool_hint_block = f"工具选择参考:\n{tool_hints}" if tool_hints else ""
        artifact_catalog = self._build_artifact_catalog()
        return "\n\n".join(
            item
            for item in [
                self._settings.system_prompt.strip(),
                (
                    "你正在群聊中工作，并通过系统提供的原生 tools 获取事实和执行能力。"
                    "需要事实、实时资料或程序能力时直接调用真实工具，不要在正文中伪造工具调用，也不要输出 JSON 决策协议。"
                    "工具返回后，根据 observation 判断是否继续调用工具；证据足够时直接输出最终纯文本。"
                    "普通群聊没有明确指向机器人或用户要求不要回复时，调用 ignore_message。"
                    "总原则：能可靠直接回答就回答；需要事实就依赖工具；信息不足时只问一个关键问题；不要编造。"
                    "当请求涉及可能已经获取或生成过的活动资料、报告、图片等产物时，"
                    "先阅读当前有效产物清单，按标题、摘要、标签、日期和文件角色做语义判断；"
                    "不要求标签完全一致。存在合适项时调用 read_artifact 直接取用，"
                    "没有合适项时再调用新的获取或生成工具。"
                    "模拟开箱是实时随机执行，不属于可复用产物；用户每次要求开箱时都调用 "
                    "simulate_monkey_box_opening 生成新结果。"
                    "BTD6 游戏知识必须先检索知识库，命中后读取候选文件；Harness 会确定性执行这一证据规则。"
                    "用户明确要求生成图片时，最终结果必须来自真实图片工具，不能只回复画面描述就结束。"
                    "短创意生图请求要自行补全可执行的主体、构图和视觉要求。"
                    "竞速或征程事实型情报图直接调用 get_activity_brief_image，"
                    "不要先把活动详情改写成 generate_image 提示词；"
                    "generate_image 只用于明确的同人插画、壁纸、头像、场景画等创意图片。"
                ),
                artifact_catalog,
                tool_hint_block,
                f"策略提示: {json.dumps(policy_hints, ensure_ascii=False)}",
                f"当前事件上下文: {event_context}",
            ]
            if item
        )

    def _build_artifact_catalog(self) -> str:
        if self._artifact_store is None:
            return ""
        try:
            records = self._artifact_store.list_active(limit=20)
        except Exception:
            return "当前有效产物清单暂时不可用；需要时可调用 list_artifacts 重试。"
        if not records:
            return "当前有效产物清单：暂无未过期产物。"

        lines = [
            "当前有效产物清单（动态目录；选中后调用 read_artifact，完整目录可调用 list_artifacts）："
        ]
        for record in records:
            tags = "、".join(record.tags) or "无"
            files = "、".join(
                f"{item.role}={item.path}" for item in record.files
            )
            summary = record.summary or "无摘要"
            if len(summary) > 180:
                summary = f"{summary[:179].rstrip()}…"
            lines.append(
                f"- [{record.artifact_id}] {record.title} | "
                f"类型={record.kind} | 摘要={summary} | 标签={tags} | "
                f"生成={record.created_at.strftime('%Y-%m-%d %H:%M UTC')} | "
                f"过期={record.expires_at.strftime('%Y-%m-%d %H:%M UTC')} | "
                f"文件={files}"
            )
        return "\n".join(lines)
