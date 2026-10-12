from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from test_agent_tool_calling import (
    AgentResponse,
    AgentRunner,
    BotApplication,
    FakeContextManager,
    FakeDb,
    FakeLLM,
    ToolRegistry,
    _assistant_response,
    _event,
    _runtime,
    _settings,
    _tool_response,
)

from bot_app.context_budget import ContextBudget, ContextBudgetExceeded
from bot_app.context_contracts import (
    serialize_compact_user_event,
    serialize_session_summary,
)
from bot_app.context_manager import ContextManager
from bot_app.context_selection import evidence_excerpt
from bot_app.harness.context import ContextAssembler
from bot_app.memory import GroupChatMemory
from bot_app.model.multimodal import build_multimodal_user_content
from bot_app.session_store import SessionStore
from bot_app.tools.knowledge_service import build_read_game_knowledge_file_result
from bot_app.tools.session_memory import build_memory_tools


def event(text: str, number: int = 1, **kwargs: str) -> dict:
    return {**_event(text), "message_id": str(number), **kwargs}


def add_turn(memory: GroupChatMemory, value: dict) -> None:
    memory.append_turn(
        value["group_id"], value["user_id"], serialize_compact_user_event(value), "收到"
    )


def summary(**overrides: object) -> dict:
    return {
        "user_preferences": [],
        "open_loops": [],
        "established_facts": [],
        "recent_topic": "测试会话",
        "restrictions": [],
        "raw_summary": "已记录旧消息。",
        **overrides,
    }


