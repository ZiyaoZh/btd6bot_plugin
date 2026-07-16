from __future__ import annotations

from pathlib import Path
from typing import Any

from .agent import Agent
from .agent.tool_registry import ToolRegistry
from .application import BotApplication
from .db_manager import BotDatabase
from .llm_client import LLMClient
from .logging_utils import configure_flow_logger
from .memory import GroupChatMemory
from .receiver import GroupMessageReceiver
from .sender import MessageSender
from .settings import AppSettings
from .tools import ToolDependencies


def build_application(
    *,
    settings: AppSettings,
    bot: Any,
    plugin_root: Path,
) -> BotApplication:
    """Single composition root shared by plugin and standalone adapters."""

    configure_flow_logger(settings.flow_log_file)
    db = BotDatabase(settings.db_file)
    receiver = GroupMessageReceiver(settings, db=db)
    sender = MessageSender(bot)
    memory = GroupChatMemory(history_turns=settings.history_turns)
    llm = LLMClient(settings=settings)
    dependencies = ToolDependencies.create(
        db=db,
        plugin_root=plugin_root,
        manual_file=settings.manual_file,
        image_gen_api_base_url=settings.image_gen_api_base_url,
        image_gen_api_key=settings.image_gen_api_key,
        image_gen_model=settings.image_gen_model,
        image_gen_request_timeout=settings.image_gen_request_timeout,
        image_gen_max_retries=settings.image_gen_max_retries,
        image_gen_retry_delay_seconds=settings.image_gen_retry_delay_seconds,
        image_gen_use_proxy=settings.image_gen_use_proxy,
        image_gen_max_bytes=settings.image_gen_max_bytes,
    )
    registry = ToolRegistry(dependencies)
    agent = Agent(
        llm=llm,
        memory=memory,
        settings=settings,
        tool_registry=registry,
        tool_dependencies=dependencies,
    )
    return BotApplication(
        settings=settings,
        receiver=receiver,
        sender=sender,
        agent=agent,
        llm=llm,
        database=db,
    )
