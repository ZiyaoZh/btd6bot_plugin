from __future__ import annotations

import asyncio
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError

try:
    import ncatbot  # noqa: F401
except ModuleNotFoundError:
    ncatbot_module = types.ModuleType("ncatbot")
    event_module = types.ModuleType("ncatbot.event")
    qq_module = types.ModuleType("ncatbot.event.qq")
    types_module = types.ModuleType("ncatbot.types")

    class _StubGroupMessageEvent:
        pass

    class _StubMessageArray:
        def __init__(self) -> None:
            self.items: list[tuple[str, str]] = []

        def add_at(self, value: str) -> None:
            self.items.append(("at", value))

        def add_text(self, value: str) -> None:
            self.items.append(("text", value))

        def add_image(self, value: str) -> None:
            self.items.append(("image", value))

    qq_module.GroupMessageEvent = _StubGroupMessageEvent
    types_module.MessageArray = _StubMessageArray
    sys.modules.update(
        {
            "ncatbot": ncatbot_module,
            "ncatbot.event": event_module,
            "ncatbot.event.qq": qq_module,
            "ncatbot.types": types_module,
        }
    )

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from bot_app.agent.runner import AgentRunner  # noqa: E402
from bot_app.agent.prompt_manager import PromptManager  # noqa: E402
from bot_app.agent.tool_registry import ToolRegistry  # noqa: E402
from bot_app.agent.types import AgentResponse, ToolCallRecord  # noqa: E402
from bot_app.application import BotApplication  # noqa: E402
from bot_app.artifact_store import ArtifactSource  # noqa: E402
from bot_app.context_contracts import (  # noqa: E402
    build_user_session_memory_context,
    serialize_session_summary,
)
from bot_app.context_manager import ContextManager  # noqa: E402
from bot_app.db_manager import BotDatabase  # noqa: E402
from bot_app.group_context import (  # noqa: E402
    build_message_record_metadata,
    build_recent_group_window,
)
from bot_app.harness.image_policy import classify_image_request  # noqa: E402
from bot_app.harness.policies import GroundingPolicy  # noqa: E402
from bot_app.harness.sessions import SessionLockManager  # noqa: E402
from bot_app.harness.tool_executor import ToolExecutor  # noqa: E402
from bot_app.harness.types import ModelTurnUpdate, ToolInvocation  # noqa: E402
from bot_app.llm_client import (  # noqa: E402
    LLMClient,
    LLMRequestError,
    LLMResponse,
    LLMToolCall,
)
from bot_app.memory import GroupChatMemory  # noqa: E402
from bot_app.model.types import ModelToolCall  # noqa: E402
from bot_app.protocol import CommandResultPayload, EventPayload  # noqa: E402
from bot_app.receiver import GroupMessageReceiver  # noqa: E402
from bot_app.sender import MessageSender  # noqa: E402
from bot_app.settings import AppSettings  # noqa: E402
from bot_app.tools import ToolDependencies  # noqa: E402
from bot_app.tools.knowledge_service import sanitize_kb_query  # noqa: E402
from bot_app.tools.web_search_service import parse_duckduckgo_results  # noqa: E402
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
    def __init__(self, *, include_brief_json: bool = True) -> None:
        self.calls: list[list[str]] = []
        self.include_brief_json = include_brief_json

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

        if mode == "brief-image":
            image_path = root / "output" / "fake_activity_brief.png"
            json_path = root / "output" / "fake_activity_snapshot.json"
            image_path.write_bytes(b"fake png")
            report_lines = ["# 来源: 测试"]
            if self.include_brief_json:
                json_path.write_text('{"activity_type":"race"}', encoding="utf-8")
                report_lines.append("# JSON: output/fake_activity_snapshot.json")
            report_lines.extend(
                [
                    "# 图片: output/fake_activity_brief.png",
                    "",
                    "# 竞速情报",
                ]
            )
            output_path.write_text(
                "\n".join(report_lines),
                encoding="utf-8",
            )
        elif mode == "leaderboard":
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
            json_path = root / "output" / "fake_activity_detail.json"
            json_path.write_text('{"activity_type":"race"}', encoding="utf-8")
            output_path.write_text(
                "# JSON: output/fake_activity_detail.json\n\n# 详情\n测试活动详情",
                encoding="utf-8",
            )

        return ProcessResult(returncode=0)