class ContextGovernanceTests(unittest.IsolatedAsyncioTestCase):
    def test_large_tool_results_retain_tail_evidence_and_native_pairs(self) -> None:
        """CG-01: 有界观察保留尾部匹配证据和全部原生工具配对，不修改原始材料。"""
        question = "TARGET999 对 DDT 的伤害是多少"
        material = (
            "# 数据\n| 对象 | 伤害 |\n| --- | --- |\n"
            + "| 普通对象 | 1 |\n" * 12000
            + "| TARGET999 | 42，仅限 DDT |\n"
        )
        messages = [
            {"role": "system", "content": "依赖证据回答"},
            {"role": "user", "content": question},
        ]
        for index in range(3):
            call_id = f"call_{index}"
            messages.extend(
                [
                    {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": "provider-required",
                        "tool_calls": [
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": "read_game_knowledge_file",
                                    "arguments": "{}",
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps(
                            {
                                "ok": True,
                                "data": {
                                    "file_path": f"{index}.md",
                                    "content": material,
                                },
                            },
                            ensure_ascii=False,
                        ),
                    },
                ]
            )
        budget = ContextBudget(6000)
        fitted = budget.fit(messages, [], query=question)
        self.assertLessEqual(budget.count(fitted, []), 6000)
        for index in range(3):
            self.assertEqual(
                fitted[2 + index * 2]["tool_calls"][0]["id"],
                fitted[3 + index * 2]["tool_call_id"],
            )
            self.assertEqual(
                fitted[2 + index * 2]["reasoning_content"], "provider-required"
            )
            data = json.loads(fitted[3 + index * 2]["content"])["data"]
            self.assertIn("42，仅限 DDT", data["content"])
            self.assertIn("| 对象 | 伤害 |", data["content"])
            self.assertTrue(data["context_excerpted"])
        self.assertEqual(
            json.loads(messages[3]["content"])["data"]["content"], material
        )

    def test_duplicate_results_keep_call_ids(self) -> None:
        """CG-02: 相同工具结果只提供一份正文，同时保留原调用 ID。"""
        raw = json.dumps({"ok": True, "data": {"content": "same evidence"}})
        fitted = ContextBudget(2000).fit(
            [
                {"role": "tool", "tool_call_id": "a", "content": raw},
                {"role": "tool", "tool_call_id": "b", "content": raw},
            ],
            [],
            query="test",
        )
        self.assertEqual(fitted[1]["tool_call_id"], "b")
        self.assertEqual(json.loads(fitted[1]["content"])["duplicate_of_call_id"], "a")

    def test_budget_includes_schemas_and_images_and_preserves_current_question(
        self,
    ) -> None:
        """CG-03: 预算包括 schema 和图片；不能通过截断当前问题伪装达标。"""
        budget = ContextBudget(3000, image_tokens=2000)
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "问题"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,private"},
                    },
                ],
            }
        ]
        self.assertGreater(budget.count(messages, []), 2000)
        with self.assertRaises(ContextBudgetExceeded):
            budget.fit(
                messages, [{"description": "完整不可删的工具说明" * 1000}], query="问题"
            )
        self.assertEqual(messages[0]["content"][0]["text"], "问题")

    async def test_compaction_has_no_visibility_gap(self) -> None:
        """CG-04: 每轮信息必须进入摘要输入或仍可见的近期原文，不能出现 7–10 轮空档。"""
        memory = GroupChatMemory(6)
        for number in range(1, 13):
            add_turn(memory, event(f"唯一轮次{number}:" + "约束信息" * 30, number))
        llm = FakeLLM([_assistant_response(json.dumps(summary()))])
        manager = ContextManager(
            llm,
            replace(
                _settings(),
                context_compact_max_chars=1000,
                context_compact_keep_recent_turns=10,
            ),
        )
        self.assertTrue(await manager.compact_if_needed(memory, "1000", "2000"))
        old = json.loads(llm.requests[0]["messages"][1]["content"])["history"]
        old_ids = {
            json.loads(m["content"])["event"]["message_id"]
            for m in old
            if m["role"] == "user"
        }
        visible_ids = {
            m["message_id"]
            for m in memory.build_user_session_memory_context("1000", "2000")[
                "recent_turns"
            ]
            if m["role"] == "user"
        }
        self.assertEqual(old_ids | visible_ids, {str(i) for i in range(1, 13)})
        self.assertFalse(old_ids & visible_ids)

    def test_uncompacted_messages_are_not_silently_hidden(self) -> None:
        """CG-05: 尚未生成摘要时，不提前隐藏展示窗口之外的未压缩消息。"""
        memory = GroupChatMemory(6)
        for number in range(10):
            add_turn(memory, event(f"独立约束{number}", number))
        visible = memory.build_user_session_memory_context("1000", "2000")[
            "recent_turns"
        ]
        self.assertEqual(len([v for v in visible if v["role"] == "user"]), 10)

    async def test_bad_summary_or_request_error_does_not_replace_history(self) -> None:
        """CG-06: 空摘要、格式错误和压缩故障均保留原历史，允许后续正常回复。"""
        for raw in (
            "{}",
            "",
            "模型忙，请重试",
            json.dumps(summary(user_preferences="wrong type")),
        ):
            memory = GroupChatMemory(1)
            add_turn(memory, event("旧约束" * 600))
            add_turn(memory, event("继续", 2))
            before = memory.get_history("1000", "2000")
            manager = ContextManager(
                FakeLLM([_assistant_response(raw)]),
                replace(_settings(), context_compact_max_chars=1000),
            )
            self.assertFalse(await manager.compact_if_needed(memory, "1000", "2000"))
            self.assertEqual(memory.get_history("1000", "2000"), before)
        manager._llm.chat_with_override = AsyncMock(side_effect=RuntimeError("offline"))
        manager._retry_after.clear()
        self.assertFalse(await manager.compact_if_needed(memory, "1000", "2000"))
        self.assertEqual(memory.get_history("1000", "2000"), before)

    async def test_entire_summary_is_bounded(self) -> None:
        """CG-07: 全部摘要字段的总序列化长度受限，不仅限制 raw_summary。"""
        llm = FakeLLM(
            [
                _assistant_response(
                    json.dumps(
                        summary(
                            user_preferences=["喜欢" * 100] * 20,
                            restrictions=["不要编造"],
                            raw_summary="旧内容" * 1000,
                        )
                    )
                )
            ]
        )
        manager = ContextManager(
            llm, replace(_settings(), context_compact_summary_max_chars=360)
        )
        value = await manager._summarize_old_history([])
        self.assertIsNotNone(value)
        self.assertLessEqual(len(serialize_session_summary(value)), 360)
        self.assertEqual(value["restrictions"], ["不要编造"])

    def test_restart_isolation_and_reset(self) -> None:
        """CG-08: 重启恢复已提交会话；跨群、跨用户隔离，reset 同时清理持久副本。"""
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "memory.db")
            first = GroupChatMemory(6, store=SessionStore(path))
            add_turn(first, event("我的任务是测试路线", 1))
            second = GroupChatMemory(6, store=SessionStore(path))
            self.assertEqual(
                first.get_history("1000", "2000"), second.get_history("1000", "2000")
            )
            self.assertEqual(second.get_history("other", "2000"), [])
            self.assertEqual(second.get_history("1000", "other"), [])
            second.reset("1000", "2000")
            self.assertEqual(
                GroupChatMemory(6, store=SessionStore(path)).get_history(
                    "1000", "2000"
                ),
                [],
            )

    async def test_memory_tool_is_committed_only_after_delivery(self) -> None:
        """CG-09: Model→tool→最终回复只准备记忆，delivery 后 commit 才持久化。"""
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "memory.db")
            memory = GroupChatMemory(6, store=SessionStore(path))
            dependencies = _runtime()
            dependencies.memory = memory
            value = event("请记住：以后回答简短一些。")
            llm = FakeLLM(
                [
                    _tool_response(
                        "manage_memory",
                        {
                            "action": "remember",
                            "key": "reply_style",
                            "content": "以后回答简短一些。",
                        },
                    ),
                    _assistant_response("记住了。"),
                ]
            )
            runner = AgentRunner(
                llm=llm,
                memory=memory,
                settings=_settings(),
                tool_registry=ToolRegistry(dependencies),
                tool_dependencies=dependencies,
                context_manager=FakeContextManager(),
            )
            response = await runner.run(value, commit=False)
            self.assertEqual(SessionStore(path).recall("1000", "2000", "回答"), [])
            self.assertEqual(SessionStore(path).load("1000", "2000"), [])
            await runner.commit_turn(value, response)
            notes = SessionStore(path).recall("1000", "2000", "回答")
            self.assertEqual(notes[0]["text"], "以后回答简短一些。")
            self.assertEqual(notes[0]["source_message_id"], "1")

    async def test_abandoned_memory_update_is_not_committed_by_next_request(
        self,
    ) -> None:
        """CG-10: 发送失败留下的待提交变更不会被下一条消息带入数据库。"""
        with tempfile.TemporaryDirectory() as directory:
            memory = GroupChatMemory(
                6, store=SessionStore(str(Path(directory) / "memory.db"))
            )
            dependencies = _runtime()
            dependencies.memory = memory
            handler = build_memory_tools(dependencies)[0].handler
            await handler(
                {"action": "remember", "key": "style", "content": "以后简短"},
                event("记住以后简短"),
            )
            memory.begin_turn("1000", "2000")
            add_turn(memory, event("新的消息", 2))
            self.assertEqual(memory.recall("1000", "2000", "简短"), [])

    async def test_explicit_memories_authorization_correction_forget_and_expiry(
        self,
    ) -> None:
        """CG-11: 记忆须引用当前用户原文，群规则须管理员；更正、删除和过期生效。"""
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "memory.db")
            memory = GroupChatMemory(6, store=SessionStore(path))
            dependencies = _runtime()
            dependencies.memory = memory
            handler = build_memory_tools(dependencies)[0].handler
            args = {
                "action": "remember",
                "scope": "group",
                "key": "style",
                "kind": "rule",
                "content": "以后简短",
            }
            value = event("记住以后简短")
            self.assertFalse((await handler(args, value))["ok"])
            self.assertFalse(
                (
                    await handler(
                        {**args, "scope": "user", "content": "用户没说过的内容"}, value
                    )
                )["ok"]
            )
            value["identity"]["role"] = "admin"
            self.assertTrue((await handler(args, value))["ok"])
            add_turn(memory, value)
            self.assertEqual(
                SessionStore(path).recall("1000", "other", "规则")[0]["text"],
                "以后简短",
            )
            self.assertEqual(SessionStore(path).recall("other", "other", "规则"), [])
            correction = event("更正群规，以后详细", 2)
            correction["identity"]["role"] = "admin"
            await handler({**args, "content": "以后详细"}, correction)
            add_turn(memory, correction)
            notes = SessionStore(path).recall("1000", "other", "规则")
            self.assertEqual([n["text"] for n in notes], ["以后详细"])
            forget = event("忘记这条群规", 3)
            forget["identity"]["role"] = "admin"
            await handler(
                {"action": "forget", "scope": "group", "key": "style"}, forget
            )
            add_turn(memory, forget)
            self.assertEqual(SessionStore(path).recall("1000", "2000", "以后简短"), [])
            await handler({**args, "scope": "user"}, value)
            add_turn(memory, {**value, "message_id": "4"})
            with sqlite3.connect(path) as conn:
                conn.execute("UPDATE agent_memories SET expires_at='2000-01-01'")
            self.assertEqual(SessionStore(path).recall("1000", "2000", "以后简短"), [])

    def test_knowledge_selects_tail_table_with_source_lines_and_paging(self) -> None:
        """CG-12: 大表格末尾证据可定位，保留标题/表头，并能按原文件行继续读取。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docs = root / "knowledge_docs"
            docs.mkdir()
            source = docs / "test.md"
            source.write_text(
                "# 巨齿鲨\n## 详细数据\n| 对象 | 伤害 |\n| --- | --- |\n"
                + "| OTHER | 1 |\n" * 15000
                + "| TARGET999 | 42 |\n",
                encoding="utf-8",
            )
            arguments = {
                "file_path": str(source),
                "query": "TARGET999",
                "max_chars": 1200,
            }
            result = build_read_game_knowledge_file_result(
                kb_root=root, arguments=arguments, module="test", max_read_chars=350000
            )
            data = result["data"]
            self.assertTrue(result["ok"])
            self.assertLessEqual(len(data["content"]), 1200)
            self.assertIn("| TARGET999 | 42 |", data["content"])
            self.assertIn("| 对象 | 伤害 |", data["content"])
            self.assertIn("## 详细数据", data["content"])
            line = len(source.read_text().splitlines())
            page = build_read_game_knowledge_file_result(
                kb_root=root,
                arguments={**arguments, "start_line": line},
                module="test",
                max_read_chars=350000,
            )
            self.assertIn("TARGET999", page["data"]["content"])
            self.assertIsNone(page["data"]["next_line"])
            self.assertEqual(page["data"]["start_line"], line)

    def test_unrelated_text_has_no_historical_pixels(self) -> None:
        """CG-13: 无视觉诉求不注入历史像素；明确追问上一张图仍可使用。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.png"
            path.write_bytes(b"test")
            context = {
                "recent_group_window": [
                    {
                        "sequence": 1,
                        "images": [
                            {"cache_path": str(path), "processing_status": "cached"}
                        ],
                    }
                ]
            }
            settings = replace(_settings(), vision_enabled=True)
            self.assertIsInstance(
                build_multimodal_user_content(
                    event=event("查询今天的活动"),
                    reply_context=context,
                    settings=settings,
                ),
                str,
            )
            self.assertIsInstance(
                build_multimodal_user_content(
                    event=event("上一张图是什么"),
                    reply_context=context,
                    settings=settings,
                ),
                list,
            )

    async def test_group_window_filters_stale_and_duplicate_messages(self) -> None:
        """CG-14: 群窗口按时效筛选，与用户历史按消息 ID 去重且保留其他发言者。"""
        memory = GroupChatMemory(6)
        add_turn(memory, event("我的约束", 7))
        db = FakeDb()
        now = datetime.now(UTC)
        db.recent_rows = [
            {
                "message_id": "old",
                "normalized_text": "旧活动",
                "created_at": (now - timedelta(days=2)).isoformat(),
            },
            {
                "message_id": "7",
                "normalized_text": "我的约束",
                "created_at": now.isoformat(),
            },
            {
                "message_id": "different",
                "normalized_text": "我的约束",
                "created_at": now.isoformat(),
            },
        ]
        context = ContextAssembler(
            memory=memory,
            context_manager=FakeContextManager(),
            settings=_settings(),
            tool_dependencies=_runtime(db=db),
        )
        payload = await context.prepare(event("继续", 8), trace_id="test")
        self.assertEqual(
            [row["message_id"] for row in payload["recent_group_window"]], ["different"]
        )

    async def test_compaction_exception_does_not_abort_main_reply(self) -> None:
        """CG-15: prepare 压缩故障降级后仍执行正常模型回复。"""
        manager = FakeContextManager()
        manager.compact_if_needed = AsyncMock(
            side_effect=RuntimeError("summary unavailable")
        )
        runner = AgentRunner(
            llm=FakeLLM([_assistant_response("正常回复")]),
            memory=GroupChatMemory(6),
            settings=_settings(),
            tool_registry=ToolRegistry(_runtime()),
            context_manager=manager,
        )
        response = await runner.run(event("你好"))
        self.assertEqual(response.text, "正常回复")

    def test_oversized_matching_line_wins_over_unrelated_short_lines(self) -> None:
        """CG-16: 超长单行中的相关事实不能被无关短行挤掉。"""
        material = (
            "# 数据\n"
            + "noise\n" * 100
            + "x" * 6000
            + " target_code=7777 "
            + "y" * 6000
            + "\nfooter"
        )
        excerpt, ranges = evidence_excerpt(material, "target_code", 1000)
        self.assertLessEqual(len(excerpt), 1000)
        self.assertIn("target_code=7777", excerpt)
        self.assertIn("行内节选", excerpt)
        self.assertTrue(any(r["start_line"] <= 102 <= r["end_line"] for r in ranges))
        small, _ = evidence_excerpt(
            "DDT notes " + "padding " * 1000 + " TARGET999 damage=42",
            "TARGET999 DDT",
            160,
        )
        self.assertIn("TARGET999 damage=42", small)

    def test_relevant_array_tail_survives_observation_selection(self) -> None:
        """CG-17: 工具数组超过 20 项时仍保留末尾唯一匹配项。"""
        messages = [
            {
                "role": "tool",
                "tool_call_id": "call",
                "content": json.dumps(
                    {
                        "data": {
                            "rows": [
                                *[{"name": f"unrelated_{i}"} for i in range(20)],
                                {"name": "target_code", "damage": 7777},
                            ]
                        }
                    }
                ),
            }
        ]
        fitted = ContextBudget(24000).fit(messages, [], query="target_code")
        self.assertIn(
            {"name": "target_code", "damage": 7777},
            json.loads(fitted[0]["content"])["data"]["rows"],
        )

    def test_many_uncompacted_turns_and_legacy_summary_can_fit(self) -> None:
        """CG-18: 摘要失效后仍可削减工作历史至预算内，当前问题和原历史不变。"""
        memory = GroupChatMemory(6)
        memory.replace_history(
            "1000",
            "2000",
            [
                {
                    "role": "assistant",
                    "content": serialize_session_summary(
                        summary(raw_summary="旧记录" * 10000)
                    ),
                }
            ],
        )
        for index in range(30):
            add_turn(memory, event(f"第{index}条旧消息" + "历史内容" * 400, index))
        context = memory.build_reply_context(
            current_event={"text": "保留当前完整问题"}, group_id="1000", user_id="2000"
        )
        messages = [
            {
                "role": "user",
                "content": json.dumps({"reply_context": context}, ensure_ascii=False),
            }
        ]
        budget = ContextBudget(5000)
        fitted = budget.fit(messages, [], query="保留当前完整问题")
        self.assertLessEqual(budget.count(fitted, []), 5000)
        self.assertEqual(
            json.loads(fitted[0]["content"])["reply_context"]["current_event"],
            context["current_event"],
        )
        self.assertEqual(len(context["user_session_memory"]["recent_turns"]), 60)

    def test_observation_offsets_do_not_masquerade_as_file_lines(self) -> None:
        """CG-19: 多档裁剪从原观察选取，片段内行号与原文件行号明确区分。"""
        original = (
            "# 页面\n"
            + "旧数据行 abcdefghijklmnopqrstuvwxyz\n" * 300
            + "target_code=7777\n"
        )
        messages = [{"role": "user", "content": "target_code"}]
        for index in range(4):
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(index),
                    "content": json.dumps(
                        {
                            "data": {
                                "file_path": f"{index}.md",
                                "mode": "page",
                                "start_line": 200,
                                "content": original,
                            }
                        },
                        ensure_ascii=False,
                    ),
                }
            )
        fitted = ContextBudget(2500).fit(messages, [], query="target_code")
        for message in fitted[1:]:
            data = json.loads(message["content"])["data"]
            self.assertIn("target_code=7777", data["content"])
            self.assertIn("观察片段", data["content"])
            self.assertNotIn("[原文 L", data["content"])
            self.assertEqual(data["content"].count("其余内容已省略"), 1)
            self.assertEqual(data["start_line"], 200)

    async def test_memory_search_prefers_matching_task_and_supports_key(self) -> None:
        """CG-20: 查询命中优先于无关偏好，显式搜索支持 key 且不回填无关偏好。"""
        with tempfile.TemporaryDirectory() as directory:
            memory = GroupChatMemory(
                6, store=SessionStore(str(Path(directory) / "memory.db"))
            )
            for index in range(6):
                memory.queue_update(
                    "1000",
                    "2000",
                    {
                        "action": "remember",
                        "scope": "user",
                        "key": "route_task" if index == 0 else f"pref_{index}",
                        "kind": "task" if index == 0 else "preference",
                        "content": "todo" if index == 0 else "unrelated preference",
                        "ttl_days": 7,
                    },
                )
                add_turn(
                    memory,
                    event(
                        "记住 todo" if index == 0 else "记住 unrelated preference",
                        index,
                    ),
                )
            self.assertEqual(
                memory.recall("1000", "2000", "todo")[0]["key"], "route_task"
            )
            dependencies = _runtime()
            dependencies.memory = memory
            handler = build_memory_tools(dependencies)[0].handler
            found = await handler(
                {"action": "search", "query": "route_task"}, event("找 route_task")
            )
            self.assertEqual(
                [n["key"] for n in found["data"]["memories"]], ["route_task"]
            )
            missing = await handler(
                {"action": "search", "query": "nothing_matches"}, event("查询")
            )
            self.assertEqual(missing["data"]["memories"], [])

    async def test_default_knowledge_read_provides_advancing_page(self) -> None:
        """CG-21: 默认无 query 读取返回可推进的原文件分页；显式全文读取仍可用。"""
        from bot_app.tools.knowledge import build_knowledge_tools

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "knowledge_docs").mkdir()
            source = root / "knowledge_docs" / "test.md"
            source.write_text("# 数据\n" + "一行数据\n" * 5000, encoding="utf-8")
            dependencies = _runtime()
            dependencies.kb_root = root
            handler = build_knowledge_tools(dependencies)[1].handler
            first = await handler({"file_path": str(source)}, event("读取"))
            self.assertEqual(first["data"]["mode"], "page")
            self.assertGreater(first["data"]["next_line"], 1)
            second = await handler(
                {"file_path": str(source), "start_line": first["data"]["next_line"]},
                event("继续"),
            )
            self.assertEqual(
                second["data"]["start_line"], first["data"]["end_line"] + 1
            )

    async def test_commit_failure_keeps_delivery_and_reports_unsaved_memory(
        self,
    ) -> None:
        """CG-22: SQLite 提交失败保留已发送会话、清空待写变更，明确报告未保存。"""
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(str(Path(directory) / "memory.db"))
            memory = GroupChatMemory(6, store=store)
            value = event("记住以后简短")
            memory.queue_update(
                "1000",
                "2000",
                {
                    "action": "remember",
                    "scope": "user",
                    "key": "style",
                    "kind": "preference",
                    "content": "以后简短",
                    "ttl_days": 90,
                },
            )
            sender = SimpleNamespace(send_group_text=AsyncMock())

            async def commit(*args: object, **kwargs: object) -> None:
                add_turn(memory, value)

            agent = SimpleNamespace(
                run=AsyncMock(return_value=AgentResponse(text="记住了")),
                commit_turn=commit,
            )
            app = BotApplication(
                settings=_settings(),
                receiver=object(),
                sender=sender,
                agent=agent,
                llm=SimpleNamespace(pop_trace_total_tokens=Mock(return_value=0)),
            )
            with patch.object(
                store, "save", side_effect=sqlite3.OperationalError("disk full")
            ):
                await app.process_event(
                    event=value,
                    trace_id="commit-failed",
                    reply_group_id="1000",
                    at_user_id="2000",
                    send_failure_notice=True,
                )
            self.assertEqual(
                app._runs.get("commit-failed").status, "delivered_memory_failed"
            )
            self.assertEqual(sender.send_group_text.call_count, 2)
            self.assertIn("未能保存", sender.send_group_text.call_args.kwargs["text"])
            self.assertEqual(len(memory.get_history("1000", "2000")), 2)
            add_turn(memory, event("继续", 2))
            self.assertEqual(
                store.recall("1000", "2000", "style", include_preferences=False), []
            )

    def test_reply_image_reference_and_expiry(self) -> None:
        """CG-23: 结构化回复引用只选目标图片，过期缓存不进入模型。"""
        from bot_app.receiver import extract_reply_message_id

        self.assertEqual(
            extract_reply_message_id([{"type": "reply", "data": {"id": "7"}}]), "7"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.png"
            path.write_bytes(b"test")
            image = {"cache_path": str(path), "processing_status": "cached"}
            context = {"recent_group_window": [{"message_id": "7", "images": [image]}]}

            def content(reply_id: str) -> object:
                return build_multimodal_user_content(
                    event=event("继续", reply_to_message_id=reply_id),
                    reply_context=context,
                    settings=replace(_settings(), vision_enabled=True),
                )

            self.assertIsInstance(content("7"), list)
            self.assertIsInstance(content("8"), str)
            unrelated = Path(directory) / "other.png"
            unrelated.write_bytes(b"other")
            context["recent_group_window"].append(
                {
                    "message_id": "8",
                    "sequence": 8,
                    "images": [
                        {"cache_path": str(unrelated), "processing_status": "cached"}
                    ],
                }
            )
            focused = build_multimodal_user_content(
                event=event("这张图是什么", reply_to_message_id="7"),
                reply_context=context,
                settings=replace(
                    _settings(), vision_enabled=True, vision_history_image_limit=1
                ),
            )
            self.assertNotIn("第 8 条图片", focused[1]["text"])
            self.assertIn("dGVzdA==", focused[2]["image_url"]["url"])
            image["expires_at"] = "2000-01-01T00:00:00+00:00"
            self.assertIsInstance(content("7"), str)

    async def test_compaction_catches_up_without_dropping_unsummarized_turns(
        self,
    ) -> None:
        """CG-24: 积压摘要按预算分批，只替换实际送入摘要模型的历史。"""
        memory = GroupChatMemory(1)
        for index in range(30):
            add_turn(
                memory, event(f"第{index}条约束 " + "需要保留的用户消息 " * 100, index)
            )
        original = memory.get_history("1000", "2000")
        llm = FakeLLM([_assistant_response(json.dumps(summary()))])
        manager = ContextManager(
            llm,
            replace(
                _settings(),
                context_compact_max_chars=1000,
                context_max_input_tokens=2400,
            ),
        )
        self.assertTrue(await manager.compact_if_needed(memory, "1000", "2000"))
        messages = llm.requests[0]["messages"]
        self.assertLessEqual(ContextBudget(2400).count(messages, []), 2400)
        consumed = json.loads(messages[1]["content"])["history"]
        self.assertLess(len(consumed), len(original) - 2)
        self.assertEqual(
            memory.get_history("1000", "2000")[1:], original[len(consumed) :]
        )


if __name__ == "__main__":
    unittest.main()
