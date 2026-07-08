from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from bot_app.agent.runner import AgentRunner  # noqa: E402
from bot_app.agent.prompt_manager import PromptManager  # noqa: E402
from bot_app.agent.tool_registry import ToolRegistry  # noqa: E402
from bot_app.agent.types import ToolCallRecord  # noqa: E402
from bot_app.context_contracts import (  # noqa: E402
    build_user_session_memory_context,
    serialize_session_summary,
)
from bot_app.command_processor import CommandProcessor  # noqa: E402
from bot_app.context_manager import ContextManager  # noqa: E402
from bot_app.db_manager import BotDatabase  # noqa: E402
from bot_app.group_context import (  # noqa: E402
    build_message_record_metadata,
    build_recent_group_window,
)
from bot_app.llm_client import LLMClient, LLMResponse, LLMToolCall  # noqa: E402
from bot_app.llm_processor import (  # noqa: E402
    LLMMessageProcessor,
    LegacyLLMPipelineDisabled,
)
from bot_app.memory import GroupChatMemory  # noqa: E402
from bot_app.protocol import CommandResultPayload, EventPayload, IntentPayload  # noqa: E402
from bot_app.sender import MessageSender  # noqa: E402
from bot_app.settings import AppSettings  # noqa: E402
from bot_app.tools import ToolRuntime  # noqa: E402
from bot_app.tools.knowledge_service import sanitize_kb_query  # noqa: E402
from bot_app.tools.web_search_service import parse_duckduckgo_results  # noqa: E402
from bot_app.tools.features import get_feature_catalog  # noqa: E402
from bot_app.tools.social_service import build_summarize_recent_messages_result  # noqa: E402
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
                "message_id": "m1",
                "sender_card": "A",
                "sender_nickname": "",
                "raw_message": "hello",
                "normalized_text": "hello",
                "mention_relation": "none",
                "is_text_meaningful": 1,
                "message_type": "text",
                "created_at": "2026-06-10 10:00:00",
                "user_id": "1",
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

    async def chat_with_override(self, **kwargs: Any) -> str:
        self.requests.append(kwargs)
        if not self.responses:
            raise AssertionError("unexpected extra LLM call")
        response = self.responses.pop(0)
        return response.content


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
    (corpus_dir / "beast.md").write_text(
        "野兽之主 Beast Handler 下路 0-0-4 数据。", encoding="utf-8"
    )
    (corpus_dir / "long.md").write_text("忍者猴\n" * 9000, encoding="utf-8")
    (kb_root / "kb_interface.py").write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "def interface_search(query, top_n, db_path, corpus_dir, alias_file):",
                "    Path(corpus_dir, 'last_query.txt').write_text(query, encoding='utf-8')",
                "    if '野兽之主' in query or 'Beast Handler' in query:",
                "        return [str(Path(corpus_dir) / 'beast.md')][:top_n]",
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


