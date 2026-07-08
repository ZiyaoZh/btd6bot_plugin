"""Legacy command-intent adapter.

The plugin main path uses AgentRunner + ToolRegistry. Keep this module as a
compatibility adapter for the historical IntentPayload protocol only; new tool
business logic belongs in bot_app.tools service modules.
"""

from __future__ import annotations

from pathlib import Path

from .db_manager import BotDatabase
from .protocol import CommandResultPayload, EventPayload, IntentPayload
from .tools.command_guide_service import build_view_command_guide_result
from .tools.features import get_feature_catalog
from .tools.knowledge_service import (
    build_read_game_knowledge_file_result,
    build_search_game_knowledge_result,
)
from .tools.manual_service import build_script_manual_query_result
from .tools.mini_game_service import build_monkey_box_opening_result
from .tools.web_search_service import build_search_web_result
from .tools.social_service import (
    build_summarize_recent_messages_result,
    build_today_most_active_result,
)
from .tools.activity_service import (
    build_activity_detail_result,
    build_activity_leaderboard_result,
    build_collection_event_info_result,
)


class CommandProcessor:
    """Compatibility router for legacy command intents."""

    def __init__(
        self, db: BotDatabase, manual_file: str, command_guide_dir: str
    ) -> None:
        self._db = db
        self._runtime_root_dir = Path(__file__).resolve().parent.parent

        manual_path = Path(manual_file)
        self._manual_file = (
            manual_path
            if manual_path.is_absolute()
            else self._runtime_root_dir / manual_path
        )

        guide_dir_path = Path(command_guide_dir)
        self._command_guide_dir = (
            guide_dir_path
            if guide_dir_path.is_absolute()
            else self._runtime_root_dir / guide_dir_path
        )

        self._kb_root_dir = self._runtime_root_dir / "btd6_knowledge_base"
        self._api_processor_root = self._runtime_root_dir / "btd6_api_processor"
        self._chat_monkey_root = self._runtime_root_dir / "ChatMonkeyTD"
        self._manual_text_cache = ""

    async def execute(
        self, intent: IntentPayload, event: EventPayload
    ) -> CommandResultPayload:
        command_name = intent.get("intent", "unknown")
        arguments = intent.get("arguments", {}) or {}

        if command_name in {"list", "help", "list_features"}:
            return self._handle_list_features()

        if command_name in {
            "view_command_guide",
            "command_guide",
            "read_command_guide",
        }:
            return self._handle_view_command_guide(arguments)

        if command_name in {
            "script_manual_query",
            "manual_query",
            "manual_qa",
            "doc_query",
        }:
            return self._handle_script_manual_query(event, arguments)

        if command_name == "today_most_active":
            return self._handle_today_most_active(event, arguments)

        if command_name in {
            "summarize_recent_messages",
            "recent_chat_summary",
            "群聊总结",
        }:
            return self._handle_summarize_recent_messages(event, arguments)

        if command_name in {
            "search_game_knowledge",
            "game_kb_search",
            "btd6_kb_search",
        }:
            return self._handle_search_game_knowledge(event, arguments)

        if command_name in {"read_game_knowledge_file", "read_kb_file", "kb_read"}:
            return self._handle_read_game_knowledge_file(arguments)

        if command_name in {"search_web", "web_search", "联网搜索", "网页搜索"}:
            return self._handle_search_web(arguments)

        if command_name in {
            "get_activity_detail",
            "activity_detail",
            "game_activity_detail",
        }:
            return await self._handle_get_activity_detail(arguments)

        if command_name in {
            "get_activity_leaderboard",
            "activity_leaderboard",
            "game_leaderboard",
        }:
            return await self._handle_get_activity_leaderboard(arguments)

        if command_name in {
            "get_collection_event_info",
            "collection_event",
            "collection_schedule",
        }:
            return await self._handle_get_collection_event_info(arguments)

        if command_name in {
            "simulate_monkey_box_opening",
            "open_monkey_box",
            "game_box_opening",
        }:
            return await self._handle_simulate_monkey_box_opening(arguments)

        return {
            "module": "command_processor",
            "ok": False,
            "command": command_name,
            "data": {},
            "message": f"暂不支持该指令: {command_name}",
            "extra_prompt": "",
            "response_type": "text",
            "image_path": "",
        }

    def _handle_today_most_active(
        self, event: EventPayload, arguments: dict
    ) -> CommandResultPayload:
        return build_today_most_active_result(
            db=self._db,
            event=event,
            arguments=arguments,
            module="command_processor",
        )

    def _handle_list_features(self) -> CommandResultPayload:
        features = get_feature_catalog()

        return {
            "module": "command_processor",
            "ok": True,
            "command": "list_features",
            "data": {
                "feature_count": len(features),
                "features": features,
            },
            "message": "功能列表已整理完成",
            "extra_prompt": "",
            "response_type": "text",
            "image_path": "",
        }

    async def _handle_get_activity_detail(
        self, arguments: dict
    ) -> CommandResultPayload:
        return await build_activity_detail_result(
            api_processor_root=self._api_processor_root,
            arguments=arguments,
            module="command_processor",
        )

    async def _handle_get_activity_leaderboard(
        self, arguments: dict
    ) -> CommandResultPayload:
        return await build_activity_leaderboard_result(
            api_processor_root=self._api_processor_root,
            arguments=arguments,
            module="command_processor",
        )

    async def _handle_get_collection_event_info(
        self, arguments: dict
    ) -> CommandResultPayload:
        return await build_collection_event_info_result(
            api_processor_root=self._api_processor_root,
            arguments=arguments,
            module="command_processor",
        )

    async def _handle_simulate_monkey_box_opening(
        self, arguments: dict
    ) -> CommandResultPayload:
        return await build_monkey_box_opening_result(
            chat_monkey_root=self._chat_monkey_root,
            arguments=arguments,
            module="command_processor",
        )

    def _handle_view_command_guide(self, arguments: dict) -> CommandResultPayload:
        return build_view_command_guide_result(
            command_guide_dir=self._command_guide_dir,
            arguments=arguments,
            module="command_processor",
        )

    def _handle_script_manual_query(
        self, event: EventPayload, arguments: dict
    ) -> CommandResultPayload:
        result, manual_text = build_script_manual_query_result(
            manual_file=self._manual_file,
            manual_text_cache=self._manual_text_cache,
            event=event,
            arguments=arguments,
            module="command_processor",
        )
        self._manual_text_cache = manual_text
        return result

    def _handle_summarize_recent_messages(
        self, event: EventPayload, arguments: dict
    ) -> CommandResultPayload:
        return build_summarize_recent_messages_result(
            db=self._db,
            event=event,
            arguments=arguments,
            module="command_processor",
        )

    def _handle_search_game_knowledge(
        self, event: EventPayload, arguments: dict
    ) -> CommandResultPayload:
        return build_search_game_knowledge_result(
            kb_root=self._kb_root_dir,
            event=event,
            arguments=arguments,
            module="command_processor",
        )

    def _handle_read_game_knowledge_file(self, arguments: dict) -> CommandResultPayload:
        return build_read_game_knowledge_file_result(
            kb_root=self._kb_root_dir,
            arguments=arguments,
            module="command_processor",
        )

    def _handle_search_web(self, arguments: dict) -> CommandResultPayload:
        return build_search_web_result(
            arguments=arguments,
            module="command_processor",
        )