def _runtime(
    *,
    db: FakeDb | None = None,
    manual_file: Path | None = None,
    plugin_root: Path = PLUGIN_ROOT,
    subprocess_runner: Any = None,
) -> ToolDependencies:
    return ToolDependencies.create(
        db=db or FakeDb(),
        plugin_root=plugin_root,
        manual_file=manual_file or Path("/missing/manual.md"),
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
    corpus_dir = kb_root / "knowledge_docs"
    corpus_dir.mkdir(parents=True)
    (corpus_dir / "monkey.md").write_text(
        "忍者猴可以升级为大师忍者。", encoding="utf-8"
    )
    (corpus_dir / "beast.md").write_text(
        "野兽之主 Beast Handler 下路 0-0-4 数据。", encoding="utf-8"
    )
    (corpus_dir / "long.md").write_text("忍者猴\n" * 9000, encoding="utf-8")
    rounds_content = "# 回合列表\n\n" + "| 100 | 红气球 | 测试回合数据 |\n" * 5000
    (corpus_dir / "rounds.md").write_text(rounds_content, encoding="utf-8")
    (corpus_dir / "legacy.txt").write_text("旧知识库文本", encoding="utf-8")
    (kb_root / "kb_interface.py").write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "def interface_search(query, top_n, corpus_dir, alias_file):",
                "    Path(corpus_dir, 'last_query.txt').write_text(query, encoding='utf-8')",
                "    if '野兽之主' in query or 'Beast Handler' in query:",
                "        return [str(Path(corpus_dir) / 'beast.md')][:top_n]",
                "    return [str(Path(corpus_dir) / 'monkey.md')][:top_n]",
                "def interface_extract_paragraphs(file_name, keywords_query, corpus_dir, alias_file, max_chars, top_k=8):",
                "    if Path(file_name).name != 'rounds.md':",
                "        return {}",
                "    text = Path(file_name).read_text(encoding='utf-8').strip()[:max_chars]",
                "    return {",
                "        'file': file_name,",
                "        'text': text,",
                "        'chars': len(text),",
                "        'max_chars': max_chars,",
                "        'paragraphs': [{",
                "            'section': '回合列表',",
                "            'level': 1,",
                "            'content': text,",
                "            'score': 100.0,",
                "            'matched_terms': ['回合列表'],",
                "        }],",
                "    }",
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
    corpus_dir = kb_root / "knowledge_docs"
    corpus_dir.mkdir(parents=True)
    (kb_root / "kb_interface.py").write_text(
        "\n".join(
            [
                "def interface_search(query, top_n, corpus_dir, alias_file):",
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


def _tool_response(
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    call_id: str = "call_1",
) -> LLMResponse:
    return _assistant_response(
        "",
        [
            LLMToolCall(
                id=call_id,
                name=name,
                arguments=json.dumps(arguments or {}, ensure_ascii=False),
            )
        ],
    )


class AgentToolCallingTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_notifies_each_model_turn(self) -> None:
        updates: list[ModelTurnUpdate] = []

        async def _capture(update: ModelTurnUpdate) -> None:
            updates.append(update)

        runner = AgentRunner(
            llm=FakeLLM(
                [
                    _tool_response("list_features"),
                    _assistant_response("功能已列出"),
                ]
            ),  # type: ignore[arg-type]
            memory=GroupChatMemory(history_turns=3),
            settings=_settings(),
            tool_registry=ToolRegistry(_runtime()),
            context_manager=FakeContextManager(),  # type: ignore[arg-type]
        )

        response = await runner.run(
            _event(),
            trace_id="model-turn-notifications",
            on_model_turn=_capture,
        )

        self.assertEqual(response.text, "功能已列出")
        self.assertEqual([update.index for update in updates], [1, 2])
        self.assertFalse(updates[0].is_final)
        self.assertEqual(updates[0].tool_calls[0].name, "list_features")
        self.assertTrue(updates[1].is_final)
        self.assertEqual(updates[1].content, "功能已列出")

    async def test_application_sends_only_turn_content_then_final_when_enabled(
        self,
    ) -> None:
        sent: list[str] = []
        committed: list[str] = []

        class _Agent:
            async def run(self, *args: Any, **kwargs: Any) -> AgentResponse:
                handler = kwargs["on_model_turn"]
                if handler is None:
                    raise AssertionError("model turn handler was not configured")
                await handler(
                    ModelTurnUpdate(
                        index=1,
                        content="",
                        tool_calls=(
                            ModelToolCall(
                                id="call_empty",
                                name="search_game_knowledge",
                                arguments={"query": "功能列表"},
                            ),
                        ),
                        model="fake-model",
                        is_final=False,
                    )
                )
                await handler(
                    ModelTurnUpdate(
                        index=2,
                        content="我先查询功能列表。",
                        tool_calls=(
                            ModelToolCall(
                                id="call_1",
                                name="list_features",
                                arguments={},
                            ),
                        ),
                        model="fake-model",
                        is_final=False,
                    )
                )
                await handler(
                    ModelTurnUpdate(
                        index=3,
                        content="功能已列出",
                        model="fake-model",
                        is_final=True,
                    )
                )
                return AgentResponse(text="功能已列出")

            async def commit_turn(self, *args: Any, **kwargs: Any) -> None:
                committed.append("committed")

        class _Sender:
            async def send_group_text(self, **kwargs: Any) -> None:
                sent.append(kwargs["text"])

            async def send_group_image(self, **kwargs: Any) -> None:
                raise AssertionError("unexpected image")

        class _Usage:
            def pop_trace_total_tokens(self, trace_id: str) -> int:
                return 34

        with tempfile.TemporaryDirectory() as temp_dir:
            database = BotDatabase(str(Path(temp_dir) / "bot.db"))
            database.set_group_setting(
                "1000", "model_turn_messages_enabled", "true"
            )
            application = BotApplication(
                settings=_settings(),
                receiver=object(),  # type: ignore[arg-type]
                sender=_Sender(),  # type: ignore[arg-type]
                agent=_Agent(),  # type: ignore[arg-type]
                llm=_Usage(),  # type: ignore[arg-type]
                database=database,
            )

            await application.process_event(
                event=_event(),
                trace_id="process-messages",
                reply_group_id="1000",
                at_user_id="2000",
                send_failure_notice=True,
            )

        self.assertEqual(
            sent,
            ["我先查询功能列表。", "功能已列出"],
        )
        self.assertEqual(committed, ["committed"])

    async def test_admin_model_turn_command_is_persisted_per_group(self) -> None:
        sent: list[str] = []

        class _Sender:
            async def send_group_text(self, **kwargs: Any) -> None:
                sent.append(kwargs["text"])

        class _Usage:
            def pop_trace_total_tokens(self, trace_id: str) -> int:
                return 0

        with tempfile.TemporaryDirectory() as temp_dir:
            database = BotDatabase(str(Path(temp_dir) / "bot.db"))
            receiver = GroupMessageReceiver(_settings(), db=database)
            application = BotApplication(
                settings=_settings(),
                receiver=receiver,
                sender=_Sender(),  # type: ignore[arg-type]
                agent=object(),  # type: ignore[arg-type]
                llm=_Usage(),  # type: ignore[arg-type]
                database=database,
            )
            msg = types.SimpleNamespace(
                group_id="1000",
                user_id="2000",
                raw_message="@机器人 开启模型过程",
                sender=types.SimpleNamespace(role="admin"),
            )

            handled = await application._handle_admin_control(
                msg, trace_id="model-turns-on"
            )

            self.assertTrue(handled)
            self.assertTrue(
                database.get_group_bool_setting(
                    "1000", "model_turn_messages_enabled"
                )
            )
            self.assertFalse(
                database.get_group_bool_setting(
                    "another-group", "model_turn_messages_enabled"
                )
            )

            msg.raw_message = "@机器人 关闭模型过程"
            await application._handle_admin_control(msg, trace_id="model-turns-off")

            self.assertFalse(
                database.get_group_bool_setting(
                    "1000", "model_turn_messages_enabled"
                )
            )

        self.assertEqual(
            sent,
            ["已开启本群模型过程消息", "已关闭本群模型过程消息，仅发送最终结果"],
        )

    async def test_proactive_chat_setting_is_persisted_per_group(self) -> None:
        settings = AppSettings(
            **{
                **_settings().__dict__,
                "target_group_ids": ("1000", "1001"),
                "proactive_chat_enabled": True,
            }
        )

        def _message(group_id: str, raw_message: str) -> Any:
            return types.SimpleNamespace(
                group_id=group_id,
                user_id="2000",
                raw_message=raw_message,
                sender=types.SimpleNamespace(role="member"),
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            database = BotDatabase(str(Path(temp_dir) / "bot.db"))
            receiver = GroupMessageReceiver(settings, db=database)

            receiver.set_proactive_chat_enabled("1000", False)

            self.assertFalse(receiver.should_accept(_message("1000", "普通消息")))
            self.assertTrue(receiver.should_accept(_message("1001", "普通消息")))
            self.assertTrue(
                receiver.should_accept(_message("1000", "@机器人 普通消息"))
            )

            restarted_receiver = GroupMessageReceiver(settings, db=database)
            self.assertFalse(
                restarted_receiver.is_proactive_chat_enabled("1000")
            )
            self.assertTrue(restarted_receiver.is_proactive_chat_enabled("1001"))

    async def test_admin_start_stop_and_proactive_commands_are_per_group(self) -> None:
        sent: list[tuple[str, str]] = []

        class _Sender:
            async def send_group_text(self, **kwargs: Any) -> None:
                sent.append((kwargs["group_id"], kwargs["text"]))

        class _Usage:
            def pop_trace_total_tokens(self, trace_id: str) -> int:
                return 0

        settings = AppSettings(
            **{
                **_settings().__dict__,
                "target_group_ids": ("1000", "1001"),
                "proactive_chat_enabled": True,
            }
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            database = BotDatabase(str(Path(temp_dir) / "bot.db"))
            receiver = GroupMessageReceiver(settings, db=database)
            application = BotApplication(
                settings=settings,
                receiver=receiver,
                sender=_Sender(),  # type: ignore[arg-type]
                agent=object(),  # type: ignore[arg-type]
                llm=_Usage(),  # type: ignore[arg-type]
                database=database,
            )
            msg = types.SimpleNamespace(
                group_id="1000",
                user_id="2000",
                raw_message="@机器人 关闭主动接话",
                sender=types.SimpleNamespace(role="admin"),
            )

            self.assertTrue(
                await application._handle_admin_control(
                    msg, trace_id="proactive-off"
                )
            )
            self.assertFalse(receiver.is_proactive_chat_enabled("1000"))
            self.assertTrue(receiver.is_proactive_chat_enabled("1001"))

            msg.raw_message = "@机器人 终止"
            self.assertTrue(
                await application._handle_admin_control(msg, trace_id="stop-group")
            )
            self.assertFalse(application.is_group_enabled("1000"))
            self.assertTrue(application.is_group_enabled("1001"))

            restarted_application = BotApplication(
                settings=settings,
                receiver=GroupMessageReceiver(settings, db=database),
                sender=_Sender(),  # type: ignore[arg-type]
                agent=object(),  # type: ignore[arg-type]
                llm=_Usage(),  # type: ignore[arg-type]
                database=database,
            )
            self.assertFalse(restarted_application.is_group_enabled("1000"))
            self.assertTrue(restarted_application.is_group_enabled("1001"))

        self.assertEqual(
            sent,
            [
                ("1000", "已关闭本群主动接话，仅响应@消息"),
                ("1000", "已终止本群机器人对话处理"),
            ],
        )

    async def test_process_event_skips_only_a_disabled_group(self) -> None:
        class _Agent:
            def __init__(self) -> None:
                self.calls = 0

            async def run(self, *args: Any, **kwargs: Any) -> AgentResponse:
                self.calls += 1
                return AgentResponse(text="不应生成")

        agent = _Agent()
        with tempfile.TemporaryDirectory() as temp_dir:
            database = BotDatabase(str(Path(temp_dir) / "bot.db"))
            database.set_group_setting("1000", "bot_enabled", "false")
            application = BotApplication(
                settings=_settings(),
                receiver=object(),  # type: ignore[arg-type]
                sender=object(),  # type: ignore[arg-type]
                agent=agent,  # type: ignore[arg-type]
                llm=object(),  # type: ignore[arg-type]
                database=database,
            )

            await application.process_event(
                event=_event(),
                trace_id="disabled-group-event",
                reply_group_id="1000",
                at_user_id=None,
                send_failure_notice=False,
            )

        self.assertEqual(agent.calls, 0)

    async def test_system_command_list_reports_group_state_to_members(self) -> None:
        sent: list[str] = []

        class _Sender:
            async def send_group_text(self, **kwargs: Any) -> None:
                sent.append(kwargs["text"])

        class _Usage:
            def pop_trace_total_tokens(self, trace_id: str) -> int:
                return 0

        with tempfile.TemporaryDirectory() as temp_dir:
            database = BotDatabase(str(Path(temp_dir) / "bot.db"))
            database.set_group_setting("1000", "bot_enabled", "false")
            database.set_group_setting("1000", "proactive_chat_enabled", "false")
            database.set_group_setting(
                "1000", "model_turn_messages_enabled", "true"
            )
            receiver = GroupMessageReceiver(_settings(), db=database)
            application = BotApplication(
                settings=_settings(),
                receiver=receiver,
                sender=_Sender(),  # type: ignore[arg-type]
                agent=object(),  # type: ignore[arg-type]
                llm=_Usage(),  # type: ignore[arg-type]
                database=database,
            )
            msg = types.SimpleNamespace(
                group_id="1000",
                user_id="2000",
                raw_message="@机器人 系统指令列表",
                sender=types.SimpleNamespace(role="member"),
            )

            control = receiver.parse_admin_control(msg)
            self.assertTrue(control["matched"])
            self.assertTrue(control["authorized"])
            self.assertEqual(control["action"], "system_commands")
            await application.handle_group_message(msg)

            msg.raw_message = "@机器人 启动"
            self.assertFalse(receiver.parse_admin_control(msg)["authorized"])

        self.assertEqual(len(sent), 1)
        self.assertIn("@机器人 系统指令列表", sent[0])
        self.assertIn("对话处理：关闭", sent[0])
        self.assertIn("主动接话：关闭", sent[0])
        self.assertIn("模型过程消息：开启", sent[0])

    async def test_tool_executor_rejects_invalid_schema_before_handler(self) -> None:
        registry = ToolRegistry(_runtime())
        executor = ToolExecutor(registry)

        result = await executor.execute(
            ToolInvocation(
                id="call_invalid",
                name="list_features",
                arguments={"unexpected": True},
            ),
            _event(),
        )

        self.assertFalse(result["ok"])
        self.assertIn("validation_errors", result["data"])
        self.assertIn("not allowed", result["data"]["validation_errors"][0])

    async def test_tool_executor_normalizes_timeout(self) -> None:
        registry = ToolRegistry(_runtime())
        executor = ToolExecutor(registry, timeout_seconds=0.01)

        async def _slow_execute(*args: Any, **kwargs: Any) -> CommandResultPayload:
            await asyncio.sleep(1)
            raise AssertionError("unreachable")

        with patch.object(registry, "execute", side_effect=_slow_execute):
            result = await executor.execute(
                ToolInvocation(id="call_slow", name="list_features", arguments={}),
                _event(),
            )

        self.assertFalse(result["ok"])
        self.assertIn("超时", result["message"])

    async def test_tool_executor_enforces_observation_size_limit(self) -> None:
        registry = ToolRegistry(_runtime())
        executor = ToolExecutor(registry, max_result_chars=1000)

        async def _large_result(*args: Any, **kwargs: Any) -> CommandResultPayload:
            return {
                "module": "test",
                "ok": True,
                "command": "list_features",
                "data": {"rows": ["x" * 1000 for _ in range(100)]},
                "message": "done",
                "extra_prompt": "",
                "response_type": "text",
                "image_path": "",
            }

        with patch.object(registry, "execute", side_effect=_large_result):
            result = await executor.execute(
                ToolInvocation(id="call_large", name="list_features", arguments={}),
                _event(),
            )

        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False)), 1000)
        self.assertTrue(result["data"]["result_truncated"])

    async def test_session_lock_serializes_same_session(self) -> None:
        sessions = SessionLockManager()
        active = 0
        max_active = 0

        async def _work() -> None:
            nonlocal active, max_active
            async with sessions.hold("1000:2000"):
                active += 1
                max_active = max(max_active, active)
                await asyncio.sleep(0.01)
                active -= 1

        await asyncio.gather(_work(), _work(), _work())

        self.assertEqual(max_active, 1)

    def test_grounding_policy_avoids_generic_can_you_false_positive(self) -> None:
        policy = GroundingPolicy()

        self.assertFalse(policy.is_knowledge_query("你能不能先别回复我"))
        self.assertTrue(policy.is_knowledge_query("DDT 数据和机制是什么"))

    def test_image_policy_classifies_short_requests_without_meta_false_positives(
        self,
    ) -> None:
        race_brief = classify_image_request("竞速情报图")
        creative = classify_image_request("画一张日落")

        self.assertIsNotNone(race_brief)
        self.assertEqual(race_brief.kind, "infographic")  # type: ignore[union-attr]
        self.assertEqual(race_brief.activity_types, ("race",))  # type: ignore[union-attr]
        self.assertIsNotNone(creative)
        self.assertEqual(creative.kind, "creative")  # type: ignore[union-attr]
        self.assertIsNone(classify_image_request("怎么生成图片"))
        self.assertIsNone(classify_image_request("这幅画是谁画的？"))
        self.assertIsNotNone(classify_image_request("能帮我画一张日落吗？"))
        self.assertIsNone(classify_image_request("把刚才那个画出来"))
        generic_activity = classify_image_request("把刚才那个活动图画出来")
        self.assertIsNotNone(generic_activity)
        self.assertEqual(
            generic_activity.activity_types,  # type: ignore[union-attr]
            ("race", "boss", "odyssey", "daily"),
        )
        daily_overview = classify_image_request("生成今天的 BTD6 活动早报图")
        self.assertIsNotNone(daily_overview)
        self.assertEqual(daily_overview.kind, "infographic")  # type: ignore[union-attr]
        self.assertEqual(
            daily_overview.activity_types,  # type: ignore[union-attr]
            ("race", "boss", "odyssey", "daily"),
        )
        self.assertEqual(classify_image_request("画一张 race car 图片").kind, "creative")  # type: ignore[union-attr]
        self.assertEqual(
            classify_image_request("画一只猴子参加竞速比赛的卡通图").kind,  # type: ignore[union-attr]
            "creative",
        )
        self.assertEqual(
            classify_image_request("画一张 Boss 战斗场景图").kind,  # type: ignore[union-attr]
            "creative",
        )
        latest_race = classify_image_request("生成最新竞速情报图")
        self.assertEqual(latest_race.event_scope, "latest")  # type: ignore[union-attr]
        collection = classify_image_request("生成收集活动轮换图")
        self.assertEqual(collection.kind, "collection")  # type: ignore[union-attr]
        leaderboard = classify_image_request("竞速排行榜图片")
        self.assertIsNotNone(leaderboard)
        self.assertEqual(leaderboard.kind, "leaderboard")  # type: ignore[union-attr]

    async def test_application_does_not_commit_when_delivery_fails(self) -> None:
        calls: list[str] = []

        class _Agent:
            async def run(self, *args: Any, **kwargs: Any) -> AgentResponse:
                calls.append("generated")
                self.assert_commit_deferred = kwargs["commit"] is False
                return AgentResponse(text="reply")

            async def commit_turn(self, *args: Any, **kwargs: Any) -> None:
                calls.append("committed")

        class _Sender:
            async def send_group_text(self, **kwargs: Any) -> None:
                calls.append("delivery_failed")
                raise RuntimeError("send failed")

            async def send_group_image(self, **kwargs: Any) -> None:
                raise AssertionError("unexpected image")

        class _Usage:
            def pop_trace_total_tokens(self, trace_id: str) -> int:
                calls.append("usage_finalized")
                return 17

        agent = _Agent()
        application = BotApplication(
            settings=_settings(),
            receiver=object(),  # type: ignore[arg-type]
            sender=_Sender(),  # type: ignore[arg-type]
            agent=agent,  # type: ignore[arg-type]
            llm=_Usage(),  # type: ignore[arg-type]
        )

        await application.process_event(
            event=_event(),
            trace_id="delivery-failure",
            reply_group_id="1000",
            at_user_id="2000",
            send_failure_notice=False,
        )

        self.assertTrue(agent.assert_commit_deferred)
        self.assertEqual(calls, ["generated", "delivery_failed", "usage_finalized"])

    async def test_application_commits_only_after_successful_delivery(self) -> None:
        calls: list[str] = []

        class _Agent:
            async def run(self, *args: Any, **kwargs: Any) -> AgentResponse:
                calls.append("generated")
                return AgentResponse(text="reply")

            async def commit_turn(self, *args: Any, **kwargs: Any) -> None:
                calls.append("committed")

        class _Sender:
            async def send_group_text(self, **kwargs: Any) -> None:
                calls.append("delivered")

            async def send_group_image(self, **kwargs: Any) -> None:
                raise AssertionError("unexpected image")

        class _Usage:
            def pop_trace_total_tokens(self, trace_id: str) -> int:
                calls.append("usage_finalized")
                return 17

        application = BotApplication(
            settings=_settings(),
            receiver=object(),  # type: ignore[arg-type]
            sender=_Sender(),  # type: ignore[arg-type]
            agent=_Agent(),  # type: ignore[arg-type]
            llm=_Usage(),  # type: ignore[arg-type]
        )

        await application.process_event(
            event=_event(),
            trace_id="delivery-success",
            reply_group_id="1000",
            at_user_id="2000",
            send_failure_notice=True,
        )

        self.assertEqual(
            calls, ["generated", "delivered", "committed", "usage_finalized"]
        )

    async def test_malformed_native_tool_arguments_are_not_executed(self) -> None:
        registry = ToolRegistry(_runtime())
        llm = FakeLLM(
            [
                _assistant_response(
                    "",
                    [
                        LLMToolCall(
                            id="call_bad_json",
                            name="list_features",
                            arguments="{not-json",
                        )
                    ],
                ),
                _assistant_response("工具参数有误，请重试。"),
            ]
        )
        runner = AgentRunner(
            llm=llm,  # type: ignore[arg-type]
            memory=GroupChatMemory(history_turns=3),
            settings=_settings(),
            tool_registry=registry,
            context_manager=FakeContextManager(),  # type: ignore[arg-type]
        )

        response = await runner.run(_event(), trace_id="bad-arguments")

        self.assertEqual(response.text, "工具参数有误，请重试。")
        self.assertFalse(response.tool_calls[0].result["ok"])
        self.assertIn(
            "invalid JSON",
            response.tool_calls[0].result["data"]["validation_errors"][0],
        )

    async def test_harness_stops_before_executing_tool_over_budget(self) -> None:
        settings = AppSettings(**{**_settings().__dict__, "max_tool_rounds": 1})
        llm = FakeLLM(
            [
                _tool_response("list_features", call_id="call_1"),
                _tool_response("list_features", call_id="call_2"),
            ]
        )
        runner = AgentRunner(
            llm=llm,  # type: ignore[arg-type]
            memory=GroupChatMemory(history_turns=3),
            settings=settings,
            tool_registry=ToolRegistry(_runtime()),
            context_manager=FakeContextManager(),  # type: ignore[arg-type]
        )

        response = await runner.run(_event(), trace_id="budget")

        self.assertEqual(response.terminal_reason, "tool_budget_exhausted")
        self.assertEqual(len(response.tool_calls), 1)
        self.assertEqual(response.tool_calls[0].call_id, "call_1")

    def test_sanitize_kb_query_strips_invocation_noise_and_rewrites_domain_terms(
        self,
    ) -> None:
        self.assertEqual(
            sanitize_kb_query("@小污猫 野兽之主数据"),
            "野兽之主",
        )
        self.assertEqual(
            sanitize_kb_query("[CQ:at,qq=1693903050] 004野兽之主能不能抓DDT"),
            "004 0-0-4 野兽之主 DDT 逆戟鲸",
        )
        self.assertEqual(sanitize_kb_query("野兽之主路线3详细数据"), "野兽之主")
        self.assertEqual(
            sanitize_kb_query("野兽之主下路详细数据"),
            "野兽之主",
        )
        self.assertEqual(
            sanitize_kb_query("Mermaid Monkey stats"),
            "人鱼猴",
        )
        self.assertEqual(
            sanitize_kb_query("Beast Handler 004 DDT"),
            "野兽之主 004 0-0-4 DDT 逆戟鲸",
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
        registry = ToolRegistry(_runtime())

        tool_names = {tool["function"]["name"] for tool in registry.to_openai_tools()}

        self.assertEqual(
            tool_names,
            {
                "ignore_message",
                "list_features",
                "list_artifacts",
                "read_artifact",
                "generate_image",
                "today_most_active",
                "summarize_recent_messages",
                "script_manual_query",
                "search_game_knowledge",
                "read_game_knowledge_file",
                "get_activity_detail",
                "get_activity_brief_image",
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

        brief_tool = next(
            item["function"]
            for item in registry.to_openai_tools()
            if item["function"]["name"] == "get_activity_brief_image"
        )
        self.assertEqual(brief_tool["parameters"]["required"], ["activity_type"])
        self.assertEqual(
            brief_tool["parameters"]["properties"]["activity_type"]["enum"],
            ["all", "race", "boss", "odyssey", "daily"],
        )
        self.assertEqual(
            brief_tool["parameters"]["properties"]["event_scope"]["enum"],
            ["current", "upcoming", "ended", "relevant", "latest"],
        )

    async def test_concrete_social_tool_handler_uses_injected_db(self) -> None:
        db = FakeDb()
        registry = ToolRegistry(_runtime(db=db))

        result = await registry.execute(
            "today_most_active",
            {"top_n": 5},
            _event("今日活跃榜前5"),
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["rows"], db.active_rows)

    async def test_concrete_manual_tool_reads_injected_manual_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manual_path = Path(temp_dir) / "manual.md"
            manual_path.write_text("启动方式：运行 main.py", encoding="utf-8")
            registry = ToolRegistry(_runtime(manual_file=manual_path))

            result = await registry.execute(
                "script_manual_query",
                {"query": "怎么启动"},
                _event("怎么启动脚本"),
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["manual_full_text"], "启动方式：运行 main.py")

    async def test_tool_results_follow_contract(self) -> None:
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
                    plugin_root=_create_temp_plugin_root(temp_dir),
                    subprocess_runner=activity_runner,
                )
            )
            cases = [
                ("ignore_message", {"reason": "test"}),
                ("list_features", {}),
                ("today_most_active", {"top_n": 3}),
                ("summarize_recent_messages", {"top_n": 5}),
                ("script_manual_query", {"query": "怎么启动"}),
                ("search_game_knowledge", {"query": "忍者猴"}),
                ("read_game_knowledge_file", {"file_path": "a.md"}),
                ("get_activity_detail", {"activity_type": "race"}),
                (
                    "get_activity_brief_image",
                    {
                        "activity_type": "odyssey",
                        "difficulty": "hard",
                        "event_scope": "current",
                    },
                ),
                ("get_activity_leaderboard", {"leaderboard_type": "race"}),
                ("get_collection_event_info", {"only_upcoming": True}),
                ("search_web", {}),
                (
                    "simulate_monkey_box_opening",
                    {"box_type": "钻石", "box_count": 1},
                ),
            ]

            for name, arguments in cases:
                result = await registry.execute(name, arguments, _event())

                self.assertTrue(required_fields.issubset(result.keys()), name)
                self.assertIsInstance(result["data"], dict, name)
                self.assertIn(result["response_type"], {"text", "image", "none"}, name)
                self.assertIsInstance(result["image_path"], str, name)

            unknown = await registry.execute("missing_tool", {}, _event())
        self.assertTrue(required_fields.issubset(unknown.keys()))
        self.assertFalse(unknown["ok"])

    async def test_mini_game_tool_uses_injected_process_runner(self) -> None:
        fake_runner = FakeActivityRunner()
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = _create_temp_plugin_root(temp_dir)
            registry = ToolRegistry(
                _runtime(
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
            repeated_image_result = await registry.execute(
                "simulate_monkey_box_opening",
                {"box_type": "diamond", "box_count": 1},
                _event("再开 1 个钻石箱"),
            )
            rejected_result = await registry.execute(
                "simulate_monkey_box_opening",
                {"box_type": "钻石", "box_count": 10001},
                _event("开很多钻石箱"),
            )
            catalog = await registry.execute(
                "list_artifacts",
                {},
                _event("查看产物清单"),
            )

        self.assertEqual(image_result["module"], "tool.mini_game")
        self.assertEqual(image_result["response_type"], "image")
        self.assertTrue(image_result["image_path"].endswith(".png"))
        self.assertTrue(image_result["data"]["fresh_run"])
        self.assertFalse(image_result["data"]["stored_as_artifact"])
        self.assertIn("tool_outputs", Path(image_result["image_path"]).parts)
        self.assertIn("mini_game", Path(image_result["image_path"]).parts)
        self.assertNotEqual(
            image_result["image_path"], repeated_image_result["image_path"]
        )
        self.assertEqual(text_result["module"], "tool.mini_game")
        self.assertEqual(text_result["response_type"], "text")
        self.assertEqual(text_result["image_path"], "")
        self.assertFalse(rejected_result["ok"])
        self.assertEqual(catalog["data"]["artifact_count"], 0)
        self.assertEqual(len(fake_runner.calls), 3)

    async def test_knowledge_tools_use_injected_kb_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = _create_temp_plugin_root(temp_dir)
            kb_root = _create_fake_knowledge_base(plugin_root)
            registry = ToolRegistry(_runtime(plugin_root=plugin_root))

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
                kb_root / "knowledge_docs" / "last_query.txt"
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
                    "file_path": str(kb_root / "knowledge_docs/long.md"),
                    "keywords": ["忍者猴"],
                    "max_chars": 1200,
                },
                _event("读长文"),
            )
            rounds_result = await ToolExecutor(registry).execute(
                ToolInvocation(
                    id="call_rounds",
                    name="read_game_knowledge_file",
                    arguments={"file_path": str(kb_root / "knowledge_docs/rounds.md")},
                ),
                _event("读取完整回合列表"),
            )
            queried_rounds_result = await ToolExecutor(
                registry,
                max_result_chars=_settings().max_tool_result_chars,
            ).execute(
                ToolInvocation(
                    id="call_queried_rounds",
                    name="read_game_knowledge_file",
                    arguments={
                        "file_path": str(kb_root / "knowledge_docs/rounds.md"),
                        "query": "回合列表",
                    },
                ),
                _event("查询回合列表"),
            )
            rejected_result = await registry.execute(
                "read_game_knowledge_file",
                {"file_path": str(plugin_root / "outside.md")},
                _event("读越界文件"),
            )
            rejected_legacy_result = await registry.execute(
                "read_game_knowledge_file",
                {"file_path": str(kb_root / "knowledge_docs/legacy.txt")},
                _event("读旧格式文件"),
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
        self.assertEqual(long_result["data"]["mode"], "keyword_extract")
        self.assertGreater(long_result["data"]["content_chars"], 0)
        self.assertTrue(rounds_result["ok"])
        self.assertEqual(rounds_result["data"]["mode"], "full")
        self.assertFalse(rounds_result["data"]["content_truncated"])
        self.assertGreater(rounds_result["data"]["content_chars"], 60000)
        self.assertEqual(
            rounds_result["data"]["content_chars"],
            rounds_result["data"]["source_content_chars"],
        )
        self.assertTrue(rounds_result["data"]["content"].startswith("# 回合列表"))
        self.assertEqual(
            rounds_result["data"]["content"].count("| 100 | 红气球 | 测试回合数据 |"),
            5000,
        )
        self.assertNotIn("result_truncated", rounds_result["data"])
        self.assertTrue(queried_rounds_result["ok"])
        self.assertEqual(queried_rounds_result["data"]["mode"], "paragraph_extract")
        self.assertGreater(queried_rounds_result["data"]["content_chars"], 60000)
        self.assertEqual(
            queried_rounds_result["data"]["content"].count(
                "| 100 | 红气球 | 测试回合数据 |"
            ),
            5000,
        )
        self.assertEqual(len(queried_rounds_result["data"]["paragraphs"]), 1)
        self.assertNotIn("content", queried_rounds_result["data"]["paragraphs"][0])
        self.assertNotIn("result_truncated", queried_rounds_result["data"])
        self.assertFalse(rejected_result["ok"])
        self.assertFalse(rejected_legacy_result["ok"])
        self.assertEqual(rejected_legacy_result["message"], "文件类型不允许读取。")
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

        registry = ToolRegistry(_runtime())
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

    def test_build_knowledge_summary_marks_grounded_read(self) -> None:
        runner = AgentRunner(
            llm=FakeLLM([]),  # type: ignore[arg-type]
            memory=GroupChatMemory(history_turns=3),
            settings=_settings(),
            tool_registry=ToolRegistry(_runtime()),
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
                        "matched_paths": ["/kb/人鱼猴.md"],
                    },
                    "message": "游戏知识检索完成",
                    "extra_prompt": "",
                    "response_type": "text",
                    "image_path": "",
                },
            ),
            ToolCallRecord(
                name="read_game_knowledge_file",
                arguments={"file_path": "/kb/人鱼猴.md"},
                result={
                    "module": "tool.knowledge",
                    "ok": True,
                    "command": "read_game_knowledge_file",
                    "data": {
                        "file_path": "/kb/人鱼猴.md",
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
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = _create_temp_plugin_root(temp_dir)
            _create_fake_knowledge_base(plugin_root)
            registry = ToolRegistry(_runtime(plugin_root=plugin_root))
            llm = FakeLLM([_assistant_response("忍者猴可以升级为大师忍者")])
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

    async def test_agent_forces_grounding_before_model_for_knowledge_query(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = _create_temp_plugin_root(temp_dir)
            _create_fake_knowledge_base(plugin_root)
            dependencies = _runtime(plugin_root=plugin_root, db=FakeDb())
            registry = ToolRegistry(dependencies)
            llm = FakeLLM([_assistant_response("野兽之主的资料已基于知识库读取。")])
            runner = AgentRunner(
                llm=llm,  # type: ignore[arg-type]
                memory=GroupChatMemory(history_turns=3),
                settings=_settings(),
                tool_registry=registry,
                tool_dependencies=dependencies,
                context_manager=FakeContextManager(),  # type: ignore[arg-type]
            )

            response = await runner.run(_event("野兽之主数据"), trace_id="test-trace")

        self.assertEqual(response.text, "野兽之主的资料已基于知识库读取。")
        self.assertEqual(len(response.tool_calls), 2)
        self.assertEqual(response.tool_calls[0].name, "search_game_knowledge")
        self.assertEqual(response.tool_calls[1].name, "read_game_knowledge_file")
        self.assertEqual(response.tool_calls[0].source, "policy")
        self.assertEqual(len(llm.requests), 1)
        request_messages = llm.requests[0]["messages"]
        self.assertFalse(
            any(
                message.get("role") in {"assistant", "tool"}
                for message in request_messages
            )
        )
        policy_observations = []
        for message in request_messages:
            if message.get("role") != "user":
                continue
            payload = json.loads(message["content"])
            if payload.get("kind") == "harness_tool_observations":
                policy_observations.append(payload)
        self.assertEqual(len(policy_observations), 2)
        self.assertEqual(
            policy_observations[0]["results"][0]["name"],
            "search_game_knowledge",
        )
        self.assertEqual(
            policy_observations[1]["results"][0]["name"],
            "read_game_knowledge_file",
        )

    async def test_agent_forces_read_after_search_hit_for_knowledge_query(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = _create_temp_plugin_root(temp_dir)
            _create_fake_knowledge_base(plugin_root)
            dependencies = _runtime(plugin_root=plugin_root, db=FakeDb())
            registry = ToolRegistry(dependencies)
            llm = FakeLLM([_assistant_response("人鱼猴的数据如下")])
            runner = AgentRunner(
                llm=llm,  # type: ignore[arg-type]
                memory=GroupChatMemory(history_turns=3),
                settings=_settings(),
                tool_registry=registry,
                tool_dependencies=dependencies,
                context_manager=FakeContextManager(),  # type: ignore[arg-type]
            )

            response = await runner.run(_event("人鱼猴数据"), trace_id="test-trace")

        self.assertEqual(response.text, "人鱼猴的数据如下")
        self.assertEqual(len(response.tool_calls), 2)
        self.assertEqual(response.tool_calls[0].name, "search_game_knowledge")
        self.assertEqual(response.tool_calls[1].name, "read_game_knowledge_file")

    async def test_agent_uses_deterministic_fallback_after_knowledge_miss(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = _create_temp_plugin_root(temp_dir)
            _create_empty_fake_knowledge_base(plugin_root)
            dependencies = _runtime(plugin_root=plugin_root, db=FakeDb())
            registry = ToolRegistry(dependencies)
            llm = FakeLLM([_assistant_response("我印象里它可能存在。")])
            runner = AgentRunner(
                llm=llm,  # type: ignore[arg-type]
                memory=GroupChatMemory(history_turns=3),
                settings=_settings(),
                tool_registry=registry,
                tool_dependencies=dependencies,
                context_manager=FakeContextManager(),  # type: ignore[arg-type]
            )

            response = await runner.run(_event("不存在塔数据"), trace_id="test-trace")

        self.assertIn("知识库里暂时没有检索到", response.text)
        self.assertIn("不能基于知识库证据确认结论", response.text)
        self.assertEqual(len(response.tool_calls), 1)
        self.assertEqual(
            [request["purpose"] for request in llm.requests],
            ["agent_turn"],
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

        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = _create_temp_plugin_root(temp_dir)
            _create_empty_fake_knowledge_base(plugin_root)
            dependencies = _runtime(plugin_root=plugin_root, db=FakeDb())
            registry = ToolRegistry(dependencies)
            llm = FakeLLM(
                [
                    _tool_response(
                        "search_web",
                        {"query": "BTD6 DDT counter guide", "top_n": 3},
                    ),
                    _assistant_response("联网资料显示，DDT 需要迷彩侦测和破铅伤害。"),
                ]
            )
            runner = AgentRunner(
                llm=llm,  # type: ignore[arg-type]
                memory=GroupChatMemory(history_turns=3),
                settings=_settings(),
                tool_registry=registry,
                tool_dependencies=dependencies,
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
                "agent_turn",
                "agent_turn",
            ],
        )
        fake_get.assert_called_once()

    async def test_agent_uses_deterministic_fallback_after_knowledge_read_failure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = _create_temp_plugin_root(temp_dir)
            _create_fake_knowledge_base(plugin_root)
            dependencies = _runtime(plugin_root=plugin_root, db=FakeDb())
            registry = ToolRegistry(dependencies)
            llm = FakeLLM([_assistant_response("我尝试根据印象回答。")])
            runner = AgentRunner(
                llm=llm,  # type: ignore[arg-type]
                memory=GroupChatMemory(history_turns=3),
                settings=_settings(),
                tool_registry=registry,
                tool_dependencies=dependencies,
                context_manager=FakeContextManager(),  # type: ignore[arg-type]
            )

            original_execute = registry.execute

            async def _execute_with_read_failure(
                name: str, arguments: dict[str, Any], event: EventPayload
            ) -> CommandResultPayload:
                if name == "read_game_knowledge_file":
                    return {
                        "module": "tool.knowledge",
                        "ok": False,
                        "command": name,
                        "data": {"file_path": arguments.get("file_path", "")},
                        "message": "读取失败",
                        "extra_prompt": "",
                        "response_type": "text",
                        "image_path": "",
                    }
                return await original_execute(name, arguments, event)

            with patch.object(
                registry, "execute", side_effect=_execute_with_read_failure
            ):
                response = await runner.run(_event("人鱼猴数据"), trace_id="test-trace")

        self.assertIn("没有成功读取到可用内容", response.text)
        self.assertEqual(len(response.tool_calls), 2)
        self.assertEqual(
            [request["purpose"] for request in llm.requests],
            ["agent_turn"],
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
        registry = ToolRegistry(_runtime())
        llm = FakeLLM(
            [
                _tool_response("list_features"),
                _assistant_response("功能已列出"),
            ]
        )
        context_manager = FakeContextManager()
        runner = AgentRunner(
            llm=llm,  # type: ignore[arg-type]
            memory=GroupChatMemory(history_turns=3),
            settings=_settings(),
            tool_registry=registry,
            tool_dependencies=_runtime(db=FakeDb()),
            context_manager=context_manager,  # type: ignore[arg-type]
        )

        response = await runner.run(_event(), trace_id="test-trace")

        self.assertTrue(response.should_send)
        self.assertEqual(response.text, "功能已列出")
        self.assertEqual(response.token_usage, 34)
        self.assertEqual(
            context_manager.calls,
            [("1000", "2000", "test-trace"), ("1000", "2000", "test-trace")],
        )
        self.assertEqual(llm.requests[0]["purpose"], "agent_turn")
        self.assertIn("tools", llm.requests[0])
        self.assertEqual(llm.requests[0]["tool_choice"], "auto")
        self.assertEqual(llm.requests[1]["purpose"], "agent_turn")
        request_messages = llm.requests[0]["messages"]
        self.assertEqual(request_messages[-1]["role"], "user")
        user_payload = json.loads(request_messages[-1]["content"])
        self.assertEqual(user_payload["task"], "respond_to_event")
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

    async def test_multimodal_user_message_survives_tool_call_round_trip(self) -> None:
        """VX-05: 图片输入与既有原生工具调用消息可共同完成多轮请求。"""
        registry = ToolRegistry(_runtime())
        llm = FakeLLM(
            [_tool_response("list_features"), _assistant_response("我看到了图片")]
        )
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "current.png"
            image_path.write_bytes(b"normalized image")
            settings = AppSettings(
                env_file=".env",
                target_group_id="1000",
                target_group_ids=("1000",),
                api_key="test-key",
                system_prompt="你是测试机器人。",
                max_tool_rounds=3,
                vision_enabled=True,
            )
            runner = AgentRunner(
                llm=llm,  # type: ignore[arg-type]
                memory=GroupChatMemory(history_turns=3),
                settings=settings,
                tool_registry=registry,
                tool_dependencies=_runtime(db=FakeDb()),
                context_manager=FakeContextManager(),  # type: ignore[arg-type]
            )
            event = _event("看看这张图")
            event["message_id"] = "current"
            event["images"] = [
                {
                    "segment_index": 0,
                    "processing_status": "cached",
                    "mime_type": "image/png",
                    "cache_path": str(image_path),
                }
            ]

            response = await runner.run(event, trace_id="vision-tool-trace")

        self.assertEqual(response.text, "我看到了图片")
        first_content = llm.requests[0]["messages"][-1]["content"]
        self.assertIsInstance(first_content, list)
        self.assertEqual(
            [part["type"] for part in first_content],
            ["text", "text", "image_url"],
        )
        second_messages = llm.requests[1]["messages"]
        self.assertTrue(any(message["role"] == "assistant" for message in second_messages))
        self.assertTrue(any(message["role"] == "tool" for message in second_messages))

    async def test_ignore_message_tool_suppresses_send(self) -> None:
        registry = ToolRegistry(_runtime())
        llm = FakeLLM([_tool_response("ignore_message", {"reason": "普通闲聊"})])
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

    async def test_reply_direct_returns_without_tool_or_rendering(self) -> None:
        registry = ToolRegistry(_runtime())
        llm = FakeLLM([_assistant_response("我在，有什么事？")])
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
        self.assertEqual(len(llm.requests), 1)
        self.assertEqual(llm.requests[0]["purpose"], "agent_turn")

    def test_system_prompt_includes_native_tool_hints(self) -> None:
        settings = AppSettings(
            **{
                **_settings().__dict__,
                "router_tool_hints_prompt": "活动查询优先使用 game_news",
            }
        )
        manager = PromptManager(settings)

        prompt = manager.build_system_prompt(_event())

        self.assertIn("活动查询优先使用 game_news", prompt)
        self.assertIn("原生 tools", prompt)
        self.assertIn("ignore_message", prompt)
        self.assertIn("不要输出 JSON 决策协议", prompt)
        self.assertIn("不能只回复画面描述就结束", prompt)
        self.assertIn("get_activity_brief_image", prompt)
        self.assertIn("generate_image 只用于明确的同人插画", prompt)
        self.assertIn("read_artifact", prompt)

    async def test_agent_sees_artifact_catalog_and_reuses_matching_image(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = _create_temp_plugin_root(temp_dir)
            dependencies = ToolDependencies.create(
                db=FakeDb(),
                plugin_root=plugin_root,
                manual_file=Path("/missing/manual.md"),
                image_gen_api_base_url="https://images.example/v1",
                image_gen_api_key="secret",
                image_gen_model="image-model",
            )
            registry = ToolRegistry(dependencies)
            source = plugin_root / "existing_race.png"
            source.write_bytes(b"fake png")
            artifact = dependencies.artifact_store.register(  # type: ignore[union-attr]
                kind="activity.brief",
                title="BTD6 竞速活动情报图",
                summary="极速峡谷竞速活动，包含时间、地图、模式和限制",
                tags=("BTD6", "竞速", "活动情报图", "图片"),
                files=[ArtifactSource(path=source, role="image")],
                ttl_seconds=3600,
                metadata={
                    "activity_type": "race",
                    "difficulty": "hard",
                    "event_scope": "current",
                },
            )
            llm = FakeLLM(
                [
                    _tool_response(
                        "read_artifact",
                        {"artifact_id": artifact.artifact_id, "role": "image"},
                    ),
                    _assistant_response("这张竞速情报图还在有效期内，直接给你。"),
                ]
            )
            runner = AgentRunner(
                llm=llm,  # type: ignore[arg-type]
                memory=GroupChatMemory(history_turns=3),
                settings=_settings(),
                tool_registry=registry,
                tool_dependencies=dependencies,
                context_manager=FakeContextManager(),  # type: ignore[arg-type]
            )

            with patch(
                "bot_app.tools.image.generate_image_file",
                return_value=source,
            ) as generate:
                response = await runner.run(
                    _event("竞速情报图"),
                    trace_id="race-infographic",
                )

        self.assertEqual(
            [record.name for record in response.tool_calls],
            ["read_artifact"],
        )
        self.assertEqual(len(llm.requests), 2)
        self.assertEqual(response.response_type, "image")
        self.assertEqual(response.image_path, str(artifact.file("image")))
        self.assertEqual(response.text, "这张竞速情报图还在有效期内，直接给你。")
        system_prompt = llm.requests[0]["messages"][0]["content"]
        self.assertIn("当前有效产物清单", system_prompt)
        self.assertIn("BTD6 竞速活动情报图", system_prompt)
        self.assertIn(artifact.artifact_id, system_prompt)
        generate.assert_not_called()

    async def test_factual_activity_request_rejects_old_ai_image_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = _create_temp_plugin_root(temp_dir)
            dependencies = _runtime(plugin_root=plugin_root)
            source = plugin_root / "old_ai_activity.png"
            source.write_bytes(b"fake png")
            artifact = dependencies.artifact_store.register(  # type: ignore[union-attr]
                kind="image.generated",
                title="旧竞速活动图",
                summary="旧通用生图产物",
                tags=("BTD6", "竞速", "图片"),
                files=[ArtifactSource(path=source, role="image")],
                ttl_seconds=3600,
            )
            registry = ToolRegistry(dependencies)

            result = await registry.execute(
                "read_artifact",
                {"artifact_id": artifact.artifact_id, "role": "image"},
                _event("当前竞速情报图"),
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["data"]["reason"], "incompatible_activity_image")
        self.assertEqual(result["response_type"], "text")

    async def test_image_tool_result_is_returned_with_final_text(self) -> None:
        activity_runner = FakeActivityRunner()
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = _create_temp_plugin_root(temp_dir)
            registry = ToolRegistry(
                _runtime(
                    plugin_root=plugin_root,
                    subprocess_runner=activity_runner,
                )
            )
            llm = FakeLLM(
                [
                    _tool_response(
                        "get_activity_leaderboard",
                        {"leaderboard_type": "race"},
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
        normalized_path = Path(response.image_path)
        self.assertEqual(normalized_path.name, "image.png")
        self.assertIn("activity_leaderboard", normalized_path.parts)
        self.assertEqual(response.text, "竞速排行榜来啦")
        self.assertEqual(len(activity_runner.calls), 1)

    async def test_activity_brief_tool_returns_three_role_artifact_image(self) -> None:
        activity_runner = FakeActivityRunner()
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = _create_temp_plugin_root(temp_dir)
            dependencies = _runtime(
                plugin_root=plugin_root,
                subprocess_runner=activity_runner,
            )
            registry = ToolRegistry(dependencies)

            result = await registry.execute(
                "get_activity_brief_image",
                {
                    "activity_type": "odyssey",
                    "difficulty": "hard",
                    "event_scope": "upcoming",
                },
                _event("生成下一期困难征程情报图"),
            )
            records = dependencies.artifact_store.list_active()  # type: ignore[union-attr]

        self.assertTrue(result["ok"])
        self.assertEqual(result["response_type"], "image")
        self.assertTrue(result["data"]["stored_as_artifact"])
        self.assertIn("activity_brief", Path(result["image_path"]).parts)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].kind, "activity.brief")
        self.assertEqual({item.role for item in records[0].files}, {"report", "image", "data"})
        command = activity_runner.calls[0]
        self.assertEqual(command[command.index("--activity-type") + 1], "odyssey")
        self.assertEqual(command[command.index("--difficulty") + 1], "hard")
        self.assertEqual(command[command.index("--event-scope") + 1], "upcoming")

    async def test_boss_and_daily_use_deterministic_activity_brief_tool(self) -> None:
        activity_runner = FakeActivityRunner()
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = _create_temp_plugin_root(temp_dir)
            registry = ToolRegistry(
                _runtime(
                    plugin_root=plugin_root,
                    subprocess_runner=activity_runner,
                )
            )

            for activity_type, text in (
                ("boss", "生成当前 Boss 情报图"),
                ("daily", "生成当前每日挑战情报图"),
            ):
                result = await registry.execute(
                    "get_activity_brief_image",
                    {"activity_type": activity_type, "event_scope": "current"},
                    _event(text),
                )
                self.assertTrue(result["ok"], activity_type)
                self.assertEqual(result["response_type"], "image", activity_type)

        self.assertEqual(len(activity_runner.calls), 2)
        self.assertEqual(
            [
                command[command.index("--activity-type") + 1]
                for command in activity_runner.calls
            ],
            ["boss", "daily"],
        )

    async def test_all_activity_overview_uses_one_deterministic_brief_image(self) -> None:
        activity_runner = FakeActivityRunner()
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = _create_temp_plugin_root(temp_dir)
            registry = ToolRegistry(
                _runtime(
                    plugin_root=plugin_root,
                    subprocess_runner=activity_runner,
                )
            )

            result = await registry.execute(
                "get_activity_brief_image",
                {
                    "activity_type": "all",
                    "difficulty": "hard",
                    "event_scope": "relevant",
                },
                _event("生成今天的 BTD6 活动早报图"),
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["response_type"], "image")
        command = activity_runner.calls[0]
        self.assertEqual(command[command.index("--activity-type") + 1], "all")
        self.assertEqual(command[command.index("--difficulty") + 1], "hard")
        self.assertEqual(command[command.index("--event-scope") + 1], "relevant")

    async def test_scheduled_daily_brief_policy_always_generates_overview_image(
        self,
    ) -> None:
        activity_runner = FakeActivityRunner()
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = _create_temp_plugin_root(temp_dir)
            registry = ToolRegistry(
                _runtime(
                    plugin_root=plugin_root,
                    subprocess_runner=activity_runner,
                )
            )
            llm = FakeLLM([_assistant_response("早上好，今天的活动都在图里。")])
            runner = AgentRunner(
                llm=llm,  # type: ignore[arg-type]
                memory=GroupChatMemory(history_turns=3),
                settings=_settings(),
                tool_registry=registry,
                context_manager=FakeContextManager(),  # type: ignore[arg-type]
            )
            event = {
                **_event("请按竞速活动生成一张情报图"),
                "module": "scheduler",
                "event_type": "scheduled_daily_activity_brief",
                "user_id": "__daily_activity_brief__",
            }

            response = await runner.run(event, trace_id="scheduled-overview")

        self.assertEqual(
            [item.name for item in response.tool_calls],
            ["get_activity_brief_image"],
        )
        self.assertEqual(response.response_type, "image")
        self.assertEqual(response.text, "早上好，今天的活动都在图里。")
        command = activity_runner.calls[0]
        self.assertEqual(command[command.index("--activity-type") + 1], "all")
        self.assertEqual(command[command.index("--difficulty") + 1], "hard")
        self.assertEqual(command[command.index("--event-scope") + 1], "relevant")

    async def test_factual_activity_request_cannot_use_generic_image_tool(self) -> None:
        registry = ToolRegistry(_runtime())

        result = await registry.execute(
            "generate_image",
            {"prompt": "制作一张竞速活动情报图"},
            _event("制作当前竞速情报图"),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["data"]["reason"], "use_activity_brief_tool")
        self.assertIn("get_activity_brief_image", result["message"])

        for activity_type, label in (("boss", "Boss"), ("daily", "每日挑战")):
            activity_result = await registry.execute(
                "generate_image",
                {"prompt": f"制作一张{label}活动情报图"},
                _event(f"制作当前{label}活动情报图"),
            )
            self.assertFalse(activity_result["ok"], activity_type)
            self.assertEqual(
                activity_result["data"]["reason"],
                "use_activity_brief_tool",
                activity_type,
            )

        contextual_result = await registry.execute(
            "generate_image",
            {"prompt": "当前竞速活动情报图"},
            _event("把刚才那个画出来"),
        )
        self.assertFalse(contextual_result["ok"])
        self.assertEqual(contextual_result["data"]["reason"], "use_activity_brief_tool")

        generic_result = await registry.execute(
            "generate_image",
            {"prompt": "BTD6 活动情报图"},
            _event("生成一张 BTD6 活动情报图"),
        )
        self.assertFalse(generic_result["ok"])
        self.assertEqual(generic_result["data"]["reason"], "use_activity_brief_tool")

        leaderboard_result = await registry.execute(
            "generate_image",
            {"prompt": "竞速排行榜图片"},
            _event("生成竞速排行榜图片"),
        )
        self.assertFalse(leaderboard_result["ok"])
        self.assertEqual(leaderboard_result["data"]["reason"], "use_activity_brief_tool")

        collection_result = await registry.execute(
            "generate_image",
            {"prompt": "收集活动轮换图"},
            _event("生成收集活动轮换图"),
        )
        self.assertFalse(collection_result["ok"])
        self.assertEqual(collection_result["data"]["reason"], "use_activity_brief_tool")

    async def test_activity_brief_requires_one_activity_and_snapshot_json(self) -> None:
        multi_runner = FakeActivityRunner()
        missing_json_runner = FakeActivityRunner(include_brief_json=False)
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = _create_temp_plugin_root(temp_dir)
            multi_result = await ToolRegistry(
                _runtime(plugin_root=plugin_root, subprocess_runner=multi_runner)
            ).execute(
                "get_activity_brief_image",
                {"activity_type": "race", "event_scope": "current"},
                _event("生成竞速和征程情报图"),
            )
            missing_json_result = await ToolRegistry(
                _runtime(plugin_root=plugin_root, subprocess_runner=missing_json_runner)
            ).execute(
                "get_activity_brief_image",
                {"activity_type": "race", "event_scope": "current"},
                _event("生成当前竞速情报图"),
            )

        self.assertFalse(multi_result["ok"])
        self.assertEqual(multi_result["data"]["reason"], "activity_selection_mismatch")
        self.assertEqual(multi_runner.calls, [])
        self.assertFalse(missing_json_result["ok"])
        self.assertIn("结构化快照", missing_json_result["message"])

    async def test_agent_sends_deterministic_activity_brief_without_ai_generation(self) -> None:
        activity_runner = FakeActivityRunner()
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = _create_temp_plugin_root(temp_dir)
            registry = ToolRegistry(
                _runtime(
                    plugin_root=plugin_root,
                    subprocess_runner=activity_runner,
                )
            )
            llm = FakeLLM(
                [
                    _tool_response(
                        "get_activity_brief_image",
                        {"activity_type": "race", "event_scope": "current"},
                    ),
                    _assistant_response("当前竞速的地图和塔限制都在图里。"),
                ]
            )
            runner = AgentRunner(
                llm=llm,  # type: ignore[arg-type]
                memory=GroupChatMemory(history_turns=3),
                settings=_settings(),
                tool_registry=registry,
                context_manager=FakeContextManager(),  # type: ignore[arg-type]
            )

            with patch("bot_app.tools.image.generate_image_file") as generate:
                response = await runner.run(
                    _event("生成当前竞速情报图"),
                    trace_id="deterministic-race-brief",
                )

        self.assertEqual([item.name for item in response.tool_calls], ["get_activity_brief_image"])
        self.assertEqual(response.response_type, "image")
        self.assertIn("activity_brief", Path(response.image_path).parts)
        self.assertEqual(response.text, "当前竞速的地图和塔限制都在图里。")
        generate.assert_not_called()

    async def test_activity_artifact_is_visible_and_readable(self) -> None:
        activity_runner = FakeActivityRunner()
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = _create_temp_plugin_root(temp_dir)
            registry = ToolRegistry(
                _runtime(
                    plugin_root=plugin_root,
                    subprocess_runner=activity_runner,
                )
            )

            created = await registry.execute(
                "get_activity_detail",
                {"activity_type": "race"},
                _event("看竞速详情"),
            )
            catalog = await registry.execute(
                "list_artifacts",
                {},
                _event("看看已有产物"),
            )
            artifact = catalog["data"]["artifacts"][0]
            reused = await registry.execute(
                "read_artifact",
                {"artifact_id": artifact["artifact_id"], "role": "report"},
                _event("读取这个竞速报告"),
            )

        self.assertEqual(len(activity_runner.calls), 1)
        self.assertTrue(created["data"]["stored_as_artifact"])
        self.assertTrue(created["data"]["json_path"].endswith("data.json"))
        self.assertEqual(artifact["title"], "BTD6 最新发布竞速活动详情")
        self.assertIn("竞速", artifact["tags"])
        self.assertTrue(artifact["created_at"].endswith("UTC"))
        self.assertTrue(artifact["expires_at"].endswith("UTC"))
        self.assertEqual(
            {item["role"] for item in artifact["files"]},
            {"report", "data"},
        )
        self.assertTrue(reused["data"]["reused"])
        self.assertIn("测试活动详情", reused["data"]["content"])

    async def test_image_tool_result_survives_later_text_tool(self) -> None:
        activity_runner = FakeActivityRunner()
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_root = _create_temp_plugin_root(temp_dir)
            registry = ToolRegistry(
                _runtime(
                    plugin_root=plugin_root,
                    subprocess_runner=activity_runner,
                )
            )
            llm = FakeLLM(
                [
                    _assistant_response(
                        "",
                        [
                            LLMToolCall(
                                id="call_image",
                                name="get_activity_leaderboard",
                                arguments=json.dumps(
                                    {"leaderboard_type": "race"},
                                    ensure_ascii=False,
                                ),
                            ),
                            LLMToolCall(
                                id="call_search",
                                name="search_game_knowledge",
                                arguments=json.dumps(
                                    {"query": "竞速"}, ensure_ascii=False
                                ),
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
        normalized_path = Path(response.image_path)
        self.assertEqual(normalized_path.name, "image.png")
        self.assertIn("activity_leaderboard", normalized_path.parts)
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

    def test_llm_client_preserves_reasoning_fields_for_tool_followup(self) -> None:
        client = LLMClient(_settings())

        normalized = client._normalize_assistant_message(
            {
                "role": "assistant",
                "content": None,
                "reasoning_content": "先查询功能列表。",
                "reasoning_details": [{"type": "summary", "text": "查询工具"}],
                "tool_calls": [
                    {
                        "id": "call_reasoning",
                        "type": "function",
                        "function": {"name": "list_features", "arguments": "{}"},
                    }
                ],
            }
        )

        self.assertIsNone(normalized["content"])
        self.assertEqual(normalized["reasoning_content"], "先查询功能列表。")
        self.assertEqual(
            normalized["reasoning_details"],
            [{"type": "summary", "text": "查询工具"}],
        )
        self.assertEqual(normalized["tool_calls"][0]["id"], "call_reasoning")

    async def test_reasoning_content_round_trips_after_tool_call(self) -> None:
        first_turn = _tool_response("list_features", call_id="call_reasoning")
        first_turn.assistant_message["reasoning_content"] = "先查询功能列表。"
        llm = FakeLLM([first_turn, _assistant_response("功能已列出")])
        runner = AgentRunner(
            llm=llm,  # type: ignore[arg-type]
            memory=GroupChatMemory(history_turns=3),
            settings=_settings(),
            tool_registry=ToolRegistry(_runtime()),
            context_manager=FakeContextManager(),  # type: ignore[arg-type]
        )

        response = await runner.run(_event(), trace_id="reasoning-roundtrip")

        self.assertEqual(response.text, "功能已列出")
        assistant_turns = [
            message
            for message in llm.requests[1]["messages"]
            if message.get("role") == "assistant" and message.get("tool_calls")
        ]
        self.assertEqual(assistant_turns[0]["reasoning_content"], "先查询功能列表。")

    async def test_non_retryable_http_error_is_not_retried_for_same_model(
        self,
    ) -> None:
        client = LLMClient(_settings())
        failure = HTTPError(
            url="https://example.invalid/chat/completions",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=io.BytesIO(b'{"error":{"message":"invalid request"}}'),
        )

        with patch(
            "bot_app.llm_client.request.urlopen", side_effect=failure
        ) as urlopen:
            with self.assertRaises(LLMRequestError) as raised:
                await client.chat_completion(
                    messages=[{"role": "user", "content": "hello"}],
                    trace_id="non-retryable",
                )

        self.assertFalse(raised.exception.retryable)
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(urlopen.call_count, 1)

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
                {
                    "role": "assistant",
                    "turn_type": "assistant_reply",
                    "text": "我帮你查活动",
                },
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

    def test_bot_database_migrates_legacy_messages_table_before_creating_indexes(
        self,
    ) -> None:
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

    def test_build_message_record_metadata_marks_meaningful_text_and_mentions(
        self,
    ) -> None:
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
                "context_compact_max_chars": 1000,
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
                            "event": {"text": "最近 boss 什么时间开" * 80},
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