def _create_empty_fake_knowledge_base(plugin_root: Path) -> Path:
    kb_root = plugin_root / "btd6_knowledge_base"
    corpus_dir = kb_root / "wiki_data_second_cleaned" / "btd6"
    corpus_dir.mkdir(parents=True)
    (kb_root / "wiki_data_cleaned" / "btd6").mkdir(parents=True)
    (kb_root / "search_index.db").write_text("", encoding="utf-8")
    (kb_root / "kb_interface.py").write_text(
        "\n".join(
            [
                "def interface_search(query, top_n, db_path, corpus_dir, alias_file):",
                "    return []",
                "def interface_extract(file_name, keywords_query, corpus_dir, max_chars):",
                "    return {'file': file_name, 'max_chars': max_chars, 'text': '', 'keywords': [], 'chars': 0}",
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


def _planning_response(
    decision: str,
    *,
    reason: str = "",
    draft_reply: str = "",
    tool_calls: list[dict[str, Any]] | None = None,
) -> LLMResponse:
    return _assistant_response(
        json.dumps(
            {
                "decision": decision,
                "reason": reason,
                "draft_reply": draft_reply,
                "tool_calls": tool_calls or [],
            },
            ensure_ascii=False,
        )
    )


class AgentToolCallingTests(unittest.IsolatedAsyncioTestCase):
    def test_sanitize_kb_query_strips_invocation_noise_and_rewrites_domain_terms(
        self,
    ) -> None:
        self.assertEqual(
            sanitize_kb_query("@小污猫 野兽之主数据"),
            "野兽之主",
        )
        self.assertEqual(
            sanitize_kb_query("[CQ:at,qq=1693903050] 004野兽之主能不能抓DDT"),
            "004 0-0-4 野兽之主 DDT",
        )
        self.assertIn("下路", sanitize_kb_query("野兽之主路线3详细数据"))
        self.assertEqual(
            sanitize_kb_query("野兽之主下路详细数据"),
            "野兽之主 下路",
        )
        self.assertEqual(
            sanitize_kb_query("Mermaid Monkey stats"),
            "人鱼猴",
        )
        self.assertEqual(
            sanitize_kb_query("Beast Handler 004 DDT"),
            "野兽之主 004 0-0-4 DDT",
        )
        self.assertIn("能量", sanitize_kb_query("能量图腾数据"))

    def test_extract_keyword_windows_prioritizes_relevant_snippets(self) -> None:
        from bot_app.tools.knowledge_service import extract_keyword_windows

        content = (
            "基础资料 野兽之主 价格 250。"
            + "图标 000 001 002 003 004 0-0-4 " * 20
            + "逆戟鲸 升级价格 12500，可抓住所有大型气球并拖入海底深渊。"
        )

        snippet = extract_keyword_windows(
            content,
            keywords_query="004野兽之主能不能抓DDT",
            max_chars=300,
        )

        self.assertIn("逆戟鲸", snippet)
        self.assertLessEqual(len(snippet), 300)

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
                "search_web",
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
                ("search_web", {}, False),
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
            noisy_search_result = await registry.execute(
                "search_game_knowledge",
                {"query": "@小污猫 野兽之主数据", "top_n": 3},
                _event("@小污猫 野兽之主数据"),
            )
            cleaned_query = (
                kb_root / "wiki_data_second_cleaned" / "btd6" / "last_query.txt"
            ).read_text(encoding="utf-8")
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
        self.assertEqual(search_result["data"]["knowledge_query"], "忍者猴")
        self.assertEqual(search_result["data"]["knowledge_query_raw"], "忍者猴机制")
        self.assertTrue(noisy_search_result["ok"])
        self.assertEqual(
            noisy_search_result["data"]["knowledge_query"],
            "野兽之主",
        )
        self.assertEqual(noisy_search_result["data"]["matched_count"], 1)
        self.assertNotIn("@小污猫", cleaned_query)
        self.assertTrue(read_result["ok"])
        self.assertIn("大师忍者", read_result["data"]["content"])
        self.assertGreater(read_result["data"]["content_chars"], 0)
        self.assertTrue(long_result["ok"])
        self.assertEqual(long_result["data"]["mode"], "extract")
        self.assertGreater(long_result["data"]["content_chars"], 0)
        self.assertFalse(rejected_result["ok"])
        self.assertEqual(command_processor.calls, [])
        self.assertNotIn(str(kb_root.resolve()), sys.path)

    def test_parse_duckduckgo_results_extracts_public_result_fields(self) -> None:
        html = """
        <html><body>
          <div class="result">
            <a class="result__a" href="/l/?kh=-1&amp;uddg=https%3A%2F%2Fexample.com%2Fbtd6%2Fpatch">
              BTD6 Patch Notes
            </a>
            <a class="result__snippet">Latest update notes and tower changes.</a>
          </div>
          <div class="result">
            <a class="result__a" href="https://wiki.biligame.com/btd6/DDT">DDT</a>
            <div class="result__snippet">DDT properties and counters.</div>
          </div>
          <table>
            <tr>
              <td>
                <a class="result-link" href="https://tdgametools.com/tools/btd6-bloon-counter-checker">
                  BTD6 Bloon Counter Checker
                </a>
              </td>
            </tr>
            <tr><td>Plain-English counter plan for DDTs.</td></tr>
          </table>
        </body></html>
        """

        results = parse_duckduckgo_results(html, top_n=5)

        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]["rank"], 1)
        self.assertEqual(results[0]["title"], "BTD6 Patch Notes")
        self.assertEqual(results[0]["url"], "https://example.com/btd6/patch")
        self.assertEqual(results[0]["source_domain"], "example.com")
        self.assertIn("tower changes", results[0]["snippet"])
        self.assertEqual(results[1]["source_domain"], "wiki.biligame.com")
        self.assertEqual(results[2]["source_domain"], "tdgametools.com")

    async def test_web_search_tool_uses_runtime_registry_without_real_network(
        self,
    ) -> None:
        class FakeSearchResponse:
            text = """
            <html><body>
              <div class="result">
                <a class="result__a" href="/l/?uddg=https%3A%2F%2Fexample.com%2Fguide">
                  BTD6 DDT counter guide
                </a>
                <div class="result__snippet">Use camo detection and lead-popping damage.</div>
              </div>
            </body></html>
            """

            def raise_for_status(self) -> None:
                return None

        command_processor = FakeCommandProcessor()
        registry = ToolRegistry(_runtime(command_processor))
        with patch(
            "bot_app.tools.web_search_service.requests.post",
            return_value=FakeSearchResponse(),
        ) as fake_get:
            result = await registry.execute(
                "search_web",
                {"query": "@小污猫 BTD6 DDT counter", "top_n": 3},
                _event("搜一下 DDT 怎么处理"),
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["module"], "tool.web")
        self.assertEqual(result["data"]["query"], "BTD6 DDT counter")
        self.assertEqual(result["data"]["result_count"], 1)
        self.assertEqual(result["data"]["results"][0]["source_domain"], "example.com")
        self.assertIn("法律", result["extra_prompt"])
        self.assertIn("违法违规", result["extra_prompt"])
        fake_get.assert_called_once()
        self.assertEqual(command_processor.calls, [])

    def test_build_knowledge_summary_marks_grounded_read(self) -> None:
        runner = AgentRunner(
            llm=FakeLLM([]),  # type: ignore[arg-type]
            memory=GroupChatMemory(history_turns=3),
            settings=_settings(),
            tool_registry=ToolRegistry(_runtime(FakeCommandProcessor())),
            context_manager=FakeContextManager(),  # type: ignore[arg-type]
        )
        tool_records = [
            ToolCallRecord(
                name="search_game_knowledge",
                arguments={"query": "人鱼猴数据"},
                result={
                    "module": "tool.knowledge",
                    "ok": True,
                    "command": "search_game_knowledge",
                    "data": {
                        "knowledge_query": "人鱼猴",
                        "knowledge_query_raw": "人鱼猴数据",
                        "matched_count": 1,
                        "matched_paths": ["/kb/人鱼猴.html"],
                    },
                    "message": "游戏知识检索完成",
                    "extra_prompt": "",
                    "response_type": "text",
                    "image_path": "",
                },
            ),
            ToolCallRecord(
                name="read_game_knowledge_file",
                arguments={"file_path": "/kb/人鱼猴.html"},
                result={
                    "module": "tool.knowledge",
                    "ok": True,
                    "command": "read_game_knowledge_file",
                    "data": {
                        "file_path": "/kb/人鱼猴.html",
                        "content": "人鱼猴详细数据",
                        "content_chars": 8,
                    },
                    "message": "知识库文件读取完成",
                    "extra_prompt": "",
                    "response_type": "text",
                    "image_path": "",
                },
            ),
        ]

        summary = runner._build_knowledge_summary(  # type: ignore[attr-defined]
            event=_event("人鱼猴数据"),
            planning_result=None,
            tool_records=tool_records,
            final_text="人鱼猴的数据如下",
        )

        assert summary is not None
        self.assertEqual(summary["knowledge_query"], "人鱼猴")
        self.assertEqual(summary["matched_count"], 1)
        self.assertEqual(summary["read_count"], 1)
        self.assertTrue(summary["answer_grounded"])
        self.assertEqual(summary["fallback_reason"], "")
        self.assertEqual(summary["content_chars"], 8)

    async def test_runner_logs_knowledge_summary_after_forced_read(self) -> None:
        command_processor = FakeCommandProcessor()
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = _create_temp_plugin_root(temp_dir)
            _create_fake_knowledge_base(plugin_root)
            registry = ToolRegistry(_runtime(command_processor, plugin_root=plugin_root))
            llm = FakeLLM(
                [
                    _planning_response(
                        "call_tools",
                        tool_calls=[
                            {
                                "name": "search_game_knowledge",
                                "arguments": {"query": "忍者猴机制"},
                            }
                        ],
                    ),
                    _planning_response("final", reason="搜索结果已足够"),
                    _planning_response("final", reason="读取结果已足够"),
                    _assistant_response("忍者猴可以升级为大师忍者"),
                ]
            )
            runner = AgentRunner(
                llm=llm,  # type: ignore[arg-type]
                memory=GroupChatMemory(history_turns=3),
                settings=_settings(),
                tool_registry=registry,
                context_manager=FakeContextManager(),  # type: ignore[arg-type]
            )
            flow_calls: list[dict[str, Any]] = []

            def _capture_flow(*, stage: str, trace_id: str, **fields: Any) -> None:
                flow_calls.append({"stage": stage, "trace_id": trace_id, **fields})

            with patch("bot_app.agent.runner.log_flow", side_effect=_capture_flow):
                response = await runner.run(
                    _event("查一下忍者猴机制"), trace_id="test-trace"
                )

        self.assertEqual(response.text, "忍者猴可以升级为大师忍者")
        summaries = [
            item for item in flow_calls if item["stage"] == "agent.knowledge_summary"
        ]
        self.assertEqual(len(summaries), 1)
        summary = summaries[0]
        self.assertEqual(summary["knowledge_query"], "忍者猴")
        self.assertEqual(summary["matched_count"], 1)
        self.assertEqual(summary["read_count"], 1)
        self.assertTrue(summary["answer_grounded"])
        self.assertEqual(summary["fallback_reason"], "")

    async def test_agent_forces_search_for_knowledge_query_when_model_returns_invalid_category_tool(self) -> None:
        command_processor = FakeCommandProcessor()
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = _create_temp_plugin_root(temp_dir)
            _create_fake_knowledge_base(plugin_root)
            runtime = _runtime(command_processor, plugin_root=plugin_root, db=FakeDb())
            registry = ToolRegistry(runtime)
            llm = FakeLLM(
                [
                    _planning_response(
                        "call_tools",
                        tool_calls=[
                            {
                                "name": "game_knowledge",
                                "arguments": {"query": "野兽之主数据"},
                            }
                        ],
                    ),
                    _planning_response("final", reason="已拿到检索结果"),
                    _assistant_response("野兽之主的资料已基于知识库读取。"),
                ]
            )
            runner = AgentRunner(
                llm=llm,  # type: ignore[arg-type]
                memory=GroupChatMemory(history_turns=3),
                settings=_settings(),
                tool_registry=registry,
                tool_runtime=runtime,
                context_manager=FakeContextManager(),  # type: ignore[arg-type]
            )

            response = await runner.run(_event("野兽之主数据"), trace_id="test-trace")

        self.assertEqual(response.text, "野兽之主的资料已基于知识库读取。")
        self.assertEqual(len(response.tool_calls), 2)
        self.assertEqual(response.tool_calls[0].name, "search_game_knowledge")
        self.assertEqual(response.tool_calls[1].name, "read_game_knowledge_file")

    async def test_agent_forces_read_after_search_hit_for_knowledge_query(self) -> None:
        command_processor = FakeCommandProcessor()
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = _create_temp_plugin_root(temp_dir)
            _create_fake_knowledge_base(plugin_root)
            runtime = _runtime(command_processor, plugin_root=plugin_root, db=FakeDb())
            registry = ToolRegistry(runtime)
            llm = FakeLLM(
                [
                    _planning_response(
                        "call_tools",
                        tool_calls=[
                            {
                                "name": "search_game_knowledge",
                                "arguments": {"query": "人鱼猴数据"},
                            }
                        ],
                    ),
                    _planning_response("final", reason="搜到候选页了"),
                    _planning_response("final", reason="读完文件可以回答"),
                    _assistant_response("人鱼猴的数据如下"),
                ]
            )
            runner = AgentRunner(
                llm=llm,  # type: ignore[arg-type]
                memory=GroupChatMemory(history_turns=3),
                settings=_settings(),
                tool_registry=registry,
                tool_runtime=runtime,
                context_manager=FakeContextManager(),  # type: ignore[arg-type]
            )

            response = await runner.run(_event("人鱼猴数据"), trace_id="test-trace")

        self.assertEqual(response.text, "人鱼猴的数据如下")
        self.assertEqual(len(response.tool_calls), 2)
        self.assertEqual(response.tool_calls[0].name, "search_game_knowledge")
        self.assertEqual(response.tool_calls[1].name, "read_game_knowledge_file")

    async def test_agent_uses_deterministic_fallback_after_knowledge_miss(self) -> None:
        command_processor = FakeCommandProcessor()
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = _create_temp_plugin_root(temp_dir)
            _create_empty_fake_knowledge_base(plugin_root)
            runtime = _runtime(command_processor, plugin_root=plugin_root, db=FakeDb())
            registry = ToolRegistry(runtime)
            llm = FakeLLM(
                [
                    _planning_response(
                        "call_tools",
                        tool_calls=[
                            {
                                "name": "search_game_knowledge",
                                "arguments": {"query": "不存在塔数据"},
                            }
                        ],
                    ),
                    _planning_response("final", reason="知识库未命中"),
                ]
            )
            runner = AgentRunner(
                llm=llm,  # type: ignore[arg-type]
                memory=GroupChatMemory(history_turns=3),
                settings=_settings(),
                tool_registry=registry,
                tool_runtime=runtime,
                context_manager=FakeContextManager(),  # type: ignore[arg-type]
            )

            response = await runner.run(_event("不存在塔数据"), trace_id="test-trace")

        self.assertIn("知识库里暂时没有检索到", response.text)
        self.assertIn("不能基于知识库证据确认结论", response.text)
        self.assertEqual(len(response.tool_calls), 1)
        self.assertEqual(
            [request["purpose"] for request in llm.requests],
            ["agent_planning", "agent_planning"],
        )

    async def test_agent_can_use_web_search_after_knowledge_miss(self) -> None:
        class FakeSearchResponse:
            text = """
            <html><body>
              <div class="result">
                <a class="result__a" href="/l/?uddg=https%3A%2F%2Fexample.com%2Fbtd6%2Fddt">
                  BTD6 DDT guide
                </a>
                <div class="result__snippet">DDTs need camo detection and lead-popping damage.</div>
              </div>
            </body></html>
            """

            def raise_for_status(self) -> None:
                return None

        command_processor = FakeCommandProcessor()
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = _create_temp_plugin_root(temp_dir)
            _create_empty_fake_knowledge_base(plugin_root)
            runtime = _runtime(command_processor, plugin_root=plugin_root, db=FakeDb())
            registry = ToolRegistry(runtime)
            llm = FakeLLM(
                [
                    _planning_response("final", reason="先让硬规则检索知识库"),
                    _planning_response(
                        "call_tools",
                        reason="知识库未命中，改用联网搜索",
                        tool_calls=[
                            {
                                "name": "search_web",
                                "arguments": {
                                    "query": "BTD6 DDT counter guide",
                                    "top_n": 3,
                                },
                            }
                        ],
                    ),
                    _planning_response("final", reason="联网结果足够回答"),
                    _assistant_response("联网资料显示，DDT 需要迷彩侦测和破铅伤害。"),
                ]
            )
            runner = AgentRunner(
                llm=llm,  # type: ignore[arg-type]
                memory=GroupChatMemory(history_turns=3),
                settings=_settings(),
                tool_registry=registry,
                tool_runtime=runtime,
                context_manager=FakeContextManager(),  # type: ignore[arg-type]
            )

            with patch(
                "bot_app.tools.web_search_service.requests.post",
                return_value=FakeSearchResponse(),
            ) as fake_get:
                response = await runner.run(
                    _event("DDT怎么处理比较稳 数据"), trace_id="test-trace"
                )

        self.assertEqual(response.text, "联网资料显示，DDT 需要迷彩侦测和破铅伤害。")
        self.assertEqual(
            [record.name for record in response.tool_calls],
            ["search_game_knowledge", "search_web"],
        )
        self.assertEqual(
            [request["purpose"] for request in llm.requests],
            [
                "agent_planning",
                "agent_planning",
                "agent_planning",
                "agent_rendering",
            ],
        )
        fake_get.assert_called_once()

    async def test_agent_uses_deterministic_fallback_after_knowledge_read_failure(
        self,
    ) -> None:
        command_processor = FakeCommandProcessor()
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = _create_temp_plugin_root(temp_dir)
            _create_fake_knowledge_base(plugin_root)
            runtime = _runtime(command_processor, plugin_root=plugin_root, db=FakeDb())
            registry = ToolRegistry(runtime)
            llm = FakeLLM(
                [
                    _planning_response(
                        "call_tools",
                        tool_calls=[
                            {
                                "name": "search_game_knowledge",
                                "arguments": {"query": "人鱼猴数据"},
                            },
                            {
                                "name": "read_game_knowledge_file",
                                "arguments": {"file_path": "/outside.md"},
                            },
                        ],
                    ),
                    _planning_response("final", reason="读取失败但尝试回答"),
                ]
            )
            runner = AgentRunner(
                llm=llm,  # type: ignore[arg-type]
                memory=GroupChatMemory(history_turns=3),
                settings=_settings(),
                tool_registry=registry,
                tool_runtime=runtime,
                context_manager=FakeContextManager(),  # type: ignore[arg-type]
            )

            response = await runner.run(_event("人鱼猴数据"), trace_id="test-trace")

        self.assertIn("没有成功读取到可用内容", response.text)
        self.assertEqual(len(response.tool_calls), 2)
        self.assertEqual(
            [request["purpose"] for request in llm.requests],
            ["agent_planning", "agent_planning"],
        )

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
                _planning_response(
                    "call_tools",
                    tool_calls=[{"name": "list_features", "arguments": {}}],
                ),
                _planning_response("final", reason="工具结果已足够"),
                _assistant_response("功能已列出"),
            ]
        )
        context_manager = FakeContextManager()
        runner = AgentRunner(
            llm=llm,  # type: ignore[arg-type]
            memory=GroupChatMemory(history_turns=3),
            settings=_settings(),
            tool_registry=registry,
            tool_runtime=_runtime(command_processor, db=FakeDb()),
            context_manager=context_manager,  # type: ignore[arg-type]
        )

        response = await runner.run(_event(), trace_id="test-trace")

        self.assertTrue(response.should_send)
        self.assertEqual(response.text, "功能已列出")
        self.assertEqual(response.token_usage, 51)
        self.assertEqual(command_processor.calls, [])
        self.assertEqual(
            context_manager.calls,
            [("1000", "2000", "test-trace"), ("1000", "2000", "test-trace")],
        )
        self.assertEqual(llm.requests[0]["purpose"], "agent_planning")
        self.assertEqual(llm.requests[1]["purpose"], "agent_planning")
        self.assertEqual(llm.requests[2]["purpose"], "agent_rendering")
        request_messages = llm.requests[0]["messages"]
        self.assertEqual(request_messages[-1]["role"], "user")
        user_payload = json.loads(request_messages[-1]["content"])
        self.assertEqual(user_payload["task"], "agent_planning")
        self.assertIn("reply_context", user_payload)
        self.assertEqual(
            user_payload["reply_context"]["current_event"]["text"],
            "你会什么",
        )
        self.assertEqual(
            user_payload["reply_context"]["recent_group_window"],
            [
                {
                    "message_id": "m1",
                    "speaker": "A",
                    "cleaned_text": "hello",
                    "created_at": "2026-06-10 10:00:00",
                    "mention_relation": "none",
                    "sequence": 1,
                }
            ],
        )

    async def test_legacy_llm_processor_run_delegates_to_agent_runner(self) -> None:
        command_processor = FakeCommandProcessor()
        registry = ToolRegistry(_runtime(command_processor))
        llm = FakeLLM(
            [
                _planning_response(
                    "call_tools",
                    tool_calls=[{"name": "list_features", "arguments": {}}],
                ),
                _planning_response("final", reason="工具结果已足够"),
                _assistant_response("功能已列出"),
            ]
        )
        processor = LLMMessageProcessor(
            llm=llm,  # type: ignore[arg-type]
            memory=GroupChatMemory(history_turns=3),
            settings=_settings(),
            context_manager=FakeContextManager(),  # type: ignore[arg-type]
            tool_registry=registry,
            tool_runtime=_runtime(command_processor, db=FakeDb()),
        )

        response = await processor.run(_event(), trace_id="legacy-trace")

        self.assertTrue(response.should_send)
        self.assertEqual(response.text, "功能已列出")
        self.assertEqual(command_processor.calls, [])
        self.assertEqual(
            [request["purpose"] for request in llm.requests],
            ["agent_planning", "agent_planning", "agent_rendering"],
        )

    async def test_legacy_llm_processor_step_methods_are_disabled(self) -> None:
        processor = LLMMessageProcessor(
            llm=FakeLLM([]),  # type: ignore[arg-type]
            memory=GroupChatMemory(history_turns=3),
            settings=_settings(),
        )

        with self.assertRaises(LegacyLLMPipelineDisabled):
            await processor.plan_intent(_event(), trace_id="legacy-trace")

        self.assertEqual(processor._llm.requests, [])  # type: ignore[attr-defined]

    async def test_ignore_message_tool_suppresses_send(self) -> None:
        command_processor = FakeCommandProcessor()
        registry = ToolRegistry(_runtime(command_processor))
        llm = FakeLLM([_planning_response("ignore", reason="普通闲聊")])
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

    async def test_reply_direct_returns_without_tool_or_rendering(self) -> None:
        command_processor = FakeCommandProcessor()
        registry = ToolRegistry(_runtime(command_processor))
        llm = FakeLLM(
            [_planning_response("reply_direct", draft_reply="我在，有什么事？")]
        )
        runner = AgentRunner(
            llm=llm,  # type: ignore[arg-type]
            memory=GroupChatMemory(history_turns=3),
            settings=_settings(),
            tool_registry=registry,
            context_manager=FakeContextManager(),  # type: ignore[arg-type]
        )

        response = await runner.run(_event("在吗"), trace_id="test-trace")

        self.assertTrue(response.should_send)
        self.assertEqual(response.text, "我在，有什么事？")
        self.assertEqual(response.response_type, "text")
        self.assertEqual(response.token_usage, 17)
        self.assertEqual(command_processor.calls, [])
        self.assertEqual(len(llm.requests), 1)
        self.assertEqual(llm.requests[0]["purpose"], "agent_planning")

    def test_planning_prompt_includes_router_tool_hints(self) -> None:
        settings = AppSettings(
            **{
                **_settings().__dict__,
                "router_tool_hints_prompt": "活动查询优先使用 game_news",
            }
        )
        manager = PromptManager(settings)

        prompt = manager.build_planning_prompt(_event())

        self.assertIn("活动查询优先使用 game_news", prompt)
        self.assertIn("ignore 规则", prompt)
        self.assertIn("闲聊规则", prompt)
        self.assertIn("工具规则", prompt)
        self.assertIn("完成规则", prompt)
        self.assertIn("search_web", prompt)
        self.assertIn("当前公开网页资料", prompt)
        self.assertIn("法律法规限制", prompt)

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
                    _planning_response(
                        "call_tools",
                        tool_calls=[
                            {
                                "name": "get_activity_leaderboard",
                                "arguments": {"leaderboard_type": "race"},
                            }
                        ],
                    ),
                    _planning_response("final", reason="已有图片工具结果"),
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
                    _planning_response(
                        "call_tools",
                        tool_calls=[
                            {
                                "name": "get_activity_leaderboard",
                                "arguments": {"leaderboard_type": "race"},
                            },
                            {
                                "name": "search_game_knowledge",
                                "arguments": {"query": "竞速"},
                            },
                        ],
                    ),
                    _planning_response("final", reason="图片和文本资料都已齐全"),
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

    def test_group_chat_memory_stores_compact_user_event(self) -> None:
        memory = GroupChatMemory(history_turns=3)

        memory.append_turn(
            group_id="1000",
            user_id="2000",
            user_text=json.dumps(
                {
                    "kind": "user_event",
                    "event": {
                        "group_id": "1000",
                        "user_id": "2000",
                        "text": "查今天活动",
                    },
                },
                ensure_ascii=False,
            ),
            assistant_text="我来查一下",
        )

        history = memory.get_history("1000", "2000")
        self.assertEqual(len(history), 2)
        stored_payload = json.loads(history[0]["content"])
        self.assertEqual(stored_payload["kind"], "user_event")
        self.assertEqual(stored_payload["event"]["text"], "查今天活动")
        self.assertNotIn("instruction", stored_payload)

    def test_build_user_session_memory_context_extracts_recent_turns_and_summary(
        self,
    ) -> None:
        history = [
            {"role": "assistant", "content": "[历史会话摘要] 用户最近在问活动时间"},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "kind": "user_event",
                        "event": {"text": "今天 boss 开了吗"},
                    },
                    ensure_ascii=False,
                ),
            },
            {"role": "assistant", "content": "我帮你查活动"},
        ]

        session_memory = build_user_session_memory_context(history, recent_turn_limit=6)

        self.assertEqual(
            session_memory["summary"]["raw_summary"], "用户最近在问活动时间"
        )
        self.assertEqual(
            session_memory["recent_turns"],
            [
                {"role": "user", "turn_type": "user_event", "text": "今天 boss 开了吗"},
                {"role": "assistant", "turn_type": "assistant_reply", "text": "我帮你查活动"},
            ],
        )

    def test_build_user_session_memory_context_reads_structured_session_summary(
        self,
    ) -> None:
        history = [
            {
                "role": "assistant",
                "content": serialize_session_summary(
                    {
                        "user_preferences": ["喜欢简短回答"],
                        "open_loops": ["等 boss 时间"],
                        "established_facts": ["用户在问活动"],
                        "recent_topic": "活动查询",
                        "restrictions": ["不要编造"],
                        "raw_summary": "用户最近在查活动时间。",
                    }
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "kind": "user_event",
                        "event": {"text": "今天 race 有吗"},
                    },
                    ensure_ascii=False,
                ),
            },
        ]

        session_memory = build_user_session_memory_context(history, recent_turn_limit=6)

        self.assertEqual(
            session_memory["summary"],
            {
                "user_preferences": ["喜欢简短回答"],
                "open_loops": ["等 boss 时间"],
                "established_facts": ["用户在问活动"],
                "recent_topic": "活动查询",
                "restrictions": ["不要编造"],
                "raw_summary": "用户最近在查活动时间。",
            },
        )
        self.assertEqual(
            session_memory["recent_turns"],
            [{"role": "user", "turn_type": "user_event", "text": "今天 race 有吗"}],
        )

    def test_build_recent_group_window_cleans_cq_and_marks_mentions(self) -> None:
        window = build_recent_group_window(
            [
                {
                    "message_id": "m1",
                    "user_id": "1",
                    "sender_card": "A",
                    "sender_nickname": "",
                    "raw_message": "[CQ:at,qq=999] 你好 [CQ:image,file=a.png]",
                    "normalized_text": "你好",
                    "mention_relation": "mention_bot",
                    "created_at": "2026-06-10 10:00:00",
                },
                {
                    "message_id": "m2",
                    "user_id": "2",
                    "sender_card": "",
                    "sender_nickname": "B",
                    "raw_message": "   [CQ:image,file=b.png]   ",
                    "normalized_text": "",
                    "mention_relation": "none",
                    "created_at": "2026-06-10 10:01:00",
                },
                {
                    "message_id": "m3",
                    "user_id": "3",
                    "sender_card": "",
                    "sender_nickname": "C",
                    "raw_message": "[@dummy] @测试机器人 来看下",
                    "normalized_text": "[@dummy] @测试机器人 来看下",
                    "mention_relation": "mention_bot",
                    "created_at": "2026-06-10 10:02:00",
                },
            ],
            limit=10,
            bot_qq="999",
            bot_name="测试机器人",
        )

        self.assertEqual(
            window,
            [
                {
                    "message_id": "m1",
                    "speaker": "A",
                    "cleaned_text": "你好",
                    "created_at": "2026-06-10 10:00:00",
                    "mention_relation": "mention_bot",
                    "sequence": 1,
                },
                {
                    "message_id": "m3",
                    "speaker": "C",
                    "cleaned_text": "[@dummy] @测试机器人 来看下",
                    "created_at": "2026-06-10 10:02:00",
                    "mention_relation": "mention_bot",
                    "sequence": 2,
                },
            ],
        )

    async def test_summarize_recent_messages_uses_cleaned_group_window(self) -> None:
        db = FakeDb()
        db.recent_rows = [
            {
                "message_id": "m1",
                "user_id": "1",
                "sender_card": "A",
                "sender_nickname": "",
                "raw_message": "[CQ:at,qq=999] 今天打 boss 吗",
                "normalized_text": "今天打 boss 吗",
                "mention_relation": "mention_user",
                "is_text_meaningful": 1,
                "message_type": "text",
                "created_at": "2026-06-10 10:00:00",
            },
            {
                "message_id": "m2",
                "user_id": "2",
                "sender_card": "",
                "sender_nickname": "B",
                "raw_message": "[CQ:image,file=a.png]",
                "normalized_text": "",
                "mention_relation": "none",
                "is_text_meaningful": 0,
                "message_type": "non_text",
                "created_at": "2026-06-10 10:01:00",
            },
        ]

        result = build_summarize_recent_messages_result(
            db=db,
            event=_event("总结最近消息"),
            arguments={"top_n": 5},
            module="tool.social",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["message_count"], 1)
        self.assertEqual(
            result["data"]["messages"],
            [
                {
                    "display_name": "A",
                    "text": "今天打 boss 吗",
                    "created_at": "2026-06-10 10:00:00",
                    "mention_relation": "mention_user",
                }
            ],
        )

    def test_bot_database_migrates_legacy_messages_table_before_creating_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "legacy.db"
            conn = __import__("sqlite3").connect(db_path)
            cur = conn.cursor()
            cur.executescript(
                """
                CREATE TABLE users (
                    user_id TEXT PRIMARY KEY,
                    nickname TEXT DEFAULT '',
                    card TEXT DEFAULT '',
                    role TEXT DEFAULT '',
                    title TEXT DEFAULT '',
                    level TEXT DEFAULT '',
                    total_messages INTEGER NOT NULL DEFAULT 0,
                    last_message_at TEXT DEFAULT ''
                );

                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT,
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    raw_message TEXT NOT NULL DEFAULT '',
                    sender_nickname TEXT DEFAULT '',
                    sender_card TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    extra_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(message_id, group_id)
                );

                CREATE TABLE daily_user_stats (
                    day TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    message_count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(day, group_id, user_id)
                );
                """
            )
            conn.commit()
            conn.close()

            db = BotDatabase(str(db_path))
            db._conn.close()

            conn = __import__("sqlite3").connect(db_path)
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(messages)")
            columns = {row[1] for row in cur.fetchall()}
            self.assertIn("normalized_text", columns)
            self.assertIn("mention_relation", columns)
            self.assertIn("is_text_meaningful", columns)
            self.assertIn("message_type", columns)

            cur.execute("PRAGMA index_list(messages)")
            index_names = {row[1] for row in cur.fetchall()}
            self.assertIn("idx_messages_group_created", index_names)
            self.assertIn("idx_messages_group_meaningful_created", index_names)
            conn.close()

    def test_build_message_record_metadata_marks_meaningful_text_and_mentions(self) -> None:
        metadata = build_message_record_metadata(
            "[CQ:at,qq=999]  今天打 boss 吗  [CQ:image,file=a.png]",
            bot_qq="999",
            bot_name="测试机器人",
        )

        self.assertEqual(
            metadata,
            {
                "normalized_text": "今天打 boss 吗",
                "mention_relation": "mention_bot",
                "is_text_meaningful": True,
                "message_type": "text",
                "mentioned_qqs": ["999"],
            },
        )

    async def test_context_manager_writes_structured_summary_message(self) -> None:
        llm = FakeLLM(
            [
                _assistant_response(
                    json.dumps(
                        {
                            "user_preferences": ["喜欢活动简报"],
                            "open_loops": ["等 boss 轮换"],
                            "established_facts": ["用户多次问活动"],
                            "recent_topic": "活动轮换查询",
                            "restrictions": ["不要编造"],
                            "raw_summary": "用户最近持续在询问各类活动轮换与开放时间。",
                        },
                        ensure_ascii=False,
                    )
                )
            ]
        )
        settings = _settings()
        settings = AppSettings(
            **{
                **settings.__dict__,
                "context_compact_max_chars": 30,
                "context_compact_keep_recent_turns": 1,
            }
        )
        manager = ContextManager(llm=llm, settings=settings)  # type: ignore[arg-type]
        memory = GroupChatMemory(history_turns=3)
        memory.replace_history(
            "1000",
            "2000",
            [
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "kind": "user_event",
                            "event": {"text": "最近 boss 什么时间开"},
                        },
                        ensure_ascii=False,
                    ),
                },
                {"role": "assistant", "content": "我帮你看下"},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "kind": "user_event",
                            "event": {"text": "还有 race 呢"},
                        },
                        ensure_ascii=False,
                    ),
                },
                {"role": "assistant", "content": "我再一起查"},
            ],
        )

        changed = await manager.compact_if_needed(
            memory,
            group_id="1000",
            user_id="2000",
            trace_id="compact-trace",
        )

        self.assertTrue(changed)
        history = memory.get_history("1000", "2000")
        self.assertEqual(history[0]["role"], "assistant")
        summary_payload = json.loads(history[0]["content"])
        self.assertEqual(summary_payload["kind"], "session_summary")
        self.assertEqual(summary_payload["recent_topic"], "活动轮换查询")
        session_memory = memory.build_user_session_memory_context("1000", "2000")
        self.assertEqual(
            session_memory["summary"]["open_loops"],
            ["等 boss 轮换"],
        )


if __name__ == "__main__":
    unittest.main()
