from __future__ import annotations

from pathlib import Path
from typing import Any

from .agent import Agent
from .agent.tool_registry import ToolRegistry
from .application import BotApplication
from .db_manager import BotDatabase
from .llm_client import LLMClient
from .logging_utils import configure_flow_logger
from .media_store import ChatMediaStore
from .memory import GroupChatMemory
from .receiver import GroupMessageReceiver
from .sender import MessageSender
from .settings import AppSettings
from .tools import ToolDependencies
from .visual_summary import VisualSummaryService


def build_application(
    *,
    settings: AppSettings,
    bot: Any,
    plugin_root: Path,
) -> BotApplication:
    """Single composition root shared by plugin and standalone adapters."""

    configure_flow_logger(settings.flow_log_file)
    db = BotDatabase(settings.db_file)
    media_store = None
    if settings.vision_enabled:
        media_store = ChatMediaStore(
            settings.vision_cache_root,
            timeout_seconds=settings.vision_download_timeout_seconds,
            max_image_bytes=settings.vision_max_image_bytes,
            max_pixels=settings.vision_max_pixels,
            max_dimension=settings.vision_max_dimension,
            ttl_seconds=settings.vision_cache_ttl_seconds,
            capacity_bytes=settings.vision_cache_capacity_bytes,
        )
        media_store.cleanup()
    receiver = GroupMessageReceiver(settings, db=db, media_store=media_store)
    sender = MessageSender(bot)
    memory = GroupChatMemory(history_turns=settings.history_turns)
    llm = LLMClient(settings=settings)
    visual_summaries = VisualSummaryService(
        llm=llm,
        settings=settings,
        database=db,
    )
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
        knowledge_max_read_chars=settings.knowledge_max_read_chars,
        artifact_root=settings.artifact_root,
        artifact_activity_ttl_seconds=settings.artifact_activity_ttl_seconds,
        artifact_image_ttl_seconds=settings.artifact_image_ttl_seconds,
        artifact_default_ttl_seconds=settings.artifact_default_ttl_seconds,
        artifact_archive_retention_seconds=(
            settings.artifact_archive_retention_seconds
        ),
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
        artifact_store=dependencies.artifact_store,
        visual_summaries=visual_summaries,
    )
