from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from bot_app.agent.runner import AgentRunner  # noqa: E402
from bot_app.agent.tool_registry import ToolRegistry  # noqa: E402
from bot_app.command_processor import CommandProcessor  # noqa: E402
from bot_app.llm_client import LLMClient, LLMResponse, LLMToolCall  # noqa: E402
from bot_app.memory import GroupChatMemory  # noqa: E402
from bot_app.protocol import CommandResultPayload, EventPayload, IntentPayload  # noqa: E402
from bot_app.sender import MessageSender  # noqa: E402
from bot_app.settings import AppSettings  # noqa: E402
from bot_app.tools import ToolRuntime  # noqa: E402
from bot_app.tools.features import get_feature_catalog  # noqa: E402
from bot_app.tools.subprocess_runner import ProcessResult  # noqa: E402


def _settings() -> AppSettings:
    return AppSettings(
        env_file=".env",
        target_group_id="1000",
        target_group_ids=("1000",),
        api_key="test-key",
        system_prompt="你是测试机器人。",
        max_tool_rounds=3,
    )


def _event(text: str = "你会什么") -> EventPayload:
    return {
        "module": "test",
        "event_type": "group_message",
        "group_id": "1000",
        "user_id": "2000",
        "text": text,
        "bot_mentioned": True,
        "mentioned_qqs": [],
        "identity": {
            "nickname": "tester",
            "card": "",
            "role": "member",
            "title": "",
            "level": "",
        },
    }


class FakeCommandProcessor:
    def __init__(self) -> None:
        self.calls: list[IntentPayload] = []
        self.next_result: CommandResultPayload | None = None
        self.next_results: list[CommandResultPayload] = []

    async def execute(
        self, intent: IntentPayload, event: EventPayload
    ) -> CommandResultPayload:
        self.calls.append(intent)
        if self.next_results:
            return self.next_results.pop(0)
        if self.next_result is not None:
            return self.next_result
        return {
            "module": "command_processor",
            "ok": True,
            "command": intent["intent"],
            "data": {"called_with": intent["arguments"], "event_text": event["text"]},
            "message": "工具执行完成",
            "extra_prompt": "",
            "response_type": "text",
            "image_path": "",
        }


class FakeDb:
    def __init__(self) -> None:
        self.active_rows = [
            {"user_id": "1", "nickname": "A", "card": "", "message_count": 7}
        ]
        self.recent_rows = [
            {
                "sender_card": "A",
                "sender_nickname": "",
                "raw_message": "hello",
                "created_at": "2026-06-10 10:00:00",
            }
        ]

    def get_today_most_active(self, group_id: str, top_n: int) -> list[dict[str, Any]]:
        return self.active_rows[:top_n]

    def get_recent_group_messages(
        self, group_id: str, limit: int
    ) -> list[dict[str, Any]]:
        return self.recent_rows[:limit]


class FakeLLM:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = responses
        self.requests: list[dict[str, Any]] = []

    async def chat_completion(self, **kwargs: Any) -> LLMResponse:
        self.requests.append(kwargs)
        if not self.responses:
            raise AssertionError("unexpected extra LLM call")
        return self.responses.pop(0)


class FakeContextManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def compact_if_needed(
        self,
        memory: GroupChatMemory,
        group_id: str,
        user_id: str,
        trace_id: str = "",
    ) -> bool:
        self.calls.append((group_id, user_id, trace_id))
        return False


class FakeQQApi:
    def __init__(self) -> None:
        self.group_text_calls: list[dict[str, Any]] = []
        self.group_array_calls: list[tuple[str, Any]] = []

    async def post_group_msg(self, **kwargs: Any) -> None:
        self.group_text_calls.append(kwargs)

    async def post_group_array_msg(self, group_id: str, message: Any) -> None:
        self.group_array_calls.append((group_id, message))


class FakeBot:
    def __init__(self) -> None:
        self.api = type("FakeApi", (), {"qq": FakeQQApi()})()


class FakeActivityRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def run(
        self,
        cmd: list[str],
        *,
        cwd: str,
        env: dict[str, str] | None = None,
        timeout: float,
    ) -> ProcessResult:
        self.calls.append(cmd)
        root = Path(cwd)
        if "--mode" not in cmd:
            report_path = Path(cmd[cmd.index("--report") + 1])
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text("# 开箱统计\n测试报告", encoding="utf-8")
            if "--output" in cmd:
                image_path = Path(cmd[cmd.index("--output") + 1])
                image_path.parent.mkdir(parents=True, exist_ok=True)
                image_path.write_bytes(b"fake png")
                return ProcessResult(returncode=0, stdout="已生成拼图")
            return ProcessResult(returncode=0, stdout="统计 Markdown 已保存")

        output_path = root / cmd[cmd.index("--output") + 1]
        mode = cmd[cmd.index("--mode") + 1]
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if mode == "leaderboard":
            image_path = root / "output" / "fake_leaderboard.png"
            image_path.write_bytes(b"fake png")
            output_path.write_text(
                "# 来源: 测试\n# 文件: output/fake_leaderboard.png\n\n已生成排行榜图片。",
                encoding="utf-8",
            )
        elif mode == "collection-event":
            image_path = root / "output" / "fake_collection.png"
            json_path = root / "output" / "fake_collection.json"
            image_path.write_bytes(b"fake png")
            json_path.write_text("{}", encoding="utf-8")
            output_path.write_text(
                "\n".join(
                    [
                        "# 收集活动轮换",
                        "# JSON: output/fake_collection.json",
                        "# 图片: output/fake_collection.png",
                    ]
                ),
                encoding="utf-8",
            )
        else:
            output_path.write_text("# 详情\n测试活动详情", encoding="utf-8")

        return ProcessResult(returncode=0)


def _runtime(
    command_processor: FakeCommandProcessor,
    *,
    db: FakeDb | None = None,
    manual_file: Path | None = None,
    plugin_root: Path = PLUGIN_ROOT,
    subprocess_runner: Any = None,
) -> ToolRuntime:
    return ToolRuntime.create(
        db=db or FakeDb(),
        plugin_root=plugin_root,
        manual_file=manual_file or Path("/missing/manual.md"),
        command_guide_dir=plugin_root / "command_skills",
        subprocess_runner=subprocess_runner,
    )


def _create_temp_plugin_root(temp_dir: str) -> Path:
    plugin_root = Path(temp_dir)
    api_root = plugin_root / "btd6_api_processor"
    api_root.mkdir(parents=True)
    (api_root / "btd6_cli.py").write_text("# fake cli", encoding="utf-8")
    chat_root = plugin_root / "ChatMonkeyTD"
    chat_root.mkdir(parents=True)
    (chat_root / "main.py").write_text("# fake cli", encoding="utf-8")
    return plugin_root


def _create_fake_knowledge_base(plugin_root: Path) -> Path:
    kb_root = plugin_root / "btd6_knowledge_base"
    corpus_dir = kb_root / "wiki_data_second_cleaned" / "btd6"
    corpus_dir.mkdir(parents=True)
    (kb_root / "wiki_data_cleaned" / "btd6").mkdir(parents=True)
    (kb_root / "search_index.db").write_text("", encoding="utf-8")
    (corpus_dir / "monkey.md").write_text(
        "忍者猴可以升级为大师忍者。", encoding="utf-8"
    )
    (corpus_dir / "long.md").write_text("忍者猴\n" * 9000, encoding="utf-8")
    (kb_root / "kb_interface.py").write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "def interface_search(query, top_n, db_path, corpus_dir, alias_file):",
                "    return [str(Path(corpus_dir) / 'monkey.md')][:top_n]",
                "def interface_extract(file_name, keywords_query, corpus_dir, max_chars):",
                "    return {",
                "        'file': file_name,",
                "        'max_chars': max_chars,",
                "        'text': '提取到的忍者猴文本',",
                "        'keywords': keywords_query.split(),",
                "        'chars': 9,",
                "    }",
            ]
        ),
        encoding="utf-8",
    )
    return kb_root


def _assistant_response(
    content: str, tool_calls: list[LLMToolCall] | None = None
) -> LLMResponse:
    raw_tool_calls = []
    for tool_call in tool_calls or []:
        raw_tool_calls.append(
            {
                "id": tool_call.id,
                "type": "function",
                "function": {"name": tool_call.name, "arguments": tool_call.arguments},
            }
        )
    assistant_message: dict[str, Any] = {"role": "assistant", "content": content}
    if raw_tool_calls:
        assistant_message["tool_calls"] = raw_tool_calls
    return LLMResponse(
        content=content,
        assistant_message=assistant_message,
        tool_calls=tool_calls or [],
        total_tokens=17,
        raw={"choices": [{"message": assistant_message}]},
        model="fake-model",
    )


