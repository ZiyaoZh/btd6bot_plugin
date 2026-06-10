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
from bot_app.llm_client import LLMClient, LLMResponse, LLMToolCall  # noqa: E402
from bot_app.memory import GroupChatMemory  # noqa: E402
from bot_app.protocol import CommandResultPayload, EventPayload, IntentPayload  # noqa: E402
from bot_app.settings import AppSettings  # noqa: E402


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
        self._db = FakeDb()
        self._manual_file = Path("/missing/manual.md")

    async def execute(
        self, intent: IntentPayload, event: EventPayload
    ) -> CommandResultPayload:
        self.calls.append(intent)
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
        registry = ToolRegistry(command_processor)  # type: ignore[arg-type]

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
        registry = ToolRegistry(command_processor)  # type: ignore[arg-type]

        result = await registry.execute(
            "today_most_active",
            {"top_n": 5},
            _event("今日活跃榜前5"),
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["rows"], command_processor._db.active_rows)
        self.assertEqual(command_processor.calls, [])

    async def test_concrete_manual_tool_reads_runtime_manual_file(self) -> None:
        command_processor = FakeCommandProcessor()
        with tempfile.TemporaryDirectory() as temp_dir:
            manual_path = Path(temp_dir) / "manual.md"
            manual_path.write_text("启动方式：运行 main.py", encoding="utf-8")
            command_processor._manual_file = manual_path
            registry = ToolRegistry(command_processor)  # type: ignore[arg-type]

            result = await registry.execute(
                "script_manual_query",
                {"query": "怎么启动"},
                _event("怎么启动脚本"),
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["manual_full_text"], "启动方式：运行 main.py")
        self.assertEqual(command_processor.calls, [])

    async def test_agent_executes_tool_call_and_returns_final_text(self) -> None:
        command_processor = FakeCommandProcessor()
        registry = ToolRegistry(command_processor)  # type: ignore[arg-type]
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
        registry = ToolRegistry(command_processor)  # type: ignore[arg-type]
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
        command_processor.next_result = {
            "module": "command_processor",
            "ok": True,
            "command": "get_activity_leaderboard",
            "data": {"leaderboard_type": "race"},
            "message": "排行榜图片已生成",
            "extra_prompt": "请给出一条简短说明，随后发送图片。",
            "response_type": "image",
            "image_path": "/tmp/race.png",
        }
        registry = ToolRegistry(command_processor)  # type: ignore[arg-type]
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
        self.assertEqual(response.image_path, "/tmp/race.png")
        self.assertEqual(response.text, "竞速排行榜来啦")

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