class AgentToolCallingTests(unittest.IsolatedAsyncioTestCase):
    def test_tool_registry_loads_concrete_tool_modules(self) -> None:
        command_processor = FakeCommandProcessor()
        registry = ToolRegistry(_runtime(command_processor))

        tool_names = {tool["function"]["name"] for tool in registry.to_openai_tools()}

        self.assertEqual(
            tool_names,
            {
                "ignore_message",
                "list_features",
                "today_most_active",
                "summarize_recent_messages",
                "script_manual_query",
                "search_game_knowledge",
                "read_game_knowledge_file",
                "get_activity_detail",
                "get_activity_leaderboard",
                "get_collection_event_info",
                "simulate_monkey_box_opening",
            },
        )
        for tool in registry.to_openai_tools():
            self.assertEqual(tool["type"], "function")
            function = tool["function"]
            self.assertIsInstance(function["description"], str)
            json.dumps(function["parameters"])
            self.assertFalse(function["parameters"].get("additionalProperties", True))

    async def test_concrete_social_tool_handler_uses_runtime_db(self) -> None:
        command_processor = FakeCommandProcessor()
        db = FakeDb()
        registry = ToolRegistry(_runtime(command_processor, db=db))

        result = await registry.execute(
            "today_most_active",
            {"top_n": 5},
            _event("今日活跃榜前5"),
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["rows"], db.active_rows)
        self.assertEqual(command_processor.calls, [])

    async def test_concrete_manual_tool_reads_runtime_manual_file(self) -> None:
        command_processor = FakeCommandProcessor()
        with tempfile.TemporaryDirectory() as temp_dir:
            manual_path = Path(temp_dir) / "manual.md"
            manual_path.write_text("启动方式：运行 main.py", encoding="utf-8")
            registry = ToolRegistry(
                _runtime(command_processor, manual_file=manual_path)
            )

            result = await registry.execute(
                "script_manual_query",
                {"query": "怎么启动"},
                _event("怎么启动脚本"),
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["manual_full_text"], "启动方式：运行 main.py")
        self.assertEqual(command_processor.calls, [])

    async def test_tool_results_follow_contract(self) -> None:
        command_processor = FakeCommandProcessor()
        activity_runner = FakeActivityRunner()
        required_fields = {
            "module",
            "ok",
            "command",
            "data",
            "message",
            "extra_prompt",
            "response_type",
            "image_path",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = ToolRegistry(
                _runtime(
                    command_processor,
                    plugin_root=_create_temp_plugin_root(temp_dir),
                    subprocess_runner=activity_runner,
                )
            )
            cases = [
                ("ignore_message", {"reason": "test"}, False),
                ("list_features", {}, False),
                ("today_most_active", {"top_n": 3}, False),
                ("summarize_recent_messages", {"top_n": 5}, False),
                ("script_manual_query", {"query": "怎么启动"}, False),
                ("search_game_knowledge", {"query": "忍者猴"}, False),
                ("read_game_knowledge_file", {"file_path": "a.md"}, False),
                ("get_activity_detail", {"activity_type": "race"}, False),
                ("get_activity_leaderboard", {"leaderboard_type": "race"}, False),
                ("get_collection_event_info", {"only_upcoming": True}, False),
                (
                    "simulate_monkey_box_opening",
                    {"box_type": "钻石", "box_count": 1},
                    False,
                ),
            ]

            for name, arguments, is_forwarded in cases:
                if is_forwarded:
                    command_processor.next_results.append(
                        {
                            "module": "legacy",
                            "ok": True,
                            "command": name,
                            "data": {"arguments": arguments},
                            "message": "legacy ok",
                            "extra_prompt": "",
                        }  # type: ignore[typeddict-item]
                    )
                result = await registry.execute(name, arguments, _event())

                self.assertTrue(required_fields.issubset(result.keys()), name)
                self.assertIsInstance(result["data"], dict, name)
                self.assertIn(result["response_type"], {"text", "image", "none"}, name)
                self.assertIsInstance(result["image_path"], str, name)

            unknown = await registry.execute("missing_tool", {}, _event())
        self.assertTrue(required_fields.issubset(unknown.keys()))
        self.assertFalse(unknown["ok"])

    async def test_mini_game_tool_uses_runtime_runner(self) -> None:
        command_processor = FakeCommandProcessor()
        fake_runner = FakeActivityRunner()
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = _create_temp_plugin_root(temp_dir)
            registry = ToolRegistry(
                _runtime(
                    command_processor,
                    plugin_root=plugin_root,
                    subprocess_runner=fake_runner,
                )
            )

            image_result = await registry.execute(
                "simulate_monkey_box_opening",
                {"box_type": "diamond", "box_count": 1},
                _event("开 1 个钻石箱"),
            )
            text_result = await registry.execute(
                "simulate_monkey_box_opening",
                {"box_type": "钻石", "box_count": 51},
                _event("开 51 个钻石箱"),
            )
            rejected_result = await registry.execute(
                "simulate_monkey_box_opening",
                {"box_type": "钻石", "box_count": 10001},
                _event("开很多钻石箱"),
            )

        self.assertEqual(image_result["module"], "tool.mini_game")
        self.assertEqual(image_result["response_type"], "image")
        self.assertTrue(image_result["image_path"].endswith(".png"))
        self.assertEqual(text_result["module"], "tool.mini_game")
        self.assertEqual(text_result["response_type"], "text")
        self.assertEqual(text_result["image_path"], "")
        self.assertFalse(rejected_result["ok"])
        self.assertEqual(command_processor.calls, [])
        self.assertEqual(len(fake_runner.calls), 2)

    async def test_knowledge_tools_use_runtime_kb_root(self) -> None:
        command_processor = FakeCommandProcessor()
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = _create_temp_plugin_root(temp_dir)
            kb_root = _create_fake_knowledge_base(plugin_root)
            registry = ToolRegistry(
                _runtime(command_processor, plugin_root=plugin_root)
            )

            search_result = await registry.execute(
                "search_game_knowledge",
                {"query": "忍者猴机制", "top_n": 3},
                _event("查忍者猴机制"),
            )
            file_path = search_result["data"]["matched_paths"][0]
            read_result = await registry.execute(
                "read_game_knowledge_file",
                {"file_path": file_path},
                _event("读文件"),
            )
            long_result = await registry.execute(
                "read_game_knowledge_file",
                {
                    "file_path": str(kb_root / "wiki_data_second_cleaned/btd6/long.md"),
                    "keywords": ["忍者猴"],
                    "max_chars": 1200,
                },
                _event("读长文"),
            )
            rejected_result = await registry.execute(
                "read_game_knowledge_file",
                {"file_path": str(plugin_root / "outside.md")},
                _event("读越界文件"),
            )

        self.assertTrue(search_result["ok"])
        self.assertEqual(search_result["module"], "tool.knowledge")
        self.assertTrue(read_result["ok"])
        self.assertIn("大师忍者", read_result["data"]["content"])
        self.assertTrue(long_result["ok"])
        self.assertEqual(long_result["data"]["mode"], "extract")
        self.assertFalse(rejected_result["ok"])
        self.assertEqual(command_processor.calls, [])
        self.assertNotIn(str(kb_root.resolve()), sys.path)

    async def test_legacy_command_processor_list_features_uses_shared_catalog(
        self,
    ) -> None:
        command_processor = CommandProcessor(
            db=FakeDb(),  # type: ignore[arg-type]
            manual_file=Path("/missing/manual.md"),
            command_guide_dir=PLUGIN_ROOT / "command_skills",
        )

        result = await command_processor.execute(
            {
                "module": "test",
                "target": "command_processor",
                "intent": "list_features",
                "arguments": {},
                "reply": "",
                "reason": "test",
            },
            _event(),
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["features"], get_feature_catalog())

    async def test_legacy_command_processor_view_command_guide_uses_service(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            guide_dir = Path(temp_dir) / "command_skills"
            guide_dir.mkdir()
            (guide_dir / "script_manual_query.md").write_text(
                "说明书查询指南", encoding="utf-8"
            )
            command_processor = CommandProcessor(
                db=FakeDb(),  # type: ignore[arg-type]
                manual_file=Path("/missing/manual.md"),
                command_guide_dir=guide_dir,
            )

            result = await command_processor.execute(
                {
                    "module": "test",
                    "target": "command_processor",
                    "intent": "view_command_guide",
                    "arguments": {"command": "script_manual_query"},
                    "reply": "",
                    "reason": "test",
                },
                _event(),
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["guide_text"], "说明书查询指南")

    async def test_legacy_command_processor_uses_shared_social_and_manual_services(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manual_path = Path(temp_dir) / "manual.md"
            manual_path.write_text("启动方式：运行 main.py", encoding="utf-8")
            command_processor = CommandProcessor(
                db=FakeDb(),  # type: ignore[arg-type]
                manual_file=manual_path,
                command_guide_dir=PLUGIN_ROOT / "command_skills",
            )

            active_result = await command_processor.execute(
                {
                    "module": "test",
                    "target": "command_processor",
                    "intent": "today_most_active",
                    "arguments": {"top_n": 5},
                    "reply": "",
                    "reason": "test",
                },
                _event("今日活跃榜前5"),
            )
            summary_result = await command_processor.execute(
                {
                    "module": "test",
                    "target": "command_processor",
                    "intent": "summarize_recent_messages",
                    "arguments": {"top_n": 5},
                    "reply": "",
                    "reason": "test",
                },
                _event("总结最近消息"),
            )
            manual_result = await command_processor.execute(
                {
                    "module": "test",
                    "target": "command_processor",
                    "intent": "script_manual_query",
                    "arguments": {"query": "怎么启动"},
                    "reply": "",
                    "reason": "test",
                },
                _event("怎么启动脚本"),
            )

        self.assertEqual(active_result["module"], "command_processor")
        self.assertEqual(active_result["data"]["rows"][0]["nickname"], "A")
        self.assertEqual(summary_result["module"], "command_processor")
        self.assertEqual(summary_result["data"]["message_count"], 1)
        self.assertEqual(manual_result["module"], "command_processor")
        self.assertEqual(
            manual_result["data"]["manual_full_text"], "启动方式：运行 main.py"
        )

    async def test_sender_uses_rich_message_api_for_group_image(self) -> None:
        bot = FakeBot()
        sender = MessageSender(bot)

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "result.png"
            image_path.write_bytes(b"fake image bytes")

            await sender.send_group_image(
                group_id="1000",
                image_path=str(image_path),
                text="图片说明",
                at_user_id="2000",
            )

        qq = bot.api.qq
        self.assertEqual(qq.group_text_calls, [])
        self.assertEqual(len(qq.group_array_calls), 1)
        group_id, message = qq.group_array_calls[0]
        self.assertEqual(group_id, "1000")
        self.assertTrue(hasattr(message, "add_image"))

    async def test_agent_executes_tool_call_and_returns_final_text(self) -> None:
        command_processor = FakeCommandProcessor()
        registry = ToolRegistry(_runtime(command_processor))
        llm = FakeLLM(
            [
                _assistant_response(
                    "",
                    [
                        LLMToolCall(
                            id="call_1",
                            name="list_features",
                            arguments="{}",
                        )
                    ],
                ),
                _assistant_response("功能已列出"),
            ]
        )
        context_manager = FakeContextManager()
        runner = AgentRunner(
            llm=llm,  # type: ignore[arg-type]
            memory=GroupChatMemory(history_turns=3),
            settings=_settings(),
            tool_registry=registry,
            context_manager=context_manager,  # type: ignore[arg-type]
        )

        response = await runner.run(_event(), trace_id="test-trace")

        self.assertTrue(response.should_send)
        self.assertEqual(response.text, "功能已列出")
        self.assertEqual(response.token_usage, 34)
        self.assertEqual(command_processor.calls, [])
        self.assertIn("tools", llm.requests[0])
        self.assertTrue(
            any(
                tool["function"]["name"] == "list_features"
                for tool in llm.requests[0]["tools"]
            )
        )
        self.assertEqual(
            context_manager.calls,
            [("1000", "2000", "test-trace"), ("1000", "2000", "test-trace")],
        )

    async def test_ignore_message_tool_suppresses_send(self) -> None:
        command_processor = FakeCommandProcessor()
        registry = ToolRegistry(_runtime(command_processor))
        llm = FakeLLM(
            [
                _assistant_response(
                    "",
                    [
                        LLMToolCall(
                            id="call_1",
                            name="ignore_message",
                            arguments='{"reason":"闲聊"}',
                        )
                    ],
                )
            ]
        )
        runner = AgentRunner(
            llm=llm,  # type: ignore[arg-type]
            memory=GroupChatMemory(history_turns=3),
            settings=_settings(),
            tool_registry=registry,
            context_manager=FakeContextManager(),  # type: ignore[arg-type]
        )

        response = await runner.run(_event("路过一下"), trace_id="test-trace")

        self.assertFalse(response.should_send)
        self.assertEqual(response.response_type, "none")
        self.assertEqual(command_processor.calls, [])

    async def test_image_tool_result_is_returned_with_final_text(self) -> None:
        command_processor = FakeCommandProcessor()
        activity_runner = FakeActivityRunner()
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = _create_temp_plugin_root(temp_dir)
            registry = ToolRegistry(
                _runtime(
                    command_processor,
                    plugin_root=plugin_root,
                    subprocess_runner=activity_runner,
                )
            )
            expected_image_path = str(
                (
                    plugin_root / "btd6_api_processor/output/fake_leaderboard.png"
                ).resolve()
            )
            llm = FakeLLM(
                [
                    _assistant_response(
                        "",
                        [
                            LLMToolCall(
                                id="call_1",
                                name="get_activity_leaderboard",
                                arguments='{"leaderboard_type":"race"}',
                            )
                        ],
                    ),
                    _assistant_response("竞速排行榜来啦"),
                ]
            )
            runner = AgentRunner(
                llm=llm,  # type: ignore[arg-type]
                memory=GroupChatMemory(history_turns=3),
                settings=_settings(),
                tool_registry=registry,
                context_manager=FakeContextManager(),  # type: ignore[arg-type]
            )

            response = await runner.run(_event("看竞速排行榜"), trace_id="test-trace")

        self.assertTrue(response.should_send)
        self.assertEqual(response.response_type, "image")
        self.assertEqual(response.image_path, expected_image_path)
        self.assertEqual(response.text, "竞速排行榜来啦")
        self.assertEqual(command_processor.calls, [])
        self.assertEqual(len(activity_runner.calls), 1)

    async def test_image_tool_result_survives_later_text_tool(self) -> None:
        command_processor = FakeCommandProcessor()
        activity_runner = FakeActivityRunner()
        command_processor.next_results = [
            {
                "module": "command_processor",
                "ok": True,
                "command": "search_game_knowledge",
                "data": {"query": "竞速"},
                "message": "知识库检索完成",
                "extra_prompt": "",
                "response_type": "text",
                "image_path": "",
            },
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = _create_temp_plugin_root(temp_dir)
            registry = ToolRegistry(
                _runtime(
                    command_processor,
                    plugin_root=plugin_root,
                    subprocess_runner=activity_runner,
                )
            )
            expected_image_path = str(
                (
                    plugin_root / "btd6_api_processor/output/fake_leaderboard.png"
                ).resolve()
            )
            llm = FakeLLM(
                [
                    _assistant_response(
                        "",
                        [
                            LLMToolCall(
                                id="call_1",
                                name="get_activity_leaderboard",
                                arguments='{"leaderboard_type":"race"}',
                            ),
                            LLMToolCall(
                                id="call_2",
                                name="search_game_knowledge",
                                arguments='{"query":"竞速"}',
                            ),
                        ],
                    ),
                    _assistant_response("竞速排行榜和相关资料都整理好了"),
                ]
            )
            runner = AgentRunner(
                llm=llm,  # type: ignore[arg-type]
                memory=GroupChatMemory(history_turns=3),
                settings=_settings(),
                tool_registry=registry,
                context_manager=FakeContextManager(),  # type: ignore[arg-type]
            )

            response = await runner.run(
                _event("看竞速排行榜，再查竞速资料"), trace_id="test-trace"
            )

        self.assertTrue(response.should_send)
        self.assertEqual(response.response_type, "image")
        self.assertEqual(response.image_path, expected_image_path)
        self.assertEqual(response.text, "竞速排行榜和相关资料都整理好了")

    def test_llm_client_parses_openai_tool_calls(self) -> None:
        client = LLMClient(_settings())

        parsed = client._parse_tool_calls(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "today_most_active",
                            "arguments": '{"top_n":5}',
                        },
                    }
                ],
            }
        )

        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].id, "call_1")
        self.assertEqual(parsed[0].name, "today_most_active")
        self.assertEqual(parsed[0].arguments, '{"top_n":5}')


if __name__ == "__main__":
    unittest.main()
